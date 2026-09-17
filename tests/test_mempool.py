"""内存池的准入规则（AcceptTransaction）。"""

from bitcoin import params
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.mempool import MemPool
from bitcoin.script import script_pubkey_for_hash160, sign_signature
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut
from tests.conftest import SEED_HEIGHT, KeyStore, coinbase_of, make_spend, mine_block

COIN, CENT = params.COIN, params.CENT


def test_accept_valid_spend(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    seen = []
    pool.listeners.append(seen.append)
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    assert pool.accept(spend)
    assert spend.get_hash() in pool and len(pool) == 1
    assert pool.get(spend.get_hash()) is spend
    assert pool.map_next_tx == {(spend.vin[0].prevout.hash, 0): spend.get_hash()}
    assert seen == [spend]                                          # 监听器被通知了
    assert not pool.accept(spend)                                   # 重复提交
    assert seen == [spend]


def test_double_spend_in_pool_rejected(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    cb = coinbase_of(chain, 1)
    first = make_spend(cb, 0, keys[1], 50 * COIN)
    assert pool.accept(first)
    assert not pool.accept(make_spend(cb, 0, keys[1], 49 * COIN))   # 先到先得
    assert list(pool.map_transactions) == [first.get_hash()]


def test_missing_input_rejected(funded_chain):
    chain, _ = funded_chain
    pool = MemPool(chain)
    ghost = CTransaction(vin=[CTxIn(COutPoint(12345, 0), b"\x01\x51")],
                         vout=[CTxOut(COIN, script_pubkey_for_hash160(b"\x00" * 20))])
    assert not pool.accept(ghost)


def test_coinbase_maturity_in_mempool(funded_chain):
    """能进内存池的条件：假如它被打包进**下一个**区块，coinbase 已满 100 个确认。"""
    chain, keys = funded_chain
    pool = MemPool(chain)
    ok_height = SEED_HEIGHT + 1 - 100
    assert not pool.accept(make_spend(coinbase_of(chain, ok_height + 1), 0,
                                      keys[ok_height + 1], 50 * COIN))
    assert pool.accept(make_spend(coinbase_of(chain, ok_height), 0, keys[ok_height], 50 * COIN))


def test_coinbase_transaction_itself_rejected(funded_chain):
    chain, _ = funded_chain
    assert not MemPool(chain).accept(coinbase_of(chain, 1))


def test_already_confirmed_tx_rejected(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    assert chain.process_block(mine_block(chain, txs=[spend]))
    assert not pool.accept(spend)


def test_spending_an_already_spent_output_rejected(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    cb = coinbase_of(chain, 1)
    assert chain.process_block(mine_block(chain, txs=[make_spend(cb, 0, keys[1], 50 * COIN)]))
    assert not pool.accept(make_spend(cb, 0, keys[1], 49 * COIN))


def test_bad_signature_rejected(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    spend.vout[0].n_value -= 1
    assert not pool.accept(spend)


def test_outputs_exceeding_inputs_rejected(funded_chain):
    chain, keys = funded_chain
    assert not MemPool(chain).accept(
        make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN + 1))


def test_malformed_transactions_rejected(funded_chain):
    chain, _ = funded_chain
    pool = MemPool(chain)
    assert not pool.accept(CTransaction(vin=[], vout=[CTxOut(1, b"")]))
    assert not pool.accept(CTransaction(vin=[CTxIn(COutPoint(1, 0))], vout=[]))
    assert not pool.accept(CTransaction(vin=[CTxIn(COutPoint(1, 0))], vout=[CTxOut(-5, b"")]))


def test_mempool_does_not_enforce_fees(funded_chain):
    """v0.1.5 的内存池**不检查**手续费：哪怕是该收费的"粉尘"交易，零手续费也能进池。
    手续费门槛是矿工打包时才执行的（见 test_miner.py）。"""
    chain, keys = funded_chain
    pool = MemPool(chain)
    cb = coinbase_of(chain, 1)
    dust = CTransaction(
        vin=[CTxIn(COutPoint(cb.get_hash(), 0))],
        vout=[CTxOut(CENT - 1, script_pubkey_for_hash160(b"\x11" * 20)),
              CTxOut(50 * COIN - (CENT - 1), script_pubkey_for_hash160(b"\x22" * 20))])
    assert sign_signature(KeyStore(keys[1]), cb, dust, 0)
    assert dust.get_min_fee(True) == CENT                           # 按规则它该付一分钱
    assert pool.accept(dust)                                        # 但内存池照收


def test_chain_of_unconfirmed_spends(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    middle = CKey.generate()
    first = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN,
                       to_h160=hash160(middle.get_pubkey()))
    second = make_spend(first, 0, middle, 50 * COIN)
    assert not pool.accept(second)                                  # 父交易还没见过
    assert pool.accept(first)
    assert pool.accept(second)
    assert not pool.accept(make_spend(first, 0, middle, 40 * COIN))  # 池内双花


def test_remove(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    spend = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN)
    assert pool.accept(spend)
    pool.remove(spend)
    assert len(pool) == 0 and not pool.map_next_tx
    pool.remove(spend)                                              # 删不存在的也没事
    assert pool.accept(spend)                                       # 删掉后可以重新进池


def test_removing_a_confirmed_tx_keeps_the_conflicting_pool_tx_mapping(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    cb = coinbase_of(chain, 1)
    in_pool = make_spend(cb, 0, keys[1], 50 * COIN)
    confirmed_elsewhere = make_spend(cb, 0, keys[1], 49 * COIN)
    assert pool.accept(in_pool)
    pool.remove(confirmed_elsewhere)                                # 花同一个输出的另一笔交易
    assert pool.map_next_tx[(cb.get_hash(), 0)] == in_pool.get_hash()


def test_accept_without_input_checks_is_used_for_resurrection(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    spend = make_spend(coinbase_of(chain, SEED_HEIGHT), 0, keys[SEED_HEIGHT], 50 * COIN)
    assert not pool.accept(spend)                                   # coinbase 未成熟
    assert pool.accept(spend, check_inputs=False)                   # "复活"模式跳过输入检查
    assert not pool.accept(coinbase_of(chain, 1), check_inputs=False)   # 但基本检查仍然做


# ------------------------------------------------------ 交易替换（nSequence）
def replaceable(chain, keys, sequence, value=50 * COIN):
    return make_spend(coinbase_of(chain, 1), 0, keys[1], value,
                      to_h160=b"\x33" * 20, n_sequence=sequence, n_lock_time=10**6)


def test_newer_version_replaces_older(funded_chain):
    """0.1.x 的"可替换交易"：同样的输入、更高的序列号 -> 新版本顶替旧版本。
    中本聪设想用它做"高频微支付通道"，这个机制后来被禁用，多年后以 RBF 的形式回归。"""
    chain, keys = funded_chain
    pool = MemPool(chain)
    replaced = []
    pool.replaced_listeners.append(replaced.append)
    v1 = replaceable(chain, keys, 1)
    v2 = replaceable(chain, keys, 2, value=49 * COIN)
    assert pool.accept(v1)
    assert pool.accept(v2)
    assert list(pool.map_transactions) == [v2.get_hash()]
    assert pool.map_next_tx == {(v2.vin[0].prevout.hash, 0): v2.get_hash()}
    assert replaced == [v1]


def test_older_or_equal_version_does_not_replace(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    v2 = replaceable(chain, keys, 2)
    assert pool.accept(v2)
    assert not pool.accept(replaceable(chain, keys, 1, value=49 * COIN))
    assert not pool.accept(replaceable(chain, keys, 2, value=48 * COIN))
    assert list(pool.map_transactions) == [v2.get_hash()]


def test_final_transaction_cannot_be_replaced(funded_chain):
    chain, keys = funded_chain
    pool = MemPool(chain)
    final = replaceable(chain, keys, 0xFFFFFFFF)
    assert pool.accept(final)
    assert not pool.accept(replaceable(chain, keys, 5, value=49 * COIN))
