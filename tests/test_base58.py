import pytest

from bitcoin import base58


def test_satoshi_genesis_address():
    pubkey = bytes.fromhex(
        "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
        "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f"
    )
    assert base58.pubkey_to_address(pubkey) == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


def test_leading_zeros():
    assert base58.encode(b"\x00\x00\x01") == "112"
    assert base58.decode("112") == b"\x00\x00\x01"


def test_check_roundtrip():
    payload = bytes(range(21))
    s = base58.encode_check(payload)
    assert base58.decode_check(s) == payload


def test_bad_checksum_rejected():
    addr = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    broken = addr[:-1] + ("b" if addr[-1] != "b" else "c")
    with pytest.raises(ValueError):
        base58.decode_check(broken)
    assert not base58.is_valid_address(broken)


def test_is_valid_address():
    assert base58.is_valid_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert not base58.is_valid_address("not an address")
    assert not base58.is_valid_address("")
