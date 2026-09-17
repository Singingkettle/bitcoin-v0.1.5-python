"""网络底层：20 字节无校验和的消息头、CAddress / CInv 的字节布局、分帧与重同步、AskFor 调度。"""

import socket

import pytest

from bitcoin import params, util
from bitcoin.net import CAddress, CInv, CNode, pack_message, parse_messages
from bitcoin.serialize import DataStream


# ------------------------------------------------------------------ 消息头
def test_header_layout_has_no_checksum():
    msg = pack_message("version", b"\xab\xcd")
    assert msg[:4] == b"\xf9\xbe\xb4\xd9"                           # 魔数
    assert msg[4:16] == b"version\x00\x00\x00\x00\x00"              # 12 字节命令，不足补 0
    assert msg[16:20] == (2).to_bytes(4, "little")                  # 载荷长度
    assert msg[20:] == b"\xab\xcd"                                  # 紧接着就是载荷：没有校验和字段
    assert len(msg) == params.MESSAGE_HEADER_SIZE + 2 == 22


def test_empty_payload_and_max_length_command():
    assert len(pack_message("getaddr", b"")) == 20
    assert pack_message("abcdefghijkl", b"")[4:16] == b"abcdefghijkl"   # 恰好 12 字节，不补 0
    with pytest.raises(AssertionError):
        pack_message("abcdefghijklm", b"")


def test_magic_bytes_are_all_high_ascii():
    """中本聪在 net.h 的注释里写道：魔数选用了不常用的高位字符，它们不是合法的 UTF-8，
    在任何字节对齐方式下看都是一个很大的 4 字节整数——尽量不和正常数据撞车。"""
    assert all(b >= 0x80 for b in params.MESSAGE_START)
    with pytest.raises(UnicodeDecodeError):
        params.MESSAGE_START.decode("utf-8")


# -------------------------------------------------------------------- 分帧
def test_parse_several_messages_in_one_buffer():
    buf = bytearray(pack_message("inv", b"\x01" * 5) + pack_message("tx", b"")
                    + pack_message("block", b"\x02" * 100))
    assert list(parse_messages(buf)) == [("inv", b"\x01" * 5), ("tx", b""), ("block", b"\x02" * 100)]
    assert buf == b""


@pytest.mark.parametrize("cut", [1, 3, 4, 10, 19, 20, 21, 60])
def test_message_split_at_any_point_is_reassembled(cut):
    """TCP 可能在任何位置把数据切开。"""
    whole = pack_message("block", bytes(range(100)))
    buf = bytearray(whole[:cut])
    assert list(parse_messages(buf)) == []
    buf += whole[cut:]
    assert list(parse_messages(buf)) == [("block", bytes(range(100)))]


def test_byte_by_byte_delivery():
    whole = pack_message("inv", b"abc") + pack_message("tx", b"defgh")
    buf, got = bytearray(), []
    for byte in whole:
        buf.append(byte)
        got += list(parse_messages(buf))
    assert got == [("inv", b"abc"), ("tx", b"defgh")]


def test_resync_after_garbage():
    buf = bytearray(b"\x00garbage\xf9\xbejunk" + pack_message("inv", b"xyz") + b"\x11\x22"
                    + pack_message("tx", b""))
    assert list(parse_messages(buf)) == [("inv", b"xyz"), ("tx", b"")]


def test_pure_garbage_is_discarded_but_a_split_magic_is_kept():
    buf = bytearray(b"A" * 1000)
    assert list(parse_messages(buf)) == []
    assert len(buf) <= 3
    buf = bytearray(b"junk" + params.MESSAGE_START[:2])             # 魔数刚好被切成两半
    assert list(parse_messages(buf)) == []
    buf += params.MESSAGE_START[2:] + pack_message("tx", b"ok")[4:]
    assert list(parse_messages(buf)) == [("tx", b"ok")]


def test_magic_bytes_inside_payload_do_not_confuse_the_parser():
    payload = b"xx" + params.MESSAGE_START + b"yy" + pack_message("fake", b"zz")
    buf = bytearray(pack_message("block", payload) + pack_message("tx", b""))
    assert list(parse_messages(buf)) == [("block", payload), ("tx", b"")]


def test_header_with_absurd_length_is_skipped():
    poisoned = params.MESSAGE_START + b"block".ljust(12, b"\x00") + (0xFFFFFFFF).to_bytes(4, "little")
    buf = bytearray(poisoned + pack_message("tx", b"ok"))
    assert list(parse_messages(buf)) == [("tx", b"ok")]


