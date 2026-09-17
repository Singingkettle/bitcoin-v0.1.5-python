"""节点的消息处理（ProcessMessage / SendMessages）。
不走真实网络：直接把消息喂给 Node.on_message，再检查它往"对端"的发送队列里放了什么。"""

import shutil
import socket

import pytest

from bitcoin import params
from bitcoin.block import CBlock, CBlockLocator
from bitcoin.config import Config
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.net import CAddress, CInv, CNode, parse_messages
from bitcoin.node import Node
from bitcoin.serialize import DataStream, SerializationError
from bitcoin.tx import CTransaction
from tests.conftest import SEED_HEIGHT, coinbase_of, free_port, make_spend, mine_block

COIN = params.COIN


@pytest.fixture
def rig(seed_chain_dir, tmp_path):
    """一个建立在种子链上的、不联网的节点，外加造假对端的工具。"""
    import json
    datadir = tmp_path / "node"
    shutil.copytree(seed_chain_dir, datadir)
    node = Node(Config([f"-datadir={datadir}", f"-port={free_port()}", "-nolisten"]))
    keys = {int(h): CKey.from_secret(bytes.fromhex(s))
            for h, s in json.loads((datadir / "keys.json").read_text()).items()}
    socks = []

    def new_peer(handshake=True, port=5000) -> CNode:
        a, b = socket.socketpair()
        socks.extend([a, b])
        peer = CNode(a, CAddress("127.0.0.1", port + len(socks)), f_inbound=True)
        node.net.nodes.append(peer)
        if handshake:
            node.on_message(peer, "version", version_payload())
            drain(peer)
        return peer

    yield node, keys, new_peer
    node.wallet.close()
    node.chain.close()
    for s in socks:
        s.close()


def version_payload(version=105) -> bytes:
    s = DataStream()
    s.write_int32(version)
    s.write_uint64(1)
    s.write_int64(1234567890)
    CAddress("10.0.0.1", 8333).serialize(s)
    return s.getvalue()


def drain(peer: CNode) -> list[tuple[str, bytes]]:
    """取出节点发给这个对端的所有消息。"""
    buf = bytearray()
    while not peer.send_queue.empty():
        item = peer.send_queue.get_nowait()
        if item is not None:
            buf += item
    return list(parse_messages(buf))


def inv_payload(*invs) -> bytes:
    s = DataStream()
    s.write_vector(list(invs), lambda i: i.serialize(s))
    return s.getvalue()


def read_invs(payload: bytes) -> list[CInv]:
    s = DataStream(payload)
    return s.read_vector(lambda: CInv.deserialize(s))


def getblocks_payload(node, pindex, hash_stop=0) -> bytes:
    s = DataStream()
    node.chain.get_locator(pindex).serialize(s)
    s.write_uint256(hash_stop)
    return s.getvalue()


def parse_getblocks(payload: bytes):
    s = DataStream(payload)
    return CBlockLocator.deserialize(s), s.read_uint256()


# -------------------------------------------------------------------- 握手
def test_version_message_we_send(rig):
    node, _, new_peer = rig
    peer = new_peer(handshake=False)
    node.on_peer_connected(peer)
    (command, payload), = drain(peer)
    assert command == "version"
    s = DataStream(payload)
    assert s.read_int32() == 105
    assert s.read_uint64() == params.NODE_NETWORK
    assert abs(s.read_int64() - __import__("time").time()) < 5
    addr = CAddress.deserialize(s)
    # 原版 version 里带的是**对方**的地址（PushMessage("version", ..., addr)），不是自己的
    assert (addr.ip, addr.port) == (peer.addr.ip, peer.addr.port)
    assert s.remaining() == 0 and len(payload) == 46


def test_first_peer_is_asked_for_blocks_no_verack(rig):
    node, _, new_peer = rig
    first = new_peer(handshake=False)
    node.on_message(first, "version", version_payload())
    msgs = drain(first)
    assert [c for c, _ in msgs] == ["getblocks"]                    # 没有 verack 这种东西
    locator, hash_stop = parse_getblocks(msgs[0][1])
    assert locator.v_have[0] == node.chain.best_index.hash and hash_stop == 0
    assert locator.v_have[-1] == node.chain.genesis_index.hash
    assert first.n_version == 105

    second = new_peer(handshake=False)
    node.on_message(second, "version", version_payload())
    assert drain(second) == []                                      # 只向第一个对端要区块


