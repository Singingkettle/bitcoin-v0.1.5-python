import pytest

from bitcoin.serialize import (
    DataStream,
    SerializationError,
    ser_compact_size,
    uint256_from_hex,
    uint256_to_hex,
)


@pytest.mark.parametrize(
    "n,encoded",
    [
        (0, "00"),
        (252, "fc"),
        (253, "fdfd00"),
        (65535, "fdffff"),
        (65536, "fe00000100"),
        (0x01FFFFFF, "feffffff01"),
    ],
)
def test_compact_size_encoding(n, encoded):
    assert ser_compact_size(n).hex() == encoded
    s = DataStream(bytes.fromhex(encoded))
    assert s.read_compact_size() == n


def test_compact_size_too_large():
    s = DataStream(b"\xff" + (2**40).to_bytes(8, "little"))
    with pytest.raises(SerializationError):
        s.read_compact_size()


def test_scalar_roundtrip():
    s = DataStream()
    s.write_int32(-7)
    s.write_uint32(0xDEADBEEF)
    s.write_int64(-(2**40))
    s.write_uint64(2**63)
    s.write_uint16(8333)
    r = DataStream(s.getvalue())
    assert r.read_int32() == -7
    assert r.read_uint32() == 0xDEADBEEF
    assert r.read_int64() == -(2**40)
    assert r.read_uint64() == 2**63
    assert r.read_uint16() == 8333
    assert r.remaining() == 0


def test_string_and_vector_roundtrip():
    s = DataStream()
    s.write_string(b"hello")
    s.write_vector([1, 2, 3], s.write_uint32)
    r = DataStream(s.getvalue())
    assert r.read_string() == b"hello"
    assert r.read_vector(r.read_uint32) == [1, 2, 3]


def test_uint256_hex_is_byte_reversed():
    h = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
    n = uint256_from_hex(h)
    assert uint256_to_hex(n) == h
    s = DataStream()
    s.write_uint256(n)
    # on the wire it is little-endian, i.e. the reverse of the display hex
    assert s.getvalue() == bytes.fromhex(h)[::-1]
    assert DataStream(s.getvalue()).read_uint256() == n


def test_read_past_end():
    with pytest.raises(SerializationError):
        DataStream(b"\x01").read(2)
