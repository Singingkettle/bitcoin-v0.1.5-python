"""实验 7：区块、默克尔树、亲手重建比特币创世块。
对应教程第 8 章。运行：python labs/lab07_block_merkle.py
"""
from _common import step, title

from bitcoin import params
from bitcoin.block import CBlock
from bitcoin.hashes import hash256
from bitcoin.script import OP_CHECKSIG, CScript, script_to_asm
from bitcoin.serialize import uint256_to_hex
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut

title("实验 7：区块与默克尔树")


def fake_tx(n: int) -> CTransaction:
    return CTransaction(vin=[CTxIn(COutPoint(n, 0), b"\x51")], vout=[CTxOut(n, b"\x51")])


def H(a: int, b: int) -> int:
    return int.from_bytes(hash256(a.to_bytes(32, "little") + b.to_bytes(32, "little")), "little")


def short(n: int) -> str:
    return uint256_to_hex(n)[:8]


step("1. 五笔交易的默克尔树（奇数个时，最后一个和自己配对）")
txs = [fake_tx(i) for i in range(1, 6)]
block = CBlock(vtx=txs)
h = [t.get_hash() for t in txs]
print("   叶子（交易哈希）：", "  ".join(short(x) for x in h))
l1 = [H(h[0], h[1]), H(h[2], h[3]), H(h[4], h[4])]
print("   第一层：          ", "  ".join(short(x) for x in l1), "   <- 第三个 = H(tx5, tx5)")
l2 = [H(l1[0], l1[1]), H(l1[2], l1[2])]
print("   第二层：          ", "  ".join(short(x) for x in l2))
root = H(l2[0], l2[1])
print("   根：              ", short(root))
print(f"   和 CBlock.get_merkle_root() 的结果一致吗？{root == block.get_merkle_root()}")

step("2. 改动任何一笔交易的任何一个比特，根都会变")
txs[2].vout[0].n_value += 1
print(f"   改动前的根 {short(root)} -> 改动后 {short(CBlock(vtx=txs).get_merkle_root())}")
txs[2].vout[0].n_value -= 1

step("3. 默克尔证明：不下载整个区块，也能证明“tx3 在这个区块里”")
print("   只需要 3 个哈希：tx4 的哈希、第一层的第 1 个、第二层的第 2 个")
proof = [(h[3], "右"), (l1[0], "左"), (l2[1], "右")]
acc = h[2]
for sibling, side in proof:
    acc = H(acc, sibling) if side == "右" else H(sibling, acc)
print(f"   沿着路径一路哈希上去得到 {short(acc)}，等于根吗？{acc == root}")
print("   区块里有 N 笔交易时，证明只需要 log2(N) 个哈希——几千笔交易也只要十几个。")
print("   这就是白皮书第 8 节“简化支付验证（SPV）”的基础：手机钱包不用存整条链。")

step("4. 区块头只有 80 字节，却“锁死”了区块里的每一笔交易")
block = CBlock(n_version=1, hash_prev_block=12345, n_time=1700000000, n_bits=0x1F00FFFF, vtx=txs)
block.hash_merkle_root = block.get_merkle_root()
print(f"   区块头 {len(block.header_bytes())} 字节 = 版本4 + 前块哈希32 + 默克尔根32 + 时间4 + 难度4 + nonce4")
print(f"   {block.header_bytes().hex()}")
before = block.get_hash()
block.n_nonce += 1
print(f"   区块哈希（nonce=0）：{uint256_to_hex(before)}")
print(f"   区块哈希（nonce=1）：{uint256_to_hex(block.get_hash())}")

step("5. 按中本聪源码里的写法，逐字段重建真实的比特币创世块")
timestamp = b"The Times 03/Jan/2009 Chancellor on brink of second bailout for banks"
pubkey = bytes.fromhex(
    "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
    "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f")
coinbase = CTransaction(
    vin=[CTxIn(COutPoint(), CScript().push_int(486604799).push_bignum(4).push_data(timestamp))],
    vout=[CTxOut(50 * params.COIN, CScript().push_data(pubkey).push_opcode(OP_CHECKSIG))])
genesis = CBlock(n_version=1, hash_prev_block=0, n_time=1231006505,
                 n_bits=0x1D00FFFF, n_nonce=2083236893, vtx=[coinbase])
genesis.hash_merkle_root = genesis.get_merkle_root()
print(f"   coinbase 的 scriptSig：{script_to_asm(coinbase.vin[0].script_sig)[:24]}...")
print(f"   其中第三段数据是一句话：{timestamp.decode()}")
print("   （《泰晤士报》2009 年 1 月 3 日头版标题：财政大臣即将对银行实施第二轮救助）")
print(f"   默克尔根 = {uint256_to_hex(genesis.hash_merkle_root)}")
print(f"   区块哈希 = {uint256_to_hex(genesis.get_hash())}")
assert uint256_to_hex(genesis.get_hash()) == (
    "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f")
print(f"   整个区块序列化后 {len(genesis.serialized())} 字节。和真实的创世块分毫不差。")
print("\n实验 7 完成。")
