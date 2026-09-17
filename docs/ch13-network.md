# 第 13 章　P2P 网络

> **本章目标**：弄明白在没有任何中心服务器的情况下，节点之间怎么认识、怎么同步区块、怎么广播交易。
> 学完你能画出两个节点从握手到同步完成的完整消息时序图。
>
> **对应源码**：[bitcoin/net.py](../bitcoin/net.py)、[bitcoin/node.py](../bitcoin/node.py)、[bitcoin/config.py](../bitcoin/config.py)
> 　　**实验**：[labs/lab11_network.py](../labs/lab11_network.py)

## 13.1　点对点网络

平时上网用的是"客户端—服务器"模式：你的浏览器（客户端）向网站的服务器发请求。服务器一关，服务就没了。

比特币用的是**点对点**（peer-to-peer，P2P）网络：没有服务器，每个节点地位平等，既是客户端也是服务器。
每个节点和若干个其他节点（**邻居**，peer）保持着 TCP 连接。一条消息从一个节点发出，
邻居转给邻居的邻居……几秒钟内就能传遍全网。这种传播方式叫**泛洪**（flooding）或**八卦协议**（gossip）。

没有中心意味着：**没有任何一台机器是关掉之后网络就会停的。** 这是比特币无法被"关闭"的技术原因。

### 最开始怎么找到别的节点

一个新节点刚启动时谁也不认识。原版 v0.1.5 的办法现在看来相当有趣：
它会连上一个 **IRC 聊天服务器**，加入一个叫 `#bitcoin` 的频道，把自己的 IP 地址编码成昵称；
频道里的其他"人"其实都是比特币节点，大家通过昵称互相发现（`irc.cpp`）。之后节点之间再用 `addr` 消息互相介绍更多的节点。

本项目运行在私有网络上，没有实现 IRC，而是**用命令行参数手动指定邻居**：

```powershell
python run_node.py -datadir=data\nodeA -port=18444
python run_node.py -datadir=data\nodeB -port=18445 -connect=127.0.0.1:18444
```

## 13.2　TCP 是一根水管

TCP 保证"你塞进去的字节，按顺序、不丢不重地从另一头流出来"。但它**只认字节，不认"消息"**。
你分两次发出的两条消息，对方可能一次就全收到了，也可能分五次零零碎碎地收到——切在哪里完全随机。

所以接收方必须自己把字节流重新切成一条一条的消息。这件事叫**分帧**（framing）。

## 13.3　消息的格式

比特币的每条消息 = **20 字节的消息头** + **载荷**（payload，消息的具体内容）：

```text
┌─────────────┬───────────────────────────┬──────────────┐┌──────────────┐
│ 魔数 4 字节 │   命令名 12 字节          │ 载荷长度 4   ││  载荷 N 字节 │
│ f9 be b4 d9 │ "version" + 5 个 00       │ 2e 00 00 00  ││  ……           │
└─────────────┴───────────────────────────┴──────────────┘└──────────────┘
```

```python
def pack_message(command: str, payload: bytes) -> bytes:
    """CMessageHeader + 载荷：魔数 + 命令 + 长度 + 内容。"""
    cmd = command.encode("ascii")
    return (params.MESSAGE_START
            + cmd.ljust(params.COMMAND_SIZE, b"\x00")       # 命令名不足 12 字节就补 0
            + struct.pack("<I", len(payload))               # 载荷长度，4 字节小端
            + payload)
```

**魔数**（magic）`f9 be b4 d9` 是一个固定的"接头暗号"，它本身没有任何含义。作用有两个：

1. **标识网络**：比特币主网、测试网各用不同的魔数。收到魔数不对的消息，说明对方不是同一个网络的，直接忽略。
2. **重新对齐的路标**：万一字节流里混进了垃圾，接收方只要向前搜索下一个魔数，就能重新找到消息的开头。

中本聪在 `net.h` 的注释里解释过为什么选这四个字节（大意）：它们是不常用的高位字符，
不是合法的 UTF-8，而且不管按什么字节对齐方式读，都是一个很大的整数——总之，尽量不和正常数据撞车。

**长度字段**告诉接收方"后面有多少字节属于这条消息"。有了它，切分就很简单了。

> ⚠️ v0.1.5 的消息头**没有校验和**。后来的版本（0.2.x 起）在长度后面加了 4 字节的校验和（载荷两次 SHA-256 的前 4 字节），
> 消息头变成了 24 字节。本项目严格复刻 0.1.5，是 20 字节。

