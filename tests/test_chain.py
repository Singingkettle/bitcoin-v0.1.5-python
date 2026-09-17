"""区块的接收流程：ProcessBlock / CheckBlock / AcceptBlock / ConnectBlock。"""

import time

import pytest

from bitcoin import params
from bitcoin.blockchain import Blockchain
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.script import CScript, script_pubkey_for_hash160, sign_signature
from bitcoin.serialize import uint256_to_hex
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut
from tests.conftest import (
    SEED_HEIGHT,
    KeyStore,
    coinbase_of,
    make_spend,
    mine_block,
    solve_block,
)

COIN = params.COIN


# ------------------------------------------------------------------ 基本流程
def test_new_datadir_starts_at_genesis(fresh_chain):
    assert fresh_chain.best_height == 0
    assert uint256_to_hex(fresh_chain.best_index.hash) == params.GENESIS_HASH
    assert fresh_chain.main_chain == [fresh_chain.genesis_index]


def test_genesis_coinbase_is_not_in_tx_index(fresh_chain):
    """创世块不经过 ConnectBlock，所以那 50 BTC 不在交易索引里，永远花不了（与真实比特币一致）。"""
    genesis_cb = coinbase_of(fresh_chain, 0)
    assert fresh_chain.load_tx(genesis_cb.get_hash()) is None
    assert not fresh_chain.contains_tx(genesis_cb.get_hash())


def test_build_ten_block_chain(fresh_chain):
    for i in range(10):
        assert fresh_chain.process_block(mine_block(fresh_chain))
        assert fresh_chain.best_height == i + 1
    assert [p.n_height for p in fresh_chain.main_chain] == list(range(11))
    # pprev / pnext 指针把主链串成一条双向链表
    for a, b in zip(fresh_chain.main_chain, fresh_chain.main_chain[1:]):
        assert a.pnext is b and b.pprev is a
    assert fresh_chain.best_index.pnext is None


def test_block_listeners_fire_on_each_new_best(fresh_chain):
    seen = []
    fresh_chain.block_listeners.append(lambda pindex: seen.append(pindex.n_height))
    for _ in range(3):
        fresh_chain.process_block(mine_block(fresh_chain))
    assert seen == [1, 2, 3]


def test_duplicate_block_rejected(fresh_chain):
    block = mine_block(fresh_chain)
    assert fresh_chain.process_block(block)
    assert not fresh_chain.process_block(block)
    assert fresh_chain.best_height == 1


def test_restart_reloads_chain_and_keeps_growing(funded_chain, tmp_path):
    chain, _ = funded_chain
    best = chain.best_index.hash
    chain.close()
    again = Blockchain(str(tmp_path / "chain"))
    assert again.best_height == SEED_HEIGHT and again.best_index.hash == best
    assert len(again.main_chain) == SEED_HEIGHT + 1
    assert again.process_block(mine_block(again))
    assert again.best_height == SEED_HEIGHT + 1
    again.close()


# ---------------------------------------------------------- CheckBlock 的各项检查
def test_bad_proof_of_work_rejected(fresh_chain):
    block = mine_block(fresh_chain)
    target = params.compact_to_target(block.n_bits)
    while block.get_hash() <= target:           # 找一个**不**满足难度的 nonce
        block.n_nonce += 1
    assert not fresh_chain.process_block(block)


def test_block_without_transactions_rejected(fresh_chain):
    block = mine_block(fresh_chain)
    block.vtx = []
    block.hash_merkle_root = 0
    assert not fresh_chain.process_block(solve_block(block))


def test_first_tx_must_be_coinbase(funded_chain):
    chain, keys = funded_chain
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    block = mine_block(chain)
    block.vtx = [spend]
    block.hash_merkle_root = block.get_merkle_root()
    assert not chain.process_block(solve_block(block))


def test_second_coinbase_rejected(fresh_chain):
    block = mine_block(fresh_chain)
    extra = CTransaction(vin=[CTxIn(COutPoint(), CScript().push_int(1).push_int(2))],
                         vout=[CTxOut(1, b"\x51")])
    block.vtx.append(extra)
    block.hash_merkle_root = block.get_merkle_root()
    assert not fresh_chain.process_block(solve_block(block))


def test_merkle_root_mismatch_rejected(fresh_chain):
    block = mine_block(fresh_chain)
    block.hash_merkle_root ^= 1
    assert not fresh_chain.process_block(solve_block(block))


def test_tampering_with_a_tx_after_mining_is_detected(funded_chain):
    chain, keys = funded_chain
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    block = mine_block(chain, txs=[spend])
    block.vtx[1].vout[0].n_value -= 1           # 区块头没动，但默克尔根对不上了
    assert not chain.process_block(block)