def test_version_negotiation_and_duplicates(rig):
    node, _, new_peer = rig
    newer = new_peer(handshake=False)
    node.on_message(newer, "version", version_payload(version=31900))
    assert newer.n_version == 105                                   # 取双方较小的
    older = new_peer(handshake=False)
    node.on_message(older, "version", version_payload(version=100))
    assert older.n_version == 100
    node.on_message(older, "version", version_payload(version=105))
    assert older.n_version == 100                                   # version 只认第一次
    zero = new_peer(handshake=False)
    node.on_message(zero, "version", version_payload(version=0))
    assert zero.n_version == 0


def test_messages_before_version_are_ignored(rig):
    node, _, new_peer = rig
    peer = new_peer(handshake=False)
    node.on_message(peer, "inv", inv_payload(CInv(params.MSG_BLOCK, 12345)))
    node.on_message(peer, "getblocks", getblocks_payload(node, node.chain.genesis_index))
    assert not peer.map_ask_for and not peer.inventory_to_send and drain(peer) == []


def test_unknown_command_is_ignored(rig):
    node, _, new_peer = rig
    peer = new_peer()
    for command in ("checkorder", "review", "verack", "ping", "whatever"):
        node.on_message(peer, command, b"\x01\x02\x03")
    assert drain(peer) == []


def test_malformed_payload_raises_and_changes_nothing(rig):
    """截断的消息会抛异常——在真实运行中由网络层捕获并记日志，节点状态不受影响。"""
    node, _, new_peer = rig
    peer = new_peer()
    height = node.chain.best_height
    for command in ("block", "tx", "inv", "getblocks", "getdata"):
        with pytest.raises(SerializationError):
            node.on_message(peer, command, b"\x05\x00")
    assert node.chain.best_height == height and len(node.mempool) == 0


# ------------------------------------------------------------ inv / getdata
def test_inv_for_unknown_items_schedules_getdata(rig):
    node, _, new_peer = rig
    peer = new_peer()
    unknown_block = CInv(params.MSG_BLOCK, 0xABCDEF)
    unknown_tx = CInv(params.MSG_TX, 0x123456)
    known_block = CInv(params.MSG_BLOCK, node.chain.best_index.hash)
    known_tx = CInv(params.MSG_TX, coinbase_of(node.chain, 5).get_hash())
    node.on_message(peer, "inv", inv_payload(unknown_block, known_block, unknown_tx, known_tx))
    assert {e[2].key() for e in peer.map_ask_for} == {unknown_block.key(), unknown_tx.key()}
    assert {unknown_block.key(), known_block.key()} <= peer.inventory_known

    node.send_messages(peer)
    (command, payload), = drain(peer)
    assert command == "getdata"
    assert [i.key() for i in read_invs(payload)] == [unknown_block.key(), unknown_tx.key()]
    node.send_messages(peer)
    assert drain(peer) == []                                        # 不会重复要


def test_same_item_announced_by_two_peers_is_requested_once(rig):
    node, _, new_peer = rig
    p1, p2 = new_peer(), new_peer()
    inv = CInv(params.MSG_BLOCK, 0xABCDEF)
    node.on_message(p1, "inv", inv_payload(inv))
    node.on_message(p2, "inv", inv_payload(inv))
    node.send_messages(p1)
    node.send_messages(p2)
    assert [c for c, _ in drain(p1)] == ["getdata"]
    assert drain(p2) == []                                          # 第二个对端的请求排到了 2 分钟后
    assert len(p2.map_ask_for) == 1


