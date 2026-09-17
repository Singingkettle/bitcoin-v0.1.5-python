"""集成测试：多个**真实节点**通过本机的真实 TCP socket 互相通信。"""

import socket
import time

from bitcoin import params
from bitcoin.net import pack_message
from bitcoin.serialize import uint256_from_hex
from tests.conftest import mine_block, wait_until

COIN = params.COIN


def mine_to_wallet(node, n: int):
    """用测试辅助函数替 node 挖 n 个块，奖励付给 node 自己的钱包。"""
    for _ in range(n):
        key = node.wallet.generate_new_key()
        assert node.chain.process_block(mine_block(node.chain, key=key))


def connect_arg(node) -> str:
    return f"-connect=127.0.0.1:{node.test_port}"


def synced(*nodes):
    tips = {n.chain.best_index.hash for n in nodes}
    return len(tips) == 1


def test_initial_sync_payment_and_remote_confirmation(make_node):
    """最完整的一条主线：A 先挖矿 -> B 上线同步 -> A 付款给 B -> 交易广播到 B
    -> B 挖出确认区块 -> 区块传回 A。"""
    node_a = make_node("A")
    node_b = make_node("B", connect_arg(node_a))
    mine_to_wallet(node_a, 125)
    assert node_a.wallet.get_balance() == 6 * 50 * COIN             # 高度 1..6 的 coinbase 已满 120 确认

    node_a.start()
    node_b.start()
    wait_until(lambda: node_b.chain.best_height == 125, what="B 同步完 125 个区块")
    assert synced(node_a, node_b)
    wait_until(lambda: node_a.net.connection_count() == 1 and node_b.net.connection_count() == 1)

    ok, txid = node_a.wallet.send_money(node_b.wallet.get_default_address(), 10 * COIN)
    assert ok, txid
    wait_until(lambda: len(node_b.mempool) == 1, what="交易传播到 B 的内存池")
    assert node_b.wallet.get_balance() == 10 * COIN                 # 零确认即可见

    block = node_b.miner.mine_one_block()                           # B 挖出确认区块
    assert len(block.vtx) == 2
    wait_until(lambda: node_a.chain.best_height == 126, what="B 挖的区块传回 A")
    for node in (node_a, node_b):
        assert node.chain.get_tx_depth(uint256_from_hex(txid)) == 1
        assert len(node.mempool) == 0
    assert node_a.wallet.get_balance() == 7 * 50 * COIN - 10 * COIN


def test_three_nodes_in_a_line_relay_blocks_and_transactions(make_node):
    """A — B — C：A 和 C 没有直接连接，一切都要经过 B 中转。"""
    node_a = make_node("A")
    node_b = make_node("B", connect_arg(node_a))
    node_c = make_node("C", connect_arg(node_b))
    mine_to_wallet(node_a, 125)
    for node in (node_a, node_b, node_c):
        node.start()
    wait_until(lambda: node_c.chain.best_height == 125, timeout=60, what="C 经由 B 同步")

    mine_to_wallet(node_a, 1)                                       # A 新出一个块
    wait_until(lambda: synced(node_a, node_b, node_c), what="新区块 A -> B -> C")

    ok, txid = node_a.wallet.send_money(node_c.wallet.get_default_address(), 5 * COIN)
    assert ok, txid
    wait_until(lambda: len(node_c.mempool) == 1, what="交易 A -> B -> C")
    assert node_c.wallet.get_balance() == 5 * COIN

    node_c.miner.mine_one_block()                                   # C 挖矿，区块要传回 A
    wait_until(lambda: synced(node_a, node_b, node_c) and node_a.chain.best_height == 127,
               what="区块 C -> B -> A")
    assert all(len(n.mempool) == 0 for n in (node_a, node_b, node_c))


def test_orphan_backfill_over_the_network(make_node):
    """B 只听说了第 5 个新区块（前 4 个的通告它没收到）：它应当把该块当作孤块暂存，
    然后用 getblocks 向 A 要回缺失的那一段，最后 5 个块全部接上。
    （修复前的 get_orphan_root 返回了错误的哈希，B 会永远卡在这里。）"""
    node_a = make_node("A")
    node_b = make_node("B", connect_arg(node_a))
    node_a.start()
    node_b.start()
    wait_until(lambda: node_a.net.connection_count() == 1 and
               all(p.n_version for p in node_a.net.peers()), what="握手完成")

    listener = node_a.chain.block_listeners.pop()                   # A 暂时"闭嘴"，不通告新区块
    mine_to_wallet(node_a, 4)
    node_a.chain.block_listeners.append(listener)
    time.sleep(0.3)
    assert node_b.chain.best_height == 0

    mine_to_wallet(node_a, 1)                                       # 第 5 个块正常通告
    wait_until(lambda: node_b.chain.best_height == 5, what="B 通过孤块补链追上来")
    assert synced(node_a, node_b) and not node_b.chain.map_orphan_blocks


def test_longer_chain_wins_when_two_isolated_miners_meet(make_node):
    """A、B 各自离线挖矿（A 挖 2 个、B 挖 3 个），然后连上：双方都应当收敛到 B 那条更长的链，
    A 白挖的那 2 个块的奖励作废。这就是"最长链原则"。"""
    node_a = make_node("A")
    node_b = make_node("B", connect_arg(node_a))
    mine_to_wallet(node_a, 2)
    mine_to_wallet(node_b, 3)
    a_coinbases = list(node_a.wallet.map_wallet.values())
    assert all(node_a.wallet.get_depth(w) > 0 for w in a_coinbases)

    node_a.start()
    node_b.start()
    wait_until(lambda: synced(node_a, node_b), what="两条链收敛")
    assert node_a.chain.best_height == 3
    assert node_a.chain.best_index.hash == node_b.chain.best_index.hash
    assert all(node_a.wallet.get_depth(w) == 0 for w in a_coinbases)    # A 的挖矿所得"未被接受"
    assert len(node_a.chain.map_block_index) == 1 + 2 + 3               # 支链上的块仍保留在索引里
    wait_until(lambda: len(node_b.chain.map_block_index) == 6, what="B 也收到了 A 的两个支链块")