### 分帧器

```python
def parse_messages(buf: bytearray):
    while True:
        start = buf.find(params.MESSAGE_START)
        if start < 0:
            del buf[:max(0, len(buf) - 3)]      # 整段都没有魔数：只留最后 3 个字节（魔数可能刚好被切成两半）
            return
        if start > 0:
            del buf[:start]                     # 跳过魔数之前的垃圾
        if len(buf) < params.MESSAGE_HEADER_SIZE:
            return                              # 消息头还没收全，等下一批字节
        raw_command = bytes(buf[4:16])
        (size,) = struct.unpack_from("<I", buf, 16)
        if not _command_is_valid(raw_command) or size > params.MAX_SIZE:
            del buf[:4]                         # 头部不合法：丢掉这个假魔数，继续往后找
            continue
        total = params.MESSAGE_HEADER_SIZE + size
        if len(buf) < total:
            return                              # 载荷还没到齐
        ...
        del buf[:total]
        yield command, payload
```

每个连接有一个接收缓冲区 `recv_buf`。每收到一批字节，就追加进去，然后调用这个函数：
能切出几条完整的消息就切几条，切不出完整消息的"尾巴"留在缓冲区里，等下一批字节到了再说。

`tests/test_net.py` 对它做了各种刁难：把一条消息在任意位置切成两半、一个字节一个字节地喂、
在消息之间塞垃圾、载荷里恰好包含魔数、长度字段写成 40 亿……都要能正确处理。

## 13.4　八种消息

v0.1.5 的全部消息类型就这么几种（还有四种是给未完成的"IP 直接付款"和"交易市场"功能预留的，本项目没有实现）：

| 命令 | 载荷 | 意思 |
|---|---|---|
| `version` | 协议版本、服务标志、时间、地址 | "你好，我是这个版本的" |
| `inv` | 一组 (类型, 哈希) | "我有这些东西"（inventory，库存） |
| `getdata` | 一组 (类型, 哈希) | "请把这些东西发给我" |
| `block` | 一个完整的区块 | "这是你要的区块" |
| `tx` | 一笔完整的交易 | "这是你要的交易" |
| `getblocks` | 区块定位器 + 截止哈希 | "这是我的链的概况，后面的区块你有吗？" |
| `getaddr` | 空 | "你还认识哪些节点？" |
| `addr` | 一组网络地址 | "我认识这些节点" |

其中 (类型, 哈希) 这个小结构叫 `CInv`，36 字节：类型 1 = 交易，2 = 区块。

**核心模式是"三步走"：`inv` → `getdata` → `block`/`tx`**

```text
   A：  inv       "我有一个新区块，哈希是 abc…"         （只发 36 字节）
   B：  getdata   "这个我没有，发给我"                  （如果 B 已经有了，就不理会）
   A：  block     （完整的区块）
```

为什么不直接把区块发过去？因为每个节点有好几个邻居，同一个区块会从好几个方向传来。
先用 36 字节的哈希"问一声"，对方没有才发全文，避免了大量的重复传输。

## 13.5　握手

两个节点建立 TCP 连接后，**双方立刻各自发出一条 `version`**，不等对方先开口：

```python
    def on_peer_connected(self, peer: CNode):
        s = DataStream()
        s.write_int32(params.VERSION)               # 协议版本 105
        s.write_uint64(params.NODE_NETWORK)         # 服务标志：我是全节点
        s.write_int64(util.get_adjusted_time())     # 我的时间
        peer.addr.serialize(s)                      # 对方的地址（对方借此知道自己的外网 IP）
        peer.send_message("version", s.getvalue())
```

载荷共 4 + 8 + 8 + 26 = 46 字节。最后那个 26 字节的网络地址是：服务标志(8) + 12 字节保留 + IP(4) + 端口(2)。
12 字节保留区固定是 `00…00 ff ff`——这是"把 IPv4 地址写成 IPv6 形式"的标准前缀，中本聪 2009 年就给 IPv6 留好了位置。
另外，**IP 和端口是全协议里仅有的两个大端字段**（因为它们沿用的是操作系统网络接口的字节序）。

收到对方的 `version` 之后，握手就算完成了：

