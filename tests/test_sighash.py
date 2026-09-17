"""签名相关：SignatureHash 的四种模式、CHECKSIG 系列、多重签名，以及 0.1.x 的几个著名漏洞。"""

import pytest

from bitcoin import params
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.script import (
    OP_1,
    OP_2,
    OP_3,
    OP_CHECKMULTISIG,
    OP_CHECKMULTISIGVERIFY,
    OP_CHECKSIG,
    OP_CHECKSIGVERIFY,
    OP_CODESEPARATOR,
    OP_RETURN,
    SIGHASH_ALL,
    SIGHASH_ANYONECANPAY,
    SIGHASH_NONE,
    SIGHASH_SINGLE,
    CScript,
    eval_script,
    script_pubkey_for_hash160,
    script_pubkey_for_pubkey,
    sign_signature,
    signature_hash,
    verify_signature,
)
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut
from tests.conftest import KeyStore

COIN = params.COIN


def funding_tx(*script_pubkeys) -> CTransaction:
    """造一笔"以前的交易"，每个输出 50 BTC，锁分别是传入的脚本。"""
    return CTransaction(
        vin=[CTxIn(COutPoint(), CScript().push_int(0).push_int(999))],
        vout=[CTxOut(50 * COIN, spk) for spk in script_pubkeys],
    )


def two_in_two_out(prev: CTransaction) -> CTransaction:
    return CTransaction(
        vin=[CTxIn(COutPoint(prev.get_hash(), 0)), CTxIn(COutPoint(prev.get_hash(), 1))],
        vout=[CTxOut(30 * COIN, script_pubkey_for_hash160(b"\x11" * 20)),
              CTxOut(70 * COIN, script_pubkey_for_hash160(b"\x22" * 20))],
    )


# ------------------------------------------------------------ 两种标准交易
@pytest.mark.parametrize("kind", ["p2pk", "p2pkh"])
def test_standard_sign_and_verify(kind):
    key = CKey.generate()
    spk = (script_pubkey_for_pubkey(key.get_pubkey()) if kind == "p2pk"
           else script_pubkey_for_hash160(hash160(key.get_pubkey())))
    prev = funding_tx(spk)
    spend = CTransaction(vin=[CTxIn(COutPoint(prev.get_hash(), 0))],
                         vout=[CTxOut(50 * COIN, script_pubkey_for_hash160(b"\x00" * 20))])
    assert sign_signature(KeyStore(key), prev, spend, 0)
    assert verify_signature(prev, spend, 0)
    ops = list(CScript(spend.vin[0].script_sig).ops())
    assert len(ops) == (1 if kind == "p2pk" else 2)     # P2PK 只要签名；P2PKH 要签名+公钥


def test_signing_without_the_key_fails():
    owner, thief = CKey.generate(), CKey.generate()
    prev = funding_tx(script_pubkey_for_hash160(hash160(owner.get_pubkey())))
    spend = CTransaction(vin=[CTxIn(COutPoint(prev.get_hash(), 0))], vout=[CTxOut(1)])
    assert not sign_signature(KeyStore(thief), prev, spend, 0)


def test_p2pkh_with_someone_elses_pubkey_fails():
    """小偷用自己的钥匙签名、附上自己的公钥——公钥哈希对不上，EQUALVERIFY 这一步就过不去。"""
    owner, thief = CKey.generate(), CKey.generate()
    prev = funding_tx(script_pubkey_for_hash160(hash160(owner.get_pubkey())))
    spend = CTransaction(vin=[CTxIn(COutPoint(prev.get_hash(), 0))], vout=[CTxOut(1)])
    digest = signature_hash(CScript(prev.vout[0].script_pubkey), spend, 0, SIGHASH_ALL)
    sig = thief.sign(digest.to_bytes(32, "little")) + bytes([SIGHASH_ALL])
    spend.vin[0].script_sig = CScript().push_data(sig).push_data(thief.get_pubkey())
    assert not verify_signature(prev, spend, 0)


def test_verify_checks_prevout_consistency():
    key = CKey.generate()
    prev = funding_tx(script_pubkey_for_pubkey(key.get_pubkey()))
    spend = CTransaction(vin=[CTxIn(COutPoint(prev.get_hash(), 0))], vout=[CTxOut(1)])
    assert sign_signature(KeyStore(key), prev, spend, 0)
    other = funding_tx(script_pubkey_for_pubkey(key.get_pubkey()), CScript())
    assert not verify_signature(other, spend, 0)            # 哈希对不上
    spend.vin[0].prevout.n = 5
    assert not verify_signature(prev, spend, 0)             # 序号越界