def test_getdata_serves_blocks_and_mempool_txs(rig):
    node, keys, new_peer = rig
    peer = new_peer()
    spend = make_spend(coinbase_of(node.chain, 1), 0, keys[1], 50 * COIN)
    assert node.mempool.accept(spend)
    pindex = node.chain.main_chain[7]
    node.on_message(peer, "getdata", inv_payload(
        CInv(params.MSG_BLOCK, pindex.hash), CInv(params.MSG_TX, spend.get_hash()),
        CInv(params.MSG_BLOCK, 0x999), CInv(params.MSG_TX, 0x888)))   # 后两个我们没有：不回应
    msgs = drain(peer)
    assert [c for c, _ in msgs] == ["block", "tx"]
    assert CBlock.deserialize(DataStream(msgs[0][1])).get_hash() == pindex.hash
    assert CTransaction.deserialize(DataStream(msgs[1][1])).get_hash() == spend.get_hash()


# ----------------------------------------------------------------- getblocks
def test_getblocks_from_genesis_announces_the_whole_chain(rig):
    """0.1.5 的 getblocks **没有**"每次最多 500 个"的限制，一次把后面的全部通告出去。"""
    node, _, new_peer = rig
    peer = new_peer()
    node.on_message(peer, "getblocks", getblocks_payload(node, node.chain.genesis_index))
    node.send_messages(peer)
    (command, payload), = drain(peer)
    assert command == "inv"
    assert [i.hash for i in read_invs(payload)] == [p.hash for p in node.chain.main_chain[1:]]


def test_getblocks_stops_before_hash_stop(rig):
    node, _, new_peer = rig
    peer = new_peer()
    chain = node.chain.main_chain
    node.on_message(peer, "getblocks", getblocks_payload(node, chain[100], chain[105].hash))
    node.send_messages(peer)
    hashes = [i.hash for i in read_invs(drain(peer)[0][1])]
    assert hashes == [p.hash for p in chain[101:105]]               # 不含 hashStop 自己


def test_getblocks_when_peer_is_already_up_to_date(rig):
    node, _, new_peer = rig
    peer = new_peer()
    node.on_message(peer, "getblocks", getblocks_payload(node, node.chain.best_index))
    node.send_messages(peer)
    assert drain(peer) == []


def test_getblocks_with_unknown_locator_falls_back_to_genesis(rig):
    node, _, new_peer = rig
    peer = new_peer()
    s = DataStream()
    CBlockLocator([0x111, 0x222]).serialize(s)
    s.write_uint256(0)
    node.on_message(peer, "getblocks", s.getvalue())
    node.send_messages(peer)
    assert len(read_invs(drain(peer)[0][1])) == SEED_HEIGHT


def test_repeated_getblocks_bypasses_the_known_filter(rig):
    """万一上一条 inv 在路上丢了，对方重新 getblocks 时必须再通告一遍。"""
    node, _, new_peer = rig
    peer = new_peer()
    payload = getblocks_payload(node, node.chain.main_chain[SEED_HEIGHT - 3])
    for _ in range(2):
        node.on_message(peer, "getblocks", payload)
        node.send_messages(peer)
        assert len(read_invs(drain(peer)[0][1])) == 3


# --------------------------------------------------------------- block / tx
def test_new_block_is_accepted_and_relayed_to_others_but_not_back(rig):
    node, _, new_peer = rig
    sender, other = new_peer(), new_peer()
    block = mine_block(node.chain)
    node.on_message(sender, "block", block.serialized())
    assert node.chain.best_index.hash == block.get_hash()
    node.send_messages(sender)
    node.send_messages(other)
    assert drain(sender) == []                                      # 发送者自己当然已经有了
    (command, payload), = drain(other)
    assert command == "inv" and [i.hash for i in read_invs(payload)] == [block.get_hash()]


