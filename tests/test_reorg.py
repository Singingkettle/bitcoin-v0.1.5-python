"""链重组（Reorganize）：分叉切换、交易索引回滚、内存池的"复活"与清理、失败时的完整回滚。"""

from bitcoin import params
from bitcoin.blockchain import Blockchain
from bitcoin.mempool import MemPool
from bitcoin.serialize import uint256_to_hex
from tests.conftest import SEED_HEIGHT, coinbase_of, make_spend, mine_block

COIN = params.COIN


def extend(chain, prev_block, **kw):
    """在某个已提交的区块之上再挖一个并提交，返回新区块。"""
    block = mine_block(chain, prev=chain.map_block_index[prev_block.get_hash()], **kw)
    assert chain.process_block(block)
    return block


def test_equal_height_fork_does_not_switch(funded_chain):
    """等高的分叉：先到的留下（只有**更高**才切换）。"""
    chain, _ = funded_chain
    base = chain.best_index
    a1 = mine_block(chain, prev=base)
    b1 = mine_block(chain, prev=base)
    assert chain.process_block(a1) and chain.process_block(b1)
    assert chain.best_index.hash == a1.get_hash()
    assert b1.get_hash() in chain.map_block_index                   # 支链上的块也记在索引里
    assert not chain.is_in_main_chain(chain.map_block_index[b1.get_hash()])


def test_longer_fork_wins(funded_chain):
    chain, _ = funded_chain
    base = chain.best_index
    a1 = mine_block(chain, prev=base)
    assert chain.process_block(a1)
    b1 = mine_block(chain, prev=base)
    assert chain.process_block(b1)
    b2 = extend(chain, b1)

    assert chain.best_height == SEED_HEIGHT + 2
    assert chain.best_index.hash == b2.get_hash()
    idx = chain.map_block_index
    assert chain.main_chain[-2:] == [idx[b1.get_hash()], idx[b2.get_hash()]]
    # pnext 指针：分叉点现在指向 b1；被抛弃的 a1 不在主链上
    assert base.pnext is idx[b1.get_hash()]
    assert idx[b1.get_hash()].pnext is idx[b2.get_hash()]
    assert idx[b2.get_hash()].pnext is None
    assert not chain.is_in_main_chain(idx[a1.get_hash()])
    assert chain.db.read_best_chain() == uint256_to_hex(b2.get_hash())


def test_deep_reorg_clears_stale_pnext_pointers(funded_chain):
    chain, _ = funded_chain
    base = chain.best_index
    a = [mine_block(chain, prev=base)]
    assert chain.process_block(a[0])
    for _ in range(2):
        a.append(extend(chain, a[-1]))                              # 旧链：a1-a2-a3
    b = [mine_block(chain, prev=base)]
    assert chain.process_block(b[0])
    for _ in range(3):
        b.append(extend(chain, b[-1]))                              # 新链：b1-b2-b3-b4，更高
    assert chain.best_index.hash == b[-1].get_hash()
    for blk in a:
        assert chain.map_block_index[blk.get_hash()].pnext is None  # 旧链上的 pnext 全部清掉
    assert [p.hash for p in chain.main_chain[-4:]] == [x.get_hash() for x in b]


def test_reorg_and_back_again(funded_chain):
    """A 链 -> 被 B 链超过 -> A 链再反超：状态应当完全正确。"""
    chain, keys = funded_chain
    base = chain.best_index
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    a1 = mine_block(chain, prev=base, txs=[spend])
    assert chain.process_block(a1)
    b1 = mine_block(chain, prev=base)
    assert chain.process_block(b1)
    b2 = extend(chain, b1)
    assert not chain.contains_tx(spend.get_hash())
    a2 = extend(chain, a1)                                          # 等高，不切换
    assert chain.best_index.hash == b2.get_hash()
    a3 = extend(chain, a2)                                          # 反超
    assert chain.best_index.hash == a3.get_hash()
    assert chain.contains_tx(spend.get_hash())
    assert chain.get_tx_depth(spend.get_hash()) == 3


