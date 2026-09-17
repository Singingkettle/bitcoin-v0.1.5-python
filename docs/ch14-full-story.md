# 第 14 章　节点的一生：完整走一遍

> **本章目标**：把前面 13 章的零件装成一台完整的机器。我们跟着一笔付款，
> 从 Alice 点下"发送"开始，一直追到它在 Bob 的窗口里显示"6 个确认"为止，看它依次经过了哪些代码。
>
> **对应源码**：[bitcoin/node.py](../bitcoin/node.py)、[run_gui.py](../run_gui.py)、[qt/](../qt/)
> 　　**实验**：[labs/lab12_double_spend.py](../labs/lab12_double_spend.py)

## 14.1　一个节点是怎么装起来的

`Node.__init__` 只有十几行，却是整个系统的接线图：

```python
class Node:
    def __init__(self, config: Config):
        self.chain = Blockchain(datadir, self.log)                          # ① 区块链（第 10 章）
        self.mempool = MemPool(self.chain)                                  # ② 内存池（第 11 章）
        self.wallet = Wallet(datadir, self.chain, self.mempool, self.log)   # ③ 钱包（第 12 章）
        self.miner = Miner(self.chain, self.mempool, self.wallet, self.log) # ④ 矿工（第 9 章）
        self.net = NetEngine(config.port, config.peers(), handler=self, …)  # ⑤ 网络（第 13 章）

        self.chain.block_listeners.append(self._on_new_best_block)
        self.mempool.listeners.append(self._on_pool_tx)
        self.wallet.relay_listeners.append(self._on_pool_tx)
```

各个部件之间不直接互相调用，而是通过**监听器**（回调函数的列表）松散地连在一起：

```text
                         ┌───────────────────────────────┐
   有交易进/出区块       │           Blockchain           │     出现新的最佳区块
  ┌──────────────────────┤  tx_listeners  block_listeners ├──────────────────────┐
  │                      └───────────────────────────────┘                      │
  ▼                                                                              ▼
┌──────────┐   有新交易进池   ┌──────────┐            向所有邻居通告 inv    ┌──────────┐
│  Wallet  │ ◄─────────────── │ MemPool  │ ───────────────────────────────► │   Node   │
│          │                  │ listeners│                                  │  / Net   │
└────┬─────┘                  └──────────┘                                  └──────────┘
     │ changed_listeners
     ▼
┌──────────┐
│   界面   │   （只设一个"需要刷新"的标志，由界面自己的定时器去刷新）
└──────────┘
```

这样设计的好处是：区块链模块完全不知道钱包、网络、界面的存在，可以单独测试
（`tests/test_chain.py` 里就只有一个 `Blockchain` 对象）。

## 14.2　启动

```python
    def start(self):
        self.wallet.reaccept_wallet_transactions()      # 重启后内存池是空的：先把钱包里还没进块的交易放回去
        self.net.start()                                # 开始监听端口、连接邻居
        self.sender_thread = threading.Thread(target=self._send_messages_loop, …)
        self.sender_thread.start()
        if self.config.generate:
            self.miner.start()
```

在此之前，`Blockchain.__init__` 已经做完了最重的活：

* **全新的数据目录**：用 `params.py` 里的常数重建创世块，核对哈希，写入磁盘；
* **已有的数据目录**：从 `blkindex.sqlite` 读出所有区块的索引，重建内存里的区块树，
  顺着"最佳链尖"往回走到创世块，得到主链。

## 14.3　跟着一笔付款走一遍

场景：Alice 和 Bob 各运行一个带界面的节点，互相连接。Alice 付 10 BTC 给 Bob。

### 第 1 步　Alice 点下"发送"　（界面线程）

`qt/senddialog.py` → `wallet.send_money(地址, 金额)`：

```text
send_money
 ├─ 检查：金额 > 0？地址的 Base58 校验和对吗？余额够吗？                       第 5、12 章
 ├─ 地址 → 20 字节公钥哈希 → 拼出 P2PKH 锁                                     第 5、7 章
 ├─ create_transaction
 │    ├─ select_coins：挑出要花的钱包交易                                      第 12 章
 │    ├─ 组装输出（付款 + 找零）和输入                                         第 6 章
 │    ├─ 对每个输入：signature_hash → 私钥签名 → 拼出 scriptSig               第 4、7 章
 │    └─ get_min_fee：手续费够吗？不够就加上重来                               第 11 章
 ├─ commit_transaction_spent：记入钱包，把花掉的币标记为已花
 └─ mempool.accept：自己先验证一遍，放进自己的内存池                           第 11 章
```

### 第 2 步　交易离开 Alice 的电脑