@pytest.mark.parametrize("command", [b"ver\x00sion\x00\x00\x00\x00", b"bad\x01cmd\x00\x00\x00\x00\x00",
                                     b"\xff" * 12])
def test_header_with_invalid_command_is_skipped(command):
    """CMessageHeader::IsValid：命令名必须是可见 ASCII，且第一个 0 之后只能是 0。"""
    bad = params.MESSAGE_START + command + (0).to_bytes(4, "little")
    buf = bytearray(bad + pack_message("tx", b"ok"))
    assert list(parse_messages(buf)) == [("tx", b"ok")]


# ------------------------------------------------------------ CAddress / CInv
def test_caddress_wire_layout():
    s = DataStream()
    CAddress("192.168.1.2", 18444).serialize(s)
    raw = s.getvalue()
    assert len(raw) == 26
    assert raw[:8] == (1).to_bytes(8, "little")                     # 服务标志 NODE_NETWORK
    assert raw[8:20] == b"\x00" * 10 + b"\xff\xff"                  # IPv4 映射到 IPv6 的固定前缀
    assert raw[20:24] == bytes([192, 168, 1, 2])                    # IP：网络字节序
    assert raw[24:26] == (18444).to_bytes(2, "big")                 # 端口：网络字节序（大端）
    again = CAddress.deserialize(DataStream(raw))
    assert (again.ip, again.port, again.services) == ("192.168.1.2", 18444, 1)


def test_cinv_wire_layout():
    s = DataStream()
    CInv(params.MSG_BLOCK, 0xDEADBEEF).serialize(s)
    raw = s.getvalue()
    assert len(raw) == 36
    assert raw[:4] == (2).to_bytes(4, "little")
    assert CInv.deserialize(DataStream(raw)).key() == (params.MSG_BLOCK, 0xDEADBEEF)
    assert CInv(params.MSG_TX, 5).key() != CInv(params.MSG_BLOCK, 5).key()


# ---------------------------------------------------------------- CNode
@pytest.fixture
def peer():
    a, b = socket.socketpair()
    node = CNode(a, CAddress("127.0.0.1", 1), f_inbound=False)
    yield node
    a.close()
    b.close()


def test_push_inventory_skips_what_the_peer_already_knows(peer):
    known, fresh = CInv(params.MSG_TX, 1), CInv(params.MSG_TX, 2)
    peer.add_inventory_known(known)
    peer.push_inventory(known)
    peer.push_inventory(fresh)
    assert [i.key() for i in peer.inventory_to_send] == [fresh.key()]


def test_send_message_never_blocks_and_queues_in_order(peer):
    peer.send_message("inv", b"1")
    peer.send_message("tx", b"2")
    assert peer.send_queue.get_nowait() == pack_message("inv", b"1")
    assert peer.send_queue.get_nowait() == pack_message("tx", b"2")
    peer.disconnect()
    peer.send_message("tx", b"3")                                   # 断开后发送被静默丢弃
    assert peer.send_queue.get_nowait() is None                     # 只有那个"让发送线程退出"的哨兵


def test_ask_for_first_request_is_immediate_repeat_is_delayed_two_minutes(peer):
    already_asked = {}
    inv = CInv(params.MSG_BLOCK, 99)
    now = util.get_time() * 1_000_000
    peer.ask_for(inv, already_asked)
    first_time = peer.map_ask_for[0][0]
    assert first_time <= now                                        # 第一次：马上就可以发

    other = CNode(peer.socket, CAddress("127.0.0.1", 2), f_inbound=True)
    other.ask_for(inv, already_asked)                               # 另一个对端也通告了同一个东西
    assert other.map_ask_for[0][0] == first_time + 2 * 60 * 1_000_000
    other.ask_for(inv, already_asked)
    assert sorted(t for t, _, _ in other.map_ask_for)[1] == first_time + 4 * 60 * 1_000_000


def test_ask_for_queue_is_ordered_by_time(peer):
    already_asked = {(params.MSG_TX, 1): (util.get_time() + 1000) * 1_000_000}
    peer.ask_for(CInv(params.MSG_TX, 1), already_asked)             # 这个被推迟了
    peer.ask_for(CInv(params.MSG_TX, 2), already_asked)
    assert peer.map_ask_for[0][2].hash == 2
