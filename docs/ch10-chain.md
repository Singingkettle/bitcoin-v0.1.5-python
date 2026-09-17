# 第 10 章　区块链与共识：最长链、分叉与重组

> **本章目标**：这是全教程的核心。你将弄明白一个节点收到区块后要做哪些检查、
> "账本"在节点里到底是怎么存的、出现分叉时听谁的、链重组时发生了什么，以及"等 6 个确认"背后的数学。
>
> **对应源码**：[bitcoin/blockchain.py](../bitcoin/blockchain.py)、[bitcoin/db.py](../bitcoin/db.py)
> 　　**实验**：[labs/lab09_chain_reorg.py](../labs/lab09_chain_reorg.py)

## 10.1　没有人宣布"官方版本"

比特币网络里没有任何一台机器保存着"官方的"区块链。每个节点都保存着**自己的一份**，
并且遵循同一套规则独立地做判断：

1. 收到一个区块，**按规则验证**它。不合规则的，不管是谁发来的，一律丢弃。
2. 在所有合规的链里，**认最长的那一条**。

因为所有诚实节点执行的规则完全相同，所以只要它们看到了相同的区块，就会得出相同的结论。
**"共识"不是投票投出来的，而是大家各自独立计算、殊途同归的结果。**

`blockchain.py` 就是这套规则的实现。这个文件对应原版 `main.cpp` 里最核心的几个函数，函数名都保留了：

```text
process_block          ProcessBlock         收到一个区块的总入口
 ├─ check_block        CheckBlock           不依赖上下文的检查
 └─ accept_block       AcceptBlock          依赖上下文的检查 + 写盘
     └─ _add_to_block_index   AddToBlockIndex    登记索引；必要时切换最佳链
         ├─ connect_block     ConnectBlock       让一个区块"生效"
         │   └─ connect_inputs ConnectInputs     逐个检查交易的输入
         └─ reorganize        Reorganize         链重组
             └─ disconnect_block  DisconnectBlock    让一个区块"失效"
```

## 10.2　账本在节点里是怎么存的

先看数据。一个节点要回答的核心问题是：**"某笔交易的第 n 个输出，现在还能不能花？"**

[db.py](../bitcoin/db.py) 里有三样东西（原版用的是 Berkeley DB，这里换成了 Python 自带的 sqlite，结构是对应的）：

**① `blk0001.dat`——区块仓库。** 收到的每一个合法区块都原样追加到这个文件末尾，永不修改。

**② 区块索引表 `blockindex`**——每个区块一行：哈希、父块哈希、高度、在 `blk0001.dat` 里的位置。
节点启动时把它全部读进内存，重建出由 `CBlockIndex` 组成的那棵树（第 8 章）。

**③ 交易索引表 `txindex`——这就是"账本"。** 每一笔**在最佳链上**的交易一行：

```text
txhash      这笔交易的哈希
blockhash   它在哪个区块里
txn         是那个区块的第几笔交易
spent       一个数组，每个输出一格：null = 还没被花；否则写着花掉它的那笔交易的哈希
```

例如一笔有两个输出的交易，第 0 个输出已经被花了、第 1 个还没有，它的 `spent` 就是 `["ab12…", null]`。

**所谓 UTXO（未花费的交易输出），就是这张表里所有还是 `null` 的格子。** 判断双花，就是看格子是不是 `null`。

还有一个只有一行的小表 `kv`，记着"当前最佳链的链尖是哪个区块"（`hashBestChain`）。

> 原版的 `CTxIndex` 里，`vSpent` 存的是花费者在磁盘上的位置，这里存的是花费者的哈希，作用相同。

### 事务：要么全做，要么全不做

连接一个区块要改很多行：区块里每笔交易各加一行，它们花掉的每个输出各改一格。
如果改到一半程序崩溃了（或者发现区块里第 50 笔交易是无效的），账本就会处在一个"半新半旧"的错乱状态。

数据库的**事务**（transaction，和比特币的"交易"是同一个英文词，但意思不同）解决了这个问题：
一批修改先攒着，最后要么 `commit()`（提交，全部生效），要么 `rollback()`（回滚，全部撤销）。

```python
class BlockIndexDB:
    def commit(self):   ...     # 对应原版的 TxnCommit
    def rollback(self): ...     # 对应原版的 TxnAbort
```

## 10.3　第一关：check_block