```python
    def _msg_version(self, peer, s):
        if peer.n_version != 0:
            return                      # version 只能发一次
        their_version = s.read_int32()
        ...
        peer.n_version = min(their_version, params.VERSION)     # 以后按双方都支持的较低版本通信
        # 只向**第一个**连上的节点索要区块；之后的同步靠新区块的 inv 驱动
        if not self.asked_for_blocks:
            self.asked_for_blocks = True
            self.push_get_blocks(peer, self.chain.best_index, 0)
```

在收到对方的 `version` 之前，其他任何消息都会被忽略。

> v0.1.5 **没有 `verack`**（对 version 的确认回执），也**没有 `ping`**。这两种消息都是后来的版本加的。

## 13.6　同步区块：getblocks 与区块定位器

新节点（或离线了一段时间的节点）怎么追上大家？

它需要告诉邻居"我的链现在长什么样"，好让邻居判断该从哪里开始发。如果把自己所有区块的哈希都列出来，
几十万个区块就是十几 MB——太浪费了。

**区块定位器**（block locator）是个很聪明的办法：**越靠近链尖列得越密，越往回越稀疏。**

```python
    @classmethod
    def from_index(cls, pindex, genesis_hash):
        v_have = []
        step = 1
        while pindex is not None:
            v_have.append(pindex.hash)
            for _ in range(step):               # 往回走 step 步
                pindex = pindex.pprev
            if len(v_have) > 10:
                step *= 2                       # 列了 10 多个之后，步长每次翻倍
        v_have.append(genesis_hash)             # 最后总是附上创世块
        return cls(v_have)
```

一条 1000 个区块的链，定位器里的高度是：
`999, 998, 997, …, 988, 986, 982, 974, 958, 926, 862, 734, 478, 0`——只有二十来个哈希。
即使链有 100 万个区块，定位器也只有 30 多个哈希。

收到 `getblocks` 的一方，从前往后扫描定位器，找到**第一个自己主链上也有的哈希**——那就是两条链的分叉点
（如果双方在同一条链上，那就是对方的链尖）。然后把分叉点之后的所有区块的哈希用 `inv` 通告过去：

```python
    def _msg_getblocks(self, peer, s):
        locator = CBlockLocator.deserialize(s)
        hash_stop = s.read_uint256()
        pindex = self.chain.find_fork_by_locator(locator).pnext
        while pindex is not None:
            if pindex.hash == hash_stop:
                break                           # 到"截止哈希"就停（不含它本身）
            ...把 CInv(MSG_BLOCK, pindex.hash) 放进待发送队列...
            pindex = pindex.pnext
```

为什么要"前密后疏"？因为最常见的情况是两个节点只差最近的几个区块，密集的前段能精确定位；
偶尔遇到很深的分叉，稀疏的后段也能保证找到一个公共祖先（最坏情况是创世块），只是会多传一些对方已有的区块哈希，
而对方对已有的东西不会回 `getdata`，所以多传的只是几十字节的哈希而已。

### 实验 11 里的完整同步过程

```text
     0.03s  Alice -> Bob  : version       46 字节   你好，我的协议版本是 105
     0.03s  Bob   -> Alice: version       46 字节   你好，我的协议版本是 105
     0.03s  Bob   -> Alice: getblocks    101 字节   这是我的链的概况，后面的区块你有吗？
     0.03s  Alice -> Bob  : getblocks    613 字节   这是我的链的概况，后面的区块你有吗？
     0.11s  Alice -> Bob  : inv         4501 字节   我有 125 个区块
     0.22s  Bob   -> Alice: getdata     4501 字节   请发给我 125 个区块
     0.22s  Alice -> Bob  : block        215 字节   这是你要的区块
            …… （同样的消息连续 125 条，只显示了前 3 条）
   => Bob 同步完成，高度 125。
```

几个可以对着数字验证的细节：

* `version` 46 字节 = 4 + 8 + 8 + 26 ✓
* Bob 的 `getblocks` 101 字节 = 版本 4 + 个数 1 + 2 个哈希（链尖即创世块，再加上无条件追加的创世块）× 32 + 截止哈希 32 ✓
* Alice 的 `getblocks` 613 字节 = 4 + 1 + 18 个哈希 × 32 + 32 ✓（125 个区块的链，定位器 18 项）
* `inv` 4501 字节 = 个数 1 + 125 × 36 ✓
* Alice 也向 Bob 发了 `getblocks`，但 Bob 什么都没有，所以没有下文。

> v0.1.5 的 `getblocks` **没有数量上限**，一次把后面所有的区块都通告出去。
> "每次最多 500 个"的限制是后来才加的。