def test_transaction_sent_while_offline_is_rebroadcast_later(make_node):
    """A 发送交易时一个邻居都没有；B 之后才连上来。
    等 A 这边出现新区块时，钱包会把还没确认的交易重新广播一遍。"""
    node_a = make_node("A")
    mine_to_wallet(node_a, 125)
    node_a.start()
    node_b = make_node("B", connect_arg(node_a))
    ok, txid = node_a.wallet.send_money(node_b.wallet.get_default_address(), 7 * COIN)
    assert ok, txid                                                 # 此刻 A 没有任何连接
    node_b.start()
    wait_until(lambda: node_b.chain.best_height == 125, what="B 同步")
    time.sleep(0.5)
    assert len(node_b.mempool) == 0                                 # 0.1.5 不会主动同步内存池

    # 原版的重播有 10 分钟的节流（RelayWalletTransactions 里的 static nLastTime）：
    # A 刚才挖矿时已经触发过一次计时，所以紧接着的新区块**不会**引发重播……
    assert node_a.chain.process_block(mine_block(node_a.chain))
    wait_until(lambda: node_b.chain.best_height == 126)
    time.sleep(0.5)
    assert len(node_b.mempool) == 0
    # ……"10 分钟后"的下一个新区块才会
    node_a.wallet._last_relay_time -= 11 * 60
    assert node_a.chain.process_block(mine_block(node_a.chain))
    wait_until(lambda: len(node_b.mempool) == 1, what="交易被重新广播到 B")
    assert node_b.wallet.get_balance() == 7 * COIN


def test_restart_preserves_chain_wallet_and_pending_transactions(make_node, tmp_path):
    node_a = make_node("A")
    mine_to_wallet(node_a, 125)
    port = node_a.test_port
    addr = node_a.wallet.get_default_address()
    ok, txid = node_a.wallet.send_money("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", 3 * COIN)
    assert ok
    balance = node_a.wallet.get_balance()
    node_a.start()
    node_a.stop()

    reborn = make_node("A2", port=port, datadir=tmp_path / "A")     # 同一个数据目录
    assert reborn.chain.best_height == 125
    assert reborn.wallet.get_default_address() == addr
    assert reborn.wallet.get_balance() == balance
    assert len(reborn.mempool) == 0
    reborn.start()                                                  # 启动时把未确认的钱包交易放回内存池
    assert uint256_from_hex(txid) in reborn.mempool
    reborn.miner.mine_one_block()
    assert reborn.chain.get_tx_depth(uint256_from_hex(txid)) == 1


def test_late_joiner_and_reconnect_after_restart(make_node, tmp_path):
    node_a = make_node("A")
    mine_to_wallet(node_a, 10)
    node_a.start()
    node_b = make_node("B", connect_arg(node_a))
    node_b.start()
    wait_until(lambda: node_b.chain.best_height == 10)
    node_b.stop()
    wait_until(lambda: node_a.net.connection_count() == 0, what="A 发现 B 掉线")

    mine_to_wallet(node_a, 7)                                       # B 不在线期间 A 又挖了 7 个
    node_b2 = make_node("B2", connect_arg(node_a), datadir=tmp_path / "B")
    assert node_b2.chain.best_height == 10                          # 从磁盘恢复
    node_b2.start()
    wait_until(lambda: node_b2.chain.best_height == 17, what="B 重启后补齐")
    assert synced(node_a, node_b2)


def test_node_survives_garbage_and_hostile_peers(make_node):
    node_a = make_node("A")
    mine_to_wallet(node_a, 3)
    node_a.start()

    hostile = socket.create_connection(("127.0.0.1", node_a.test_port), timeout=5)
    hostile.sendall(b"GET / HTTP/1.1\r\n\r\n" + bytes(range(256)) * 20)      # 纯垃圾
    hostile.sendall(pack_message("block", b"\x00" * 10))                        # version 之前就发消息
    hostile.sendall(pack_message("version", b"\x01"))                           # 截断的 version
    hostile.sendall(pack_message("inv", b"\xff" * 9))                           # 荒谬的数组长度
    hostile.sendall(params.MESSAGE_START + b"tx".ljust(12, b"\x00") + b"\xff\xff\xff\xff")
    time.sleep(0.5)

    node_b = make_node("B", connect_arg(node_a))                    # 正常节点照常能同步
    node_b.start()
    wait_until(lambda: node_b.chain.best_height == 3, what="A 在被骚扰的同时仍正常服务 B")
    hostile.close()
    assert node_a.chain.best_height == 3


def test_background_miner_thread_with_a_follower(make_node):
    """-gen 启动后台挖矿线程；另一个节点实时跟随。"""
    node_a = make_node("A", "-gen")
    node_b = make_node("B", connect_arg(node_a))
    node_a.start()
    node_b.start()
    assert node_a.miner.running
    wait_until(lambda: node_a.chain.best_height >= 15, timeout=90, what="A 后台挖出 15 个块")
    node_a.miner.stop()
    wait_until(lambda: synced(node_a, node_b), what="B 追上 A")
    assert node_b.chain.best_height == node_a.chain.best_height >= 15
    assert node_a.status()["generating"] is False
