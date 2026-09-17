"""实验 3：椭圆曲线与数字签名——从“时钟算术”一路走到验证中本聪的真实签名。
对应教程第 4 章。运行：python labs/lab03_elliptic_curve.py

这里的椭圆曲线运算全部是用几十行纯 Python 从零写的（只用到高中数学 + 模运算），
最后会和项目里实际使用的 ecdsa 库互相对答案。
"""
import json
import pathlib

from _common import ROOT, step, title

from bitcoin.hashes import hash256
from bitcoin.key import CKey
from bitcoin.script import SIGHASH_ALL, CScript, signature_hash
from bitcoin.serialize import DataStream
from bitcoin.tx import CTransaction

title("实验 3：椭圆曲线与数字签名")


# ----------------------------------------------------------------------------
# 第一部分：模运算（“时钟算术”）
# ----------------------------------------------------------------------------
step("1. 模运算：只看余数的算术")
print("   钟面上 10 点再过 5 小时是 3 点：(10 + 5) mod 12 =", (10 + 5) % 12)
print("   在“模 17”的世界里：13 + 9 =", (13 + 9) % 17, "， 13 × 9 =", (13 * 9) % 17)

step("2. 模运算里的“除法”：乘以倒数（模逆元）")
p = 17
inv5 = pow(5, -1, p)            # Python 内置：5 在模 17 下的倒数
print(f"   5 的倒数是 {inv5}，因为 5 × {inv5} = {5 * inv5} = {5 * inv5 % p} (mod 17)")
print(f"   所以 3 ÷ 5 (mod 17) = 3 × {inv5} mod 17 = {3 * inv5 % p}；验算：{3 * inv5 % p} × 5 mod 17 = {3 * inv5 % p * 5 % p}")
print("   要点：模一个质数时，除了 0 以外每个数都有倒数——加减乘除全都能做，这叫“有限域”。")


# ----------------------------------------------------------------------------
# 第二部分：一条玩具椭圆曲线  y² = x³ + 7 (mod 17)
# ----------------------------------------------------------------------------
def make_curve(p, a, b):
    """返回这条曲线上的“点加法”和“数乘”两个函数。None 代表无穷远点（加法里的 0）。"""

    def add(P, Q):
        if P is None:
            return Q
        if Q is None:
            return P
        (x1, y1), (x2, y2) = P, Q
        if x1 == x2 and (y1 + y2) % p == 0:
            return None                                     # P + (-P) = 0
        if P == Q:
            k = (3 * x1 * x1 + a) * pow(2 * y1, -1, p) % p  # 切线的斜率
        else:
            k = (y2 - y1) * pow(x2 - x1, -1, p) % p         # 两点连线的斜率
        x3 = (k * k - x1 - x2) % p
        return x3, (k * (x1 - x3) - y1) % p

    def mul(n, P):
        """计算 n·P = P + P + ... + P（n 个）。用“倍加法”，只需要约 log2(n) 步。"""
        result = None
        while n:
            if n & 1:
                result = add(result, P)
            P = add(P, P)
            n >>= 1
        return result

    return add, mul


step("3. 玩具曲线 y² = x³ + 7 (mod 17) 上一共有哪些点？")
points = [(x, y) for x in range(17) for y in range(17) if (y * y - x**3 - 7) % 17 == 0]
print(f"   共 {len(points)} 个点（再加一个“无穷远点”共 {len(points) + 1} 个）：")
print("  ", points)

step("4. 点的“加法”：两个点相加，得到的还是曲线上的点")
add17, mul17 = make_curve(17, 0, 7)
P, Q = (1, 5), (2, 10)
R = add17(P, Q)
print(f"   {P} + {Q} = {R}，它在曲线上吗？{R in points}")

step("5. 从一个点 G 出发不断自加：G, 2G, 3G, ... 会绕一圈再回到起点")


def order_of(point):
    """一个点自加多少次会变成无穷远点，这个次数叫它的“阶”。"""
    k, acc = 1, point
    while acc is not None:
        acc = add17(acc, point)
        k += 1
    return k


G17 = max(points, key=order_of)             # 挑一个阶最大的点当“基点”
order = order_of(G17)
walk = [mul17(k, G17) for k in range(1, order + 2)]
print(f"   选基点 G = {G17}，它的阶是 {order}")
print("  ", walk)
print(f"   第 {order} 个是 None（无穷远点，相当于加法里的 0），第 {order + 1} 个又回到了 G")

step("6. 单向性：已知 k 求 k·G 很容易；已知 k·G 反求 k 只能一个个试")
secret = 11
public = mul17(secret, G17)
print(f"   私钥 k = {secret}  ->  公钥 k·G = {public}")
found = next(k for k in range(1, order) if mul17(k, G17) == public)
print(f"   攻击者从 1 开始挨个试：试到 k = {found} 撞上了。")
print(f"   玩具曲线只有 {order} 种可能，当然一试就中；真曲线有约 10^77 种可能，试到宇宙毁灭也试不完。")


# ----------------------------------------------------------------------------
# 第三部分：真正的 secp256k1
# ----------------------------------------------------------------------------
P_FIELD = 2**256 - 2**32 - 977
N_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)
add, mul = make_curve(P_FIELD, 0, 7)

