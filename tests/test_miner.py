"""矿工：候选区块的组装、手续费门槛、密钥管理、挖矿线程。"""

import time

import pytest

from bitcoin import params
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.mempool import MemPool
from bitcoin.miner import Miner
from bitcoin.script import CScript, script_pubkey_for_hash160, sign_signature
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut
from bitcoin.wallet import Wallet
from tests.conftest import SEED_HEIGHT, KeyStore, coinbase_of, make_spend

COIN, CENT = params.COIN, params.CENT


@pytest.fixture
def rig(funded_chain, tmp_path):
    chain, keys = funded_chain
    pool = MemPool(chain)
    wallet = Wallet(str(tmp_path / "minerwallet"), chain, pool)
    miner = Miner(chain, pool, wallet)
    yield chain, keys, pool, wallet, miner
    miner.stop()
    wallet.close()


def test_candidate_block_structure(rig):
    chain, _, _, _, miner = rig
    block, prev_hash = miner.create_new_block()
    assert prev_hash == chain.best_index.hash == block.hash_prev_block
    assert block.n_bits == chain.get_next_work_required(chain.best_index)
    assert block.hash_merkle_root == block.get_merkle_root()
    assert block.n_time > chain.best_index.get_median_time_past()
    cb = block.vtx[0]
    assert cb.is_coinbase() and cb.check_transaction()
    assert cb.vout[0].n_value == 50 * COIN
    # coinbase 的 scriptSig = << nBits << CBigNum(extraNonce)，与主网第 9 号区块的写法一致
    ops = list(CScript(cb.vin[0].script_sig).ops())
    assert len(ops) == 2 and ops[1][1] == b"\x01"


def test_extra_nonce_makes_every_candidate_unique(rig):
    _, _, _, _, miner = rig
    hashes = {miner.create_new_block()[0].vtx[0].get_hash() for _ in range(5)}
    assert len(hashes) == 5


def test_mined_block_is_accepted_and_pays_the_wallet(rig):
    chain, _, _, wallet, miner = rig
    block = miner.mine_one_block()
    assert chain.best_index.hash == block.get_hash()
    assert chain.best_height == SEED_HEIGHT + 1
    assert miner.blocks_found == 1
    wtx = wallet.map_wallet[block.vtx[0].get_hash()]
    assert wallet.get_credit(wtx.tx) == 50 * COIN
    assert wallet.get_balance() == 0                                # 还没成熟


def test_key_is_saved_only_when_a_block_is_found(rig):
    """原版：收款密钥先只在矿工手里，挖到块才存进钱包，然后换新的。
    没挖到的尝试不应该在钱包里留下任何痕迹。"""
    _, _, _, wallet, miner = rig
    n_keys = len(wallet.keys)
    first_key = miner.key
    for _ in range(3):
        miner.create_new_block()
    assert len(wallet.keys) == n_keys and miner.key is first_key
    miner.mine_one_block()
    assert len(wallet.keys) == n_keys + 1
    assert first_key.get_pubkey() in wallet.keys
    assert miner.key is not first_key


def test_includes_mempool_transactions_and_collects_fees(rig):
    chain, keys, pool, _, miner = rig
    paying = make_spend(coinbase_of(chain, 1), 0, keys[1], 49 * COIN)       # 手续费 1 BTC
    free = make_spend(coinbase_of(chain, 2), 0, keys[2], 50 * COIN)         # 免费
    assert pool.accept(paying) and pool.accept(free)
    block, _ = miner.create_new_block()
    assert {t.get_hash() for t in block.vtx[1:]} == {paying.get_hash(), free.get_hash()}
    assert block.vtx[0].vout[0].n_value == 51 * COIN
    assert miner.solve(block) and chain.process_block(block)
    assert len(pool) == 0


def test_fee_threshold_is_enforced_by_the_miner_not_the_mempool(rig):
    """"粉尘"交易不付 0.01 手续费：内存池收了，但矿工不打包它。"""
    chain, keys, pool, _, miner = rig
    cb = coinbase_of(chain, 1)

    def dust_tx(fee):
        tx = CTransaction(
            vin=[CTxIn(COutPoint(cb.get_hash(), 0))],
            vout=[CTxOut(CENT - 1, script_pubkey_for_hash160(b"\x11" * 20)),
                  CTxOut(50 * COIN - (CENT - 1) - fee, script_pubkey_for_hash160(b"\x22" * 20))])
        assert sign_signature(KeyStore(keys[1]), cb, tx, 0)
        return tx

    stingy = dust_tx(fee=0)
    assert pool.accept(stingy)
    assert len(miner.create_new_block()[0].vtx) == 1                # 只有 coinbase
    pool.remove(stingy)
    generous = dust_tx(fee=CENT)
    assert pool.accept(generous)
    block, _ = miner.create_new_block()
    assert [t.get_hash() for t in block.vtx[1:]] == [generous.get_hash()]
    assert block.vtx[0].vout[0].n_value == 50 * COIN + CENT


