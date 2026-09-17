"""用**真实比特币主网的历史数据**交叉验证本项目。

这些数据不是我们自己生成的，而是 2009 年 1 月由中本聪的原版程序写进区块链的。
如果我们的序列化、哈希、脚本引擎、签名哈希算法、ECDSA 验签里有任何一处与原版不一致，
这里的测试就一定会失败——这是对"复刻得对不对"最硬的检验。
"""

import json
import pathlib

import pytest

from bitcoin import base58, params
from bitcoin.block import CBlock
from bitcoin.hashes import hash256
from bitcoin.script import (
    SIGHASH_ALL,
    TX_PUBKEY,
    CScript,
    signature_hash,
    solver,
    verify_signature,
)
from bitcoin.serialize import DataStream, uint256_from_hex, uint256_to_hex
from bitcoin.tx import CTransaction

VECTORS = json.loads(
    (pathlib.Path(__file__).parent / "data" / "mainnet_vectors.json").read_text(encoding="utf-8"))


def load_tx(name: str) -> CTransaction:
    return CTransaction.deserialize(DataStream(bytes.fromhex(VECTORS[name]["hex"])))


@pytest.mark.parametrize("name", ["block9_coinbase", "block170_first_payment"])
def test_real_transactions_roundtrip_and_txid(name):
    raw = bytes.fromhex(VECTORS[name]["hex"])
    tx = load_tx(name)
    assert tx.serialized() == raw                                   # 反序列化再序列化，一个字节都不差
    assert uint256_to_hex(tx.get_hash()) == VECTORS[name]["txid"]   # 交易 ID 与区块浏览器一致
    assert tx.check_transaction()


def test_block9_coinbase_structure():
    tx = load_tx("block9_coinbase")
    assert tx.is_coinbase()
    assert tx.vout[0].n_value == 50 * params.COIN
    # scriptSig = << nBits(0x1d00ffff) << CBigNum(extraNonce=0x34)，与我们矿工的写法完全相同
    assert bytes(tx.vin[0].script_sig).hex() == "04ffff001d0134"
    assert bytes(CScript().push_int(0x1D00FFFF).push_bignum(0x34)) == bytes(tx.vin[0].script_sig)
    kind, pubkey = solver(tx.vout[0].script_pubkey)
    assert kind == TX_PUBKEY and len(pubkey) == 65
    assert base58.pubkey_to_address(pubkey) == "12cbQLTFMXRnSzktFkuoG3eHoMeFtpTu3S"


def test_first_payment_structure():
    """中本聪 -> Hal Finney：输入 50，付出 10，找零 40 回到**同一个公钥**（0.1.x 的找零方式）。"""
    prev = load_tx("block9_coinbase")
    tx = load_tx("block170_first_payment")
    assert not tx.is_coinbase()
    assert tx.vin[0].prevout.hash == prev.get_hash() and tx.vin[0].prevout.n == 0
    assert [o.n_value for o in tx.vout] == [10 * params.COIN, 40 * params.COIN]
    assert tx.get_value_out() == prev.vout[0].n_value               # 手续费为 0
    assert bytes(tx.vout[1].script_pubkey) == bytes(prev.vout[0].script_pubkey)
    assert solver(tx.vout[0].script_pubkey)[0] == TX_PUBKEY
    # scriptSig 只有一个压栈：DER 签名 + 末尾 1 字节的 hashtype
    (op, sig), = list(CScript(tx.vin[0].script_sig).ops())
    assert sig[0] == 0x30 and sig[-1] == SIGHASH_ALL


def test_first_payment_signature_verifies_with_our_script_engine():
    """最关键的一条：用我们的脚本虚拟机（拼接执行 + FindAndDelete + SignatureHash + ECDSA）
    去验证中本聪在 2009 年做出的那个真实签名。"""
    prev = load_tx("block9_coinbase")
    tx = load_tx("block170_first_payment")
    assert verify_signature(prev, tx, 0)


