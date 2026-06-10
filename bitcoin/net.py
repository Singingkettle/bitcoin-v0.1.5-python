"""net.h / net.cpp — message framing, CAddress, CInv, CNode and the
socket threads.

The v0.1.5 wire format, faithfully:

* 20-byte message header: 4 magic + 12 command (NUL padded) + 4 length.
  NO checksum — that field arrived in 0.2.x.
* no verack, no ping; a connection is "up" once `version` is exchanged.
* receivers scan forward to the next magic to resynchronise, exactly like
  CNode::vRecv handling in ProcessMessages().

Thread inventory mirrors the original: a listener (accept), a receive
thread per connection filling vRecv, and one ThreadMessageHandler in
node.py draining all peers.
"""

import socket
import struct
import threading

from . import params, util
from .serialize import DataStream


class CAddress:
    """CAddress network serialization: services + 12-byte IPv4-mapped
    prefix + ip + port (the latter two in network byte order)."""

    RESERVED = b"\x00" * 10 + b"\xff\xff"

    def __init__(self, ip: str = "0.0.0.0", port: int = 0,
                 services: int = params.NODE_NETWORK):
        self.ip = ip
        self.port = port
        self.services = services

    def serialize(self, s: DataStream):
        s.write_uint64(self.services)
        s.write(self.RESERVED)
        s.write(socket.inet_aton(self.ip))
        s.write(struct.pack(">H", self.port))

    @classmethod
    def deserialize(cls, s: DataStream) -> "CAddress":
        services = s.read_uint64()
        s.read(12)  # pchReserved
        ip = socket.inet_ntoa(s.read(4))
        (port,) = struct.unpack(">H", s.read(2))
        return cls(ip, port, services)

    def __repr__(self):
        return f"{self.ip}:{self.port}"


class CInv:
    def __init__(self, type_: int, hash_: int):
        self.type = type_
        self.hash = hash_

    def serialize(self, s: DataStream):
        s.write_int32(self.type)
        s.write_uint256(self.hash)

    @classmethod
    def deserialize(cls, s: DataStream) -> "CInv":
        return cls(s.read_int32(), s.read_uint256())

    def key(self):
        return (self.type, self.hash)

    def __repr__(self):
        kind = {params.MSG_TX: "tx", params.MSG_BLOCK: "block"}.get(self.type, "?")
        return f"inv({kind} {self.hash:064x})"


def pack_message(command: str, payload: bytes) -> bytes:
    """CMessageHeader — magic + command + length. No checksum in 0.1.x."""
    cmd = command.encode("ascii")
    assert len(cmd) <= params.COMMAND_SIZE
    return (params.MESSAGE_START
            + cmd.ljust(params.COMMAND_SIZE, b"\x00")
            + struct.pack("<I", len(payload))
            + payload)


def parse_messages(buf: bytearray):
    """Yield (command, payload) for each complete message in buf, consuming
    them; scans forward to the next magic like the original's resync."""
    while True:
        start = bytes(buf).find(params.MESSAGE_START)
        if start < 0:
            # keep a partial-magic tail
            del buf[:max(0, len(buf) - 3)]
            return
        if start > 0:
            del buf[:start]
        if len(buf) < params.MESSAGE_HEADER_SIZE:
            return
        command = buf[4:4 + params.COMMAND_SIZE].split(b"\x00", 1)[0].decode(
            "ascii", "replace")
        (size,) = struct.unpack_from("<I", buf, 4 + params.COMMAND_SIZE)
        if size > params.MAX_SIZE:
            del buf[:4]  # poisoned header: skip this magic and resync
            continue
        total = params.MESSAGE_HEADER_SIZE + size
        if len(buf) < total:
            return
        payload = bytes(buf[params.MESSAGE_HEADER_SIZE:total])
        del buf[:total]
        yield command, payload


