"""序列化：变长整数的边界、各种标量的往返、uint256 的字节序。"""

import pytest

from bitcoin.serialize import (
    DataStream,
    SerializationError,
    ser_compact_size,
    uint256_from_hex,
    uint256_to_hex,
)


@pytest.mark.parametrize("n,encoded", [
    (0, "00"),
    (1, "01"),
    (252, "fc"),                 # 单字节能表示的最大值
    (253, "fdfd00"),             # 第一个需要 0xFD 前缀的值
    (254, "fdfe00"),
    (255, "fdff00"),
    (256, "fd0001"),
    (65535, "fdffff"),
    (65536, "fe00000100"),       # 第一个需要 0xFE 前缀的值
    (0x01FFFFFF, "feffffff01"),
])
def test_compact_size_encoding(n, encoded):
    assert ser_compact_size(n).hex() == encoded
    assert DataStream(bytes.fromhex(encoded)).read_compact_size() == n


def test_compact_size_64bit_form():
    assert ser_compact_size(2**32).hex() == "ff0000000001000000"


def test_compact_size_over_max_size_rejected():
    with pytest.raises(SerializationError):
        DataStream(b"\xff" + (2**40).to_bytes(8, "little")).read_compact_size()
    with pytest.raises(SerializationError):
        DataStream(b"\xfe" + (0x02000001).to_bytes(4, "little")).read_compact_size()
    # 恰好等于上限是允许的
    assert DataStream(b"\xfe" + (0x02000000).to_bytes(4, "little")).read_compact_size() == 0x02000000


@pytest.mark.parametrize("writer,reader,values", [
    ("write_int8", "read_int8", [-128, -1, 0, 127]),
    ("write_uint8", "read_uint8", [0, 255]),
    ("write_int16", "read_int16", [-32768, 32767]),
    ("write_uint16", "read_uint16", [0, 8333, 65535]),
    ("write_int32", "read_int32", [-2**31, -7, 0, 2**31 - 1]),
    ("write_uint32", "read_uint32", [0, 0xDEADBEEF, 2**32 - 1]),
    ("write_int64", "read_int64", [-2**63, -(2**40), 0, 2**63 - 1]),
    ("write_uint64", "read_uint64", [0, 2**63, 2**64 - 1]),
])
def test_scalar_roundtrip(writer, reader, values):
    for v in values:
        s = DataStream()
        getattr(s, writer)(v)
        r = DataStream(s.getvalue())
        assert getattr(r, reader)() == v
        assert r.remaining() == 0


def test_integers_are_little_endian():
    s = DataStream()
    s.write_uint32(0x12345678)
    assert s.getvalue().hex() == "78563412"
    s = DataStream()
    s.write_int32(-1)
    assert s.getvalue().hex() == "ffffffff"


def test_string_and_vector_roundtrip():
    s = DataStream()
    s.write_string(b"hello")
    s.write_string(b"")
    s.write_string(b"x" * 300)               # 长度需要 3 字节的变长整数
    s.write_vector([1, 2, 3], s.write_uint32)
    s.write_vector([], s.write_uint32)
    r = DataStream(s.getvalue())
    assert r.read_string() == b"hello"
    assert r.read_string() == b""
    assert r.read_string() == b"x" * 300
    assert r.read_vector(r.read_uint32) == [1, 2, 3]
    assert r.read_vector(r.read_uint32) == []
    assert r.remaining() == 0


def test_uint256_display_hex_is_byte_reversed():
    h = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
    n = uint256_from_hex(h)
    assert uint256_to_hex(n) == h
    s = DataStream()
    s.write_uint256(n)
    assert s.getvalue() == bytes.fromhex(h)[::-1]       # 字节流里是反过来的
    assert DataStream(s.getvalue()).read_uint256() == n


def test_uint256_zero_and_max():
    for n in (0, 1, 2**256 - 1):
        s = DataStream()
        s.write_uint256(n)
        assert len(s.getvalue()) == 32
        assert DataStream(s.getvalue()).read_uint256() == n
        assert uint256_from_hex(uint256_to_hex(n)) == n


def test_read_past_end_raises():
    with pytest.raises(SerializationError):
        DataStream(b"\x01").read(2)
    with pytest.raises(SerializationError):
        DataStream(b"").read_uint32()
    with pytest.raises(SerializationError):
        DataStream(b"\x05abc").read_string()            # 声称 5 字节，实际只有 3 字节


def test_cursor_advances_and_remaining():
    r = DataStream(b"\x01\x02\x03\x04\x05")
    assert r.read(2) == b"\x01\x02"
    assert r.remaining() == 3
    assert r.read(3) == b"\x03\x04\x05"
    assert r.remaining() == 0
