"""Wallet + miner end-to-end on a single node: maturity, sending, change,
generation credit."""

import pytest

from bitcoin import params
from bitcoin.mempool import MemPool
from bitcoin.miner import Miner
from bitcoin.wallet import Wallet
from tests.conftest import mine_block


@pytest.fixture
def node(funded_chain, tmp_path):
    chain, keys = funded_chain
    pool = MemPool(chain)
    wallet_a = Wallet(str(tmp_path / "walletA"), chain, pool)
    wallet_b = Wallet(str(tmp_path / "walletB"), chain, pool)
    # wallet A owns the first 6 seed coinbases
    for h in range(1, 7):
        wallet_a._register_key(keys[h])
    wallet_a.rescan()
    return chain, pool, wallet_a, wallet_b


def test_generation_maturity_schedule(node):
    chain, pool, wallet_a, _ = node
    # at height 110 the deepest coinbase has 110 confirmations < 120
    assert wallet_a.get_balance() == 0
    # +15 blocks -> height 125: coinbases 1..6 have depth >= 120
    for _ in range(15):
        assert chain.process_block(mine_block(chain))
    assert wallet_a.get_balance() == 6 * 50 * params.COIN


def test_send_money_with_change(node):
    chain, pool, wallet_a, wallet_b = node
    for _ in range(15):
        assert chain.process_block(mine_block(chain))

    ok, result = wallet_a.send_money(wallet_b.get_default_address(),
                                     10 * params.COIN)
    assert ok, result
    # one 50-coin spent whole: 250 untouched + 40 change (0-conf, from us)
    assert wallet_a.get_balance() == 290 * params.COIN
    # B sees the incoming 0-conf credit via the mempool listener
    assert wallet_b.get_balance() == 10 * params.COIN
    assert len(pool) == 1

    # confirm it
    spend_tx = pool.transactions()[0]
    assert chain.process_block(mine_block(chain, txs=[spend_tx]))
    pool.remove(spend_tx)
    assert wallet_a.get_balance() == 290 * params.COIN
    assert wallet_b.get_balance() == 10 * params.COIN
    assert wallet_b.chain.get_tx_depth(spend_tx.get_hash()) == 1


def test_send_more_than_balance_fails(node):
    chain, _, wallet_a, wallet_b = node
    for _ in range(15):
        assert chain.process_block(mine_block(chain))
    ok, err = wallet_a.send_money(wallet_b.get_default_address(),
                                  10_000 * params.COIN)
    assert not ok
    assert "Insufficient" in err


def test_send_to_invalid_address_fails(node):
    _, _, wallet_a, _ = node
    ok, err = wallet_a.send_money("not-an-address", params.COIN)
    assert not ok
    assert "Invalid" in err


def test_wallet_persistence(node, tmp_path):
    chain, pool, wallet_a, _ = node
    for _ in range(15):
        assert chain.process_block(mine_block(chain))
    balance = wallet_a.get_balance()
    assert balance > 0
    reloaded = Wallet(str(tmp_path / "walletA"), chain, pool)
    assert reloaded.get_balance() == balance
    assert reloaded.get_default_address() == wallet_a.get_default_address()


def test_miner_finds_blocks_and_credits_wallet(node):
    chain, pool, _, wallet_b = node
    start_height = chain.best_height
    miner = Miner(chain, pool, wallet_b)
    block, _ = miner.create_new_block()
    # solve it inline (same loop the thread runs)
    target = params.compact_to_target(block.n_bits)
    nonce = 0
    while True:
        block.n_nonce = nonce
        if block.get_hash() <= target:
            break
        nonce += 1
    assert chain.process_block(block)
    assert chain.best_height == start_height + 1
    # generated coins land in the wallet but are immature
    assert any(w.tx.is_coinbase() for w in wallet_b.map_wallet.values())
    assert wallet_b.get_balance() == 0


def test_miner_thread_smoke(node):
    import time

    chain, pool, _, wallet_b = node
    start_height = chain.best_height
    miner = Miner(chain, pool, wallet_b)
    miner.start()
    deadline = time.time() + 30
    while miner.blocks_found < 2 and time.time() < deadline:
        time.sleep(0.05)
    miner.stop()
    assert miner.blocks_found >= 2
    assert chain.best_height >= start_height + 2


def test_miner_includes_mempool_tx_and_claims_fee(node):
    chain, pool, wallet_a, wallet_b = node
    for _ in range(15):
        assert chain.process_block(mine_block(chain))
    # a send with a forced fee
    wallet_a.settings["fee"] = params.CENT
    ok, _ = wallet_a.send_money(wallet_b.get_default_address(), 10 * params.COIN)
    assert ok
    miner = Miner(chain, pool, wallet_b)
    block, _ = miner.create_new_block()
    assert len(block.vtx) == 2  # coinbase + the send
    assert block.vtx[0].get_value_out() == 50 * params.COIN + params.CENT
