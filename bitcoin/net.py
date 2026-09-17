"""网络底层——对应原版 net.h / net.cpp：消息分帧、CAddress、CInv、CNode 和收发线程。

v0.1.5 的线路格式（忠实复刻）：

* 消息头 20 字节：4 字节魔数 + 12 字节命令名（不足补 0）+ 4 字节载荷长度。
  **没有校验和**——校验和字段是 0.2.x 才加的。
* 没有 verack、没有 ping：双方各自发出 version，收到对方的 version 就算握手完成。
* TCP 是一根"水管"，没有消息边界；接收方靠"向前搜索魔数"来切分消息，
  字节流错位时也靠它重新对齐（和原版 ProcessMessages 的做法一样）。

线程分工与原版对应：
- 监听线程接受新连接（原版 ThreadSocketHandler 的一部分）；
- 每个连接一个接收线程，把字节攒进 recv_buf 并切出完整消息；
- 每个连接一个发送线程，专门把 send_queue 里的数据写进 socket
  （对应原版"先写进 vSend 缓冲区，由 socket 线程真正发送"——处理消息的线程永远不会卡在网络上）；
- 消息的"业务处理"在 node.py。
"""

import heapq
import itertools
import queue
import socket
import struct
import threading

from . import params, util
from .serialize import DataStream


class CAddress:
    """网络地址。线路格式：服务标志(8) + 12 字节保留 + IP(4) + 端口(2) = 26 字节。

    12 字节保留区固定是 00×10 + FF FF，这是"把 IPv4 地址写成 IPv6 形式"的标准前缀——
    中本聪在 2009 年就给 IPv6 留好了位置。
    注意 IP 和端口是**网络字节序（大端）**，这是全协议里仅有的两个大端字段。
    """

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
        s.read(12)
        ip = socket.inet_ntoa(s.read(4))
        (port,) = struct.unpack(">H", s.read(2))
        return cls(ip, port, services)

    def __repr__(self):
        return f"{self.ip}:{self.port}"


class CInv:
    """库存条目："我有一个 类型=交易/区块、哈希=xxx 的东西"。36 字节。
    节点之间先用它互相通告（inv），对方感兴趣再来要完整数据（getdata）。"""

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
    """CMessageHeader + 载荷：魔数 + 命令 + 长度 + 内容。"""
    cmd = command.encode("ascii")
    assert len(cmd) <= params.COMMAND_SIZE
    return (params.MESSAGE_START
            + cmd.ljust(params.COMMAND_SIZE, b"\x00")
            + struct.pack("<I", len(payload))
            + payload)


def _command_is_valid(raw: bytes) -> bool:
    """CMessageHeader::IsValid 对命令名的检查：
    必须是可见 ASCII 字符，而且第一个 0 之后必须全是 0。"""
    seen_zero = False
    for byte in raw:
        if byte == 0:
            seen_zero = True
        elif seen_zero or byte < 0x20 or byte > 0x7E:
            return False
    return True


def parse_messages(buf: bytearray):
    """从缓冲区里切出所有完整的消息，逐个产出 (命令, 载荷)，并把它们从缓冲区删掉。
    不完整的尾巴留在缓冲区里等后续字节；垃圾字节被跳过。"""
    while True:
        start = buf.find(params.MESSAGE_START)
        if start < 0:
            # 整段都没有魔数：只留最后 3 个字节（魔数可能刚好被切成两半）
            del buf[:max(0, len(buf) - 3)]
            return
        if start > 0:
            del buf[:start]                     # 跳过魔数之前的垃圾
        if len(buf) < params.MESSAGE_HEADER_SIZE:
            return
        raw_command = bytes(buf[4:4 + params.COMMAND_SIZE])
        (size,) = struct.unpack_from("<I", buf, 4 + params.COMMAND_SIZE)
        if not _command_is_valid(raw_command) or size > params.MAX_SIZE:
            del buf[:4]                         # 头部不合法：丢掉这个假魔数，继续往后找
            continue
        total = params.MESSAGE_HEADER_SIZE + size
        if len(buf) < total:
            return                              # 载荷还没到齐
        command = raw_command.split(b"\x00", 1)[0].decode("ascii")
        payload = bytes(buf[params.MESSAGE_HEADER_SIZE:total])
        del buf[:total]
        yield command, payload


