"""钱包：余额、成熟期、选币、找零、发送、持久化、重播。"""

import pytest

from bitcoin import base58, params, util
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.mempool import MemPool
from bitcoin.script import TX_PUBKEY, solver, verify_signature
from bitcoin.wallet import Wallet
from tests.conftest import SEED_HEIGHT, coinbase_of, make_spend, mine_block

COIN, CENT = params.COIN, params.CENT
WALLET_MATURE = SEED_HEIGHT + 1 - 120       # 种子链上，高度 <= 它的 coinbase 对钱包来说已成熟


@pytest.fixture
def node(funded_chain, tmp_path):
    """一条有钱的链 + 内存池 + 两个钱包。钱包 A 拥有种子链前 20 个区块的 coinbase。"""
    chain, keys = funded_chain
    chain.test_keys = keys              # 方便个别测试取用种子链上其它 coinbase 的私钥
    pool = MemPool(chain)
    wallet_a = Wallet(str(tmp_path / "walletA"), chain, pool)
    wallet_b = Wallet(str(tmp_path / "walletB"), chain, pool)
    for h in range(1, 21):
        wallet_a.add_key(keys[h])
    wallet_a.rescan()
    yield chain, pool, wallet_a, wallet_b
    wallet_a.close()
    wallet_b.close()


def confirm(chain, pool):
    """把内存池里的交易全部打包进一个新区块。"""
    assert chain.process_block(mine_block(chain, txs=pool.transactions()))


# ---------------------------------------------------------------- 基本属性
def test_new_wallet_has_a_default_address(node):
    _, _, _, wallet_b = node
    addr = wallet_b.get_default_address()
    assert base58.is_valid_address(addr)
    assert wallet_b.get_balance() == 0
    assert wallet_b.get_key_for_hash160(base58.address_to_hash160(addr)) is wallet_b.default_key


def test_rescan_finds_owned_coinbases(node):
    _, _, wallet_a, _ = node
    assert len(wallet_a.map_wallet) == 20
    assert all(w.tx.is_coinbase() and w.hash_block != 0 for w in wallet_a.map_wallet.values())


def test_generation_maturity_is_120_in_the_wallet(node):
    """共识只要求 100 个确认，但钱包要等 120 个——原版界面上写着
    "Generated coins must wait 120 blocks before they can be spent"。"""
    chain, _, wallet_a, _ = node
    assert WALLET_MATURE == 11
    assert wallet_a.get_balance() == WALLET_MATURE * 50 * COIN
    by_height = {chain.map_block_index[w.hash_block].n_height: w
                 for w in wallet_a.map_wallet.values()}
    assert wallet_a.blocks_to_maturity(by_height[11]) == 0
    assert wallet_a.blocks_to_maturity(by_height[12]) == 1
    assert wallet_a.get_depth(by_height[12]) == 119
    assert wallet_a.get_available_credit(by_height[12]) == 0
    assert wallet_a.get_credit(by_height[12].tx) == 50 * COIN
    # 再挖一个块，高度 12 的 coinbase 就成熟了
    assert chain.process_block(mine_block(chain))
    assert wallet_a.get_balance() == (WALLET_MATURE + 1) * 50 * COIN


def test_is_mine_recognises_both_templates(node):
    from bitcoin.script import script_pubkey_for_hash160, script_pubkey_for_pubkey
    from bitcoin.tx import CTxOut
    _, _, wallet_a, _ = node
    pub = wallet_a.default_key.get_pubkey()
    assert wallet_a.is_mine_txout(CTxOut(1, script_pubkey_for_pubkey(pub)))
    assert wallet_a.is_mine_txout(CTxOut(1, script_pubkey_for_hash160(hash160(pub))))
    stranger = CKey.generate().get_pubkey()
    assert not wallet_a.is_mine_txout(CTxOut(1, script_pubkey_for_pubkey(stranger)))
    assert not wallet_a.is_mine_txout(CTxOut(1, b"\x51"))           # 非标准脚本


# -------------------------------------------------------------------- 发送
def test_send_money_with_change(node):
    chain, pool, wallet_a, wallet_b = node
    before = wallet_a.get_balance()
    ok, txid = wallet_a.send_money(wallet_b.get_default_address(), 10 * COIN)
    assert ok, txid
    assert len(pool) == 1
    tx = pool.transactions()[0]
    assert util.format_money(tx.vout[0].n_value) == "10.00"
    # 花掉一整枚 50 的币：10 给对方，40 找零；零确认的找零立刻计入余额
    assert wallet_a.get_balance() == before - 10 * COIN
    assert wallet_b.get_balance() == 10 * COIN                      # 对方零确认就能看到
    confirm(chain, pool)
    assert len(pool) == 0
    assert wallet_a.get_balance() == before - 10 * COIN + 50 * COIN  # 期间又成熟了一枚 coinbase
    assert wallet_b.get_balance() == 10 * COIN
    assert wallet_b.get_depth(next(iter(wallet_b.map_wallet.values()))) == 1