class CNode:
    """One connected peer."""

    def __init__(self, sock: socket.socket, addr: CAddress, f_inbound: bool):
        self.socket = sock
        self.addr = addr
        self.f_inbound = f_inbound
        self.n_version = 0          # set by their `version` message
        self.f_disconnect = False
        self.recv_buf = bytearray()
        self.recv_lock = threading.Lock()
        self.send_lock = threading.Lock()
        # inventory bookkeeping (setInventoryKnown / vInventoryToSend / AskFor)
        self.inventory_known: set = set()
        self.inventory_to_send: list[CInv] = []
        self.ask_for: list[CInv] = []
        self.inv_lock = threading.Lock()
        self.hash_continue = 0      # for chunked getblocks replies

    def send_message(self, command: str, payload: bytes = b""):
        data = pack_message(command, payload)
        try:
            with self.send_lock:
                self.socket.sendall(data)
        except OSError:
            self.f_disconnect = True

    def push_inventory(self, inv: CInv):
        with self.inv_lock:
            if inv.key() not in self.inventory_known:
                self.inventory_to_send.append(inv)

    def disconnect(self):
        self.f_disconnect = True
        try:
            self.socket.close()
        except OSError:
            pass

    def __repr__(self):
        return f"CNode({self.addr}{' in' if self.f_inbound else ' out'})"


class NetEngine:
    """Listener + connector + per-peer receive threads. Message dispatch
    is delegated to a handler with on_message(peer, command, payload) and
    on_peer_connected(peer)."""

    def __init__(self, port: int, peers: list[tuple[str, int]], handler,
                 listen: bool = True, logger=None):
        self.port = port
        self.want_peers = peers
        self.handler = handler
        self.listen = listen
        self.log = logger or util.setup_logging(None)
        self.nodes: list[CNode] = []
        self.nodes_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.listen_sock: socket.socket | None = None
        self.threads: list[threading.Thread] = []

    # ---------------------------------------------------------------- control
    def start(self):
        if self.listen:
            self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listen_sock.bind(("0.0.0.0", self.port))
            self.listen_sock.listen(8)
            self._spawn(self._accept_loop, "ThreadSocketHandler")
        self._spawn(self._open_connections_loop, "ThreadOpenConnections")

    def stop(self):
        self.stop_event.set()
        if self.listen_sock is not None:
            try:
                self.listen_sock.close()
            except OSError:
                pass
        with self.nodes_lock:
            for node in self.nodes:
                node.disconnect()
        for t in self.threads:
            t.join(timeout=2)

    def _spawn(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self.threads.append(t)

    # ------------------------------------------------------------ connections
    def _accept_loop(self):
        while not self.stop_event.is_set():
            try:
                sock, (ip, port) = self.listen_sock.accept()
            except OSError:
                return
            self._add_node(sock, CAddress(ip, port), f_inbound=True)

    def _open_connections_loop(self):
        while not self.stop_event.is_set():
            for host, port in self.want_peers:
                if self.stop_event.is_set():
                    return
                if self._connected_to(host, port):
                    continue
                try:
                    sock = socket.create_connection((host, port), timeout=5)
                except OSError:
                    continue
                self._add_node(sock, CAddress(host, port), f_inbound=False)
            self.stop_event.wait(5)

    def _connected_to(self, host: str, port: int) -> bool:
        with self.nodes_lock:
            return any(n.addr.ip == host and n.addr.port == port
                       and not n.f_disconnect for n in self.nodes)

    def _add_node(self, sock: socket.socket, addr: CAddress, f_inbound: bool):
        node = CNode(sock, addr, f_inbound)
        with self.nodes_lock:
            self.nodes.append(node)
        self.log.debug("connection %s", node)
        self._spawn(lambda: self._recv_loop(node), f"recv-{addr}")
        self.handler.on_peer_connected(node)

    # --------------------------------------------------------------- receive
    def _recv_loop(self, node: CNode):
        sock = node.socket
        sock.settimeout(0.5)
        while not self.stop_event.is_set() and not node.f_disconnect:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            with node.recv_lock:
                node.recv_buf += data
            self._drain(node)
        node.disconnect()
        with self.nodes_lock:
            if node in self.nodes:
                self.nodes.remove(node)
        self.log.debug("disconnected %s", node)

    def _drain(self, node: CNode):
        with node.recv_lock:
            messages = list(parse_messages(node.recv_buf))
        for command, payload in messages:
            try:
                self.handler.on_message(node, command, payload)
            except Exception:
                self.log.exception("ProcessMessage(%s) failed", command)

    # ----------------------------------------------------------------- relay
    def peers(self) -> list[CNode]:
        with self.nodes_lock:
            return [n for n in self.nodes if not n.f_disconnect]

    def relay_inventory(self, inv: CInv, skip: CNode | None = None):
        """RelayInventory — queue for every connected peer."""
        for node in self.peers():
            if node is not skip:
                node.push_inventory(inv)

    def connection_count(self) -> int:
        return len(self.peers())
