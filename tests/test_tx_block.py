"""交易与区块的数据结构：序列化布局、定稿/替换规则、手续费、默克尔树、区块定位器。"""

import pytest

from bitcoin import params
from bitcoin.block import CBlock, CBlockIndex, CBlockLocator
from bitcoin.hashes import hash256
from bitcoin.serialize import DataStream
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut

COIN, CENT = params.COIN, params.CENT


def simple_tx(**kw) -> CTransaction:
    return CTransaction(vin=[CTxIn(COutPoint(7, 0), b"\x51")],
                        vout=[CTxOut(COIN, b"\x51")], **kw)


# ---------------------------------------------------------------- 交易
def test_transaction_byte_layout():
    tx = CTransaction(n_version=1,
                      vin=[CTxIn(COutPoint(0xAABB, 3), b"\x01\x02", 0xFFFFFFFE)],
                      vout=[CTxOut(5 * COIN, b"\x76\xa9")], n_lock_time=9)
    raw = tx.serialized()
    expected = (
        "01000000"                                  # nVersion
        "01"                                        # 输入个数
        + (0xAABB).to_bytes(32, "little").hex()     # prevout.hash
        + "03000000"                                # prevout.n
        + "02" + "0102"                             # scriptSig 长度 + 内容
        + "feffffff"                                # nSequence
        + "01"                                      # 输出个数
        + (5 * COIN).to_bytes(8, "little").hex()    # nValue
        + "02" + "76a9"                             # scriptPubKey 长度 + 内容
        + "09000000")                               # nLockTime
    assert raw.hex() == expected
    again = CTransaction.deserialize(DataStream(raw))
    assert again.serialized() == raw
    assert again.get_hash() == tx.get_hash() == int.from_bytes(hash256(raw), "little")
    assert tx.get_serialize_size() == len(raw)


def test_outpoint_null_and_equality():
    assert COutPoint().is_null()
    assert not COutPoint(1, 0xFFFFFFFF).is_null()
    assert not COutPoint(0, 0).is_null()
    assert COutPoint(5, 1) == COutPoint(5, 1)
    assert COutPoint(5, 1) != COutPoint(5, 2)
    assert len({COutPoint(5, 1), COutPoint(5, 1), COutPoint(6, 1)}) == 2


def test_is_coinbase():
    assert CTransaction(vin=[CTxIn()], vout=[CTxOut(1)]).is_coinbase()
    assert not simple_tx().is_coinbase()
    assert not CTransaction(vin=[CTxIn(), CTxIn()], vout=[CTxOut(1)]).is_coinbase()


def test_check_transaction_rules():
    assert simple_tx().check_transaction()
    assert not CTransaction(vin=[], vout=[CTxOut(1)]).check_transaction()
    assert not CTransaction(vin=[CTxIn(COutPoint(1, 0))], vout=[]).check_transaction()
    assert not CTransaction(vin=[CTxIn(COutPoint(1, 0))], vout=[CTxOut(-1)]).check_transaction()
    # 普通交易的输入不能是"空指针"
    assert not CTransaction(vin=[CTxIn(COutPoint(1, 0)), CTxIn()],
                            vout=[CTxOut(1)]).check_transaction()


@pytest.mark.parametrize("size,ok", [(0, False), (1, False), (2, True), (100, True), (101, False)])
def test_coinbase_script_size_limits(size, ok):
    tx = CTransaction(vin=[CTxIn(COutPoint(), b"\x00" * size)], vout=[CTxOut(1)])
    assert tx.check_transaction() is ok


def test_get_value_out():
    tx = CTransaction(vin=[CTxIn()], vout=[CTxOut(3), CTxOut(4)])
    assert tx.get_value_out() == 7
    tx.vout.append(CTxOut(-5))
    with pytest.raises(ValueError):
        tx.get_value_out()


# ---------------------------------------------------------------- 手续费
def tx_of_size(n_bytes: int) -> CTransaction:
    tx = CTransaction(vin=[CTxIn(COutPoint(1, 0), b"")], vout=[CTxOut(COIN, b"")])
    pad = n_bytes - tx.get_serialize_size()
    tx.vin[0].script_sig = b"\x00" * (pad - (2 if pad >= 253 else 0))
    assert tx.get_serialize_size() == n_bytes
    return tx