## 13.7　孤块的回补

设想 Bob 在线，但因为某种原因**漏掉了**几个区块的通告，突然收到了一个高度 105 的区块，而他的链尖才到 100。

第 10 章说过，这个区块会被当作**孤块**暂存。同时，Bob 会向发来它的邻居要缺失的部分：

```python
    def _msg_block(self, peer, s):
        ...
        if self.chain.process_block(block):
            if inv.hash in self.chain.map_orphan_blocks:
                # 被当作孤块暂存了：请这个对端把中间缺的区块补上
                self.push_get_blocks(peer, self.chain.best_index,
                                     self.chain.get_orphan_root(block))
```

这条 `getblocks` 的意思是：**"从我的链尖（100）之后开始，一直到我手里这个孤块（105）之前，都发给我。"**
对方于是通告 101、102、103、104。Bob 收齐之后，105 自动接上。

截止哈希用的是 `get_orphan_root()` 的返回值——孤块链里**最老的那个孤块自己的哈希**。
对方发到它就停（不含它），因为它 Bob 已经有了。

> 这份代码的早期版本在这里有一个真实的 bug：`get_orphan_root` 返回了"缺失的父块"的哈希而不是"最老的孤块自己"的哈希。
> 后果是对方通告到 103 就停了（104 恰好是截止哈希，不含），Bob 永远拿不到 104，永远卡在那里。
> 是对照原版 `main.cpp` 逐行审查时发现的。
> `tests/test_integration.py::test_orphan_backfill_over_the_network` 用两个真实节点复现了这个场景。

## 13.8　广播：谁知道什么

一个节点有好几个邻居。每个邻居对应一个 `CNode` 对象，里面记着三样东西：

```python
        self.inventory_known: set = set()   # 对方已经知道的东西（不用再告诉它）
        self.inventory_to_send: list = []   # 攒着待发的通告
        self.map_ask_for: list = []         # 待发送的索取请求（按时间排序）
```

**`inventory_known`（防回声）**：A 把一个区块发给了 B，B 验证通过后要通告给自己所有的邻居——
但不应该再通告给 A（A 当然有）。所以每当从某个邻居收到 `inv`、`block`、`tx`，
或者向它发出过 `inv`，都记一笔"它已经知道这个了"。

**`map_ask_for`（防重复下载）**：如果三个邻居几乎同时通告了同一个新区块，没必要下载三遍。
规则是：**第一个通告的邻居，马上向它要；后面再通告的，请求排到 2 分钟之后**——
如果第一个邻居在 2 分钟内没给，才轮到下一个。

```python
    def ask_for(self, inv, already_asked):
        request_time = already_asked.get(inv.key(), 0)
        now = (util.get_time() - 1) * 1_000_000
        request_time = max(request_time + 2 * 60 * 1_000_000, now)      # 每次重试推后 2 分钟
        already_asked[inv.key()] = request_time
        heapq.heappush(self.map_ask_for, (request_time, next(self._ask_seq), inv))
```

节点有一个后台线程，每 0.1 秒对每个邻居执行一次 `send_messages()`：把攒着的通告合并成一条 `inv` 发出去，
把到时间的索取请求合并成一条 `getdata` 发出去。

## 13.9　线程模型与"不信任任何人"

本项目的线程分工和原版基本对应：

| 线程 | 干什么 |
|---|---|
| 监听线程 | 接受别人连进来 |
| 连接线程 | 主动去连 `-connect` 指定的节点，断线后定时重连 |
| 每个连接一个**接收**线程 | 收字节 → 分帧 → 调用 `node.on_message()` 处理 |
| 每个连接一个**发送**线程 | 把发送队列里的数据写进 socket |
| 消息处理线程 | 每 0.1 秒为每个邻居冲刷 `inv` / `getdata` |
| 矿工线程 | 挖矿 |
| （界面线程） | 每 0.5 秒刷新一次界面 |

`send_message()` 只是把数据放进队列，立刻返回。真正往网络上写由专门的发送线程做。
这样"处理消息的线程"永远不会因为对方网速慢而卡住。（原版的做法相同：先写进 `vSend` 缓冲区，由 socket 线程发送。）

处理消息时，整个过程都持有全局大锁 `chain.lock`（对应原版的 `cs_main`），所以不同邻居的消息实际上是一条一条串行处理的，
不会出现两个线程同时改账本的情况。