def test_first_payment_signature_hash_value():
    """SignatureHash 的中间结果也应当与公开资料里记载的一致。"""
    prev = load_tx("block9_coinbase")
    tx = load_tx("block170_first_payment")
    digest = signature_hash(CScript(prev.vout[0].script_pubkey), tx, 0, SIGHASH_ALL)
    # 手工按定义再算一遍：清空 scriptSig -> 填入 scriptPubKey -> 末尾接 01000000
    script_pubkey = bytes(prev.vout[0].script_pubkey)
    raw = bytes.fromhex(VECTORS["block170_first_payment"]["hex"])
    sig_len = raw[41]                       # 版本(4)+输入数(1)+prevout(36) 之后就是 scriptSig 长度
    manual = (raw[:41] + bytes([len(script_pubkey)]) + script_pubkey
              + raw[42 + sig_len:] + (1).to_bytes(4, "little"))
    assert digest == int.from_bytes(hash256(manual), "little")


@pytest.mark.parametrize("mutation", ["amount", "payee", "sig", "wrong_prev_script"])
def test_first_payment_any_tampering_breaks_the_signature(mutation):
    prev = load_tx("block9_coinbase")
    tx = load_tx("block170_first_payment")
    if mutation == "amount":
        tx.vout[0].n_value += 1                             # 想多拿一聪
    elif mutation == "payee":
        script = bytearray(tx.vout[0].script_pubkey)
        script[10] ^= 0x01                                  # 把收款公钥改掉一位
        tx.vout[0].script_pubkey = bytes(script)
    elif mutation == "sig":
        sig = bytearray(tx.vin[0].script_sig)
        sig[20] ^= 0x01
        tx.vin[0].script_sig = bytes(sig)
    else:
        script = bytearray(prev.vout[0].script_pubkey)
        script[5] ^= 0x01
        prev.vout[0].script_pubkey = bytes(script)
        tx.vin[0].prevout.hash = prev.get_hash()
    assert not verify_signature(prev, tx, 0)


def test_block170_merkle_root_matches_real_header():
    """第 170 号区块有两笔交易：用我们的默克尔树算法从两个交易 ID 算出根，
    应当等于真实区块头里的 hashMerkleRoot。"""
    header = bytes.fromhex(next(h["hex"] for h in VECTORS["headers"] if h["height"] == 170))
    real_root = int.from_bytes(header[36:68], "little")

    class FakeTx:
        def __init__(self, txid):
            self._h = uint256_from_hex(txid)

        def get_hash(self):
            return self._h

    block = CBlock(vtx=[
        FakeTx("b1fea52486ce0c62bb442b530a3f0132b826c74e473d1f2c220bfa78111c5082"),
        FakeTx(VECTORS["block170_first_payment"]["txid"]),
    ])
    assert block.get_merkle_root() == real_root


@pytest.mark.parametrize("entry", VECTORS["headers"], ids=lambda e: f"height{e['height']}")
def test_real_headers_parse_and_satisfy_mainnet_pow(entry):
    raw = bytes.fromhex(entry["hex"])
    block = CBlock.deserialize(DataStream(raw + b"\x00"))          # 头 + "0 笔交易"
    assert block.header_bytes() == raw
    assert uint256_to_hex(block.get_hash()) == entry["hash"]
    assert block.n_bits == 0x1D00FFFF
    # 主网的难度目标比私网的上限严格得多，所以这里直接比较哈希与目标值
    assert block.get_hash() <= params.compact_to_target(block.n_bits)


def test_block1_links_to_mainnet_genesis():
    raw = bytes.fromhex(next(h["hex"] for h in VECTORS["headers"] if h["height"] == 1))
    block = CBlock.deserialize(DataStream(raw + b"\x00"))
    assert uint256_to_hex(block.hash_prev_block) == (
        "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f")