step("7. 比特币用的曲线 secp256k1：还是 y² = x³ + 7，只是模数换成了一个 256 位的大质数")
print(f"   p = 2^256 - 2^32 - 977 = {P_FIELD}")
print(f"   曲线上点的个数 n ≈ {float(N_ORDER):.3e} —— 大约是宇宙中原子总数（~10^80）的千分之一")
print(f"   基点 G 在曲线上吗？{(G[1] ** 2 - G[0] ** 3 - 7) % P_FIELD == 0}")
print(f"   n·G 是无穷远点吗？{mul(N_ORDER, G) is None}")

step("8. 用我们自己写的曲线运算生成公钥，和项目用的 ecdsa 库对答案")
key = CKey.generate()
secret = int.from_bytes(key.get_secret(), "big")
our_point = mul(secret, G)
our_pubkey = b"\x04" + our_point[0].to_bytes(32, "big") + our_point[1].to_bytes(32, "big")
print(f"   私钥（一个随机的 256 位整数）：{secret:064x}")
print(f"   公钥 = 私钥·G：x = {our_point[0]:064x}")
print(f"                  y = {our_point[1]:064x}")
print(f"   与 ecdsa 库算出的公钥一致吗？{our_pubkey == key.get_pubkey()}")
assert our_pubkey == key.get_pubkey()


# ----------------------------------------------------------------------------
# 第四部分：ECDSA 签名与验签
# ----------------------------------------------------------------------------
def ecdsa_sign(secret: int, z: int, k: int):
    """签名：R = k·G，r = R.x，s = (z + r·私钥) / k        （全部 mod n）"""
    r = mul(k, G)[0] % N_ORDER
    s = (z + r * secret) * pow(k, -1, N_ORDER) % N_ORDER
    return r, s


def ecdsa_verify(pubkey_point, z: int, r: int, s: int) -> bool:
    """验签：算 P = (z/s)·G + (r/s)·公钥，看 P.x 是否等于 r。全程不需要私钥。"""
    if not (0 < r < N_ORDER and 0 < s < N_ORDER):
        return False
    w = pow(s, -1, N_ORDER)
    point = add(mul(z * w % N_ORDER, G), mul(r * w % N_ORDER, pubkey_point))
    return point is not None and point[0] % N_ORDER == r


def parse_der(sig: bytes):
    """DER 编码：30 总长 02 r长 r 02 s长 s"""
    assert sig[0] == 0x30 and sig[2] == 0x02
    r_len = sig[3]
    r = int.from_bytes(sig[4:4 + r_len], "big")
    assert sig[4 + r_len] == 0x02
    s_len = sig[5 + r_len]
    s = int.from_bytes(sig[6 + r_len:6 + r_len + s_len], "big")
    return r, s


step("9. 手写 ECDSA：签名、验签、篡改")
digest = hash256(b"Pay Bob 10 BTC")
z = int.from_bytes(digest, "big")
r, s = ecdsa_sign(secret, z, k=123456789)
print(f"   r = {r:064x}\n   s = {s:064x}")
print(f"   用公钥验证：{ecdsa_verify(our_point, z, r, s)}")
z_forged = int.from_bytes(hash256(b"Pay Bob 99 BTC"), "big")
print(f"   把消息改成“付 99 BTC”后再验证：{ecdsa_verify(our_point, z_forged, r, s)}")

step("10. 交叉验证：库签的名，用我们手写的算法验；我们手写签的名，用库验")
lib_sig = key.sign(digest)
print(f"   库签名（DER 编码，{len(lib_sig)} 字节）：{lib_sig.hex()}")
print(f"   手写算法验库的签名：{ecdsa_verify(our_point, z, *parse_der(lib_sig))}")


def to_der(r, s):
    def enc(v):
        b = v.to_bytes((v.bit_length() + 8) // 8, "big")    # 多留一位，保证最高位是 0（正数）
        return b"\x02" + bytes([len(b)]) + b
    body = enc(r) + enc(s)
    return b"\x30" + bytes([len(body)]) + body


print(f"   库验手写的签名：    {CKey.verify_with_pubkey(our_pubkey, digest, to_der(r, s))}")

step("11. 重头戏：用这几十行手写代码，验证中本聪 2009 年付给 Hal Finney 的那个真实签名")
vectors = json.loads((pathlib.Path(ROOT) / "tests" / "data" / "mainnet_vectors.json")
                     .read_text(encoding="utf-8"))
prev = CTransaction.deserialize(DataStream(bytes.fromhex(vectors["block9_coinbase"]["hex"])))
tx = CTransaction.deserialize(DataStream(bytes.fromhex(vectors["block170_first_payment"]["hex"])))
(_, sig_with_type), = list(CScript(tx.vin[0].script_sig).ops())
(_, satoshi_pubkey), _ = list(CScript(prev.vout[0].script_pubkey).ops())
sighash = signature_hash(CScript(prev.vout[0].script_pubkey), tx, 0, SIGHASH_ALL)
z = int.from_bytes(sighash.to_bytes(32, "little"), "big")
Q = (int.from_bytes(satoshi_pubkey[1:33], "big"), int.from_bytes(satoshi_pubkey[33:], "big"))
r, s = parse_der(sig_with_type[:-1])
print(f"   中本聪的公钥在曲线上吗？{(Q[1] ** 2 - Q[0] ** 3 - 7) % P_FIELD == 0}")
print(f"   签名 r = {r:064x}")
print(f"   签名 s = {s:064x}")
print(f"   验证结果：{ecdsa_verify(Q, z, r, s)}")
assert ecdsa_verify(Q, z, r, s)
print("\n实验 3 完成。")