def test_signature_cannot_be_replayed_on_another_input():
    """同一个人的两笔钱：为第 0 个输入做的签名，不能拿去花第 1 个输入。"""
    key = CKey.generate()
    spk = script_pubkey_for_pubkey(key.get_pubkey())
    prev = funding_tx(spk, spk)
    spend = two_in_two_out(prev)
    assert sign_signature(KeyStore(key), prev, spend, 0)
    spend.vin[1].script_sig = spend.vin[0].script_sig
    assert verify_signature(prev, spend, 0)
    assert not verify_signature(prev, spend, 1)


# ------------------------------------------------------------- 四种 hashtype
def signed_pair(hash_type):
    key = CKey.generate()
    spk = script_pubkey_for_pubkey(key.get_pubkey())
    prev = funding_tx(spk, spk)
    spend = two_in_two_out(prev)
    assert sign_signature(KeyStore(key), prev, spend, 0, hash_type)
    assert verify_signature(prev, spend, 0)
    return prev, spend


def test_sighash_all_locks_everything():
    prev, spend = signed_pair(SIGHASH_ALL)
    spend.vout[1].n_value -= 1
    assert not verify_signature(prev, spend, 0)

    prev, spend = signed_pair(SIGHASH_ALL)
    spend.vin[1].n_sequence = 7
    assert not verify_signature(prev, spend, 0)

    prev, spend = signed_pair(SIGHASH_ALL)
    spend.n_lock_time = 99
    assert not verify_signature(prev, spend, 0)

    prev, spend = signed_pair(SIGHASH_ALL)
    spend.vin[1].script_sig = b"\x51"           # 别的输入的 scriptSig 不在签名范围内
    assert verify_signature(prev, spend, 0)


def test_sighash_none_leaves_outputs_open():
    prev, spend = signed_pair(SIGHASH_NONE)
    spend.vout = [CTxOut(100 * COIN, script_pubkey_for_hash160(b"\x99" * 20))]
    spend.vin[1].n_sequence = 7                 # 其它输入的序列号也放开了
    assert verify_signature(prev, spend, 0)
    spend.vin[0].n_sequence = 7                 # 但自己这个输入的序列号仍被锁定
    assert not verify_signature(prev, spend, 0)


def test_sighash_single_locks_only_the_matching_output():
    prev, spend = signed_pair(SIGHASH_SINGLE)
    spend.vout[1].n_value = 1                   # 序号不同的输出：随便改
    spend.vout.append(CTxOut(5, b""))
    assert verify_signature(prev, spend, 0)
    spend.vout[0].n_value -= 1                  # 同序号的输出：锁定
    assert not verify_signature(prev, spend, 0)


def test_sighash_anyonecanpay_allows_adding_inputs():
    prev, spend = signed_pair(SIGHASH_ALL | SIGHASH_ANYONECANPAY)
    spend.vin.append(CTxIn(COutPoint(12345, 0)))        # 别人又凑了一个输入进来
    assert verify_signature(prev, spend, 0)
    spend.vout[0].n_value -= 1                          # 输出仍然是锁定的
    assert not verify_signature(prev, spend, 0)


def test_hashtype_is_part_of_what_is_signed():
    """hashtype 字节会被接到待签名数据的末尾，所以事后改 hashtype 会让签名失效。"""
    prev, spend = signed_pair(SIGHASH_ALL)
    sig_push = bytearray(spend.vin[0].script_sig)
    sig_push[-1] = SIGHASH_NONE
    spend.vin[0].script_sig = bytes(sig_push)
    assert not verify_signature(prev, spend, 0)


def test_explicit_hashtype_argument_must_match_signature():
    prev, spend = signed_pair(SIGHASH_ALL)
    assert verify_signature(prev, spend, 0, SIGHASH_ALL)
    assert not verify_signature(prev, spend, 0, SIGHASH_NONE)


def test_signature_hash_differs_per_input_and_per_type():
    prev, spend = signed_pair(SIGHASH_ALL)
    code = CScript(prev.vout[0].script_pubkey)
    digests = {signature_hash(code, spend, i, t)
               for i in (0, 1)
               for t in (SIGHASH_ALL, SIGHASH_NONE, SIGHASH_SINGLE,
                         SIGHASH_ALL | SIGHASH_ANYONECANPAY)}
    assert len(digests) == 8