不需要查账本、只看区块自己就能做的检查。孤块（见 10.5）在暂存之前也要先过这一关：

```python
    def check_block(self, block: CBlock) -> bool:
        if not block.vtx:                                   # 1. 至少有一笔交易
        if len(block.serialized()) > params.MAX_SIZE:       # 2. 不能太大
        if block.n_time > util.get_adjusted_time() + 2 * 60 * 60:   # 3. 时间戳不能超前 2 小时以上
        if not block.vtx[0].is_coinbase():                  # 4. 第一笔必须是 coinbase
        if any(tx.is_coinbase() for tx in block.vtx[1:]):   # 5. 而且只能有这一笔 coinbase
        for tx in block.vtx: tx.check_transaction()         # 6. 每笔交易各自的基本检查
        if not block.check_proof_of_work():                 # 7. 工作量证明
        if block.hash_merkle_root != block.get_merkle_root():       # 8. 默克尔根对得上
```

第 7 条是**防垃圾的闸门**：要让节点花力气去处理一个区块，发送者必须先付出真实的算力。

## 10.4　第二关：accept_block

需要知道"它接在谁后面"才能做的检查：

```python
    def accept_block(self, block: CBlock) -> bool:
        prev = self.map_block_index.get(block.hash_prev_block)
        if prev is None: ...                                        # 父块必须已知
        if block.n_time <= prev.get_median_time_past(): ...         # 时间戳必须晚于前 11 块的中位数
        if block.n_bits != self.get_next_work_required(prev): ...   # 难度必须恰好是"应有"的那个值

        pos = self.block_file.append(block.serialized())            # 通过 -> 写盘
        return self._add_to_block_index(block, pos, prev)           #      -> 登记索引
```

**时间戳的两道防线**：不能超前本机时间 2 小时（第一关），也不能早于"前 11 个区块时间戳的中位数"。
用中位数而不是"上一个区块的时间"，是为了防止单个矿工乱填时间戳把后面的人卡住——
11 个数里有一两个离谱的，中位数不受影响。

**难度必须精确相等**：矿工不能自己挑一个容易的难度。

## 10.5　孤块

网络传输不保证顺序。节点可能先收到第 105 号区块，而 101~104 还在路上。
这种"父块未知"的区块叫**孤块**（orphan）。丢掉太可惜（回头还得再要一遍），所以先存起来：

```python
            if block.hash_prev_block not in self.map_block_index:
                self.map_orphan_blocks[h] = block
                self.map_orphan_blocks_by_prev[block.hash_prev_block].append(block)
                return True
```

`map_orphan_blocks_by_prev` 是一张"谁在等谁"的表。当某个区块终于被接受时，
查一下有没有孤块在等它；有的话接上，再查有没有孤块在等**那个**……一路连锁反应下去：

```python
            work_queue = [h]
            while work_queue:
                prev = work_queue.pop(0)
                for orphan in self.map_orphan_blocks_by_prev.pop(prev, []):
                    ohash = orphan.get_hash()
                    del self.map_orphan_blocks[ohash]
                    if self.accept_block(orphan):
                        work_queue.append(ohash)
```

同时节点会主动向发来孤块的那个邻居请求缺失的部分。这时用到一个函数 `get_orphan_root`：
顺着孤块链往回走，找到"最老的那个孤块"。它的哈希会被用在请求里，意思是"请把我的链尖之后、直到这个块之前的所有区块都发给我"
（第 13 章）。

## 10.6　第三关：connect_block——让区块"生效"

登记了索引之后，如果新区块正好接在当前最佳链的末尾，就要**连接**它：把它里面的交易真正记到账本上。

```python
    def connect_block(self, block, pindex, events) -> bool:
        test_pool = {}          # 本区块内的"草稿"：改动先记在这里
        mem_txs = {}            # 本区块里已经处理过的交易
        fees = 0
        for n, tx in enumerate(block.vtx):
            if not tx.is_coinbase():
                fee = self.connect_inputs(tx, test_pool, pindex.n_height, mem_txs=mem_txs)
                if fee is None:
                    return False                    # 任何一笔交易有问题，整个区块作废
                fees += fee
            test_pool[txhash] = {..., "spent": [None] * len(tx.vout)}    # 登记本交易的输出
            mem_txs[txhash] = tx
        # coinbase 不能多拿：补贴 + 本区块的全部手续费
        if block.vtx[0].get_value_out() > params.block_value(self.best_height, fees):
            return False
        for txhash_hex, rec in test_pool.items():           # 全部通过，才把草稿写进数据库
            self.db.write_tx_index(...)
```

