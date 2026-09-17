"""序列化——对应原版 serialize.h 的 CDataStream、变长整数、各种标量的读写。

两条贯穿全项目的约定：

1. 所有整数在字节流里都是**小端序**（低位字节在前）。
2. 256 位的哈希值在程序里用 Python 的 int 表示；写进字节流时是 32 字节小端，
   而人类习惯看的十六进制字符串是**大端**（对应原版的 GetHex()）。
   所以"显示出来的哈希"和"字节流里的哈希"刚好是字节反转的关系——这是新手最大的坑。
"""

import struct

from .params import MAX_SIZE


class SerializationError(Exception):
    """字节不够读、长度超限等反序列化错误。"""


def ser_compact_size(n: int) -> bytes:
    """变长整数（CompactSize）：小数字省空间，大数字也装得下。

    n < 253          -> 1 字节
    n <= 0xFFFF      -> 0xFD + 2 字节
    n <= 0xFFFFFFFF  -> 0xFE + 4 字节
    更大             -> 0xFF + 8 字节
    """
    if n < 253:
        return struct.pack("<B", n)
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def uint256_from_bytes(b: bytes) -> int:
    return int.from_bytes(b, "little")


def uint256_to_bytes(n: int) -> bytes:
    return n.to_bytes(32, "little")


def uint256_from_hex(s: str) -> int:
    """大端十六进制字符串 -> int（先把字节反转回小端再解释）。"""
    return int.from_bytes(bytes.fromhex(s)[::-1], "little")


def uint256_to_hex(n: int) -> str:
    """int -> 人类阅读用的大端十六进制字符串（对应 GetHex()）。"""
    return n.to_bytes(32, "little")[::-1].hex()


class DataStream:
    """CDataStream：一段字节缓冲区，写总是追加到末尾，读有一个向前移动的游标。

    n_type / n_version 跟着流走，因为个别对象（如 CAddress）在网络模式和
    磁盘模式下的序列化结果不一样。
    """

    def __init__(self, data: bytes = b"", n_type: int = 0, n_version: int = 0):
        self.buf = bytearray(data)
        self.cursor = 0
        self.n_type = n_type
        self.n_version = n_version

    # ------------------------------------------------------------------ 写
    def write(self, data: bytes) -> "DataStream":
        self.buf += data
        return self

    def write_int8(self, n):    return self.write(struct.pack("<b", n))
    def write_uint8(self, n):   return self.write(struct.pack("<B", n))
    def write_int16(self, n):   return self.write(struct.pack("<h", n))
    def write_uint16(self, n):  return self.write(struct.pack("<H", n))
    def write_int32(self, n):   return self.write(struct.pack("<i", n))
    def write_uint32(self, n):  return self.write(struct.pack("<I", n))
    def write_int64(self, n):   return self.write(struct.pack("<q", n))
    def write_uint64(self, n):  return self.write(struct.pack("<Q", n))

    def write_compact_size(self, n):
        return self.write(ser_compact_size(n))

    def write_string(self, s: bytes):
        """字节串 / vector<unsigned char>：先写变长长度，再写内容。"""
        self.write_compact_size(len(s))
        return self.write(s)

    def write_uint256(self, n: int):
        return self.write(uint256_to_bytes(n))

    def write_vector(self, v, write_elem):
        """数组：先写元素个数（变长整数），再逐个写元素。"""
        self.write_compact_size(len(v))
        for elem in v:
            write_elem(elem)
        return self

    # ------------------------------------------------------------------ 读
    def read(self, n: int) -> bytes:
        if n < 0 or self.cursor + n > len(self.buf):
            raise SerializationError("读取超出缓冲区末尾")
        data = bytes(self.buf[self.cursor:self.cursor + n])
        self.cursor += n
        return data

    def read_int8(self):    return struct.unpack("<b", self.read(1))[0]
    def read_uint8(self):   return struct.unpack("<B", self.read(1))[0]
    def read_int16(self):   return struct.unpack("<h", self.read(2))[0]
    def read_uint16(self):  return struct.unpack("<H", self.read(2))[0]
    def read_int32(self):   return struct.unpack("<i", self.read(4))[0]
    def read_uint32(self):  return struct.unpack("<I", self.read(4))[0]
    def read_int64(self):   return struct.unpack("<q", self.read(8))[0]
    def read_uint64(self):  return struct.unpack("<Q", self.read(8))[0]

    def read_compact_size(self) -> int:
        first = self.read_uint8()
        if first < 253:
            n = first
        elif first == 253:
            n = self.read_uint16()
        elif first == 254:
            n = self.read_uint32()
        else:
            n = self.read_uint64()
        if n > MAX_SIZE:
            raise SerializationError("ReadCompactSize() : 长度超过上限")
        return n

    def read_string(self) -> bytes:
        return self.read(self.read_compact_size())

    def read_uint256(self) -> int:
        return uint256_from_bytes(self.read(32))

    def read_vector(self, read_elem) -> list:
        return [read_elem() for _ in range(self.read_compact_size())]

    # ---------------------------------------------------------------- 其他
    def remaining(self) -> int:
        return len(self.buf) - self.cursor

    def getvalue(self) -> bytes:
        return bytes(self.buf)

    def __len__(self):
        return len(self.buf)