class CNode:
    """一个已连接的对端。"""

    _ask_seq = itertools.count()    # 让请求时间相同的条目保持先来后到

    def __init__(self, sock: socket.socket, addr: CAddress, f_inbound: bool):
        self.socket = sock
        self.addr = addr
        self.f_inbound = f_inbound          # 是对方连过来的，还是我们连出去的
        self.n_version = 0                  # 收到对方的 version 之前一直是 0
        self.f_disconnect = False
        self.recv_buf = bytearray()
        self.send_queue: queue.Queue = queue.Queue()
        # 库存簿记
        self.inv_lock = threading.Lock()
        self.inventory_known: set = set()   # setInventoryKnown：对方已经知道的东西（不用再告诉它）
        self.inventory_known2: set = set()  # setInventoryKnown2：见 node.py 的 getblocks 处理
        self.inventory_to_send: list[CInv] = []         # vInventoryToSend：攒着待发的通告
        self.map_ask_for: list = []         # mapAskFor：(最早可发送时间, 序号, inv) 的小顶堆

    def send_message(self, command: str, payload: bytes = b""):
        """PushMessage：只是放进发送队列，立刻返回，绝不阻塞。"""
        if not self.f_disconnect:
            self.send_queue.put(pack_message(command, payload))

    def add_inventory_known(self, inv: CInv):
        with self.inv_lock:
            self.inventory_known.add(inv.key())

    def push_inventory(self, inv: CInv):
        """PushInventory：对方已经知道的就不用再通告了。"""
        with self.inv_lock:
            if inv.key() not in self.inventory_known:
                self.inventory_to_send.append(inv)

    def ask_for(self, inv: CInv, already_asked: dict):
        """AskFor：安排向这个对端索取 inv。

        already_asked 是全节点共用的表（mapAlreadyAskedFor）：同一个东西如果已经向
        别的对端要过了，这次的请求就排到 2 分钟之后——上一个对端没给，才轮到这一个。
        这样多个邻居同时通告同一个区块时，不会把它下载很多遍。
        """
        request_time = already_asked.get(inv.key(), 0)
        now = (util.get_time() - 1) * 1_000_000
        request_time = max(request_time + 2 * 60 * 1_000_000, now)
        already_asked[inv.key()] = request_time
        with self.inv_lock:
            heapq.heappush(self.map_ask_for, (request_time, next(self._ask_seq), inv))

    def disconnect(self):
        self.f_disconnect = True
        self.send_queue.put(None)           # 唤醒发送线程让它退出
        try:
            self.socket.close()
        except OSError:
            pass

    def __repr__(self):
        return f"CNode({self.addr}{' 入站' if self.f_inbound else ' 出站'})"


class NetEngine:
    """监听 + 主动连接 + 每个连接的收发线程。
    业务处理交给 handler：on_peer_connected(peer) 和 on_message(peer, 命令, 载荷)。"""

    def __init__(self, port: int, peers: list[tuple[str, int]], handler,
                 listen: bool = True, logger=None):
        self.port = port
        self.want_peers = peers             # -connect / -addnode 指定的对端
        self.handler = handler
        self.listen = listen
        self.log = logger or util.setup_logging(None)
        self.nodes: list[CNode] = []
        self.nodes_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.listen_sock: socket.socket | None = None
        self.threads: list[threading.Thread] = []

    # ---------------------------------------------------------------- 启停
    def start(self):
        if self.listen:
            self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
        for node in self.peers():
            node.disconnect()
        for t in self.threads:
            t.join(timeout=2)

    def _spawn(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self.threads.append(t)

    # ---------------------------------------------------------------- 连接
    def _accept_loop(self):
        while not self.stop_event.is_set():
            try:
                sock, (ip, port) = self.listen_sock.accept()
            except OSError:
                return
            # 入站连接：我们不知道对方提供什么服务，services 先记 0（和原版一样）
            self._add_node(sock, CAddress(ip, port, services=0), f_inbound=True)

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
            self.stop_event.wait(2)             # 断线后每隔几秒重连

    def _connected_to(self, host: str, port: int) -> bool:
        with self.nodes_lock:
            return any(n.addr.ip == host and n.addr.port == port
                       and not n.f_disconnect for n in self.nodes)

    def _add_node(self, sock: socket.socket, addr: CAddress, f_inbound: bool):
        node = CNode(sock, addr, f_inbound)
        with self.nodes_lock:
            self.nodes.append(node)
        self.log.debug("新连接 %s", node)
        self._spawn(lambda: self._send_loop(node), f"send-{addr}")
        self._spawn(lambda: self._recv_loop(node), f"recv-{addr}")
        self.handler.on_peer_connected(node)

    # ---------------------------------------------------------------- 收发
    def _send_loop(self, node: CNode):
        while True:
            data = node.send_queue.get()
            if data is None or node.f_disconnect:
                return
            try:
                node.socket.sendall(data)
            except OSError:
                node.f_disconnect = True
                return

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
                break                           # 对方关闭了连接
            node.recv_buf += data
            for command, payload in list(parse_messages(node.recv_buf)):
                try:
                    self.handler.on_message(node, command, payload)
                except Exception:
                    # 对端发来的任何垃圾数据都不允许让我们的节点崩溃
                    self.log.exception("ProcessMessage(%s) 出错", command)
        node.disconnect()
        with self.nodes_lock:
            if node in self.nodes:
                self.nodes.remove(node)
        self.log.debug("连接断开 %s", node)

    # ---------------------------------------------------------------- 转发
    def peers(self) -> list[CNode]:
        with self.nodes_lock:
            return [n for n in self.nodes if not n.f_disconnect]

    def relay_inventory(self, inv: CInv):
        """RelayInventory：向所有对端通告（各自的"已知"过滤在 push_inventory 里）。"""
        for node in self.peers():
            node.push_inventory(inv)

    def connection_count(self) -> int:
        return len(self.peers())