**`test_pool`（草稿）的作用**：同一个区块里，后面的交易可以花前面交易刚产生的输出；
但在整个区块验证完之前又不能动真正的账本。所以改动先记在草稿上，查账时"先查草稿、再查数据库"。

对每笔交易的每个输入，`connect_inputs` 依次检查（这就是第 6 章末尾列的那几关）：

```python
            rec = test_pool.get(prev_hex) or self.db.read_tx_index(prev_hex)
            if rec is None:                                     # ① 前序交易存在吗
            if txin.prevout.n >= len(prev_tx.vout):             # ② 输出序号越界了吗
            if prev_tx.is_coinbase():
                if spend_height - prev_height < 100:            # ③ 如果花的是 coinbase，成熟了吗
            if not verify_signature(prev_tx, tx, n_in):         # ④ 钥匙打得开锁吗（第 7 章）
            if rec["spent"][txin.prevout.n] is not None:        # ⑤ 这笔钱被花过了吗 —— 防双花
            rec["spent"][txin.prevout.n] = txhash_hex           #    标记为已花
            value_in += prev_tx.vout[txin.prevout.n].n_value

        fee = value_in - tx.get_value_out()
        if fee < 0:                                             # ⑥ 输出总额不能超过输入总额
```

第 ⑤ 条就是**双花检查**的全部——一次查表。这就是 UTXO 模型的简洁之处。

> **创世块的那 50 BTC 为什么花不了？** 看 `_load_or_create()`：创世块是直接写入的，
> **从不经过 `connect_block`**，所以它的 coinbase 交易从未被登记进 `txindex`。
> 任何想花它的交易都会在第 ① 关失败："找不到前序交易"。
> 原版 v0.1.5 就是这样，真实的比特币至今如此——中本聪是有意还是无心，没有人知道。

## 10.7　分叉与最长链

两个矿工几乎同时挖出了第 102 号区块 A1 和 B1。它们都合法，都指向第 101 号。
网络上一部分节点先收到 A1，另一部分先收到 B1。此时区块链**分叉**了：

```text
                     ┌── A1
    … ── 100 ── 101 ─┤
                     └── B1
```

规则很简单：**每个节点接着自己先收到的那个往下挖，但两个都记着。**

```python
        # 0.1.5 选最佳链只比高度（"累计工作量"是后来版本才引入的）
        if pindex.n_height <= self.best_height:
            self.db.commit()
            return True         # 只是某条较短支链上的块：记下来就行
```

B1 的高度和当前最佳高度一样（都是 102），不比它**更高**，所以只登记、不切换——**先到先得**。

僵局在下一个区块打破。假设下一个挖出的区块 B2 接在了 B1 后面：

```text
                     ┌── A1
    … ── 100 ── 101 ─┤
                     └── B1 ── B2          ← 高度 103 > 102，B 链更长了
```

所有节点（包括原来站在 A 这边的）都会切换到 B 链。A1 成了废块，挖出它的矿工白干了，
A1 里的交易（如果 B 链里没有）退回内存池，等待被重新打包。

**为什么大家都愿意遵守"最长链"规则？** 因为矿工的奖励只有在自己的区块留在最长链上时才有效。
继续在一条已经落后的链上挖，挖出来的奖励别人都不承认，纯属浪费电费。
所以跟随最长链是每个矿工出于**自身利益**的理性选择——不需要任何人来强制。

> **0.1.5 与后来版本的一个区别**：这里比的是**高度**（区块的个数）。
> 2010 年 7 月的版本改成了比较**累计工作量**（每个区块的难度之和）。
> 区别在于：按高度的话，攻击者可以伪造一条"区块很多但每个区块难度极低"的链来冒充最长链；
> 按工作量就不行了。在难度恒定的时候两者等价。白皮书里说的"最长链"，准确的含义其实就是"工作量最大的链"。

## 10.8　链重组

从 A 链切换到 B 链的过程叫**重组**（reorganize）。它是 `blockchain.py` 里最绕的一个函数，但思路很清楚：

