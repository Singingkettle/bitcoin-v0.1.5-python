"""实验 2：哈希函数——数据的“指纹”。
对应教程第 3 章。运行：python labs/lab02_hash.py
"""
import time

from _common import step, title

from bitcoin.hashes import hash160, hash256, sha256

title("实验 2：哈希函数")

step("1. 不管输入多长，输出永远是 32 字节")
for msg in (b"", b"a", b"bitcoin", b"x" * 1_000_000):
    print(f"   输入 {len(msg):>7} 字节 -> {sha256(msg).hex()}")

step("2. 雪崩效应：输入只差一个字符，输出面目全非")
a, b = sha256(b"I owe Alice 10 BTC"), sha256(b"I owe Alice 11 BTC")
print(f"   ...10 BTC -> {a.hex()}")
print(f"   ...11 BTC -> {b.hex()}")
diff_bits = bin(int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).count("1")
print(f"   256 个比特里有 {diff_bits} 个不同（理想值是一半，即 128 左右）")

step("3. 确定性：同样的输入，永远得到同样的输出")
print("  ", sha256(b"bitcoin") == sha256(b"bitcoin"))

step("4. 单向性：知道输出，想反推输入，只能一个个去试")
target_prefix = "0000"
start = time.time()
nonce = 0
while not sha256(f"hello{nonce}".encode()).hex().startswith(target_prefix):
    nonce += 1
print(f"   想让 sha256('hello'+数字) 以 {target_prefix} 开头，试了 {nonce + 1} 次才找到：hello{nonce}")
print(f"   {sha256(f'hello{nonce}'.encode()).hex()}  （用时 {time.time() - start:.2f} 秒）")
print(f"   每多要求一个十六进制的 0，平均要多试 16 倍。这就是“工作量证明”的雏形。")

step("5. 比特币用的两个组合")
data = b"bitcoin"
print(f"   Hash()    = SHA256(SHA256(x))    -> {hash256(data).hex()}   用于区块哈希、交易哈希")
print(f"   Hash160() = RIPEMD160(SHA256(x)) -> {hash160(data).hex()}   用于地址（只有 20 字节，更短）")

step("6. 亲手算出比特币创世块的哈希")
header = (
    (1).to_bytes(4, "little")                                # 版本
    + bytes(32)                                              # 上一个区块的哈希：创世块没有上一个，全 0
    + bytes.fromhex("4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b")[::-1]
    + (1231006505).to_bytes(4, "little")                     # 时间：2009-01-03 18:15:05 UTC
    + (0x1D00FFFF).to_bytes(4, "little")                     # 难度
    + (2083236893).to_bytes(4, "little"))                    # 中本聪找到的那个 nonce
print(f"   区块头一共 {len(header)} 字节")
print(f"   两次 SHA-256 再反转字节序：{hash256(header)[::-1].hex()}")
print("   和任何区块浏览器上查到的创世块哈希一模一样。")
print("\n实验 2 完成。")