# ------------------------------------------------------------------ 著名漏洞
def test_signature_hash_returns_one_when_input_index_out_of_range():
    """"return 1" bug：nIn 越界时不是报错，而是返回常数 1。"""
    _, spend = signed_pair(SIGHASH_ALL)
    assert signature_hash(CScript(), spend, 99, SIGHASH_ALL) == 1


def test_sighash_single_returns_one_when_no_matching_output():
    key = CKey.generate()
    spk = script_pubkey_for_pubkey(key.get_pubkey())
    prev = funding_tx(spk, spk)
    spend = two_in_two_out(prev)
    spend.vout = spend.vout[:1]                 # 只剩 1 个输出，第 1 个输入没有"同序号的输出"
    assert signature_hash(CScript(spk), spend, 1, SIGHASH_SINGLE) == 1


def test_op_return_exploit_spends_anyones_coins():
    """0.1.x 最严重的漏洞（CVE-2010-5141）的完整演示：
    不需要任何私钥，scriptSig 写成 `OP_1 OP_RETURN` 就能花掉任何一笔钱。
    因为两段脚本是拼接执行的，OP_RETURN 让执行在到达对方的"锁"之前就结束了，
    而此时栈顶是 1（真）。2010 年 7 月的修复：分两阶段执行 + OP_RETURN 改为立即失败。"""
    victim = CKey.generate()
    prev = funding_tx(script_pubkey_for_hash160(hash160(victim.get_pubkey())))
    theft = CTransaction(vin=[CTxIn(COutPoint(prev.get_hash(), 0))],
                         vout=[CTxOut(50 * COIN, script_pubkey_for_hash160(b"\x66" * 20))])
    theft.vin[0].script_sig = bytes([OP_1, OP_RETURN])
    assert verify_signature(prev, theft, 0)


# ---------------------------------------------------- CHECKSIG / CODESEPARATOR
def manual_sig(key, script_code, tx, n_in, hash_type=SIGHASH_ALL) -> bytes:
    digest = signature_hash(CScript(script_code), tx, n_in, hash_type)
    return key.sign(digest.to_bytes(32, "little")) + bytes([hash_type])


def test_checksig_pushes_false_instead_of_failing():
    key = CKey.generate()
    tx = CTransaction(vin=[CTxIn()], vout=[CTxOut(1)])
    spk = script_pubkey_for_pubkey(key.get_pubkey())
    bad_sig = manual_sig(CKey.generate(), spk, tx, 0)
    # CHECKSIG 失败只是压入"假"；后面再压个 1，整个脚本依然为真
    s = CScript().push_data(bad_sig) + CScript([OP_CODESEPARATOR]) + spk + CScript([OP_1])
    assert eval_script(s, tx, 0)
    # CHECKSIGVERIFY 失败则跳到末尾，栈顶是"假"
    spk_v = CScript().push_data(key.get_pubkey()).push_opcode(OP_CHECKSIGVERIFY)
    s = CScript().push_data(bad_sig) + CScript([OP_CODESEPARATOR]) + spk_v + CScript([OP_1])
    assert not eval_script(s, tx, 0)


def test_checksigverify_success():
    key = CKey.generate()
    tx = CTransaction(vin=[CTxIn()], vout=[CTxOut(1)])
    spk = CScript().push_data(key.get_pubkey()).push_opcode(OP_CHECKSIGVERIFY).push_opcode(OP_1)
    sig = manual_sig(key, spk, tx, 0)
    assert eval_script(CScript().push_data(sig) + CScript([OP_CODESEPARATOR]) + spk, tx, 0)


def test_empty_signature_is_false():
    key = CKey.generate()
    tx = CTransaction(vin=[CTxIn()], vout=[CTxOut(1)])
    spk = script_pubkey_for_pubkey(key.get_pubkey())
    assert not eval_script(CScript().push_data(b"") + CScript([OP_CODESEPARATOR]) + spk, tx, 0)