def test_change_goes_back_to_the_same_pubkey_like_the_original(node):
    """v0.1.5 的找零是 `<原币的公钥> OP_CHECKSIG`——和中本聪付给 Hal Finney 那笔真实交易一模一样。"""
    _, pool, wallet_a, wallet_b = node
    n_keys = len(wallet_a.keys)
    assert wallet_a.send_money(wallet_b.get_default_address(), 10 * COIN)[0]
    tx = pool.transactions()[0]
    assert len(tx.vin) == 1 and len(tx.vout) == 2
    spent = wallet_a.map_wallet[tx.vin[0].prevout.hash].tx
    kind, change_pubkey = solver(tx.vout[1].script_pubkey)
    assert kind == TX_PUBKEY
    assert bytes(tx.vout[1].script_pubkey) == bytes(spent.vout[0].script_pubkey)
    assert tx.vout[1].n_value == 40 * COIN
    assert len(wallet_a.keys) == n_keys                             # 没有为找零生成新密钥
    assert verify_signature(spent, tx, 0)


def test_exact_amount_needs_no_change(node):
    _, pool, wallet_a, wallet_b = node
    assert wallet_a.send_money(wallet_b.get_default_address(), 50 * COIN)[0]
    assert len(pool.transactions()[0].vout) == 1


def test_whole_wallet_tx_is_marked_spent(node):
    """整笔标记：花了 10 BTC，但那枚 50 的币整个被标记为已花；找零是一笔新的钱包交易。"""
    _, pool, wallet_a, wallet_b = node
    assert wallet_a.send_money(wallet_b.get_default_address(), 10 * COIN)[0]
    tx = pool.transactions()[0]
    assert wallet_a.map_wallet[tx.vin[0].prevout.hash].f_spent
    new_wtx = wallet_a.map_wallet[tx.get_hash()]
    assert new_wtx.f_from_me and not new_wtx.f_spent
    assert wallet_a.get_credit(tx) == 40 * COIN and wallet_a.get_debit(tx) == 50 * COIN


def test_spend_unconfirmed_change_immediately(node):
    chain, pool, wallet_a, wallet_b = node
    addr = wallet_b.get_default_address()
    assert wallet_a.send_money(addr, 45 * COIN)[0]                  # 找零 5 BTC（零确认）
    everything = (WALLET_MATURE - 1) * 50 * COIN + 5 * COIN
    assert wallet_a.get_balance() == everything
    assert wallet_a.send_money(addr, everything)[0]                 # 把剩下的一次花光
    last = pool.transactions()[-1]
    assert len(last.vin) == WALLET_MATURE                           # 10 枚整币 + 那份零确认的找零
    assert len(last.vout) == 1                                      # 刚好花光，没有找零
    assert wallet_a.get_balance() == 0
    confirm(chain, pool)
    assert len(pool) == 0
    assert wallet_b.get_balance() == WALLET_MATURE * 50 * COIN


def test_select_coins_prefers_exact_subset_of_small_coins_over_a_big_coin(node):
    """原版选币算法的一个有趣行为：9 份 5 BTC 的零钱恰好能凑出 45 时，
    它宁可用 9 个小输入，也不去拆一枚 50 的整币。"""
    _, pool, wallet_a, wallet_b = node
    addr = wallet_b.get_default_address()
    for _ in range(9):
        assert wallet_a.send_money(addr, 45 * COIN)[0]
    assert wallet_a.send_money(addr, 45 * COIN)[0]
    last = pool.transactions()[-1]
    assert len(last.vin) == 9 and len(last.vout) == 1


@pytest.mark.parametrize("address,amount,fragment", [
    ("not-an-address", COIN, "无效"),
    (None, 0, "金额"),
    (None, -5, "金额"),
    (None, 10_000 * COIN, "余额"),
])
def test_send_validation_errors(node, address, amount, fragment):
    _, pool, wallet_a, wallet_b = node
    ok, err = wallet_a.send_money(address or wallet_b.get_default_address(), amount)
    assert not ok and fragment in err
    assert len(pool) == 0


