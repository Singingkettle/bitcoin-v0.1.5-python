"""main.cpp's ProcessMessage / SendMessages glue plus whole-node assembly.

Message set is exactly v0.1.5: version, addr, inv, getdata, getblocks,
tx, block, getaddr. The market/IP-pay leftovers (checkorder, submitorder,
reply, review) are not implemented — unknown commands are logged and
ignored, which is also what the original did for unrecognised commands.
"""

import threading

from . import params, util
from .block import CBlock, CBlockLocator
from .blockchain import Blockchain
from .config import Config
from .mempool import MemPool
from .miner import Miner
from .net import CAddress, CInv, CNode, NetEngine
from .serialize import DataStream, uint256_to_hex
from .tx import CTransaction
from .wallet import Wallet

MAX_GETBLOCKS_RESULTS = 500


class Node:
    """One full node: chain + mempool + wallet + miner + networking."""

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

        self.orphan_txs: dict[int, CTransaction] = {}
        self.asked_for_blocks = False
        self.stop_event = threading.Event()
        self.sender_thread: threading.Thread | None = None

        self.chain.block_listeners.append(self._on_new_best_block)
        self.mempool.listeners.append(self._on_pool_tx)

    # ----------------------------------------------------------------- start
    def start(self):
        self.net.start()
        self.sender_thread = threading.Thread(
            target=self._send_messages_loop, name="ThreadMessageHandler",
            daemon=True)
        self.sender_thread.start()
        if self.config.generate:
            self.miner.start()
        self.log.debug("node started, port=%d height=%d",
                       self.config.port, self.chain.best_height)

    def stop(self):
        self.stop_event.set()
        self.miner.stop()
        self.net.stop()
        if self.sender_thread is not None:
            self.sender_thread.join(timeout=2)
        self.wallet.save()
        self.chain.close()

    # ----------------------------------------------------- outgoing handlers
    def _on_new_best_block(self, pindex):
        self.net.relay_inventory(CInv(params.MSG_BLOCK, pindex.hash))

    def _on_pool_tx(self, tx: CTransaction):
        self.net.relay_inventory(CInv(params.MSG_TX, tx.get_hash()))

    def on_peer_connected(self, peer: CNode):
        # the CNode constructor pushes `version` straight away — no verack
        s = DataStream()
        s.write_int32(params.VERSION)
        s.write_uint64(params.NODE_NETWORK)
        s.write_int64(util.get_adjusted_time())
        CAddress("127.0.0.1", self.config.port).serialize(s)
        peer.send_message("version", s.getvalue())

    def push_get_blocks(self, peer: CNode, pindex, hash_stop: int):
        """PushGetBlocks — locator + hashStop."""
        s = DataStream()
        self.chain.get_locator(pindex).serialize(s)
        s.write_uint256(hash_stop)
        peer.send_message("getblocks", s.getvalue())

    # --------------------------------------------------------- ProcessMessage
    def on_message(self, peer: CNode, command: str, payload: bytes):
        self.log.debug("received: %s (%d bytes) from %s",
                       command, len(payload), peer.addr)
        if peer.n_version == 0 and command != "version":
            return  # must hear version first
        s = DataStream(payload, n_type=params.SER_NETWORK,
                       n_version=peer.n_version or params.VERSION)
        handler = getattr(self, f"_msg_{command}", None)
        if handler is None:
            self.log.debug("unknown command %r", command)
            return
        handler(peer, s)

    def _msg_version(self, peer: CNode, s: DataStream):
        if peer.n_version != 0:
            return  # duplicate
        their_version = s.read_int32()
        s.read_uint64()       # nServices
        s.read_int64()        # nTime
        CAddress.deserialize(s)  # addrMe
        if their_version == 0:
            peer.disconnect()
            return
        peer.n_version = min(their_version, params.VERSION)
        self.log.debug("version %d from %s", their_version, peer.addr)
        # ask the first peer for block updates
        if not self.asked_for_blocks:
            self.asked_for_blocks = True
            self.push_get_blocks(peer, self.chain.best_index, 0)

    def _msg_getaddr(self, peer: CNode, s: DataStream):
        out = DataStream()
        others = [n for n in self.net.peers() if n is not peer]
        out.write_compact_size(len(others))
        for n in others:
            n.addr.serialize(out)
        peer.send_message("addr", out.getvalue())

    def _msg_addr(self, peer: CNode, s: DataStream):
        addrs = s.read_vector(lambda: CAddress.deserialize(s))
        self.log.debug("addr: %d entries (peer discovery is manual "
                       "-connect/-addnode on this private net)", len(addrs))

    def _msg_inv(self, peer: CNode, s: DataStream):
        invs = s.read_vector(lambda: CInv.deserialize(s))
        for inv in invs:
            with peer.inv_lock:
                peer.inventory_known.add(inv.key())
            if self._already_have(inv):
                # known orphan: ask this peer to fill the gap
                if (inv.type == params.MSG_BLOCK
                        and inv.hash in self.chain.map_orphan_blocks):
                    root = self.chain.get_orphan_root(
                        self.chain.map_orphan_blocks[inv.hash])
                    self.push_get_blocks(peer, self.chain.best_index, root)
            else:
                with peer.inv_lock:
                    peer.ask_for.append(inv)

    def _already_have(self, inv: CInv) -> bool:
        if inv.type == params.MSG_TX:
            return (inv.hash in self.mempool
                    or inv.hash in self.orphan_txs
                    or self.chain.db.read_tx_index(uint256_to_hex(inv.hash))
                    is not None)
        if inv.type == params.MSG_BLOCK:
            return (inv.hash in self.chain.map_block_index
                    or inv.hash in self.chain.map_orphan_blocks)
        return True

    def _msg_getdata(self, peer: CNode, s: DataStream):
        invs = s.read_vector(lambda: CInv.deserialize(s))
        for inv in invs:
            if inv.type == params.MSG_BLOCK:
                pindex = self.chain.map_block_index.get(inv.hash)
                if pindex is not None:
                    peer.send_message("block",
                                      self.chain.load_block(pindex).serialized())
                # chunked download: when they fetch the batch's last block,
                # advertise our tip to trigger their next getblocks
                if inv.hash == peer.hash_continue:
                    out = DataStream()
                    out.write_compact_size(1)
                    CInv(params.MSG_BLOCK,
                         self.chain.best_index.hash).serialize(out)
                    peer.send_message("inv", out.getvalue())
                    peer.hash_continue = 0
            elif inv.type == params.MSG_TX:
                tx = self.mempool.get(inv.hash)
                if tx is not None:
                    peer.send_message("tx", tx.serialized())

    def _msg_getblocks(self, peer: CNode, s: DataStream):
        locator = CBlockLocator.deserialize(s)
        hash_stop = s.read_uint256()
        with self.chain.lock:
            fork = self.chain.find_fork_by_locator(locator)
            batch = self.chain.main_chain[
                fork.n_height + 1: fork.n_height + 1 + MAX_GETBLOCKS_RESULTS]
        sent = []
        for pindex in batch:
            if pindex.hash == hash_stop:
                break
            sent.append(pindex)
        for pindex in sent:
            peer.push_inventory(CInv(params.MSG_BLOCK, pindex.hash))
        if len(sent) == MAX_GETBLOCKS_RESULTS:
            peer.hash_continue = sent[-1].hash

    def _msg_block(self, peer: CNode, s: DataStream):
        block = CBlock.deserialize(s)
        h = block.get_hash()
        with peer.inv_lock:
            peer.inventory_known.add((params.MSG_BLOCK, h))
        if self.chain.process_block(block):
            if h in self.chain.map_orphan_blocks:
                # stored as orphan: ask for the missing ancestry
                self.push_get_blocks(peer, self.chain.best_index,
                                     self.chain.get_orphan_root(block))
            else:
                self._retry_orphan_txs()

    def _msg_tx(self, peer: CNode, s: DataStream):
        tx = CTransaction.deserialize(s)
        h = tx.get_hash()
        with peer.inv_lock:
            peer.inventory_known.add((params.MSG_TX, h))
        if self.mempool.accept(tx):
            self._retry_orphan_txs()
        elif not self._inputs_known(tx):
            # AddOrphanTx (bounded, the original capped these too)
            if len(self.orphan_txs) < 1000:
                self.orphan_txs[h] = tx

    def _inputs_known(self, tx: CTransaction) -> bool:
        for txin in tx.vin:
            if (txin.prevout.hash not in self.mempool
                    and self.chain.db.read_tx_index(
                        uint256_to_hex(txin.prevout.hash)) is None):
                return False
        return True

    def _retry_orphan_txs(self):
        """Re-offer orphan transactions whose parents may have shown up."""
        for h, tx in list(self.orphan_txs.items()):
            if self._inputs_known(tx):
                del self.orphan_txs[h]
                self.mempool.accept(tx)

    # ----------------------------------------------------------- SendMessages
    def _send_messages_loop(self):
        while not self.stop_event.wait(0.1):
            for peer in self.net.peers():
                if peer.n_version == 0:
                    continue
                with peer.inv_lock:
                    to_send = [i for i in peer.inventory_to_send
                               if i.key() not in peer.inventory_known]
                    peer.inventory_to_send = []
                    for inv in to_send:
                        peer.inventory_known.add(inv.key())
                    asks = peer.ask_for
                    peer.ask_for = []
                if to_send:
                    out = DataStream()
                    out.write_vector(to_send, lambda i: i.serialize(out))
                    peer.send_message("inv", out.getvalue())
                if asks:
                    out = DataStream()
                    out.write_vector(asks, lambda i: i.serialize(out))
                    peer.send_message("getdata", out.getvalue())

    # ----------------------------------------------------------------- status
    def status(self) -> dict:
        return {
            "connections": self.net.connection_count(),
            "blocks": self.chain.best_height,
            "transactions": len(self.wallet.map_wallet),
            "balance": self.wallet.get_balance(),
            "generating": self.miner.running,
        }
