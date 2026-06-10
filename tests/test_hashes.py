"""RIPEMD-160 published vectors + the mainnet genesis header as a SHA256d vector."""

from bitcoin.hashes import hash160, hash256, sha256
from bitcoin.ripemd160 import ripemd160

# Bosselaers' official RIPEMD-160 test vectors
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


def test_ripemd160_vectors():
    for msg, want in RIPEMD_VECTORS.items():
        assert ripemd160(msg).hex() == want


def test_hash160_known_pubkey():
    # Satoshi's genesis output pubkey -> hash160 (verifiable from the utxo set)
    pubkey = bytes.fromhex(
        "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
        "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f"
    )
    assert hash160(pubkey).hex() == "62e907b15cbf27d5425399ebf6f0fb50ebb88f18"


def test_sha256d_mainnet_genesis_header():
    # the 80-byte mainnet genesis header, assembled field by field
    header = (
        (1).to_bytes(4, "little")
        + bytes(32)
        + bytes.fromhex(
            "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"
        )[::-1]
        + (1231006505).to_bytes(4, "little")
        + (0x1D00FFFF).to_bytes(4, "little")
        + (2083236893).to_bytes(4, "little")
    )
    assert hash256(header)[::-1].hex() == (
        "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
    )


def test_sha256_sanity():
    assert sha256(b"abc").hex() == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
