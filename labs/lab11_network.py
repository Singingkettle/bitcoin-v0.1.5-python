"""实验 11：偷听两个节点的“对话”——握手、同步区块、广播交易。
对应教程第 13 章。运行：python labs/lab11_network.py   （约需 25 秒）

在同一个进程里启动两个真实节点，它们通过本机的真实 TCP 连接通信；
我们在每个节点“收到消息”的入口处加一行打印，把整个对话记录下来。
"""
import socket
import threading
import time

from _common import mine, step, temp_datadir, title

from bitcoin import params
from bitcoin.config import Config
from bitcoin.net import CInv
from bitcoin.node import Node
from bitcoin.serialize import DataStream
from bitcoin.util import format_money

title("实验 11：P2P 网络上的对话")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


print_lock = threading.Lock()
T0 = time.time()


def describe(command: str, payload: bytes) -> str:
    """给消息加一句人话的说明。"""
    try:
        if command in ("inv", "getdata"):
            s = DataStream(payload)
            invs = s.read_vector(lambda: CInv.deserialize(s))
            kinds = {params.MSG_TX: "交易", params.MSG_BLOCK: "区块"}
            what = "、".join(sorted({kinds.get(i.type, "?") for i in invs}))
            verb = "我有" if command == "inv" else "请发给我"
            return f"{verb} {len(invs)} 个{what}"
        if command == "version":
            return f"你好，我的协议版本是 {int.from_bytes(payload[:4], 'little')}"
        if command == "getblocks":
            return "这是我的链的概况，后面的区块你有吗？"
        if command == "block":
            return "这是你要的区块"
        if command == "tx":
            return "这是你要的交易"
    except Exception:
        pass
    return ""


repeat = {"key": None, "count": 0}           # 用来把连续的同类消息折叠起来


def flush_repeat():
    if repeat["count"] > 3:
        print(f"            …… （同样的消息连续 {repeat['count']} 条，只显示了前 3 条）")
    repeat["key"], repeat["count"] = None, 0


def eavesdrop(node: Node, me: str, other: str):
    original = node.on_message

    def spy(peer, command, payload):
        with print_lock:
            key = (me, command)
            if key != repeat["key"]:
                flush_repeat()
                repeat["key"] = key
            repeat["count"] += 1
            if repeat["count"] <= 3:
                print(f"   {time.time() - T0:6.2f}s  {other} -> {me}: {command:<10} {len(payload):>5} 字节   "
                      f"{describe(command, payload)}")
        return original(peer, command, payload)

    node.on_message = spy


def wait(predicate, timeout=30):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.05)
    assert predicate(), "等待超时"
    time.sleep(0.3)             # 让屏幕上的对话打印完整
    with print_lock:
        flush_repeat()


port_a, port_b = free_port(), free_port()
alice = Node(Config([f"-datadir={temp_datadir('alice')}", f"-port={port_a}"]))
bob = Node(Config([f"-datadir={temp_datadir('bob')}", f"-port={port_b}",
                   f"-connect=127.0.0.1:{port_a}"]))
eavesdrop(alice, "Alice", "Bob  ")
eavesdrop(bob, "Bob  ", "Alice")

step("0. 准备：Alice 先独自挖 125 个区块（此时网络还没启动）")
for _ in range(125):
    mine(alice.chain, 1, key=alice.wallet.generate_new_key(), quiet=True)
print(f"   Alice 高度 {alice.chain.best_height}，余额 {format_money(alice.wallet.get_balance())}；Bob 高度 {bob.chain.best_height}")

step("1. 两个节点上线。Bob 主动连接 Alice —— 握手 + 初始区块同步")
T0 = time.time()
alice.start()
bob.start()
wait(lambda: bob.chain.best_height == 125)
print(f"   => Bob 同步完成，高度 {bob.chain.best_height}。")
print("   注意看：① 双方各发一条 version 就算握手完成，没有 verack；")
print("           ② Bob 用 getblocks 询问，Alice 用一条 inv 列出 125 个区块哈希，")
print("              Bob 再用 getdata 索取，Alice 逐个发来 block。")

step("2. Alice 付 10 BTC 给 Bob —— 交易的广播")
T0 = time.time()
ok, txid = alice.wallet.send_money(bob.wallet.get_default_address(), 10 * params.COIN)
wait(lambda: len(bob.mempool) == 1)
print(f"   => 交易进入了 Bob 的内存池；Bob 的余额（零确认）：{format_money(bob.wallet.get_balance())}")
print("   同样是三步：inv（我有一笔新交易）-> getdata（发给我）-> tx（给你）。")
print("   先通告哈希、对方需要才发全文，是为了避免把同一份数据重复发给已经有它的节点。")

step("3. Bob 挖出下一个区块 —— 区块的广播")
T0 = time.time()
bob.miner.mine_one_block()
wait(lambda: alice.chain.best_height == 126)
print(f"   => Alice 收到了 Bob 挖的区块，高度 {alice.chain.best_height}；那笔交易在两边都是 1 个确认。")
print(f"   Alice 内存池 {len(alice.mempool)} 笔，Bob 内存池 {len(bob.mempool)} 笔（已打包的交易自动移出）")

step("4. 看一眼一条消息在网线上的真实样子")
from bitcoin.net import pack_message  # noqa: E402
raw = pack_message("getaddr", b"")
print(f"   一条空载荷的 getaddr 消息共 {len(raw)} 字节：{raw.hex(' ')}")
print("   = 魔数 f9 be b4 d9 + 12 字节命令名（不足补 0）+ 4 字节载荷长度。没有校验和（那是 0.2 版才加的）。")

bob.stop()
alice.stop()
print("\n实验 11 完成。")
