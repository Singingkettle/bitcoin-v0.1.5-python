"""ProcessBlock / AcceptBlock / ConnectBlock behaviour."""

from bitcoin import params
from bitcoin.blockchain import Blockchain
from bitcoin.serialize import uint256_to_hex
from tests.conftest import make_spend, mine_block


def test_genesis_loaded(fresh_chain):
    assert fresh_chain.best_height == 0
    assert uint256_to_hex(fresh_chain.best_index.hash) == params.GENESIS_HASH


def test_build_ten_block_chain(fresh_chain):
    for _ in range(10):
        assert fresh_chain.process_block(mine_block(fresh_chain))
    assert fresh_chain.best_height == 10
    assert len(fresh_chain.main_chain) == 11


def test_duplicate_block_rejected(fresh_chain):
    block = mine_block(fresh_chain)
    assert fresh_chain.process_block(block)
    assert not fresh_chain.process_block(block)


def test_bad_pow_rejected(fresh_chain):
    block = mine_block(fresh_chain)
    block.n_nonce += 1  # break the proof of work
    assert not fresh_chain.process_block(block)


def test_restart_reloads_chain(funded_chain, tmp_path):
    chain, _ = funded_chain
    best = chain.best_index.hash
    chain.close()
    again = Blockchain(str(tmp_path / "chain"))
    assert again.best_height == 110
    assert again.best_index.hash == best
    # and the reloaded chain keeps growing
    assert again.process_block(mine_block(again))
    assert again.best_height == 111
    again.close()


def test_orphan_block_flow(tmp_path):
    chain = Blockchain(str(tmp_path / "orphan"))
    a = mine_block(chain)
    assert chain.process_block(a)
    b = mine_block(chain)
    chain.close()

    chain2 = Blockchain(str(tmp_path / "orphan2"))
    # deliver child first: stored as orphan, height unchanged
    assert chain2.process_block(b)
    assert chain2.best_height == 0
    assert b.get_hash() in chain2.map_orphan_blocks
    # parent arrives: both connect
    assert chain2.process_block(a)
    assert chain2.best_height == 2
    assert b.get_hash() not in chain2.map_orphan_blocks
    chain2.close()


def test_immature_coinbase_spend_rejected_in_block(funded_chain):
    chain, keys = funded_chain
    # coinbase at height 50 has only 61 confirmations at height 111 < 100
    cb = chain.load_block(chain.main_chain[50]).vtx[0]
    spend = make_spend(cb, 0, keys[50], 50 * params.COIN)
    block = mine_block(chain, txs=[spend])
    assert not chain.process_block(block)
    assert chain.best_height == 110


def test_mature_coinbase_spend_accepted_in_block(funded_chain):
    chain, keys = funded_chain
    cb = chain.load_block(chain.main_chain[1]).vtx[0]  # 110 confirmations
    spend = make_spend(cb, 0, keys[1], 50 * params.COIN)
    block = mine_block(chain, txs=[spend])
    assert chain.process_block(block)
    assert chain.best_height == 111
    # and the output is now marked spent
    rec = chain.db.read_tx_index(uint256_to_hex(cb.get_hash()))
    assert rec["spent"][0] == uint256_to_hex(spend.get_hash())


def test_double_spend_across_blocks_rejected(funded_chain):
    chain, keys = funded_chain
    cb = chain.load_block(chain.main_chain[1]).vtx[0]
    spend1 = make_spend(cb, 0, keys[1], 50 * params.COIN)
    spend2 = make_spend(cb, 0, keys[1], 49 * params.COIN)
    assert chain.process_block(mine_block(chain, txs=[spend1]))
    assert not chain.process_block(mine_block(chain, txs=[spend2]))


def test_double_spend_within_block_rejected(funded_chain):
    chain, keys = funded_chain
    cb = chain.load_block(chain.main_chain[1]).vtx[0]
    spend1 = make_spend(cb, 0, keys[1], 50 * params.COIN)
    spend2 = make_spend(cb, 0, keys[1], 49 * params.COIN)
    assert not chain.process_block(mine_block(chain, txs=[spend1, spend2]))


def test_coinbase_overpay_rejected(funded_chain):
    chain, _ = funded_chain
    block = mine_block(chain, coinbase_value=50 * params.COIN + 1)
    assert not chain.process_block(block)


def test_wrong_nbits_rejected(funded_chain):
    chain, _ = funded_chain
    block = mine_block(chain)
    # claim a different difficulty than GetNextWorkRequired demands, re-solve
    block.n_bits = params.target_to_compact(params.PROOF_OF_WORK_LIMIT >> 4)
    assert block.n_bits != chain.get_next_work_required(chain.best_index)
    target = params.compact_to_target(block.n_bits)
    nonce = 0
    while True:
        block.n_nonce = nonce
        if block.get_hash() <= target:
            break
        nonce += 1
    assert not chain.process_block(block)
