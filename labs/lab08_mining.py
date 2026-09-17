"""实验 8：工作量证明——挖矿就是“掷一个 2^256 面的骰子”。
对应教程第 9 章。运行：python labs/lab08_mining.py   （约需 20~40 秒）
"""
import time

from _common import step, title

from bitcoin import params
from bitcoin.block import CBlock, CBlockIndex
from bitcoin.blockchain import Blockchain
from bitcoin.hashes import hash256

title("实验 8：工作量证明与难度")


def mine_header(prefix: bytes, target: int, start_nonce: int = 0):
    """从 start_nonce 开始试，返回 (成功的 nonce, 试了多少次)。"""
    nonce = start_nonce
    while int.from_bytes(hash256(prefix + (nonce & 0xFFFFFFFF).to_bytes(4, "little")), "little") > target:
        nonce += 1
    return nonce, nonce - start_nonce + 1


step("1. “难度目标”是一个 256 位的大数：区块哈希必须 <= 它")
for name, bits in (("主网创世难度", 0x1D00FFFF), ("本私网创世难度", 0x1F00FFFF)):
    target = params.compact_to_target(bits)
    p = target / 2**256
    print(f"   {name} nBits={bits:#x}")
    print(f"      目标值 = {target:064x}")
    print(f"      一次哈希命中的概率 = 目标值 / 2^256 ≈ 1/{1 / p:,.0f}")
print("   哈希值可以看成 0 到 2^256 之间的一个均匀随机数；目标值越小，越难掷中。")

step("2. 先测一下这台机器的算力")
prefix = bytes(76)
t0 = time.time()
for nonce in range(200_000):
    hash256(prefix + nonce.to_bytes(4, "little"))
hashrate = 200_000 / (time.time() - t0)
print(f"   约 {hashrate:,.0f} 次哈希/秒（纯 Python + hashlib，单线程）")

step("3. 难度每提高 16 倍（目标值多一个十六进制的 0），平均尝试次数也提高 16 倍")
print(f"   {'目标值（前几位）':<22}{'理论平均次数':>14}{'实测平均次数':>14}{'每块平均耗时':>14}")
for zeros, rounds in ((2, 40), (3, 40), (4, 20), (5, 6)):
    target = (1 << (256 - 4 * zeros)) - 1
    expected = 2**256 / target
    total, start = 0, 0
    t0 = time.time()
    for r in range(rounds):
        header_prefix = r.to_bytes(4, "little") * 19        # 每轮换一个不同的“区块头”
        _, tries = mine_header(header_prefix, target)
        total += tries
    elapsed = (time.time() - t0) / rounds
    print(f"   {'0' * zeros + 'fff...':<26}{expected:>14,.0f}{total / rounds:>16,.0f}{elapsed:>14.3f} 秒")
print("   实测值围绕理论值波动——挖矿是纯粹的概率游戏，没有任何捷径。")

step("4. 不对称性：找到答案要试几万次，验证答案只要 1 次")
target = params.compact_to_target(0x1F00FFFF)
nonce, tries = mine_header(bytes(76), target)
digest = hash256(bytes(76) + nonce.to_bytes(4, "little"))
print(f"   试了 {tries:,} 次找到 nonce = {nonce}")
print(f"   任何人验证：hash = {digest[::-1].hex()}")
print(f"              <= 目标值吗？{int.from_bytes(digest, 'little') <= target}  （一次哈希，瞬间完成）")

step("5. 难度调整：每 2016 个块回头看一次，花的时间比两周短就调难，长就调易")
next_bits = Blockchain.get_next_work_required


class _Stub:                       # get_next_work_required 不依赖实例状态，随便给个壳
    pass


def simulate(spacing_seconds, bits):
    prev, last = None, None
    for h in range(params.INTERVAL):
        idx = CBlockIndex(CBlock(n_time=1_700_000_000 + h * spacing_seconds, n_bits=bits), h)
        idx.n_height, idx.pprev = h, prev
        prev = last = idx
    return next_bits(_Stub(), last)


bits = params.target_to_compact(params.PROOF_OF_WORK_LIMIT >> 16)
old = params.compact_to_target(bits)
print(f"   {'这 2016 个块的平均出块间隔':<26}{'新目标值 / 旧目标值':>20}")
for spacing, label in ((600, "10 分钟（正好）"), (300, "5 分钟（太快）"), (1200, "20 分钟（太慢）"),
                       (10, "10 秒（快得离谱）"), (60000, "1000 分钟（慢得离谱）")):
    ratio = params.compact_to_target(simulate(spacing, bits)) / old
    print(f"   {label:<30}{ratio:>18.4f}")
print("   注意两点：① 单次调整最多 4 倍；② “正好 10 分钟”时比值是 2015/2016 而不是 1，")
print("   因为原版代码往回数的是 2015 个间隔，却拿去和完整的两周比——一个保留至今的小失误。")

step("6. 预测：如果让这台机器一直挖本私网，难度会怎么演化？")
target = params.compact_to_target(params.GENESIS_BITS)
elapsed_total = 0.0
print(f"   {'区块高度':<14}{'每块平均耗时':>14}{'累计用时':>16}")
for period in range(12):
    per_block = (2**256 / target) / hashrate
    print(f"   {period * 2016:>6} 起{per_block:>16.2f} 秒{elapsed_total / 3600:>14.2f} 小时")
    elapsed_total += per_block * 2016
    actual = min(max(per_block * 2015, params.TARGET_TIMESPAN / 4), params.TARGET_TIMESPAN * 4)
    target = min(int(target * actual / params.TARGET_TIMESPAN), params.PROOF_OF_WORK_LIMIT)
    if abs(per_block - 600) < 60:
        break
print("   难度会一路上调，直到出块间隔稳定在 10 分钟左右——不管算力是一台笔记本还是全世界的矿场。")
print("\n实验 8 完成。")
