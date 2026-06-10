"""AcceptTransaction rules."""

from bitcoin import params
from bitcoin.mempool import MemPool
from bitcoin.script import script_pubkey_for_hash160
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut
from tests.conftest import KeyStore, make_spend, mine_block


def test_accept_valid_spend(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    cb = chain.load_block(chain.main_chain[1]).vtx[0]
    spend = make_spend(cb, 0, keys[1], 50 * params.COIN)
    assert pool.accept(spend)
    assert spend.get_hash() in pool
    assert len(pool) == 1


def test_conflicting_spend_rejected(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    cb = chain.load_block(chain.main_chain[1]).vtx[0]
    assert pool.accept(make_spend(cb, 0, keys[1], 50 * params.COIN))
    assert not pool.accept(make_spend(cb, 0, keys[1], 49 * params.COIN))


def test_missing_input_rejected(funded_chain):
    chain, _ = funded_chain
    pool = MemPool(chain)
    ghost = CTransaction(
        vin=[CTxIn(COutPoint(12345, 0), b"\x01\x51")],
        vout=[CTxOut(params.COIN, script_pubkey_for_hash160(b"\x00" * 20))],
    )
    assert not pool.accept(ghost)


def test_immature_coinbase_rejected(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    cb = chain.load_block(chain.main_chain[50]).vtx[0]  # 61 confirmations
    assert not pool.accept(make_spend(cb, 0, keys[50], 50 * params.COIN))


def test_coinbase_itself_rejected(funded_chain):
    chain, _ = funded_chain
    pool = MemPool(chain)
    cb = chain.load_block(chain.main_chain[1]).vtx[0]
    assert not pool.accept(cb)


def test_already_confirmed_tx_rejected(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    cb = chain.load_block(chain.main_chain[1]).vtx[0]
    spend = make_spend(cb, 0, keys[1], 50 * params.COIN)
    assert chain.process_block(mine_block(chain, txs=[spend]))
    assert not pool.accept(spend)


def test_sub_cent_output_needs_fee(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    cb = chain.load_block(chain.main_chain[1]).vtx[0]
    # sub-cent output, zero fee: GetMinFee says CENT, so reject
    bad = make_spend(cb, 0, keys[1], 50 * params.COIN)
    bad.vout[0].n_value = params.CENT - 1
    bad.vout.append(CTxOut(50 * params.COIN - (params.CENT - 1),
                           script_pubkey_for_hash160(b"\x11" * 20)))
    # re-sign after editing outputs
    from bitcoin.script import sign_signature
    assert sign_signature(KeyStore(keys[1]), cb, bad, 0)
    assert not pool.accept(bad)

    # same outputs but leaving a full cent as fee: accepted
    good = make_spend(cb, 0, keys[1], 50 * params.COIN)
    good.vout[0].n_value = params.CENT - 1
    good.vout.append(CTxOut(50 * params.COIN - (params.CENT - 1) - params.CENT,
                            script_pubkey_for_hash160(b"\x11" * 20)))
    assert sign_signature(KeyStore(keys[1]), cb, good, 0)
    assert pool.accept(good)


def test_spend_of_pool_tx_accepted(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    from bitcoin.hashes import hash160
    from bitcoin.key import CKey

    middle = CKey.generate()
    cb = chain.load_block(chain.main_chain[1]).vtx[0]
    first = make_spend(cb, 0, keys[1], 50 * params.COIN,
                       to_h160=hash160(middle.get_pubkey()))
    assert pool.accept(first)
    second = make_spend(first, 0, middle, 50 * params.COIN)
    assert pool.accept(second)