@pytest.mark.parametrize("n_bytes,full,discounted", [
    (200, CENT, 0),
    (999, CENT, 0),
    (1000, 2 * CENT, 0),                # 每"开始的"一千字节收一分：1000 字节就算第二个千字节了
    (9999, 10 * CENT, 0),
    (10000, 11 * CENT, 11 * CENT),      # 10K 及以上不再享受免费
])
def test_min_fee_by_size(n_bytes, full, discounted):
    tx = tx_of_size(n_bytes)
    assert tx.get_min_fee(False) == full
    assert tx.get_min_fee(True) == discounted


def test_dust_output_always_pays_one_cent():
    tx = simple_tx()
    tx.vout.append(CTxOut(CENT - 1, b""))
    assert tx.get_min_fee(True) == CENT
    tx.vout[-1].n_value = CENT                  # 恰好一分钱就不算粉尘
    assert tx.get_min_fee(True) == 0


# ------------------------------------------------------ 定稿与"可替换交易"
def test_is_final():
    assert simple_tx().is_final(n_best_height=0)                    # nLockTime=0：永远定稿
    locked = simple_tx(n_lock_time=100)
    assert locked.is_final(100)                                     # 序列号拉满 -> 定稿
    locked.vin[0].n_sequence = 5
    assert not locked.is_final(100)                                 # 还没到高度
    assert not locked.is_final(99)
    assert locked.is_final(101)                                     # nLockTime < 当前高度


def test_is_newer_than():
    def version(seq):
        tx = simple_tx(n_lock_time=500)
        tx.vin[0].n_sequence = seq
        return tx
    assert version(2).is_newer_than(version(1))
    assert not version(1).is_newer_than(version(2))
    assert not version(1).is_newer_than(version(1))
    # 输入不一样就根本不是"同一笔交易的不同版本"
    other = version(9)
    other.vin[0].prevout = COutPoint(8, 0)
    assert not other.is_newer_than(version(1))
    two_inputs = version(9)
    two_inputs.vin.append(CTxIn(COutPoint(3, 0)))
    assert not two_inputs.is_newer_than(version(1))


# ---------------------------------------------------------------- 区块
def test_block_header_is_80_bytes_and_hash_ignores_transactions():
    block = CBlock(n_version=1, hash_prev_block=5, hash_merkle_root=6,
                   n_time=7, n_bits=8, n_nonce=9, vtx=[simple_tx()])
    assert len(block.header_bytes()) == 80
    h = block.get_hash()
    block.vtx.append(simple_tx())               # 改交易列表但不更新默克尔根：哈希不变
    assert block.get_hash() == h
    block.n_nonce += 1                          # 改区块头的任何字段：哈希就变
    assert block.get_hash() != h


def test_block_serialization_roundtrip():
    block = CBlock(n_version=1, hash_prev_block=2**255, n_time=123, n_bits=0x1F00FFFF,
                   n_nonce=42, vtx=[simple_tx(), simple_tx(n_lock_time=3)])
    block.hash_merkle_root = block.get_merkle_root()
    again = CBlock.deserialize(DataStream(block.serialized()))
    assert again.serialized() == block.serialized()
    assert again.get_hash() == block.get_hash()
    assert [t.get_hash() for t in again.vtx] == [t.get_hash() for t in block.vtx]


def H(a: int, b: int) -> int:
    return int.from_bytes(hash256(a.to_bytes(32, "little") + b.to_bytes(32, "little")), "little")


class FakeTx:
    def __init__(self, h):
        self.h = h

    def get_hash(self):
        return self.h


def test_merkle_root_of_single_tx_is_the_tx_hash():
    assert CBlock(vtx=[FakeTx(77)]).get_merkle_root() == 77
    assert CBlock().get_merkle_root() == 0


def test_merkle_tree_shapes():
    """手工按定义计算 2、3、4、5 笔交易时的默克尔根，与实现对照。奇数个时最后一个与自己配对。"""
    t = [FakeTx(i + 1) for i in range(5)]
    assert CBlock(vtx=t[:2]).get_merkle_root() == H(1, 2)
    assert CBlock(vtx=t[:3]).get_merkle_root() == H(H(1, 2), H(3, 3))
    assert CBlock(vtx=t[:4]).get_merkle_root() == H(H(1, 2), H(3, 4))
    assert CBlock(vtx=t[:5]).get_merkle_root() == H(H(H(1, 2), H(3, 4)), H(H(5, 5), H(5, 5)))