```text
  1. 找分叉点：两个指针分别从旧链尖、新链尖沿 pprev 往回走，直到相遇        （这里是 101）
  2. 断开旧分支：从旧链尖倒着走到分叉点，对每个区块 disconnect_block          （A1）
       - 把它花掉的输出重新标成"未花费"
       - 把它的交易从 txindex 里删掉
       - 记下这些交易，待会儿放回内存池
  3. 连接新分支：从分叉点顺着走到新链尖，对每个区块 connect_block             （B1、B2）
       - 如果其中任何一个失败 → 回滚数据库，一切恢复原状，把无效的块清出索引
  4. 全部成功 → 提交数据库；修改内存里的 pnext 指针和 main_chain
  5. 被断开的交易放回内存池；新链上已确认的交易从内存池删除
```

第 3 步的"失败则完整回滚"非常重要。看 `reorganize()` 里的这一段：

```python
            if not self.connect_block(block, pindex, pending_events):
                # 新分支里有无效区块：撤销一切，并把这个块及其后面的块从索引里清掉
                self.db.rollback()
                for bad in v_connect[i:]:
                    self.map_block_index.pop(bad.hash, None)
                    self.db.erase_block_index(uint256_to_hex(bad.hash))
                self.db.commit()
                return self._error_none("Reorganize: ConnectBlock 失败")
```

设想攻击者造了一条更长的链，但其中一个区块里藏着双花。节点要到重组进行到那个区块时才会发现。
这时旧分支已经被断开了一半——如果不回滚，账本就坏了。
`tests/test_reorg.py::test_failed_reorg_is_rolled_back_completely` 专门测试这个场景：
重组失败后，链尖、主链、交易索引、内存池全部和重组之前一模一样。

还有一个设计细节：给钱包的通知（"某笔交易被连接/断开了"）先攒在 `events` 列表里，
**等数据库提交成功之后**才统一发出。否则重组失败回滚了，钱包却已经收到了错误的通知。

## 10.9　实验 9：一次真实的双花攻击

```powershell
python labs\lab09_chain_reorg.py
```

这个实验把上面的理论变成了一场完整的攻击演示：

```text
4. 攻击者付 50 BTC 给商家；交易被打包进区块 A1，商家看到"1 个确认"就发货了
   付款交易的确认数：1

5. 与此同时，攻击者偷偷从 A1 之前的位置另挖一条链（B 链），里面没有那笔付款
   主链：… <- #101 <- #102:3c7239        <- B1 和 A1 一样高：先到先得，主链不变

6. 攻击者的 B 链又长了一块：B 链更高了，所有节点自动切换过去（链重组）
   主链：… <- #101 <- #102:f38047 <- #103:ef20e4      <- 现在主链是 B1、B2
   付款交易的确认数：0  —— 商家收到的钱"消失"了

7. 攻击者在 B 链上把同一笔钱付给自己——商家那笔就永远无效了（双花成功）
```

**商家的货发出去了，钱却没了。**

在实验里攻击者轻松得手，是因为他是唯一的矿工。在真实网络里，他必须在诚实矿工们不停往 A 链上添加区块的同时，
独自把 B 链挖得**比 A 链还长**。这有多难？

## 10.10　为什么是"6 个确认"

一笔交易所在的区块之上每多盖一个区块，就说它多了一个**确认**（confirmation）。
交易刚进区块是 1 个确认，上面又盖了 5 个区块就是 6 个确认。

```python
    def get_block_depth(self, block_hash: int) -> int:
        """某个区块在主链上的"深度"（确认数）：链尖自己是 1。不在主链上返回 0。"""
        ...
        return self.best_height - pindex.n_height + 1
```

白皮书第 11 节做了一个计算。设攻击者掌握全网算力的比例是 q（诚实矿工是 p = 1 − q）。
商家等了 z 个确认才发货，此时攻击者偷偷挖的那条链落后 z 个区块。他能追上并反超的概率是多少？

这是一个"赌徒破产"问题：每出一个新区块，有 q 的概率是攻击者挖的（差距缩小 1），有 p 的概率是诚实方挖的（差距扩大 1）。
当 q < p 时，从落后 z 块追平的概率是 (q/p)ᶻ——**随 z 指数下降**。
再考虑到在商家等待的这段时间里攻击者已经挖出了若干个块（服从泊松分布），综合起来的公式是：

