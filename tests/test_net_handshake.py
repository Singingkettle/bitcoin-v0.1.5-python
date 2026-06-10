"""Wire format golden tests: the 20-byte checksum-less header, version
payload layout, CAddress encoding, and the resync scanner."""

from bitcoin import params
from bitcoin.net import CAddress, CInv, pack_message, parse_messages
from bitcoin.serialize import DataStream


def test_header_layout():
    msg = pack_message("version", b"\xab\xcd")
    # magic
    assert msg[:4] == b"\xf9\xbe\xb4\xd9"
    # 12-byte NUL-padded command
    assert msg[4:16] == b"version\x00\x00\x00\x00\x00"
    # uint32 LE length, then payload immediately — NO checksum field
    assert msg[16:20] == (2).to_bytes(4, "little")
    assert msg[20:] == b"\xab\xcd"
    assert len(msg) == params.MESSAGE_HEADER_SIZE + 2


def test_parse_roundtrip_and_resync():
    buf = bytearray()
    buf += b"\x00garbage\xf9junk"          # noise with a fake magic byte
    buf += pack_message("inv", b"\x01" * 5)
    buf += pack_message("tx", b"")
    half = pack_message("block", b"\x02" * 100)
    buf += half[:30]                        # incomplete tail
    got = list(parse_messages(buf))
    assert got == [("inv", b"\x01" * 5), ("tx", b"")]
    # the partial message stays buffered and completes later
    buf += half[30:]
    assert list(parse_messages(buf)) == [("block", b"\x02" * 100)]


def test_caddress_encoding():
    addr = CAddress("127.0.0.1", 18444)
    s = DataStream()
    addr.serialize(s)
    raw = s.getvalue()
    assert len(raw) == 8 + 12 + 4 + 2       # services + reserved + ip + port
    assert raw[:8] == (1).to_bytes(8, "little")          # NODE_NETWORK
    assert raw[8:20] == b"\x00" * 10 + b"\xff\xff"       # IPv4-mapped prefix
    assert raw[20:24] == bytes([127, 0, 0, 1])
    assert raw[24:26] == (18444).to_bytes(2, "big")      # network byte order
    again = CAddress.deserialize(DataStream(raw))
    assert (again.ip, again.port, again.services) == ("127.0.0.1", 18444, 1)


def test_cinv_roundtrip():
    inv = CInv(params.MSG_BLOCK, 0xDEADBEEF)
    s = DataStream()
    inv.serialize(s)
    assert len(s.getvalue()) == 36
    again = CInv.deserialize(DataStream(s.getvalue()))
    assert again.key() == inv.key()


def test_version_payload_layout():
    # the version payload a node sends: int32, uint64, int64, CAddress
    s = DataStream()
    s.write_int32(params.VERSION)
    s.write_uint64(params.NODE_NETWORK)
    s.write_int64(1234567890)
    CAddress("127.0.0.1", 18444).serialize(s)
    raw = s.getvalue()
    assert raw[:4] == (105).to_bytes(4, "little")        # protocol version 105
    assert len(raw) == 4 + 8 + 8 + 26