def test_fee_setting_is_included_and_checked(node):
    chain, pool, wallet_a, wallet_b = node
    wallet_a.set_transaction_fee(CENT)
    balance = wallet_a.get_balance()
    ok, err = wallet_a.send_money(wallet_b.get_default_address(), balance)      # 加上手续费就不够了
    assert not ok and "手续费" in err
    assert wallet_a.send_money(wallet_b.get_default_address(), 10 * COIN)[0]
    tx = pool.transactions()[0]
    assert 50 * COIN - tx.get_value_out() == CENT
    assert wallet_a.get_balance() == balance - 10 * COIN - CENT


def test_dust_send_automatically_adds_minimum_fee(node):
    """付一笔小于 0.01 的"粉尘"：CreateTransaction 发现最低手续费是 0.01，自动加上重来一轮。"""
    _, pool, wallet_a, wallet_b = node
    assert wallet_a.send_money(wallet_b.get_default_address(), CENT // 2)[0]
    tx = pool.transactions()[0]
    assert 50 * COIN - tx.get_value_out() == CENT


def test_sent_address_is_added_to_address_book(node):
    _, _, wallet_a, wallet_b = node
    addr = wallet_b.get_default_address()
    assert wallet_a.send_money(addr, COIN)[0]
    assert wallet_a.address_book == {addr: ""}
    wallet_a.set_address_label(addr, "小王")
    assert wallet_a.send_money(addr, COIN)[0]
    assert wallet_a.address_book == {addr: "小王"}                  # 已有的备注不会被覆盖


def test_failed_broadcast_restores_wallet_state(node, monkeypatch):
    _, pool, wallet_a, wallet_b = node
    balance = wallet_a.get_balance()
    n_txs = len(wallet_a.map_wallet)
    monkeypatch.setattr(pool, "accept", lambda tx, check_inputs=True: False)
    ok, err = wallet_a.send_money(wallet_b.get_default_address(), 10 * COIN)
    assert not ok
    assert wallet_a.get_balance() == balance and len(wallet_a.map_wallet) == n_txs


# -------------------------------------------------------------------- 选币
def make_wallet_with_coins(node, amounts):
    """给钱包 B 准备若干枚指定面额的、已确认的币。"""
    chain, pool, wallet_a, wallet_b = node
    addr = wallet_b.get_default_address()
    for amount in amounts:
        assert wallet_a.send_money(addr, amount)[0]
    confirm(chain, pool)
    return wallet_b


def picked(wallet, target):
    coins = wallet.select_coins(target)
    return None if coins is None else sorted(wallet.get_credit(w.tx) for w in coins)


def test_select_coins_prefers_exact_match(node):
    wallet = make_wallet_with_coins(node, [COIN, 2 * COIN, 5 * COIN, 10 * COIN])
    assert picked(wallet, 5 * COIN) == [5 * COIN]


def test_select_coins_finds_exact_subset(node):
    wallet = make_wallet_with_coins(node, [COIN, 2 * COIN, 5 * COIN, 10 * COIN])
    assert picked(wallet, 8 * COIN) == [COIN, 2 * COIN, 5 * COIN]
    assert picked(wallet, 3 * COIN) == [COIN, 2 * COIN]


def test_select_coins_uses_lowest_larger_when_small_coins_insufficient(node):
    wallet = make_wallet_with_coins(node, [COIN, 2 * COIN, 20 * COIN, 30 * COIN])
    assert picked(wallet, 4 * COIN) == [20 * COIN]


def test_select_coins_picks_closest_overshoot(node):
    wallet = make_wallet_with_coins(node, [3 * COIN, 4 * COIN, 6 * COIN])
    assert sum(picked(wallet, 5 * COIN)) == 6 * COIN                # 6 比 3+4=7 更接近 5


def test_select_coins_insufficient_funds(node):
    wallet = make_wallet_with_coins(node, [COIN, 2 * COIN])
    assert picked(wallet, 4 * COIN) is None
    assert picked(wallet, 3 * COIN) == [COIN, 2 * COIN]


# ------------------------------------------------------------ 持久化与重播
def test_wallet_persistence(node, tmp_path):
    chain, pool, wallet_a, wallet_b = node
    assert wallet_a.send_money(wallet_b.get_default_address(), 10 * COIN)[0]
    wallet_a.set_address_label("1abc", "测试")
    wallet_a.set_transaction_fee(3 * CENT)
    snapshot = (wallet_a.get_balance(), wallet_a.get_default_address(),
                len(wallet_a.keys), len(wallet_a.map_wallet))
    wallet_a.close()

    reloaded = Wallet(str(tmp_path / "walletA"), chain, pool)
    assert (reloaded.get_balance(), reloaded.get_default_address(),
            len(reloaded.keys), len(reloaded.map_wallet)) == snapshot
    assert reloaded.address_book["1abc"] == "测试"
    assert reloaded.transaction_fee == 3 * CENT
    assert sum(w.f_spent for w in reloaded.map_wallet.values()) == 1
    reloaded.close()


def test_new_default_address_keeps_old_one_working(node):
    chain, pool, wallet_a, wallet_b = node
    old = wallet_b.get_default_address()
    wallet_b.set_new_default_key()
    assert wallet_b.get_default_address() != old
    assert wallet_a.send_money(old, 3 * COIN)[0]
    assert wallet_a.send_money(wallet_b.get_default_address(), 4 * COIN)[0]
    assert wallet_b.get_balance() == 7 * COIN


def test_reaccept_wallet_transactions_after_restart(node, tmp_path):
    """重启后内存池是空的：钱包里还没进块的交易必须被放回去。"""
    chain, pool, wallet_a, wallet_b = node
    assert wallet_a.send_money(wallet_b.get_default_address(), 10 * COIN)[0]
    tx = pool.transactions()[0]
    pool.remove(tx)                                                 # 模拟重启：内存池清空
    assert len(pool) == 0
    wallet_a.reaccept_wallet_transactions()
    assert tx.get_hash() in pool
    confirm(chain, pool)
    wallet_a.reaccept_wallet_transactions()                         # 已经进块的不会再放回去
    assert len(pool) == 0


def test_relay_wallet_transactions_is_throttled(node):
    chain, pool, wallet_a, wallet_b = node
    relayed = []
    wallet_a.relay_listeners.append(relayed.append)
    assert wallet_a.send_money(wallet_b.get_default_address(), 10 * COIN)[0]
    wallet_a.relay_wallet_transactions()
    assert [t.get_hash() for t in relayed] == [pool.transactions()[0].get_hash()]
    wallet_a.relay_wallet_transactions()                            # 10 分钟内不重复广播
    assert len(relayed) == 1
    wallet_a._last_relay_time -= 11 * 60
    confirm(chain, pool)
    wallet_a.relay_wallet_transactions()                            # 已确认的不再广播
    assert len(relayed) == 1


def test_replaced_transaction_is_erased_from_wallet(node):
    """种子链第 25 块的 coinbase 不属于钱包 A；用它造两版"付给 A"的可替换交易。"""
    chain, pool, wallet_a, _ = node
    h160 = hash160(wallet_a.default_key.get_pubkey())
    cb, key = coinbase_of(chain, 25), chain.test_keys[25]
    v1 = make_spend(cb, 0, key, 50 * COIN, to_h160=h160, n_sequence=1, n_lock_time=10**6)
    v2 = make_spend(cb, 0, key, 49 * COIN, to_h160=h160, n_sequence=2, n_lock_time=10**6)
    assert pool.accept(v1)
    assert v1.get_hash() in wallet_a.map_wallet
    assert pool.accept(v2)
    assert v1.get_hash() not in wallet_a.map_wallet
    assert v2.get_hash() in wallet_a.map_wallet


def test_non_final_transaction_is_not_counted_in_balance(node):
    chain, pool, wallet_a, _ = node
    before = wallet_a.get_balance()
    open_tx = make_spend(coinbase_of(chain, 25), 0, chain.test_keys[25], 50 * COIN,
                         to_h160=hash160(wallet_a.default_key.get_pubkey()),
                         n_sequence=1, n_lock_time=10**6)
    assert pool.accept(open_tx)
    assert open_tx.get_hash() in wallet_a.map_wallet
    assert wallet_a.get_balance() == before                         # 未定稿的收款不算数


def test_orphaned_generation_is_not_spendable(funded_chain, tmp_path):
    """自己挖到的块被更长的链顶掉了：那 50 BTC 在钱包里变成"未被接受"，不计入余额。"""
    chain, _ = funded_chain
    pool = MemPool(chain)
    wallet = Wallet(str(tmp_path / "w"), chain, pool)
    base = chain.best_index
    key = wallet.generate_new_key()
    mine = mine_block(chain, prev=base, key=key)
    assert chain.process_block(mine)
    wtx = wallet.map_wallet[mine.vtx[0].get_hash()]
    assert wallet.get_depth(wtx) == 1
    b1 = mine_block(chain, prev=base)
    assert chain.process_block(b1)
    assert chain.process_block(mine_block(chain, prev=chain.map_block_index[b1.get_hash()]))
    assert wallet.get_depth(wtx) == 0
    assert wallet.blocks_to_maturity(wtx) == 120
    assert wallet.get_balance() == 0
    wallet.close()