def test_timestamp_too_far_in_future_rejected(fresh_chain):
    block = mine_block(fresh_chain, n_time=int(time.time()) + 2 * 60 * 60 + 600)
    assert not fresh_chain.process_block(block)


def test_timestamp_slightly_in_future_accepted(fresh_chain):
    assert fresh_chain.process_block(mine_block(fresh_chain, n_time=int(time.time()) + 3600))


def test_invalid_transaction_inside_block_rejected(fresh_chain):
    bad = CTransaction(vin=[CTxIn(COutPoint(5, 0))], vout=[CTxOut(-1, b"")])
    block = mine_block(fresh_chain, txs=[bad])
    assert not fresh_chain.process_block(block)


# --------------------------------------------------------- AcceptBlock 的上下文检查
def test_timestamp_not_after_median_time_past_rejected(fresh_chain):
    for _ in range(12):
        fresh_chain.process_block(mine_block(fresh_chain))
    median = fresh_chain.best_index.get_median_time_past()
    assert not fresh_chain.process_block(mine_block(fresh_chain, n_time=median))
    assert fresh_chain.process_block(mine_block(fresh_chain, n_time=median + 1))


def test_wrong_nbits_rejected(fresh_chain):
    block = mine_block(fresh_chain)
    block.n_bits = params.target_to_compact(params.PROOF_OF_WORK_LIMIT >> 4)   # 自称另一个难度
    assert block.n_bits != fresh_chain.get_next_work_required(fresh_chain.best_index)
    assert not fresh_chain.process_block(solve_block(block))


# -------------------------------------------------------------------- 孤块
def test_orphan_block_waits_for_its_parent(tmp_path):
    source = Blockchain(str(tmp_path / "source"))
    blocks = []
    for _ in range(3):
        blocks.append(mine_block(source))
        assert source.process_block(blocks[-1])
    source.close()

    chain = Blockchain(str(tmp_path / "dest"))
    # 倒着送：3、2 都成了孤块
    assert chain.process_block(blocks[2])
    assert chain.process_block(blocks[1])
    assert chain.best_height == 0 and len(chain.map_orphan_blocks) == 2
    assert not chain.process_block(blocks[2])                       # 重复的孤块
    # GetOrphanRoot：返回孤块链里**最老的那个孤块自己**的哈希（而不是它缺的父块）
    assert chain.get_orphan_root(blocks[2]) == blocks[1].get_hash()
    assert chain.get_orphan_root(blocks[1]) == blocks[1].get_hash()
    # 父块到了：三个块一口气全部接上
    assert chain.process_block(blocks[0])
    assert chain.best_height == 3
    assert not chain.map_orphan_blocks and not chain.map_orphan_blocks_by_prev
    chain.close()


def test_invalid_orphan_is_dropped_when_parent_arrives(tmp_path):
    source = Blockchain(str(tmp_path / "source"))
    parent = mine_block(source)
    source.process_block(parent)
    child = mine_block(source, coinbase_value=51 * COIN)            # 多拿了 1 BTC
    source.close()

    chain = Blockchain(str(tmp_path / "dest"))
    assert chain.process_block(child)                               # 作为孤块先收下（此时查不出问题）
    assert chain.process_block(parent)
    assert chain.best_height == 1                                   # 父块接上了，孤块被判无效
    assert not chain.map_orphan_blocks
    chain.close()


# ---------------------------------------------------------- ConnectBlock / 花费
def test_mature_coinbase_spend(funded_chain):
    chain, keys = funded_chain
    cb = coinbase_of(chain, 1)
    spend = make_spend(cb, 0, keys[1], 50 * COIN)
    assert chain.process_block(mine_block(chain, txs=[spend]))
    assert chain.best_height == SEED_HEIGHT + 1
    rec = chain.db.read_tx_index(uint256_to_hex(cb.get_hash()))
    assert rec["spent"] == [uint256_to_hex(spend.get_hash())]
    tx, rec = chain.load_tx(spend.get_hash())
    assert tx.get_hash() == spend.get_hash() and rec["spent"] == [None] and rec["txn"] == 1
    assert chain.get_tx_depth(spend.get_hash()) == 1
    assert chain.get_tx_depth(cb.get_hash()) == SEED_HEIGHT + 1


@pytest.mark.parametrize("age,ok", [(99, False), (100, True)])
def test_coinbase_maturity_boundary(funded_chain, age, ok):
    """新区块的高度 - coinbase 所在高度 必须 >= 100。"""
    chain, keys = funded_chain
    h = SEED_HEIGHT + 1 - age
    spend = make_spend(coinbase_of(chain, h), 0, keys[h], 50 * COIN)
    assert chain.process_block(mine_block(chain, txs=[spend])) is ok