`mempool.accept` 成功后触发监听器 → `node._on_pool_tx` → 给每个邻居的"待通告"队列里放一条 `inv`。
0.1 秒内，消息处理线程把它发出去：

```text
   Alice -> Bob : inv       37 字节    "我有一笔新交易 84a8…"
   Bob -> Alice : getdata   37 字节    "没见过，发给我"
   Alice -> Bob : tx       235 字节    （完整的交易）
```

### 第 3 步　Bob 的节点收到交易　（Bob 的网络接收线程）

`net._recv_loop` 收字节 → `parse_messages` 切出一条 `tx` 消息 → `node.on_message` → `_msg_tx`：

```python
    def _msg_tx(self, peer, s):
        tx = CTransaction.deserialize(s)                # 字节 -> 对象              第 2、6 章
        peer.add_inventory_known(inv)                   # 记下"这个邻居已经有了"
        if self.mempool.accept(tx):                     # 完整验证                  第 7、11 章
            ...
```

Bob 的节点**不相信 Alice**。它独立地重新验证一切：格式对吗？输入指向的钱存在吗？没被花过吗？签名有效吗？
全部通过，才放进自己的内存池——然后 Bob 的节点也会向**它的**其他邻居通告这笔交易，如此接力传遍全网。

Bob 的内存池监听器同时通知了 Bob 的钱包：`add_to_wallet_if_mine` 发现第 0 个输出的锁里是自己的公钥哈希——
**"这是给我的钱！"** 记入钱包，并设置界面的"需要刷新"标志。

半秒之内，Bob 的窗口里出现了一行：

```text
   0/未确认    2026-09-18 10:30    来自：未知，收款地址：1EgKCk…    +10.00
```

注意"来自：未知"。比特币交易里**没有"付款人"这个字段**——只有"花了哪些输出"。
Bob 的钱包无从知道那些输出属于谁。这是比特币化名性的一个体现。

### 第 4 步　某个矿工把它打包　（矿工线程）

假设 Alice 的节点在挖矿。`miner.create_new_block` 从内存池里取出这笔交易，
用 `connect_inputs` 在草稿上再验证一遍，算出手续费，装进候选区块，然后开始掷骰子（第 9 章）。
几万次哈希之后，`哈希 ≤ target`——挖到了。

### 第 5 步　区块被接受　（还是矿工线程）

`miner.found_block` → `chain.process_block(block)`：

```text
process_block
 ├─ check_block：工作量证明、默克尔根、时间戳……                               第 10 章
 ├─ accept_block：难度对吗？时间戳晚于中位数吗？→ 追加写入 blk0001.dat
 └─ _add_to_block_index：比当前最佳链高 → connect_block
      ├─ 对区块里每笔交易的每个输入：connect_inputs（存在？未花？签名？）
      ├─ coinbase 的金额 ≤ 补贴 + 手续费？
      ├─ 把改动写进交易索引；数据库 commit
      ├─ 从内存池里删掉已经上链的交易
      ├─ 通知钱包（tx_listeners）：Alice 的钱包记下"这笔交易进了区块 #126"
      └─ 通知网络（block_listeners）：向所有邻居通告这个新区块
```

### 第 6 步　区块到达 Bob

```text
   Alice -> Bob : inv       37 字节    "我有一个新区块"
   Bob -> Alice : getdata   37 字节
   Alice -> Bob : block    450 字节
```

Bob 的节点同样不相信 Alice，把第 5 步的全部检查**独立地重做一遍**。通过之后：

* Bob 的交易索引里，那 10 BTC 的输出正式登记在册，`spent` 是 `null`——现在它是 Bob 的一个 UTXO 了；
* Bob 的内存池里那笔交易被移除；
* Bob 的钱包得到通知，记下这笔交易所在的区块；
* 界面刷新，那一行变成 `1/未确认`。

### 第 7 步　等待

之后每出一个新区块，这笔交易的确认数就加 1。界面定时器发现区块高度变了，就刷新列表。
第 6 个区块之后，状态从 `5/未确认` 变成 `6 个确认`（原版界面是 "6 blocks"）。

根据第 10 章的计算，此时这笔交易被逆转的概率，在攻击者算力不超过 10% 的前提下，小于万分之三。

## 14.4　界面：一条硬规则

`qt/` 目录下的代码不涉及任何比特币的逻辑，只是把节点的状态画出来。值得一提的只有一点——**线程安全**。

钱包的监听器是在网络线程或矿工线程里被回调的，而 Qt 规定：**界面上的任何东西都只能在主线程里操作。**
所以回调函数里只做一件事：

```python
    def _mark_dirty(self):
        # 这个函数会在网络线程 / 矿工线程里被调用：只改标志，绝不碰任何 Qt 对象
        self._wallet_dirty = True
```