**最重要的一条原则：网络上来的任何东西都不可信。**

```python
            for command, payload in list(parse_messages(node.recv_buf)):
                try:
                    self.handler.on_message(node, command, payload)
                except Exception:
                    # 对端发来的任何垃圾数据都不允许让我们的节点崩溃
                    self.log.exception("ProcessMessage(%s) 出错", command)
```

截断的消息、荒谬的长度、格式错误的交易、无效的签名、不合规则的区块……对方可以发来任何东西。
节点的态度是：**能验证的就验证，验证不过的就丢弃，无论如何自己不能崩溃。**
`tests/test_integration.py::test_node_survives_garbage_and_hostile_peers` 向一个节点灌了各种垃圾，
同时验证它还能正常为另一个诚实节点服务。

这和第 1 章的出发点一脉相承：比特币不要求你信任任何邻居。邻居可以骗你、可以不理你，
但只要你至少有一个诚实的邻居，你就能得到真实的区块链——因为每个区块、每笔交易都是你**自己验证**过的，
而工作量证明让伪造一条更长的链代价高昂。

## 13.10　动手实验

```powershell
python labs\lab11_network.py
```

这个实验在同一个进程里启动两个真实节点（走真实的 TCP），并在"收到消息"的入口加了一行打印，
于是你能看到它们之间的完整对话：握手与初始同步 → 一笔交易的广播 → 一个新区块的广播。

也可以玩真的：开两个终端分别运行 `run_node.py`（一个加 `-gen`），
然后查看两个数据目录下的 `debug.log`，里面记录了每一条收到的消息。

**改一改**：

1. 在实验里再加一个节点 Carol，让她只连接 Bob（不连 Alice）。观察 Alice 挖出的区块是怎么经过 Bob 到达 Carol 的。
2. 计算：一条 100 万个区块的链，区块定位器里有多少个哈希？（写几行代码模拟 `from_index` 里的循环即可。）

## 本章小结

* P2P 网络没有中心；消息靠邻居之间接力传遍全网。
* TCP 是字节流，消息的边界靠 **20 字节消息头**（魔数 + 命令 + 长度）来切分；魔数还用于错位后的重新对齐。
* 握手：双方各发一条 `version`。**没有 verack，没有 ping，没有校验和。**
* 数据传输三步走：`inv`（我有）→ `getdata`（给我）→ `block`/`tx`（给你），避免重复传输。
* 同步：`getblocks` 带着**区块定位器**（前密后疏的哈希列表），对方找到分叉点后通告其后的所有区块。
* 收到孤块时，用"链尖 → 最老孤块"的 `getblocks` 回补缺失的部分。
* 每个邻居一份"它已经知道什么"的记录防回声；同一份数据只向一个邻居索取，2 分钟后才换人。
* **不信任任何邻居**：一切自己验证，任何垃圾数据都不能让节点崩溃。

## 思考题

1. 为什么 `inv` 里只放哈希而不直接放区块？如果一个节点有 8 个邻居，大约能省下多少流量？
2. 攻击者控制了你**所有**的邻居（这叫"日蚀攻击"）。他能对你做什么？不能做什么？
3. 消息头没有校验和，TCP 本身也可能（极小概率）传错字节。一个传错了字节的区块会造成什么后果？

<details><summary>参考答案</summary>

1. 一个新区块会从多个邻居同时传来。如果直接发全文，8 个邻居就可能收到 8 份；用 `inv` 的话，
   只需要 8 × 36 字节的通告 + 1 份全文。区块越大，省得越多（今天的区块有 1~2 MB）。
2. 能：不给你转发新区块（让你以为链停了）、给你看一条他自己挖的较短的假链、不转发你的交易。
   不能：伪造别人的签名花别人的钱、让你接受不合规则的区块、凭空造币。
   而且要让你相信假链，他得真的付出工作量去挖。只要你连上哪怕一个诚实节点，就会立刻发现更长的真链。
3. 区块的哈希会对不上（默克尔根或工作量证明验证失败），被当作无效区块丢弃；之后会从别的邻居那里重新获取。
   交易同理——签名验证会失败。所以没有校验和并不影响**安全性**，只是浪费一点带宽。
   后来加上校验和，主要是为了更早、更便宜地发现传输错误。
</details>

[← 上一章](ch12-wallet.md)　|　[下一章：节点的一生 →](ch14-full-story.md)
