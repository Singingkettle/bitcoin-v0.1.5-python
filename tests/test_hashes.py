"""哈希函数：RIPEMD-160 的官方测试向量，以及用真实主网数据验证的双 SHA-256。"""

import hashlib
import json
import pathlib

import pytest

from bitcoin.hashes import hash160, hash256, ripemd160, sha1, sha256
from bitcoin.ripemd160 import ripemd160 as ripemd160_pure

VECTORS = json.loads(
    (pathlib.Path(__file__).parent / "data" / "mainnet_vectors.json").read_text(encoding="utf-8"))

# RIPEMD-160 作者（Bosselaers）公布的官方测试向量
RIPEMD_VECTORS = {
    b"": "9c1185a5c5e9fc54612808977ee8f548b2258d31",
    b"a": "0bdc9d2d256b3ee9daae347be6f4dc835a467ffe",
    b"abc": "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc",
    b"message digest": "5d0689ef49d2fae572b881b123a85ffa21595f36",
    b"abcdefghijklmnopqrstuvwxyz": "f71c27109c692c1b56bbdceb5b9d2865b3708dbc",
    b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq":
        "12a053384a9c0c88e405a06c27dcf49ada62eb2b",
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789":
        "b0e20b6e3116640286ed3a87a5713079b21f5189",
    b"1234567890" * 8: "9b752e45573d4b39f4dbd3323cab82bf63326bfb",
    b"a" * 1_000_000: "52783243c1697bdbe16d37f97f68f08325dc1528",
}


@pytest.mark.parametrize("msg,want", list(RIPEMD_VECTORS.items()),
                         ids=[f"len{len(m)}" for m in RIPEMD_VECTORS])
def test_pure_python_ripemd160_official_vectors(msg, want):
    assert ripemd160_pure(msg).hex() == want


@pytest.mark.parametrize("n", [0, 1, 55, 56, 57, 63, 64, 65, 119, 120, 128, 1000])
def test_ripemd160_padding_boundaries(n):
    """填充规则在 55/56/64 字节附近最容易写错；有 hashlib 实现时拿它当裁判。"""
    msg = bytes(range(256)) * 4
    msg = msg[:n]
    try:
        want = hashlib.new("ripemd160", msg).digest()
    except ValueError:
        pytest.skip("本机的 hashlib 不支持 ripemd160，无法交叉验证")
    assert ripemd160_pure(msg) == want


def test_ripemd160_wrapper_matches_pure():
    assert ripemd160(b"hello") == ripemd160_pure(b"hello")


def test_sha_known_answers():
    assert sha256(b"abc").hex() == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    assert sha1(b"abc").hex() == "a9993e364706816aba3e25717850c26c9cd0d89d"
    assert hash256(b"") == hashlib.sha256(hashlib.sha256(b"").digest()).digest()


def test_hash160_of_satoshi_genesis_pubkey():
    pubkey = bytes.fromhex(
        "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
        "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f")
    assert hash160(pubkey).hex() == "62e907b15cbf27d5425399ebf6f0fb50ebb88f18"


def test_mainnet_genesis_header_hash():
    header = (
        (1).to_bytes(4, "little") + bytes(32)
        + bytes.fromhex("4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b")[::-1]
        + (1231006505).to_bytes(4, "little")
        + (0x1D00FFFF).to_bytes(4, "little")
        + (2083236893).to_bytes(4, "little"))
    assert hash256(header)[::-1].hex() == (
        "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f")


@pytest.mark.parametrize("entry", VECTORS["headers"], ids=lambda e: f"height{e['height']}")
def test_real_mainnet_block_headers(entry):
    """真实主网区块头：80 字节做两次 SHA-256，反转后就是区块浏览器上显示的哈希。"""
    raw = bytes.fromhex(entry["hex"])
    assert len(raw) == 80
    assert hash256(raw)[::-1].hex() == entry["hash"]