def test_only_first_100_transactions_are_free(rig, monkeypatch):
    """区块里的前 100 笔交易免费，之后的每笔至少要付 0.01。（为了不真的造 100 笔交易，
    这里直接检查矿工传给 GetMinFee 的参数。）"""
    chain, keys, pool, _, miner = rig
    calls = []
    original = CTransaction.get_min_fee

    def spy(self, f_discount=False):
        calls.append(f_discount)
        return original(self, f_discount)

    monkeypatch.setattr(CTransaction, "get_min_fee", spy)
    for h in (1, 2, 3):
        assert pool.accept(make_spend(coinbase_of(chain, h), 0, keys[h], 50 * COIN))
    miner.create_new_block()
    assert calls == [True, True, True]                              # vtx 还不到 100 笔：都享受免费


def test_dependent_transactions_are_included_in_order(rig):
    """池里的子交易花父交易的输出：不管池里的遍历顺序如何，区块里父交易必须排在前面。"""
    chain, keys, pool, _, miner = rig
    k1, k2 = CKey.generate(), CKey.generate()
    parent = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN,
                        to_h160=hash160(k1.get_pubkey()))
    child = make_spend(parent, 0, k1, 50 * COIN, to_h160=hash160(k2.get_pubkey()))
    grandchild = make_spend(child, 0, k2, 50 * COIN)
    for tx in (parent, child, grandchild):
        assert pool.accept(tx)
    # 故意把池里的顺序弄成 孙、子、父
    pool.map_transactions = dict(reversed(list(pool.map_transactions.items())))
    block, _ = miner.create_new_block()
    order = [t.get_hash() for t in block.vtx[1:]]
    assert order == [parent.get_hash(), child.get_hash(), grandchild.get_hash()]
    assert miner.solve(block) and chain.process_block(block)


def test_failed_candidate_does_not_poison_later_ones(rig):
    """坏交易 bad 同时花 cb1（有效）和 cb2（已被花掉）：验证到 cb2 才失败，但此前 cb1 已经在草稿里被标成"已花"。
    原版的做法是每笔交易在草稿的**副本**上试，失败就整份丢弃——否则排在后面、只花 cb1 的好交易会被误拒。"""
    chain, keys, pool, _, miner = rig
    from tests.conftest import mine_block
    cb1, cb2 = coinbase_of(chain, 1), coinbase_of(chain, 2)
    assert chain.process_block(mine_block(chain, txs=[make_spend(cb2, 0, keys[2], 50 * COIN)]))

    bad = CTransaction(vin=[CTxIn(COutPoint(cb1.get_hash(), 0)), CTxIn(COutPoint(cb2.get_hash(), 0))],
                       vout=[CTxOut(100 * COIN, script_pubkey_for_hash160(b"D" * 20))])
    assert sign_signature(KeyStore(keys[1]), cb1, bad, 0)
    assert sign_signature(KeyStore(keys[2]), cb2, bad, 1)
    good = make_spend(cb1, 0, keys[1], 50 * COIN)
    # 正常途径下这两笔交易互相冲突，进不了同一个内存池；这里直接塞进去，并保证 bad 排在前面
    pool.map_transactions[bad.get_hash()] = bad
    pool.map_transactions[good.get_hash()] = good

    block, _ = miner.create_new_block()
    assert [t.get_hash() for t in block.vtx[1:]] == [good.get_hash()]
    assert miner.solve(block) and chain.process_block(block)


def test_non_final_transactions_are_not_mined(rig):
    chain, keys, pool, _, miner = rig
    open_tx = make_spend(coinbase_of(chain, 1), 0, keys[1], 50 * COIN,
                         n_sequence=1, n_lock_time=10**6)
    assert pool.accept(open_tx)
    assert len(miner.create_new_block()[0].vtx) == 1


def test_solve_gives_up_when_chain_tip_changes(rig):
    chain, _, _, _, miner = rig
    block, prev_hash = miner.create_new_block()
    block.n_bits = 0x03000001                   # 几乎不可能满足的难度，保证它一直挖不到
    from tests.conftest import mine_block
    assert chain.process_block(mine_block(chain))                   # 别人先出块了
    started = time.time()
    assert miner.solve(block, prev_hash) is False
    assert time.time() - started < 20


def test_miner_thread_smoke(rig):
    chain, _, _, wallet, miner = rig
    miner.start()
    assert miner.running
    deadline = time.time() + 60
    while miner.blocks_found < 3 and time.time() < deadline:
        time.sleep(0.05)
    miner.stop()
    assert not miner.running
    assert miner.blocks_found >= 3
    assert chain.best_height >= SEED_HEIGHT + 3
    assert sum(w.tx.is_coinbase() for w in wallet.map_wallet.values()) == miner.blocks_found
    miner.start()                                                   # 停了之后还能再启动
    miner.stop()
