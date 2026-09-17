"""Base58 / Base58Check 编码与比特币地址——对应原版 base58.h。

为什么不用常见的 Base64？中本聪在原版注释里解释过：
- 去掉了 0（零）、O（大写 o）、I（大写 i）、l（小写 L）这几个长得太像的字符，
  免得人工抄写地址时看错；
- 只含字母和数字，双击鼠标就能选中整个地址；
- 邮件里不会在符号处被折行。
"""

from .hashes import hash160, hash256

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ADDRESS_VERSION = 0     # 地址的版本字节。0 对应主网地址，所以地址都以字符 '1' 开头


def encode(data: bytes) -> str:
    """EncodeBase58：把字节串当成一个大端的大整数，反复除以 58 取余数。"""
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, rem = divmod(n, 58)
        out = ALPHABET[rem] + out
    # 大整数会"吃掉"开头的 0 字节，所以每个前导 0 字节要补一个 '1'
    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def decode(s: str) -> bytes:
    """DecodeBase58：encode 的逆运算。遇到非法字符抛 ValueError。"""
    s = s.strip()           # 原版同样会跳过首尾空白
    n = 0
    for ch in s:
        n = n * 58 + ALPHABET.index(ch)     # index 找不到字符时会抛 ValueError
    out = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + out


def encode_check(data: bytes) -> str:
    """EncodeBase58Check：在末尾追加 4 字节校验和（对内容做两次 SHA-256 取前 4 字节）。
    这样抄错一个字符几乎必然被发现，而不会把钱打到一个不存在的地址。"""
    return encode(data + hash256(data)[:4])


def decode_check(s: str) -> bytes:
    """DecodeBase58Check：校验并去掉末尾的 4 字节校验和。"""
    data = decode(s)
    if len(data) < 4:
        raise ValueError("Base58 字符串太短")
    payload, checksum = data[:-4], data[-4:]
    if hash256(payload)[:4] != checksum:
        raise ValueError("Base58 校验和错误")
    return payload


def hash160_to_address(h160: bytes) -> str:
    """Hash160ToAddress：地址 = Base58Check(版本字节 0 + 20 字节的公钥哈希)。"""
    return encode_check(bytes([ADDRESS_VERSION]) + h160)


def pubkey_to_address(pubkey: bytes) -> str:
    """PubKeyToAddress：公钥 -> Hash160 -> 地址。"""
    return hash160_to_address(hash160(pubkey))


def address_to_hash160(addr: str) -> bytes:
    """AddressToHash160：地址 -> 20 字节公钥哈希；地址非法则抛 ValueError。"""
    payload = decode_check(addr)
    if len(payload) != 21 or payload[0] != ADDRESS_VERSION:
        raise ValueError("地址版本或长度错误")
    return payload[1:]


def is_valid_address(addr: str) -> bool:
    try:
        address_to_hash160(addr)
        return True
    except ValueError:
        return False
