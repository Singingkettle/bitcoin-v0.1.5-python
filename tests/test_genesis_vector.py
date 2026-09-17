"""用本项目的代码，按原版 LoadBlockIndex() 里的写法，逐字段重建**真实的主网创世块**，
然后核对默克尔根和区块哈希。这一个测试同时钉死了：交易/区块的序列化、
CScript 的压栈编码、默克尔树、双 SHA-256。"""

from bitcoin import params
from bitcoin.block import CBlock
from bitcoin.blockchain import build_genesis_block
from bitcoin.script import OP_CHECKSIG, CScript
from bitcoin.serialize import DataStream, uint256_to_hex
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut

SATOSHI_PUBKEY = bytes.fromhex(
    "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
    "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f")
TIMESTAMP = b"The Times 03/Jan/2009 Chancellor on brink of second bailout for banks"


def build_mainnet_genesis() -> CBlock:
    # 原版：txNew.vin[0].scriptSig = CScript() << 486604799 << CBigNum(4) << 时间戳文字
    # 注意 CBigNum(4) 是"数据压栈"(01 04)，而 << 4 会变成单字节的 OP_4。
    # 主网创世块的 scriptSig 以 04ffff001d0104 开头，靠的就是这个区别。
    script_sig = CScript().push_int(486604799).push_bignum(4).push_data(TIMESTAMP)
    script_pubkey = CScript().push_data(SATOSHI_PUBKEY).push_opcode(OP_CHECKSIG)
    txnew = CTransaction(vin=[CTxIn(COutPoint(), script_sig)],
                         vout=[CTxOut(50 * params.COIN, script_pubkey)])
    block = CBlock(n_version=1, hash_prev_block=0, n_time=1231006505,
                   n_bits=0x1D00FFFF, n_nonce=2083236893, vtx=[txnew])
    block.hash_merkle_root = block.get_merkle_root()
    return block


def test_genesis_coinbase_scriptsig_encoding():
    script_sig = CScript().push_int(486604799).push_bignum(4).push_data(TIMESTAMP)
    # 原版源码注释里给出的 coinbase 十六进制
    assert bytes(script_sig).hex() == (
        "04ffff001d0104455468652054696d65732030332f4a616e2f32303039204368616e63656c6c6f72"
        "206f6e206272696e6b206f66207365636f6e64206261696c6f757420666f722062616e6b73")


def test_genesis_merkle_root():
    assert uint256_to_hex(build_mainnet_genesis().hash_merkle_root) == (
        "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b")


def test_genesis_block_hash():
    assert uint256_to_hex(build_mainnet_genesis().get_hash()) == (
        "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f")


def test_genesis_full_serialization_is_285_bytes_and_roundtrips():
    block = build_mainnet_genesis()
    data = block.serialized()
    assert len(data) == 285                     # 主网创世块的著名大小
    again = CBlock.deserialize(DataStream(data))
    assert again.serialized() == data
    assert again.get_hash() == block.get_hash()


def test_genesis_satisfies_mainnet_difficulty():
    block = build_mainnet_genesis()
    assert block.get_hash() <= params.compact_to_target(0x1D00FFFF)


# ------------------------------------------------------------ 私网创世块
def test_private_genesis_matches_params():
    block = build_genesis_block()
    assert uint256_to_hex(block.get_hash()) == params.GENESIS_HASH
    assert uint256_to_hex(block.hash_merkle_root) == params.GENESIS_MERKLE_ROOT
    assert block.check_proof_of_work()
    assert block.vtx[0].is_coinbase() and block.vtx[0].vout[0].n_value == 50 * params.COIN


def test_private_genesis_time_is_in_the_past():
    """创世时间必须是过去：否则后续区块会因为"时间戳超前两小时"而全部被拒。"""
    import time
    assert params.GENESIS_TIME < time.time()
