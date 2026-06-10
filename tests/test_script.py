"""Script interpreter: bignum encoding, pushes, P2PK / P2PKH end-to-end."""

import pytest

from bitcoin import params
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.script import (
    OP_1,
    OP_16,
    OP_1NEGATE,
    OP_ADD,
    OP_CHECKSIG,
    OP_CODESEPARATOR,
    OP_DUP,
    OP_EQUAL,
    OP_EQUALVERIFY,
    OP_HASH160,
    OP_IF,
    OP_ELSE,
    OP_ENDIF,
    OP_RETURN,
    SIGHASH_ALL,
    CScript,
    bn_deserialize,
    bn_serialize,
    eval_script,
    script_pubkey_for_hash160,
    script_pubkey_for_pubkey,
    sign_signature,
    solver,
    verify_signature,
    TX_PUBKEY,
    TX_PUBKEYHASH,
)
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut


class KeyStore:
    def __init__(self, *keys):
        self.by_pubkey = {k.get_pubkey(): k for k in keys}
        self.by_h160 = {hash160(k.get_pubkey()): k for k in keys}

    def get_key_for_pubkey(self, pubkey):
        return self.by_pubkey.get(bytes(pubkey))

    def get_key_for_hash160(self, h160):
        return self.by_h160.get(bytes(h160))


@pytest.mark.parametrize("n", [0, 1, -1, 127, 128, -128, 255, 256, 2**31, -(2**40)])
def test_bignum_roundtrip(n):
    assert bn_deserialize(bn_serialize(n)) == n


def test_bignum_known_encodings():
    assert bn_serialize(0) == b""
    assert bn_serialize(1) == b"\x01"
    assert bn_serialize(-1) == b"\x81"
    assert bn_serialize(128) == b"\x80\x00"      # high bit needs a pad byte
    assert bn_serialize(-128) == b"\x80\x80"
    assert bn_serialize(486604799) == bytes.fromhex("ffff001d")


def _eval(script: CScript) -> bool:
    return eval_script(script, CTransaction(vin=[CTxIn()], vout=[CTxOut(0)]), 0)


def test_simple_arithmetic():
    s = CScript()
    s.push_int(2)
    s.push_int(3)
    s.push_opcode(OP_ADD)
    s.push_int(5)
    s.push_opcode(OP_EQUAL)
    assert _eval(s)


def test_if_else():
    s = CScript()
    s.push_int(1)
    s.push_opcode(OP_IF)
    s.push_int(7)
    s.push_opcode(OP_ELSE)
    s.push_opcode(OP_RETURN)
    s.push_opcode(OP_ENDIF)
    assert _eval(s)


def test_op_return_fails():
    s = CScript()
    s.push_int(1)
    s.push_opcode(OP_RETURN)
    assert not _eval(s)


def test_unbalanced_if_fails():
    s = CScript()
    s.push_int(1)
    s.push_opcode(OP_IF)
    assert not _eval(s)


def test_solver_templates():
    key = CKey.generate()
    pub = key.get_pubkey()
    kind, data = solver(script_pubkey_for_pubkey(pub))
    assert (kind, data) == (TX_PUBKEY, pub)
    kind, data = solver(script_pubkey_for_hash160(hash160(pub)))
    assert (kind, data) == (TX_PUBKEYHASH, hash160(pub))
    assert solver(CScript().push_opcode(OP_DUP)) is None


def _spend(script_pubkey, keystore):
    """Build prev tx + spending tx, sign input 0, return (prev, spend)."""
    prev = CTransaction(
        vin=[CTxIn(COutPoint(), CScript().push_int(0).push_int(999))],
        vout=[CTxOut(50 * params.COIN, script_pubkey)],
    )
    spend = CTransaction(
        vin=[CTxIn(COutPoint(prev.get_hash(), 0))],
        vout=[CTxOut(50 * params.COIN, script_pubkey_for_hash160(b"\x00" * 20))],
    )
    assert sign_signature(keystore, prev, spend, 0, SIGHASH_ALL)
    return prev, spend


def test_p2pk_sign_and_verify():
    key = CKey.generate()
    prev, spend = _spend(script_pubkey_for_pubkey(key.get_pubkey()), KeyStore(key))
    assert verify_signature(prev, spend, 0)


def test_p2pkh_sign_and_verify():
    key = CKey.generate()
    prev, spend = _spend(
        script_pubkey_for_hash160(hash160(key.get_pubkey())), KeyStore(key)
    )
    assert verify_signature(prev, spend, 0)


def test_wrong_key_rejected():
    key, other = CKey.generate(), CKey.generate()
    prev = CTransaction(
        vin=[CTxIn(COutPoint(), CScript().push_int(0).push_int(999))],
        vout=[CTxOut(50 * params.COIN,
                     script_pubkey_for_hash160(hash160(key.get_pubkey())))],
    )
    spend = CTransaction(vin=[CTxIn(COutPoint(prev.get_hash(), 0))],
                         vout=[CTxOut(1)])
    # signing with a keystore that lacks the right key fails outright
    assert not sign_signature(KeyStore(other), prev, spend, 0)


def test_tampered_output_rejected():
    key = CKey.generate()
    prev, spend = _spend(
        script_pubkey_for_hash160(hash160(key.get_pubkey())), KeyStore(key)
    )
    spend.vout[0].n_value += 1  # invalidate the signature
    assert not verify_signature(prev, spend, 0)


def test_min_fee_rules():
    tx = CTransaction(vin=[CTxIn()], vout=[CTxOut(params.COIN, b"\x51")])
    assert tx.get_min_fee(f_discount=False) == params.CENT
    assert tx.get_min_fee(f_discount=True) == 0
    # sub-cent output forces a fee even with the discount
    tx.vout[0].n_value = params.CENT - 1
    assert tx.get_min_fee(f_discount=True) == params.CENT
