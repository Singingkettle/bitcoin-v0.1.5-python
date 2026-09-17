"""Base58 / Base58Check / 地址。"""

import pytest

from bitcoin import base58

SATOSHI_PUBKEY = bytes.fromhex(
    "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
    "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f")
SATOSHI_ADDRESS = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"      # 主网创世块的收款地址


def test_satoshi_genesis_address():
    assert base58.pubkey_to_address(SATOSHI_PUBKEY) == SATOSHI_ADDRESS
    assert base58.address_to_hash160(SATOSHI_ADDRESS).hex() == (
        "62e907b15cbf27d5425399ebf6f0fb50ebb88f18")


def test_hal_finney_address_from_first_payment():
    """第一笔转账的收款公钥 -> Hal Finney 那个著名的地址。"""
    pubkey = bytes.fromhex(
        "04ae1a62fe09c5f51b13905f07f06b99a2f7159b2225f374cd378d71302fa28414"
        "e7aab37397f554a7df5f142c21c1b7303b8a0626f1baded5c72a704f7e6cd84c")
    assert base58.pubkey_to_address(pubkey) == "1Q2TWHE3GMdB6BZKafqwxXtWAWgFt5Jvm3"


def test_alphabet_has_no_confusable_characters():
    assert len(base58.ALPHABET) == 58
    for ch in "0OIl":
        assert ch not in base58.ALPHABET


@pytest.mark.parametrize("raw,encoded", [
    (b"", ""),
    (b"\x00", "1"),
    (b"\x00\x00\x01", "112"),
    (b"\x61", "2g"),
    (b"\x62\x62\x62", "a3gV"),
    (b"\x63\x63\x63", "aPEr"),
    (bytes.fromhex("73696d706c792061206c6f6e6720737472696e67"), "2cFupjhnEsSn59qHXstmK2ffpLv2"),
    (bytes.fromhex("00eb15231dfceb60925886b67d065299925915aeb172c06647"),
     "1NS17iag9jJgTHD1VXjvLCEnZuQ3rJDE9L"),
    (bytes.fromhex("572e4794"), "3EFU7m"),
    (bytes.fromhex("10c8511e"), "Rt5zm"),
    (bytes.fromhex("00000000000000000000"), "1111111111"),
])
def test_base58_known_vectors(raw, encoded):
    """这组向量取自 Bitcoin Core 自带的 base58 测试数据。"""
    assert base58.encode(raw) == encoded
    assert base58.decode(encoded) == raw


def test_decode_tolerates_surrounding_whitespace():
    assert base58.decode("  112 \n") == b"\x00\x00\x01"
    assert base58.is_valid_address("  " + SATOSHI_ADDRESS + "\n")


def test_decode_rejects_illegal_characters():
    for bad in ("0", "O", "I", "l", "abc!def"):
        with pytest.raises(ValueError):
            base58.decode(bad)


def test_check_roundtrip():
    for payload in (b"", b"\x00", bytes(range(21)), b"\xff" * 40):
        assert base58.decode_check(base58.encode_check(payload)) == payload


def test_every_single_character_typo_is_detected():
    """校验和的意义：把地址里任意一个字符改掉，都应该被识别为无效。"""
    for i in range(len(SATOSHI_ADDRESS)):
        for replacement in "23":
            if SATOSHI_ADDRESS[i] == replacement:
                continue
            typo = SATOSHI_ADDRESS[:i] + replacement + SATOSHI_ADDRESS[i + 1:]
            assert not base58.is_valid_address(typo), typo


def test_wrong_version_or_length_is_invalid():
    h160 = bytes(20)
    assert base58.is_valid_address(base58.encode_check(b"\x00" + h160))
    assert not base58.is_valid_address(base58.encode_check(b"\x05" + h160))   # 版本字节不对
    assert not base58.is_valid_address(base58.encode_check(b"\x00" + h160[:19]))
    assert not base58.is_valid_address(base58.encode_check(b"\x00" + h160 + b"\x00"))
    assert not base58.is_valid_address("")
    assert not base58.is_valid_address("not an address")


def test_addresses_start_with_1():
    from bitcoin.key import CKey
    for _ in range(5):
        assert base58.pubkey_to_address(CKey.generate().get_pubkey()).startswith("1")
