"""Reorganize: fork switching, tx index rollback, mempool resurrection."""

from bitcoin import params
from bitcoin.mempool import MemPool
from bitcoin.serialize import uint256_to_hex
from tests.conftest import make_spend, mine_block


def test_longer_fork_wins(funded_chain):
    chain, _ = funded_chain
    fork_base = chain.best_index
    a1 = mine_block(chain, prev=fork_base)
    assert chain.process_block(a1)
    assert chain.best_height == 111

    # competing branch from the same parent: same height first (no switch),
    # then one more (reorganize)
    b1 = mine_block(chain, prev=fork_base)
    assert chain.process_block(b1)
    assert chain.best_index.hash == a1.get_hash()  # ties don't switch

    b2 = mine_block(chain, prev=chain.map_block_index[b1.get_hash()])
    assert chain.process_block(b2)
    assert chain.best_height == 112
    assert chain.best_index.hash == b2.get_hash()
    # main chain bookkeeping followed the switch
    assert chain.main_chain[111].hash == b1.get_hash()
    assert chain.main_chain[112].hash == b2.get_hash()
    assert chain.map_block_index[b1.get_hash()].pnext is chain.map_block_index[b2.get_hash()]


def test_reorg_rolls_back_tx_index_and_resurrects_mempool(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    fork_base = chain.best_index

    cb = chain.load_block(chain.main_chain[1]).vtx[0]
    spend = make_spend(cb, 0, keys[1], 50 * params.COIN)

    # branch A contains the spend
    a1 = mine_block(chain, prev=fork_base, txs=[spend])
    assert chain.process_block(a1)
    rec = chain.db.read_tx_index(uint256_to_hex(cb.get_hash()))
    assert rec["spent"][0] is not None

    # branch B (longer) does not contain it
    b1 = mine_block(chain, prev=fork_base)
    assert chain.process_block(b1)
    b2 = mine_block(chain, prev=chain.map_block_index[b1.get_hash()])
    assert chain.process_block(b2)
    assert chain.best_index.hash == b2.get_hash()

    # the coinbase output is unspent again ...
    rec = chain.db.read_tx_index(uint256_to_hex(cb.get_hash()))
    assert rec["spent"][0] is None
    # ... the spend's own index entry is gone ...
    assert chain.db.read_tx_index(uint256_to_hex(spend.get_hash())) is None
    # ... and the transaction was resurrected into the mempool
    assert spend.get_hash() in pool


def test_reorg_evicts_confirmed_tx_from_mempool(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    fork_base = chain.best_index

    cb = chain.load_block(chain.main_chain[2]).vtx[0]
    spend = make_spend(cb, 0, keys[2], 50 * params.COIN)
    assert pool.accept(spend)
    assert spend.get_hash() in pool

    # a longer competing branch confirms the pooled tx
    a1 = mine_block(chain, prev=fork_base)
    assert chain.process_block(a1)
    b1 = mine_block(chain, prev=fork_base, txs=[spend])
    assert chain.process_block(b1)
    b2 = mine_block(chain, prev=chain.map_block_index[b1.get_hash()])
    assert chain.process_block(b2)

    assert chain.best_index.hash == b2.get_hash()
    assert spend.get_hash() not in pool