def test_double_spend_across_blocks_rejected(funded_chain):
    chain, keys = funded_chain
    cb = coinbase_of(chain, 1)
    assert chain.process_block(mine_block(chain, txs=[make_spend(cb, 0, keys[1], 50 * COIN)]))
    assert not chain.process_block(mine_block(chain, txs=[make_spend(cb, 0, keys[1], 49 * COIN)]))


def test_double_spend_within_one_block_rejected(funded_chain):
    chain, keys = funded_chain
    cb = coinbase_of(chain, 1)
    txs = [make_spend(cb, 0, keys[1], 50 * COIN), make_spend(cb, 0, keys[1], 49 * COIN)]
    assert not chain.process_block(mine_block(chain, txs=txs))


def test_chain_of_spends_within_one_block(funded_chain):
    """同一个区块里，后面的交易可以花前面交易刚产生的输出。"""
    chain, keys = funded_chain
    middle = CKey.generate()
    first = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN,
                       to_h160=hash160(middle.get_pubkey()))
    second = make_spend(first, 0, middle, 50 * COIN)
    assert chain.process_block(mine_block(chain, txs=[first, second]))
    assert chain.db.read_tx_index(uint256_to_hex(first.get_hash()))["spent"] == [
        uint256_to_hex(second.get_hash())]


def test_spend_before_its_parent_in_same_block_rejected(funded_chain):
    chain, keys = funded_chain
    middle = CKey.generate()
    first = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN,
                       to_h160=hash160(middle.get_pubkey()))
    second = make_spend(first, 0, middle, 50 * COIN)
    assert not chain.process_block(mine_block(chain, txs=[second, first]))


def test_spending_unknown_output_rejected(funded_chain):
    chain, _ = funded_chain
    ghost = CTransaction(vin=[CTxIn(COutPoint(123456, 0), b"\x51")],
                         vout=[CTxOut(COIN, b"\x51")])
    assert not chain.process_block(mine_block(chain, txs=[ghost]))


def test_bad_signature_in_block_rejected(funded_chain):
    chain, keys = funded_chain
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    spend.vout[0].n_value -= 1                                      # 签名之后又改了金额
    assert not chain.process_block(mine_block(chain, txs=[spend]))


def test_spending_more_than_inputs_rejected(funded_chain):
    chain, keys = funded_chain
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN + 1)
    assert not chain.process_block(mine_block(chain, txs=[spend]))


def test_prevout_index_out_of_range_rejected(funded_chain):
    chain, keys = funded_chain
    cb = coinbase_of(chain, 1)
    spend = CTransaction(vin=[CTxIn(COutPoint(cb.get_hash(), 5))], vout=[CTxOut(1, b"\x51")])
    assert not chain.process_block(mine_block(chain, txs=[spend]))


# ------------------------------------------------------------- coinbase 金额
def test_coinbase_overpay_rejected_underpay_allowed(funded_chain):
    chain, _ = funded_chain
    assert not chain.process_block(mine_block(chain, coinbase_value=50 * COIN + 1))
    assert chain.process_block(mine_block(chain, coinbase_value=50 * COIN - 1))   # 少拿是合法的
    assert chain.process_block(mine_block(chain, coinbase_value=0))


def test_coinbase_may_claim_fees(funded_chain):
    chain, keys = funded_chain
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 49 * COIN)   # 留 1 BTC 手续费
    assert not chain.process_block(
        mine_block(chain, txs=[spend], coinbase_value=51 * COIN + 1))
    assert chain.process_block(mine_block(chain, txs=[spend], coinbase_value=51 * COIN))


def test_failed_block_leaves_no_trace(funded_chain):
    """区块连接失败后：索引、交易索引、最佳链都保持原样（数据库回滚了）。"""
    chain, keys = funded_chain
    cb = coinbase_of(chain, 1)
    good = make_spend(cb, 0, keys[1], 50 * COIN)
    bad = make_spend(coinbase_of(chain, SEED_HEIGHT), 0, keys[SEED_HEIGHT], 50 * COIN)  # 未成熟
    block = mine_block(chain, txs=[good, bad])
    best = chain.best_index
    assert not chain.process_block(block)
    assert chain.best_index is best
    assert block.get_hash() not in chain.map_block_index
    assert chain.db.read_tx_index(uint256_to_hex(cb.get_hash()))["spent"] == [None]
    assert not chain.contains_tx(good.get_hash())
    # 而且之后这笔好交易仍然能正常上链
    assert chain.process_block(mine_block(chain, txs=[good]))


def test_tx_listeners_see_connected_transactions(funded_chain):
    chain, keys = funded_chain
    events = []
    chain.tx_listeners.append(lambda tx, pindex, f: events.append((tx.get_hash(), pindex, f)))
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    block = mine_block(chain, txs=[spend])
    assert chain.process_block(block)
    assert [e[0] for e in events] == [block.vtx[0].get_hash(), spend.get_hash()]
    assert all(e[1] is chain.best_index and e[2] is True for e in events)
