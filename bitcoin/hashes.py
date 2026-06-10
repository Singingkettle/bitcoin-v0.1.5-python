"""Hash() and Hash160() from util.h / base58.h."""

import hashlib

from . import ripemd160 as _ripemd_py


def _ripemd160(data: bytes) -> bytes:
    try:
        return hashlib.new("ripemd160", data).digest()
    except ValueError:  # OpenSSL 3 builds drop ripemd160
        return _ripemd_py.ripemd160(data)


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def hash256(data: bytes) -> bytes:
    """Hash() — double SHA-256."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def hash160(data: bytes) -> bytes:
    """Hash160() — RIPEMD160(SHA256(data))."""
    return _ripemd160(hashlib.sha256(data).digest())


def sha1(data: bytes) -> bytes:
    return hashlib.sha1(data).digest()
