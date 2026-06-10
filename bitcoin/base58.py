"""base58.h — Base58 / Base58Check and bitcoin addresses (version byte 0)."""

from .hashes import hash160, hash256

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ADDRESS_VERSION = 0


def encode(data: bytes) -> str:
    """EncodeBase58."""
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, rem = divmod(n, 58)
        out = ALPHABET[rem] + out
    # leading zero bytes become leading '1's
    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def decode(s: str) -> bytes:
    """DecodeBase58 — raises ValueError on a bad character."""
    n = 0
    for ch in s:
        n = n * 58 + ALPHABET.index(ch)
    out = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + out


def encode_check(data: bytes) -> str:
    """EncodeBase58Check — appends 4-byte double-SHA256 checksum."""
    return encode(data + hash256(data)[:4])


def decode_check(s: str) -> bytes:
    """DecodeBase58Check — verifies and strips the checksum."""
    data = decode(s)
    if len(data) < 4:
        raise ValueError("base58 string too short")
    payload, checksum = data[:-4], data[-4:]
    if hash256(payload)[:4] != checksum:
        raise ValueError("bad base58 checksum")
    return payload


def hash160_to_address(h160: bytes) -> str:
    """Hash160ToAddress — version byte 0 + hash160."""
    return encode_check(bytes([ADDRESS_VERSION]) + h160)


def pubkey_to_address(pubkey: bytes) -> str:
    """PubKeyToAddress."""
    return hash160_to_address(hash160(pubkey))


def address_to_hash160(addr: str) -> bytes:
    """AddressToHash160 — raises ValueError if invalid."""
    payload = decode_check(addr)
    if len(payload) != 21 or payload[0] != ADDRESS_VERSION:
        raise ValueError("bad address version or length")
    return payload[1:]


def is_valid_address(addr: str) -> bool:
    try:
        address_to_hash160(addr)
        return True
    except ValueError:
        return False
