"""节点——把区块链、内存池、钱包、矿工、网络装配在一起，
并实现原版 main.cpp 里的 ProcessMessage（处理收到的消息）和 SendMessages（定期发送）。

消息种类与 v0.1.5 完全一致：version、addr、inv、getdata、getblocks、tx、block、getaddr。
原版里为"IP 直接付款"和"市场"功能预留的 checkorder / submitorder / reply / review
没有实现；收到不认识的命令只记一条日志然后忽略——原版对未知命令也是这么处理的
（"Ignore unknown commands for extensibility"）。

一次典型的区块同步：
    B -> A : getblocks(我的链长这样，请告诉我后面还有什么)
    A -> B : inv(区块哈希 × N)
    B -> A : getdata(这些我没有，请发给我)
    A -> B : block × N
"""

import heapq
import threading

from . import params, util
from .block import CBlock, CBlockLocator
from .blockchain import Blockchain
from .config import Config
from .mempool import MemPool
from .miner import Miner
from .net import CAddress, CInv, CNode, NetEngine
from .serialize import DataStream
from .tx import CTransaction
from .wallet import Wallet

MAX_ORPHAN_TXS = 1000       # 【安全性改动】原版对孤儿交易的数量不设上限


class Node:
    """一个完整的节点：链 + 内存池 + 钱包 + 矿工 + 网络。"""

    def __init__(self, config: Config):
        self.config = config
        datadir = config.datadir
        self.log = util.setup_logging(datadir, name=f"bitcoin[{config.port}]")
        self.chain = Blockchain(datadir, self.log)
        self.mempool = MemPool(self.chain)
        self.wallet = Wallet(datadir, self.chain, self.mempool, self.log)
        self.miner = Miner(self.chain, self.mempool, self.wallet, self.log)
        self.net = NetEngine(config.port, config.peers(), handler=self,
                             listen=config.listen, logger=self.log)

        self.orphan_txs: dict[int, CTransaction] = {}       # mapOrphanTransactions
        self.map_already_asked_for: dict = {}               # mapAlreadyAskedFor
        self.asked_for_blocks = False                       # fAskedForBlocks
        self.stop_event = threading.Event()
        self.sender_thread: threading.Thread | None = None

        self.chain.block_listeners.append(self._on_new_best_block)
        self.mempool.listeners.append(self._on_pool_tx)
        self.wallet.relay_listeners.append(self._on_pool_tx)

    # ---------------------------------------------------------------- 启停
    def start(self):
        # 重启后内存池是空的：先把钱包里还没进块的交易放回去
        self.wallet.reaccept_wallet_transactions()
        self.net.start()
        self.sender_thread = threading.Thread(
            target=self._send_messages_loop, name="ThreadMessageHandler",
            daemon=True)
        self.sender_thread.start()
        if self.config.generate:
            self.miner.start()
        self.log.debug("节点启动：端口=%d 当前高度=%d",
                       self.config.port, self.chain.best_height)

    def stop(self):
        self.stop_event.set()
        self.miner.stop()
        self.net.stop()
        if self.sender_thread is not None:
            self.sender_thread.join(timeout=2)
        self.wallet.close()
        self.chain.close()

    # ------------------------------------------------------ 对外发送的入口
    def _on_new_best_block(self, pindex):
        """出现了新的最佳区块：向所有邻居通告，并顺便重播钱包里还没进块的交易。"""
        self.net.relay_inventory(CInv(params.MSG_BLOCK, pindex.hash))
        self.wallet.relay_wallet_transactions()

    def _on_pool_tx(self, tx: CTransaction):
        self.net.relay_inventory(CInv(params.MSG_TX, tx.get_hash()))

    def on_peer_connected(self, peer: CNode):
        """原版在 CNode 的构造函数里就立刻发出 version，不等对方先开口，也没有 verack。
        载荷：协议版本、我提供的服务、我的时间、**对方的地址**（对方借此知道自己的外网 IP）。"""
        s = DataStream()
        s.write_int32(params.VERSION)
        s.write_uint64(params.NODE_NETWORK)
        s.write_int64(util.get_adjusted_time())
        peer.addr.serialize(s)
        peer.send_message("version", s.getvalue())

    def push_get_blocks(self, peer: CNode, pindex, hash_stop: int):
        """发送 getblocks：区块定位器 + hashStop（0 表示"有多少给多少"）。"""
        s = DataStream()
        self.chain.get_locator(pindex).serialize(s)
        s.write_uint256(hash_stop)
        peer.send_message("getblocks", s.getvalue())

    # -------------------------------------------------------- ProcessMessage
    def on_message(self, peer: CNode, command: str, payload: bytes):
        self.log.debug("received: %-12s (%d 字节) 来自 %s",
                       command, len(payload), peer.addr)
        handler = getattr(self, f"_msg_{command}", None)
        s = DataStream(payload, n_type=params.SER_NETWORK,
                       n_version=peer.n_version or params.VERSION)
        # 和原版一样，整个消息处理过程都在全局大锁 cs_main 的保护下进行
        with self.chain.lock:
            if command == "version":
                self._msg_version(peer, s)
            elif peer.n_version == 0:
                return                  # 必须先收到 version，其他消息一概不理
            elif handler is None:
                self.log.debug("忽略未知消息 %r", command)
            else:
                handler(peer, s)

    def _msg_version(self, peer: CNode, s: DataStream):
        if peer.n_version != 0:
            return                      # version 只能发一次
        their_version = s.read_int32()
        s.read_uint64()                 # nServices
        s.read_int64()                  # nTime
        CAddress.deserialize(s)         # addrMe：对方眼里我的地址
        if their_version == 0:
            return
        peer.n_version = min(their_version, params.VERSION)
        self.log.debug("对端 %s 的协议版本是 %d", peer.addr, their_version)
        # 只向**第一个**连上的节点索要区块；之后的同步靠新区块的 inv 驱动
        if not self.asked_for_blocks:
            self.asked_for_blocks = True
            self.push_get_blocks(peer, self.chain.best_index, 0)

    def _msg_getaddr(self, peer: CNode, s: DataStream):
        """私网里节点靠命令行参数手动互连，这里只是礼貌性地回复已连接的其他节点。"""
        others = [n for n in self.net.peers() if n is not peer]
        out = DataStream()
        out.write_vector(others, lambda n: n.addr.serialize(out))
        peer.send_message("addr", out.getvalue())

    def _msg_addr(self, peer: CNode, s: DataStream):
        addrs = s.read_vector(lambda: CAddress.deserialize(s))
        self.log.debug("addr: 收到 %d 个地址（私网不做自动节点发现，仅记录）", len(addrs))

    def _msg_inv(self, peer: CNode, s: DataStream):
        for inv in s.read_vector(lambda: CInv.deserialize(s)):
            peer.add_inventory_known(inv)
            if not self._already_have(inv):
                peer.ask_for(inv, self.map_already_asked_for)
            elif (inv.type == params.MSG_BLOCK
                  and inv.hash in self.chain.map_orphan_blocks):
                # 这是个我们已经暂存的孤块：请对方把它前面缺的那一段补上
                self.push_get_blocks(
                    peer, self.chain.best_index,
                    self.chain.get_orphan_root(self.chain.map_orphan_blocks[inv.hash]))

    def _already_have(self, inv: CInv) -> bool:
        """AlreadyHave。"""
        if inv.type == params.MSG_TX:
            return inv.hash in self.mempool or self.chain.contains_tx(inv.hash)
        if inv.type == params.MSG_BLOCK:
            return (inv.hash in self.chain.map_block_index
                    or inv.hash in self.chain.map_orphan_blocks)
        return True         # 不认识的类型：就当已经有了，不去要

    def _msg_getdata(self, peer: CNode, s: DataStream):
        for inv in s.read_vector(lambda: CInv.deserialize(s)):
            if inv.type == params.MSG_BLOCK:
                pindex = self.chain.map_block_index.get(inv.hash)
                if pindex is not None:
                    peer.send_message("block", self.chain.load_block(pindex).serialized())
            elif inv.type == params.MSG_TX:
                tx = self.mempool.get(inv.hash)     # 原版从 mapRelay（转发缓存）里取
                if tx is not None:
                    peer.send_message("tx", tx.serialized())

    def _msg_getblocks(self, peer: CNode, s: DataStream):
        locator = CBlockLocator.deserialize(s)
        hash_stop = s.read_uint256()
        # 在我的主链上找到对方也有的最近一个块，然后把它之后的块全部通告给对方
        # （0.1.5 没有"每次最多 500 个"的限制，那是后来才加的）
        pindex = self.chain.find_fork_by_locator(locator).pnext
        while pindex is not None:
            if pindex.hash == hash_stop:
                break
            inv = CInv(params.MSG_BLOCK, pindex.hash)
            # 绕过"对方已知"的过滤：万一之前那条 inv 在路上丢了，对方再来问就得再说一遍
            with peer.inv_lock:
                if inv.key() not in peer.inventory_known2:
                    peer.inventory_known2.add(inv.key())
                    peer.inventory_known.discard(inv.key())
                    peer.inventory_to_send.append(inv)
            pindex = pindex.pnext

    def _msg_block(self, peer: CNode, s: DataStream):
        block = CBlock.deserialize(s)
        inv = CInv(params.MSG_BLOCK, block.get_hash())
        peer.add_inventory_known(inv)
        if self.chain.process_block(block):
            self.map_already_asked_for.pop(inv.key(), None)
            if inv.hash in self.chain.map_orphan_blocks:
                # 被当作孤块暂存了：请这个对端把中间缺的区块补上
                self.push_get_blocks(peer, self.chain.best_index,
                                     self.chain.get_orphan_root(block))
            else:
                self._retry_orphan_txs()

    def _msg_tx(self, peer: CNode, s: DataStream):
        tx = CTransaction.deserialize(s)
        inv = CInv(params.MSG_TX, tx.get_hash())
        peer.add_inventory_known(inv)
        if self.mempool.accept(tx):         # 转发由内存池的监听器 _on_pool_tx 完成
            self.map_already_asked_for.pop(inv.key(), None)
            self._retry_orphan_txs()
        elif not self._inputs_known(tx) and len(self.orphan_txs) < MAX_ORPHAN_TXS:
            self.log.debug("暂存孤儿交易（它引用的交易我们还没见过）")
            self.orphan_txs[inv.hash] = tx

    def _inputs_known(self, tx: CTransaction) -> bool:
        return all(txin.prevout.hash in self.mempool
                   or self.chain.contains_tx(txin.prevout.hash)
                   for txin in tx.vin)

    def _retry_orphan_txs(self):
        """有新交易/新区块进来后，看看哪些孤儿交易现在能被接受了。"""
        progress = True
        while progress:
            progress = False
            for h, tx in list(self.orphan_txs.items()):
                if self._inputs_known(tx):
                    del self.orphan_txs[h]
                    if self.mempool.accept(tx):
                        progress = True

    # ---------------------------------------------------------- SendMessages
    def _send_messages_loop(self):
        """原版的 ThreadMessageHandler 每 100 毫秒对每个对端调用一次 SendMessages。"""
        while not self.stop_event.wait(0.1):
            for peer in self.net.peers():
                self.send_messages(peer)

    def send_messages(self, peer: CNode):
        """SendMessages：把攒着的库存通告（inv）和到时间的索取请求（getdata）发给这个对端。"""
        if peer.n_version == 0:
            return                      # 握手完成之前什么都不发
        now = util.get_time() * 1_000_000
        with peer.inv_lock:
            # inv：把攒着的通告一次发出去，并记为"对方已知"
            to_send = []
            for inv in peer.inventory_to_send:
                if inv.key() not in peer.inventory_known:
                    peer.inventory_known.add(inv.key())
                    to_send.append(inv)
            peer.inventory_to_send = []
            peer.inventory_known2.clear()
            # getdata：取出所有"到时间了"的索取请求
            due = []
            while peer.map_ask_for and peer.map_ask_for[0][0] <= now:
                due.append(heapq.heappop(peer.map_ask_for)[2])

        if to_send:
            out = DataStream()
            out.write_vector(to_send, lambda i: i.serialize(out))
            peer.send_message("inv", out.getvalue())
        if due:
            with self.chain.lock:       # 排队期间可能已经从别处拿到了，发之前再确认一次
                asks = [inv for inv in due if not self._already_have(inv)]
            if asks:
                out = DataStream()
                out.write_vector(asks, lambda i: i.serialize(out))
                peer.send_message("getdata", out.getvalue())

    # ---------------------------------------------------------------- 状态
    def status(self) -> dict:
        return {
            "connections": self.net.connection_count(),
            "blocks": self.chain.best_height,
            "transactions": len(self.wallet.map_wallet),
            "balance": self.wallet.get_balance(),
            "generating": self.miner.running,
        }