def test_merkle_tree_layout():
    tree = CBlock(vtx=[FakeTx(1), FakeTx(2), FakeTx(3)]).build_merkle_tree()
    assert tree == [1, 2, 3, H(1, 2), H(3, 3), H(H(1, 2), H(3, 3))]


def test_merkle_root_changes_if_any_tx_changes_or_reorders():
    txs = [FakeTx(i) for i in (1, 2, 3, 4)]
    root = CBlock(vtx=txs).get_merkle_root()
    assert CBlock(vtx=[FakeTx(1), FakeTx(2), FakeTx(3), FakeTx(5)]).get_merkle_root() != root
    assert CBlock(vtx=[txs[1], txs[0], txs[2], txs[3]]).get_merkle_root() != root


def test_merkle_duplicate_tail_weakness_cve_2012_2459():
    """"与自己配对"的副作用：[1,2,3] 和 [1,2,3,3] 的默克尔根相同。
    这个设计缺陷在 2012 年被发现可用来构造"同哈希但无效"的区块（CVE-2012-2459）。"""
    a = CBlock(vtx=[FakeTx(1), FakeTx(2), FakeTx(3)]).get_merkle_root()
    b = CBlock(vtx=[FakeTx(1), FakeTx(2), FakeTx(3), FakeTx(3)]).get_merkle_root()
    assert a == b


def test_check_proof_of_work():
    block = CBlock(n_bits=0x1F00FFFF)
    target = params.compact_to_target(block.n_bits)
    while block.get_hash() > target:
        block.n_nonce += 1
    assert block.check_proof_of_work()
    easier_than_allowed = CBlock(n_bits=0x2100FFFF)                 # 目标值超过了允许的上限
    assert not easier_than_allowed.check_proof_of_work()
    assert not CBlock(n_bits=0).check_proof_of_work()


# ------------------------------------------------------ 索引与区块定位器
def make_index_chain(n: int, spacing: int = 600):
    out, prev = [], None
    for h in range(n):
        pindex = CBlockIndex(CBlock(n_time=1000 + h * spacing), hash_=h + 1000)
        pindex.n_height = h
        pindex.pprev = prev
        out.append(pindex)
        prev = pindex
    return out


def test_median_time_past():
    chain = make_index_chain(20)
    assert chain[0].get_median_time_past() == 1000                  # 只有一个块
    assert chain[1].get_median_time_past() == 1600                  # 两个取靠后的那个
    assert chain[19].get_median_time_past() == chain[14].n_time     # 最近 11 个块的中位数


def test_median_time_past_ignores_a_single_outlier():
    chain = make_index_chain(12)
    chain[11].n_time = 10**9                                        # 一个矿工乱填时间戳
    assert chain[11].get_median_time_past() == chain[6].n_time


def test_locator_steps_back_exponentially():
    chain = make_index_chain(1000)
    locator = CBlockLocator.from_index(chain[-1], genesis_hash=1000)
    heights = [h - 1000 for h in locator.v_have]
    assert heights[:12] == list(range(999, 987, -1))                # 前 12 个：一步一个
    gaps = [a - b for a, b in zip(heights[11:-2], heights[12:-1])]
    assert gaps == [2**i for i in range(1, len(gaps) + 1)]          # 之后步长 2、4、8……翻倍
    assert heights[-1] == 0                                         # 最后总是创世块
    assert len(locator.v_have) < 25                                 # 1000 个块只要二十来个哈希


def test_locator_always_appends_genesis():
    chain = make_index_chain(1)
    locator = CBlockLocator.from_index(chain[0], genesis_hash=1000)
    assert locator.v_have == [1000, 1000]                           # 原版就是无条件追加


def test_locator_serialization():
    locator = CBlockLocator([3, 2, 1])
    s = DataStream()
    locator.serialize(s)
    raw = s.getvalue()
    assert raw[:4] == (105).to_bytes(4, "little") and raw[4] == 3 and len(raw) == 5 + 96
    assert CBlockLocator.deserialize(DataStream(raw)).v_have == [3, 2, 1]