def test_codeseparator_limits_what_the_signature_covers():
    """签名只覆盖"最近一个 OP_CODESEPARATOR 之后"的脚本。
    所以拼接时放在中间的那个分隔符保证了：scriptSig 的内容不在签名范围内。"""
    key = CKey.generate()
    tx = CTransaction(vin=[CTxIn()], vout=[CTxOut(1)])
    spk = script_pubkey_for_pubkey(key.get_pubkey())
    sig = manual_sig(key, spk, tx, 0)
    base = CScript().push_data(sig)
    assert eval_script(base + CScript([OP_CODESEPARATOR]) + spk, tx, 0)
    # 在分隔符**之前**塞任何东西都不影响验签
    noisy = CScript().push_data(b"noise").push_opcode(0x75) + base      # 0x75 = OP_DROP
    assert eval_script(noisy + CScript([OP_CODESEPARATOR]) + spk, tx, 0)


# ------------------------------------------------------------------ 多重签名
def multisig_setup(n_required, n_keys):
    keys = [CKey.generate() for _ in range(n_keys)]
    spk = CScript().push_int(n_required)
    for k in keys:
        spk.push_data(k.get_pubkey())
    spk.push_int(n_keys).push_opcode(OP_CHECKMULTISIG)
    tx = CTransaction(vin=[CTxIn()], vout=[CTxOut(1)])
    return keys, spk, tx


def run_multisig(spk, tx, sigs, dummy=True):
    s = CScript()
    if dummy:
        s.push_int(0)               # 那个因为 off-by-one bug 而必须多放的元素
    for sig in sigs:
        s.push_data(sig)
    return eval_script(s + CScript([OP_CODESEPARATOR]) + spk, tx, 0)


def test_multisig_2_of_3_all_valid_key_combinations():
    keys, spk, tx = multisig_setup(2, 3)
    sig = [manual_sig(k, spk, tx, 0) for k in keys]
    assert run_multisig(spk, tx, [sig[0], sig[1]])
    assert run_multisig(spk, tx, [sig[0], sig[2]])
    assert run_multisig(spk, tx, [sig[1], sig[2]])


def test_multisig_signatures_must_follow_key_order():
    keys, spk, tx = multisig_setup(2, 3)
    sig = [manual_sig(k, spk, tx, 0) for k in keys]
    assert not run_multisig(spk, tx, [sig[1], sig[0]])


def test_multisig_rejects_insufficient_or_foreign_signatures():
    keys, spk, tx = multisig_setup(2, 3)
    sig = [manual_sig(k, spk, tx, 0) for k in keys]
    assert not run_multisig(spk, tx, [sig[0]])                          # 只有 1 个签名
    assert not run_multisig(spk, tx, [sig[0], sig[0]])                  # 同一个签名用两次
    stranger = manual_sig(CKey.generate(), spk, tx, 0)
    assert not run_multisig(spk, tx, [sig[0], stranger])


def test_multisig_1_of_1_and_3_of_3():
    keys, spk, tx = multisig_setup(1, 1)
    assert run_multisig(spk, tx, [manual_sig(keys[0], spk, tx, 0)])
    keys, spk, tx = multisig_setup(3, 3)
    assert run_multisig(spk, tx, [manual_sig(k, spk, tx, 0) for k in keys])


def test_multisig_off_by_one_requires_a_dummy_element():
    """OP_CHECKMULTISIG 会多弹一个栈元素——没有那个垫底的元素就会因为"栈不够"而失败。
    这个 bug 成了共识的一部分，至今所有多签交易的 scriptSig 都以一个 OP_0 开头。"""
    keys, spk, tx = multisig_setup(1, 2)
    sigs = [manual_sig(keys[0], spk, tx, 0)]
    assert run_multisig(spk, tx, sigs, dummy=True)
    assert not run_multisig(spk, tx, sigs, dummy=False)


def test_multisig_bad_counts_fail():
    key = CKey.generate()
    tx = CTransaction(vin=[CTxIn()], vout=[CTxOut(1)])
    for n_sigs, n_keys in ((2, 1), (-1, 1), (1, -1)):
        spk = (CScript().push_int(n_sigs).push_data(key.get_pubkey())
               .push_int(n_keys).push_opcode(OP_CHECKMULTISIG))
        assert not run_multisig(spk, tx, [b"\x30\x01"])


def test_checkmultisigverify():
    keys, _, tx = multisig_setup(1, 1)
    spk = (CScript().push_int(1).push_data(keys[0].get_pubkey()).push_int(1)
           .push_opcode(OP_CHECKMULTISIGVERIFY).push_opcode(OP_1))
    assert run_multisig(spk, tx, [manual_sig(keys[0], spk, tx, 0)])
    assert not run_multisig(spk, tx, [manual_sig(CKey.generate(), spk, tx, 0)])
