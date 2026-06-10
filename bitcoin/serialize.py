"""serialize.h — CDataStream, compact sizes, scalar/vector serialization.

Every scalar is little-endian. uint256 values are held as Python ints and
serialized as 32 little-endian bytes; their conventional hex display is
big-endian (matching GetHex()).
"""

import struct

from .params import MAX_SIZE


class SerializationError(Exception):
    pass


def ser_compact_size(n: int) -> bytes:
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
    """GetHex() is big-endian — reverse into the little-endian int."""
    return int.from_bytes(bytes.fromhex(s)[::-1], "little")


def uint256_to_hex(n: int) -> str:
    return n.to_bytes(32, "little")[::-1].hex()


class DataStream:
    """CDataStream — a byte buffer with a read cursor.

    nType/nVersion ride along because CAddress and CBlockLocator serialize
    differently for network vs disk and by protocol version.
    """

    def __init__(self, data: bytes = b"", n_type: int = 0, n_version: int = 0):
        self.buf = bytearray(data)
        self.cursor = 0
        self.n_type = n_type
        self.n_version = n_version

    # --- writing ---
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
        """vector<unsigned char> / string: compact size + raw bytes."""
        self.write_compact_size(len(s))
        return self.write(s)

    def write_uint256(self, n: int):
        return self.write(uint256_to_bytes(n))

    def write_vector(self, v, write_elem):
        self.write_compact_size(len(v))
        for elem in v:
            write_elem(elem)
        return self

    # --- reading ---
    def read(self, n: int) -> bytes:
        if self.cursor + n > len(self.buf):
            raise SerializationError("read past end of buffer")
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
            raise SerializationError("ReadCompactSize() : size too large")
        return n

    def read_string(self) -> bytes:
        return self.read(self.read_compact_size())

    def read_uint256(self) -> int:
        return uint256_from_bytes(self.read(32))

    def read_vector(self, read_elem) -> list:
        return [read_elem() for _ in range(self.read_compact_size())]

    # --- misc ---
    def remaining(self) -> int:
        return len(self.buf) - self.cursor

    def getvalue(self) -> bytes:
        return bytes(self.buf)

    def __len__(self):
        return len(self.buf)