def test_reorg_rolls_back_tx_index_and_resurrects_mempool(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    base = chain.best_index
    cb = coinbase_of(chain, 1)
    spend = make_spend(cb, 0, keys[1], 50 * COIN)

    a1 = mine_block(chain, prev=base, txs=[spend])                  # A 链包含这笔花费
    assert chain.process_block(a1)
    assert chain.db.read_tx_index(uint256_to_hex(cb.get_hash()))["spent"][0] is not None

    b1 = mine_block(chain, prev=base)                               # B 链不包含，但更长
    assert chain.process_block(b1)
    extend(chain, b1)

    assert chain.db.read_tx_index(uint256_to_hex(cb.get_hash()))["spent"] == [None]
    assert not chain.contains_tx(spend.get_hash())
    assert spend.get_hash() in pool                                 # 交易回到了内存池，等下一个矿工
    assert chain.get_tx_depth(a1.vtx[0].get_hash()) == 0            # a1 的 coinbase 作废了


def test_reorg_evicts_newly_confirmed_tx_from_mempool(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    base = chain.best_index
    spend = make_spend(coinbase_of(chain, 2), 0, keys[2], 50 * COIN)
    assert pool.accept(spend)

    assert chain.process_block(mine_block(chain, prev=base))        # A 链：没打包它
    b1 = mine_block(chain, prev=base, txs=[spend])                  # B 链：打包了它
    assert chain.process_block(b1)
    assert spend.get_hash() in pool                                 # B 还没成为主链
    extend(chain, b1)
    assert spend.get_hash() not in pool
    assert chain.get_tx_depth(spend.get_hash()) == 2


def test_extending_best_chain_removes_confirmed_tx_from_mempool(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    assert pool.accept(spend)
    assert chain.process_block(mine_block(chain, txs=[spend]))
    assert len(pool) == 0 and not pool.map_next_tx


def test_failed_reorg_is_rolled_back_completely(funded_chain):
    """更长的分叉里藏着一个无效区块：重组必须失败，而且一切恢复原状。"""
    chain, keys = funded_chain
    pool = MemPool(chain)
    base = chain.best_index
    cb = coinbase_of(chain, 1)
    spend = make_spend(cb, 0, keys[1], 50 * COIN)

    a1 = mine_block(chain, prev=base, txs=[spend])
    assert chain.process_block(a1)
    spent_before = chain.db.read_tx_index(uint256_to_hex(cb.get_hash()))["spent"]

    # B 链：b1 正常；b2 里有一笔双花（同一个输出花两次）-> 无效
    b1 = mine_block(chain, prev=base)
    assert chain.process_block(b1)
    cb2 = coinbase_of(chain, 2)
    bad_txs = [make_spend(cb2, 0, keys[2], 50 * COIN), make_spend(cb2, 0, keys[2], 49 * COIN)]
    b2 = mine_block(chain, prev=chain.map_block_index[b1.get_hash()], txs=bad_txs)
    assert not chain.process_block(b2)

    # 链尖、主链、交易索引、内存池：全都和重组之前一样
    assert chain.best_index.hash == a1.get_hash()
    assert chain.main_chain[-1].hash == a1.get_hash()
    assert base.pnext is chain.map_block_index[a1.get_hash()]
    assert chain.db.read_tx_index(uint256_to_hex(cb.get_hash()))["spent"] == spent_before
    assert chain.contains_tx(spend.get_hash())
    assert chain.db.read_tx_index(uint256_to_hex(cb2.get_hash()))["spent"] == [None]
    assert len(pool) == 0
    # 无效的 b2 被清出了索引；合法的 b1 仍然留在支链上
    assert b2.get_hash() not in chain.map_block_index
    assert b1.get_hash() in chain.map_block_index
    assert chain.db.read_best_chain() == uint256_to_hex(a1.get_hash())

    # 之后链还能正常生长，而且可以在 b1 上重新发起一次**合法**的重组
    good_b2 = extend(chain, b1)
    assert chain.best_index.hash == good_b2.get_hash()


def test_state_after_reorg_survives_restart(funded_chain, tmp_path):
    chain, _ = funded_chain
    base = chain.best_index
    assert chain.process_block(mine_block(chain, prev=base))
    b1 = mine_block(chain, prev=base)
    assert chain.process_block(b1)
    b2 = extend(chain, b1)
    chain.close()

    again = Blockchain(str(tmp_path / "chain"))
    assert again.best_index.hash == b2.get_hash()
    assert again.best_height == SEED_HEIGHT + 2
    assert [p.hash for p in again.main_chain[-2:]] == [b1.get_hash(), b2.get_hash()]
    assert len(again.map_block_index) == SEED_HEIGHT + 1 + 3        # 创世块+种子链+3 个新块
    again.close()


def test_tx_listeners_get_disconnect_then_connect_events(funded_chain):
    chain, keys = funded_chain
    base = chain.best_index
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    a1 = mine_block(chain, prev=base, txs=[spend])
    assert chain.process_block(a1)
    events = []
    chain.tx_listeners.append(lambda tx, pindex, f: events.append((tx.get_hash(), f)))
    b1 = mine_block(chain, prev=base)
    assert chain.process_block(b1)
    assert events == []                                             # 支链上的块不触发任何事件
    b2 = extend(chain, b1)
    assert events == [
        (a1.vtx[0].get_hash(), False), (spend.get_hash(), False),   # 先断开 a1 里的交易
        (b1.vtx[0].get_hash(), True), (b2.vtx[0].get_hash(), True),  # 再连接 b1、b2
    ]
