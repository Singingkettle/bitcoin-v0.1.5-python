"""哈希函数——对应原版 util.h 的 Hash() 和 base58.h 的 Hash160()。"""

import hashlib

from . import ripemd160 as _ripemd_py


def ripemd160(data: bytes) -> bytes:
    """RIPEMD-160。优先用 hashlib；很多 OpenSSL 3 的构建已经把它移除了，
    那时就退回到本项目自带的纯 Python 实现。"""
    try:
        return hashlib.new("ripemd160", data).digest()
    except ValueError:
        return _ripemd_py.ripemd160(data)


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def hash256(data: bytes) -> bytes:
    """Hash()：连续做两次 SHA-256。区块哈希、交易哈希、校验和都用它。"""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def hash160(data: bytes) -> bytes:
    """Hash160()：RIPEMD160(SHA256(data))。把 65 字节的公钥压成 20 字节，地址就是由它来的。"""
    return ripemd160(hashlib.sha256(data).digest())


def sha1(data: bytes) -> bytes:
    return hashlib.sha1(data).digest()