```python
def attacker_success(q: float, z: int) -> float:
    p = 1 - q
    lam = z * q / p                         # 诚实链挖 z 块期间，攻击者期望挖出的块数
    total = 1.0
    for k in range(z + 1):
        poisson = math.exp(-lam) * lam**k / math.factorial(k)
        total -= poisson * (1 - (q / p) ** (z - k))
    return total
```

实验 9 第 8 步算出的表（q = 10% 这一列与白皮书里印的数字逐位相同）：

```text
   确认数 z     q=10%         q=30%         q=45%
   0           1.0000000     1.0000000     1.0000000
   1           0.2045873     0.6277491     0.9197758
   2           0.0509779     0.4457171     0.8777174
   3           0.0131722     0.3245841     0.8443950
   6           0.0002428     0.1321112     0.7661055
   10          0.0000012     0.0416605     0.6854240
   50          0.0000000     0.0000006     0.2800388
```

怎么读这张表：

* 攻击者有 10% 的算力、你等 6 个确认：被双花的概率约**万分之二**。这就是"6 个确认"这个经验数字的出处。
* 但如果攻击者有 45% 的算力，等 50 个确认仍有 28% 的概率被逆转。
* 如果 q ≥ 50%，攻击者**必然**能追上，等多少个确认都没用。这就是所谓的"**51% 攻击**"。

所以比特币的安全性有一个明确的前提：**没有任何人控制接近一半的算力。**

同时也请注意 51% 攻击者**不能**做什么：他不能花别人的钱（没有私钥）、不能凭空造币（其他节点会拒绝违规的区块）、
不能改变任何规则。他能做的只有一件事：把**自己**最近花出去的钱撤回来再花一次。

## 10.11　回头看 coinbase 成熟期

现在可以完整回答上一章留下的问题了。普通交易如果因为重组被移出了区块，它会回到内存池，通常很快又会被打包——
交易本身仍然是有效的。

但 coinbase 交易不一样：**它只在那个特定的区块里才有效。** 区块被重组掉了，那笔 coinbase 就彻底不存在了。
如果这时已经有人花了它，下游所有的交易都变成了"花一笔不存在的钱"，全部作废，会牵连一串无辜的人。

要求等 100 个区块，就是等到"这个区块被重组掉的概率小到可以忽略"。

## 本章小结

* 没有"官方版本"；每个节点独立验证、独立选择，规则相同所以结论相同。
* 账本 = **交易索引**：每笔交易的每个输出一个格子，`null` 表示未花费。**双花检查就是查这个格子。**
* 区块要过三关：`check_block`（自身）→ `accept_block`（上下文）→ `connect_block`（账本）。
  任何一笔交易无效，整个区块作废。
* 父块未知的区块先作为**孤块**暂存，父块到了再连锁接上。
* 分叉时先到先得；一旦某条链**更长**，所有节点切换过去——**重组**：断开旧分支、连接新分支，
  失败则数据库**完整回滚**。
* 确认数越多，被逆转的概率指数下降；前提是没人掌握近半数算力。

## 思考题

1. 为什么 `check_block` 里工作量证明的检查是必不可少的"防垃圾闸门"？如果去掉它，攻击者能怎样轻松地搞垮一个节点？
2. 重组时，旧分支上的交易被"放回内存池"。有没有哪种交易放不回去？
3. 假如你经营一家卖咖啡的小店（一杯 30 元）和一家卖汽车的店（一辆 30 万），你分别会等几个确认？为什么？

<details><summary>参考答案</summary>

1. 去掉之后，攻击者可以不花任何代价，每秒发来成千上万个伪造的区块，节点要为每一个做签名验证、写磁盘。
   有了工作量证明，每个能让节点"认真对待"的区块都得先付出真实的算力成本。
2. 有两种：一是 coinbase（它离开了原来的区块就不存在了）；二是和新链上的某笔交易冲突的交易
   （比如实验 9 里商家那笔——它要花的钱在新链上已经被花掉了）。后者会留在内存池里，但永远不会被打包。
3. 咖啡店可以接受 0~1 个确认：为了 30 块钱去发动一次需要巨大算力的攻击不划算，攻击成本远高于收益。
   汽车店应该等 6 个甚至更多。**需要的确认数取决于交易金额和攻击成本的对比**，没有统一的标准答案。
</details>

[← 上一章](ch09-mining.md)　|　[下一章：内存池与手续费 →](ch11-mempool.md)