def test_orphan_block_triggers_getblocks_with_orphan_root_as_stop(rig, tmp_path):
    """收到一个父块未知的区块：暂存为孤块，并向对方发 getblocks，
    hashStop 必须是**最老的那个孤块自己的哈希**——这样对方才会把缺的父块也发过来。"""
    node, _, new_peer = rig
    peer = new_peer()
    missing_parent = mine_block(node.chain)
    from bitcoin.blockchain import Blockchain
    scratch_dir = tmp_path / "scratch"
    shutil.copytree(node.config.datadir, scratch_dir, ignore=shutil.ignore_patterns("debug.log"))
    scratch = Blockchain(str(scratch_dir))
    assert scratch.process_block(missing_parent)
    child = mine_block(scratch)
    assert scratch.process_block(child)
    grandchild = mine_block(scratch)
    scratch.close()

    node.on_message(peer, "block", grandchild.serialized())
    (command, payload), = drain(peer)
    assert command == "getblocks"
    locator, hash_stop = parse_getblocks(payload)
    assert hash_stop == grandchild.get_hash()
    assert locator.v_have[0] == node.chain.best_index.hash

    node.on_message(peer, "block", child.serialized())              # 又来一个更老的孤块
    _, hash_stop = parse_getblocks(drain(peer)[0][1])
    assert hash_stop == child.get_hash()                            # 孤块链的根变成了 child

    # 对方再次通告我们已经暂存的孤块：同样触发补链请求
    node.on_message(peer, "inv", inv_payload(CInv(params.MSG_BLOCK, grandchild.get_hash())))
    assert parse_getblocks(drain(peer)[0][1])[1] == child.get_hash()

    node.on_message(peer, "block", missing_parent.serialized())     # 缺的那块到了
    assert node.chain.best_index.hash == grandchild.get_hash()
    assert not node.chain.map_orphan_blocks


def test_serving_side_of_orphan_backfill(rig):
    """从"被请求方"的角度验证上面那个流程：对方链尖在 100，手里有孤块 105，
    我们应当通告 101..104（恰好是它缺的那一段）。"""
    node, _, new_peer = rig
    peer = new_peer()
    chain = node.chain.main_chain
    node.on_message(peer, "getblocks", getblocks_payload(node, chain[100], chain[105].hash))
    node.send_messages(peer)
    assert [i.hash for i in read_invs(drain(peer)[0][1])] == [p.hash for p in chain[101:105]]


def test_tx_is_accepted_and_relayed(rig):
    node, keys, new_peer = rig
    sender, other = new_peer(), new_peer()
    spend = make_spend(coinbase_of(node.chain, 1), 0, keys[1], 50 * COIN)
    node.on_message(sender, "tx", spend.serialized())
    assert spend.get_hash() in node.mempool
    node.send_messages(sender)
    node.send_messages(other)
    assert drain(sender) == []
    assert [i.key() for i in read_invs(drain(other)[0][1])] == [(params.MSG_TX, spend.get_hash())]


def test_invalid_tx_is_neither_pooled_nor_relayed(rig):
    node, keys, new_peer = rig
    sender, other = new_peer(), new_peer()
    spend = make_spend(coinbase_of(node.chain, 1), 0, keys[1], 50 * COIN)
    spend.vout[0].n_value -= 1                                      # 签名失效
    node.on_message(sender, "tx", spend.serialized())
    assert len(node.mempool) == 0 and not node.orphan_txs
    node.send_messages(other)
    assert drain(other) == []


def test_orphan_transactions_are_accepted_once_parents_arrive(rig):
    node, keys, new_peer = rig
    peer = new_peer()
    k1, k2 = CKey.generate(), CKey.generate()
    parent = make_spend(coinbase_of(node.chain, 1), 0, keys[1], 50 * COIN,
                        to_h160=hash160(k1.get_pubkey()))
    child = make_spend(parent, 0, k1, 50 * COIN, to_h160=hash160(k2.get_pubkey()))
    grandchild = make_spend(child, 0, k2, 50 * COIN)
    node.on_message(peer, "tx", grandchild.serialized())
    node.on_message(peer, "tx", child.serialized())
    assert len(node.mempool) == 0 and len(node.orphan_txs) == 2
    node.on_message(peer, "tx", parent.serialized())
    assert len(node.mempool) == 3 and not node.orphan_txs


def test_getaddr_replies_with_other_peers(rig):
    node, _, new_peer = rig
    asker, other = new_peer(), new_peer()
    node.on_message(asker, "getaddr", b"")
    (command, payload), = drain(asker)
    assert command == "addr"
    s = DataStream(payload)
    addrs = s.read_vector(lambda: CAddress.deserialize(s))
    assert [(a.ip, a.port) for a in addrs] == [(other.addr.ip, other.addr.port)]
    node.on_message(asker, "addr", payload)                         # addr 消息能正常解析