真正刷新界面的活，由主线程里每 0.5 秒触发一次的定时器 `_poll()` 来做。这也是原版 wxWidgets 界面的做法。

## 14.5　关闭

```python
    def stop(self):
        self.stop_event.set()
        self.miner.stop()           # 停止挖矿
        self.net.stop()             # 断开所有连接
        ...
        self.wallet.close()
        self.chain.close()          # 提交并关闭数据库
```

区块链和钱包的每一次修改都已经在发生时落盘了，所以关闭时没有什么需要"保存"的。
内存池里的东西会丢——没关系，下次启动时钱包会把自己的交易重新放回去。

## 14.6　压轴实验：在真实网络上尝试双花

```powershell
python labs\lab12_double_spend.py
```

现在你已经具备了看懂这个实验的全部知识。它回到了第 1 章提出的那个问题：

> 坏人 Mallory 有一枚 50 BTC 的币。她同时签了两笔交易：甲付给 Bob，乙付给 Carol。
> 她把甲直接塞给 Bob 的节点，把乙直接塞给 Carol 的节点。

两笔交易的**签名都完全合法**（第 4、7 章）——密码学只能证明"这是 Mallory 签的"，证明不了"她没有再签给别人"。

```text
   Bob 的内存池：  ['甲']
   Carol 的内存池：['乙']
   此刻 Bob 的钱包显示余额 50.00，Carol 的钱包也显示 50.00 —— 两个人都以为自己收到钱了！
```

两个节点互相转发了自己收到的交易，但都因为"和池里已有的交易冲突"而拒绝了对方的那笔（第 11 章）。
**此时全网并没有达成一致。** 裁决来自下一个区块：

```text
   Carol 的区块包含交易乙；Bob 的节点验证后接受了这个区块（高度 102）
   交易乙的确认数：1；交易甲的确认数：0
```

从这一刻起，交易甲永远无法上链了——它要花的钱在区块链上已经被花掉（第 10 章的双花检查）。
如果 Bob 在看到"零确认"的余额时就发了货，他就被骗了。

**这个实验浓缩了整个比特币的逻辑：**

1. 数字签名解决了"谁有权花这笔钱"，但解决不了"这笔钱有没有被花过两次"；
2. 每个节点"先到先得"的临时判断不足以形成共识——不同的节点看到的先后顺序不一样；
3. **区块链是全网对"交易先后顺序"的唯一权威记录**；
4. 要推翻这个记录，必须重做工作量证明，并且跑赢全网——这就是"等待确认"的意义。

## 本章小结

* 节点 = 区块链 + 内存池 + 钱包 + 矿工 + 网络，用监听器松散地连接。
* 一笔付款的旅程：钱包组装并签名 → 自己的内存池 → `inv/getdata/tx` 传给邻居 → 每个节点**独立验证** →
  矿工打包 → 工作量证明 → `process_block` → 写入账本 → `inv/getdata/block` 传给邻居 → 每个节点再次**独立验证** → 确认数逐渐增加。
* **没有任何一步依赖于"信任对方"。**
* 界面只读取状态；跨线程只传递一个标志。

## 思考题

1. 在 14.3 节的旅程里，Alice 的这笔交易一共被验证了几次签名？（假设只有 Alice 和 Bob 两个节点，Alice 是矿工。）
2. Bob 的窗口里显示"来自：未知"。Bob 有办法知道是谁付的钱吗？
3. 如果 Alice 发出交易后立刻关机，这笔交易还能被确认吗？分几种情况讨论。

<details><summary>参考答案</summary>

1. 至少 5 次：① Alice 的钱包签完名后自己验一次（`sign_signature` 末尾）；② Alice 的内存池接受时；
   ③ Bob 的内存池接受时；④ Alice 的矿工组装区块时（`connect_inputs`）；⑤ Alice 的 `connect_block`；
   ⑥ Bob 的 `connect_block`。所以其实是 6 次。冗余，但每个环节都不信任上一个环节。
2. 从协议本身不能。他只能看到钱来自哪些输出（哪些公钥/地址），而地址和真实身份之间没有登记关系。
   通常是付款人通过别的渠道（邮件、网页订单）告诉收款人"我付了"。原版为此设计过"按 IP 直接付款"功能，
   可以附带留言，但后来被移除了。
3. ① 关机前已经传给了至少一个邻居：没问题，交易会在网络里继续传播、被打包。
   ② 没来得及传出去：交易只存在于 Alice 的钱包里。她下次开机时，`reaccept_wallet_transactions` 会把它放回内存池，
   之后的新区块到来时 `relay_wallet_transactions` 会把它重新广播（最多每 10 分钟一次）。
</details>

[← 上一章](ch13-network.md)　|　[下一章：比特币的思想与经济学 →](ch15-ideas.md)
