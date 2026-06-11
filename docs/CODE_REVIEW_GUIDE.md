# 源码阅读导读：从哪里开始 review 这套代码

这是一份按**依赖顺序**编排的源码阅读路线图。整个仓库约 3500 行实现代码 +
2000 行测试，分 7 站读完。每一站的格式固定：

> **为什么先读它 → 核心代码讲解 → 与原版 v0.1.5 对照 → 配套测试 → 动手实验 → 自检问题**

7 站恰好对应仓库的 7 个 phase 提交，所以你也可以用
`git log --oneline --reverse` 把仓库"倒带"，一个提交一个提交地读：

```text
Phase 1: primitives - hashes, serialization, base58, keys   <- 第 1 站
Phase 2: transactions, blocks, and the script interpreter   <- 第 2 站
Phase 3: storage, chain state, mempool, private-net genesis <- 第 3 站
Phase 4: wallet and miner                                   <- 第 4 站
Phase 5: P2P networking                                     <- 第 5 站
Phase 6: PySide6 GUI                                        <- 第 6 站
Phase 7: README, license, two-node demo script
```

## 0. 准备工作：先跑起来，再开始读

不要干读。先把它跑起来，建立"这玩意确实能工作"的体感：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest tests -q          # 约 97 个测试，~45 秒（含真实双节点 socket 同步）
.\demo_two_nodes.ps1     # 双 GUI 节点；在 A 上勾选 Options -> Generate Bitcoins
```

每一站的推荐节奏：**先跑该站的测试 → 读测试（测试就是这站的"需求文档"）→
读实现 → 做动手实验 → 回答自检问题**。

---

## 1. 全局地图

### 1.1 分层架构（依赖只朝下）

```text
                 ┌──────────────────────────────┐
   第 6 站       │  qt/  (PySide6 界面)          │ run_gui.py
                 └──────────────┬───────────────┘
                 ┌──────────────▼───────────────┐
   第 5 站       │  node.py  (消息处理/装配)      │ run_node.py
                 │  net.py   (socket/分帧/CNode) │ config.py
                 └──────┬───────────────┬───────┘
                 ┌──────▼──────┐ ┌──────▼───────┐
   第 4 站       │  wallet.py  │ │   miner.py   │
                 └──────┬──────┘ └──────┬───────┘
                 ┌──────▼───────────────▼───────┐
   第 3 站       │ blockchain.py    mempool.py  │
                 │           db.py              │
                 └──────────────┬───────────────┘
                 ┌──────────────▼───────────────┐
   第 2 站       │  tx.py   block.py  script.py │
                 └──────────────┬───────────────┘
                 ┌──────────────▼───────────────┐
   第 1 站       │ serialize.py hashes.py       │
                 │ base58.py key.py ripemd160.py│
                 └──────────────┬───────────────┘
                 ┌──────────────▼───────────────┐
   第 0 站       │   params.py      util.py     │
                 └──────────────────────────────┘
```

各层之间用**监听器（回调列表）**解耦，方向自下而上：

```text
chain.tx_listeners      ──> wallet._on_chain_tx   (交易进/出区块时记账)
chain.block_listeners   ──> node._on_new_best_block (新最佳块 -> 向所有节点 relay inv)
mempool.listeners       ──> wallet._on_mempool_tx + node._on_pool_tx (0确认收款 + 交易转发)
wallet.changed_listeners──> GUI 置脏标志 (主窗口 QTimer 轮询刷新)
```

### 1.2 两条主数据流（读完全部 7 站后回头再看一遍）

**一笔交易的一生**：
`wallet.send_money` → `create_transaction`（选币/找零/签名/凑手续费）→
`commit_transaction` → `mempool.accept`（验签/查双花）→ 监听器触发 →
`node` 向各节点发 `inv` → 对端 `getdata` → 对端 `tx` 消息 → 对端
`mempool.accept` → 某个矿工 `miner.create_new_block` 打包 → 解出 PoW →
`chain.process_block` → `connect_block` 写入 tx 索引、从内存池删除 →
`wallet._on_chain_tx` 看到确认。

**一个区块的一生**：
`miner._mine_loop` 解出 nonce → `chain.process_block` →
`check_block`（无上下文检查）→ `accept_block`（难度/时间戳上下文检查）→
写入 `blk0001.dat` → `_add_to_block_index` → 高度更高则
`_set_best_chain`（直接延伸 → `connect_block`；分叉超车 → `reorganize`）→
`block_listeners` → 全网 `inv` 广播。

### 1.3 C++ → Python 对照（含阅读站点）

| v0.1.5 原文件 | 本仓库 | 读 |
|---|---|---|
| `serialize.h` | [bitcoin/serialize.py](../bitcoin/serialize.py) | 第 1 站 |
| `base58.h` | [bitcoin/base58.py](../bitcoin/base58.py) | 第 1 站 |
| `key.h`, `sha.cpp` | [bitcoin/key.py](../bitcoin/key.py), [bitcoin/hashes.py](../bitcoin/hashes.py), [bitcoin/ripemd160.py](../bitcoin/ripemd160.py) | 第 1 站 |
| `script.h/.cpp` | [bitcoin/script.py](../bitcoin/script.py) | 第 2 站 |
| `main.h`（数据结构半边） | [bitcoin/tx.py](../bitcoin/tx.py), [bitcoin/block.py](../bitcoin/block.py) | 第 2 站 |
| `db.cpp`（Berkeley DB） | [bitcoin/db.py](../bitcoin/db.py)（sqlite + JSON） | 第 3 站 |
| `main.cpp`（共识半边） | [bitcoin/blockchain.py](../bitcoin/blockchain.py), [bitcoin/mempool.py](../bitcoin/mempool.py) | 第 3 站 |
| `main.cpp`（钱包/矿工半边） | [bitcoin/wallet.py](../bitcoin/wallet.py), [bitcoin/miner.py](../bitcoin/miner.py) | 第 4 站 |
| `net.h/.cpp` + `main.cpp` 的 ProcessMessage | [bitcoin/net.py](../bitcoin/net.py), [bitcoin/node.py](../bitcoin/node.py) | 第 5 站 |
| `util.h/.cpp` | [bitcoin/util.py](../bitcoin/util.py), [bitcoin/config.py](../bitcoin/config.py), [bitcoin/params.py](../bitcoin/params.py) | 第 0 站 |
| `ui.cpp/uibase.cpp`（wxWidgets） | [qt/](../qt/)（PySide6） | 第 6 站 |
| `irc.cpp`, `market.cpp` | （刻意省略，见 README 偏离表） | — |

---

## 2. 第 0 站：全局约定 — `params.py` + `util.py`

**为什么先读它**：后面所有模块都 import 它；并且本仓库的三个全局表示约定
都在这里定调，不先弄清会处处别扭。

**核心代码讲解**

- 三个全局约定（**先记住，贯穿全仓库**）：
  1. **uint256 用 Python int 表示**（区块哈希、交易哈希、target 都是 int）；
  2. **金额是 satoshi 整数**，`COIN = 100_000_000`，绝不用浮点；
  3. **脚本字段在 tx/block 层是裸 bytes**，只有 `script.py` 把它包装成
     `CScript`（对应原版 main.h / script.h 的解耦，也避免了循环 import）。
- `params.py:65 compact_to_target` / `params.py:74 target_to_compact`：
  nBits 紧凑难度编码（指数+尾数，对应 CBigNum::SetCompact/GetCompact），
  注意尾数最高位是符号位所以有 `0x00800000` 的规避分支。
- `params.py:59 block_value`：`50*COIN >> (height // 210_000)` —— 减半就这一行。
- 私网偏离项**全部集中**在这个文件：`DEFAULT_PORT = 18444`、
  `PROOF_OF_WORK_LIMIT = ~0 >> 8`、`GENESIS_*` 一组常量（由
  [tools/mine_genesis.py](../tools/mine_genesis.py) 生成回填）。
- `util.py`：`format_money`/`parse_money`（仿 FormatMoney 的去尾零格式）、
  `get_adjusted_time`（私网无需网络校时，等于 `get_time`）。

**与原版对照**：常量名与数值逐一对应 main.h / net.h / serialize.h；
唯一改动是端口与 PoW 上限（README 偏离表）。

**配套测试**：`tests/test_genesis_vector.py::test_compact_target_roundtrip`。

**动手实验**：REPL 里算一下
`hex(params.compact_to_target(0x1f00ffff))`，确认 target ≈ 2^240，
所以全空间 2^256 命中率 ≈ 1/65536 —— 这就是"私网 0.1 秒出块"的数学来源。

**自检问题**

1. 为什么金额必须用整数 satoshi 而不能用 float？
2. `0x1d00ffff`（主网创世难度）和 `0x1f00ffff`（本私网）的 target 差多少倍？
<details><summary>答案</summary>

1. 浮点有舍入误差，金额参与共识（手续费 = 输入-输出），任何节点算出不同结果都会导致分叉。
2. 指数差 2（0x1f-0x1d），即 256² = 65536 倍 —— 主网创世难度本身又是"每哈希 1/2^32 命中"，私网放宽到 1/2^16。
</details>

---

## 3. 第 1 站：原语层 — `serialize.py` / `hashes.py` / `ripemd160.py` / `base58.py` / `key.py`

**为什么先读它**：比特币的一切结构都落到"怎么变成字节、怎么哈希"。
这一层错一个字节，上面全部塌方 —— 所以它的测试全是**外部已知向量**。

**核心代码讲解**

- `serialize.py:46 DataStream`：仿 CDataStream 的"写在尾部、读有游标"缓冲。
  注意所有标量都是**小端**（`struct` 格式串全带 `<`）。
- `serialize.py:17 ser_compact_size`：变长长度前缀
  （<253 直接 1 字节；0xFD+u16；0xFE+u32；0xFF+u64），读侧在
  `read_compact_size` 里有 `MAX_SIZE`（32 MiB）防御。
- **全仓库最大的新手坑 —— uint256 字节序**：
  - 链上/序列化是**小端 32 字节**（`uint256_to_bytes`）；
  - 人类显示的哈希十六进制是**大端**（`uint256_to_hex` 做了 `[::-1]` 反转）；
  - 所以创世哈希 `00001fce...` 开头的零在*显示*的最前面，但在*字节流*的最后面。
  `tests/test_serialize.py::test_uint256_hex_is_byte_reversed` 专门钉死这条。
- `hashes.py`：`hash256` = SHA256d（区块/交易哈希），`hash160` =
  RIPEMD160(SHA256(x))（地址）。`_ripemd160` 先试 hashlib，OpenSSL 3
  抛 ValueError 时回退到纯 Python 实现 `ripemd160.py`（标准五轮并行结构，
  用 Bosselaers 官方向量验证）。
- `base58.py`：Base58Check = payload + SHA256d 前 4 字节校验，
  地址版本字节 0；`encode` 里前导零字节 → 前导 `'1'` 的规则别漏看。
- `key.py:15 CKey`：包装纯 Python `ecdsa` 库。0.1.5 只认识**非压缩公钥**
  （65 字节 `04||X||Y`，`get_pubkey`）；签名是 DER。一个有意偏离：
  `sign` 用 RFC 6979 确定性 nonce 代替 OpenSSL 随机 k（线格式相同，
  且杜绝了重复 k 泄私钥的坑）。

**与原版对照**：serialize.h 的 SER_NETWORK/SER_DISK/SER_GETHASH 标志保留在
DataStream 构造参数上（真正用到的地方是第 5 站 CAddress 的两种编码）。

**配套测试**：`test_serialize.py`、`test_hashes.py`（RIPEMD 官方向量 +
主网创世块头 SHA256d）、`test_base58.py`（中本聪创世地址
`1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa`）、`test_key.py`。

**动手实验**：手算主网创世块哈希，10 行复现"区块哈希只哈希 80 字节头"：

```python
from bitcoin.hashes import hash256
header = ((1).to_bytes(4,"little") + bytes(32)
  + bytes.fromhex("4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b")[::-1]
  + (1231006505).to_bytes(4,"little") + (0x1d00ffff).to_bytes(4,"little")
  + (2083236893).to_bytes(4,"little"))
print(hash256(header)[::-1].hex())   # 000000000019d668...
```

**自检问题**

1. CompactSize 编码 253 这个数,需要几个字节？
2. 为什么 `hash160` 要先 SHA256 再 RIPEMD160，而不是直接 RIPEMD160？
3. 把 `uint256_to_hex` 里的 `[::-1]` 删掉，哪些测试会挂？
<details><summary>答案</summary>

1. 3 字节（`fd fd 00`）—— 253 是第一个需要扩展前缀的值。
2. 双层哈希提供防御纵深（其一被破不至于直接坍塌），且把 65 字节公钥压到 20 字节。
3. 所有以大端 hex 做断言的测试：创世哈希、地址、tx 深度查询（sqlite 键是大端 hex）等几乎全挂——这正说明该约定贯穿全仓库。
</details>

---

## 4. 第 2 站：数据结构与脚本引擎 — `tx.py` / `block.py` / `script.py`

**为什么先读它**：这是比特币的"宪法层"。`script.py` 是全仓库最难也最值得
精读的文件，0.1.x 的几个著名怪癖都在这里。

**核心代码讲解 — tx.py / block.py**

序列化布局（读 `serialize` 方法时对照）：

```text
CTransaction: int32 nVersion | vin[] | vout[] | uint32 nLockTime
  CTxIn :  COutPoint(uint256 hash + uint32 n) | script_sig | uint32 nSequence
  CTxOut:  int64 nValue | script_pubkey
CBlock 头(80B): int32 ver | uint256 prev | uint256 merkle | uint32 time | uint32 bits | uint32 nonce
区块哈希 = SHA256d(这 80 字节)，与交易数量无关
```

- `tx.py:113 is_coinbase`：单输入且 prevout 为 null（hash=0, n=0xFFFFFFFF）。
- `tx.py:124 get_min_fee`：`(1 + bytes//1000) * CENT`；带 `f_discount` 且
  <10000 字节免费；任一输出 < 1 分钱则至少收 1 分（防尘埃，0.1.5 原文如此）。
- `block.py:62 build_merkle_tree`：**奇数节点复制最后一个**
  （`i2 = min(i+1, size-1)`），这正是后来 CVE-2012-2459 重复交易攻击的根源
  —— 读到这里可以体会"忠实复刻"连缺陷一起复刻的含义。
- `block.py:120 CBlockLocator.from_index`：前 10 个一步一个，之后步长翻倍
  —— 用 O(log n) 个哈希向对端描述"我的链长这样"，第 5 站 getblocks 用它。

**核心代码讲解 — script.py（精读）**

- `script.py:124 bn_serialize`：CBigNum 编码 —— 小端幅值 + 末字节最高位是
  符号位，所以 `bn_serialize(128) == b"\x80\x00"`（要补一个字节）。
  脚本数字是**任意精度**的，这是 0.1.x 与现代比特币的显著差异。
- `script.py:184 push_int` vs `push_data`：`operator<<(int)` 会把 1..16
  编码成 OP_1..OP_16，而 `CBigNum(4)` 是数据推送 `01 04` ——
  **主网创世块 scriptSig famously 以 `04ffff001d 0104` 开头**，
  就是这个区别造成的（实现期间我们真的在这上面踩过一次，见
  `tests/test_genesis_vector.py` 里的注释）。
- `script.py:727 verify_signature`：**0.1.x 的灵魂怪癖** ——
  验证不是现代的"先跑 scriptSig、栈交给 scriptPubKey"两阶段，而是
  `EvalScript(scriptSig + OP_CODESEPARATOR + scriptPubKey)` **拼接单次执行**。
- `script.py:253 signature_hash`：签名哈希流程 ——
  复制交易 → 清空所有 scriptSig → 第 n_in 个填入 subscript →
  按 SIGHASH_NONE/SINGLE/ANYONECANPAY 改写 → 序列化追加 int32 hashtype →
  SHA256d。**第一行就是著名的 "return 1" bug**：n_in 越界时返回 1
  而不是报错（2010 年有人用它造出了"花任何人的钱"的 OP_CHECKSIG 绕过，
  这里为忠实而保留）。
- `script.py:300 check_sig`：hashtype 字节挂在 DER 签名**末尾**，
  验签前 pop 掉。
- `script.py:232 find_and_delete`：从 subscript 中删除签名自身的推送
  （"签名不能签到自己"），按 op 边界做字节匹配。
- `script.py:314 eval_script`：解释器主循环 —— `vf_exec` 栈实现 IF/ELSE、
  altstack、所有 0.1.5 opcode（**当年还没有任何 opcode 被禁用**，
  CAT/SUBSTR/乘除/移位都能跑）；`OP_CHECKMULTISIG` 末尾
  `popn(1)` 多弹一个栈元素 —— 著名的 off-by-one，也忠实保留。
- `script.py:654 solver`：只认两种标准模板 —— P2PK（coinbase 用）和
  P2PKH（普通转账），对应 0.1.5 的 Solver。

**配套测试**：`tests/test_genesis_vector.py` —— **本仓库说服力最强的测试，
建议精读**：用本仓库的 CScript/CTransaction/merkle 代码逐字段重建出真实
主网创世块，断言 merkle root `4a5e1e...` 和哈希 `000000000019d668...`
分毫不差。一个测试同时钉死序列化、推送编码、merkle 三层正确性。
另有 `test_script.py`（P2PK/P2PKH 签名往返、篡改拒绝、bignum 向量）。

**动手实验**：在 `test_script.py::test_tampered_output_rejected` 的思路上
玩一遍 —— 构造一笔签好名的交易，把 `spend.vout[0].n_value += 1`，
观察 `verify_signature` 返回 False；再想清楚签名到底覆盖了哪些字节。

**自检问题**

1. 拼接执行比两阶段执行危险在哪？（提示：scriptSig 里放 OP_PUSHDATA 之外的操作码会怎样）
2. SIGHASH_SINGLE 且 n_in 超过 vout 数量时会发生什么？
3. 为什么 `signature_hash` 里要先对 script_code 做 `find_and_delete(OP_CODESEPARATOR)`？
<details><summary>答案</summary>

1. scriptSig 可以放任意操作码直接操纵执行流（如压入 OP_1 直接通过）。真实历史上这正是后来引入两阶段执行 + scriptSig 只许推送的原因。
2. 返回 1（"return one" bug）——签名哈希变成常数 1，任何能让 CHECKSIG 对常数 1 验签通过的签名都有效。
3. 拼接两段脚本可能产生重复/尾部多余的 OP_CODESEPARATOR，原版注释说明要先删掉以避免兼容性问题——本仓库照搬（见 `script.py:262` 附近注释）。
</details>

---

## 5. 第 3 站：存储与共识 — `db.py` / `blockchain.py` / `mempool.py`

**为什么先读它**：这是 main.cpp 的共识半边，整个系统的"状态机"。
重组（reorganize）是全仓库逻辑最绕的函数。

**核心代码讲解 — db.py（10 分钟读完）**

- `BlockFile`：`blk0001.dat`，追加 `magic + size + 序列化区块`，按文件偏移
  随机读 —— 与原版格式相同。
- `BlockIndexDB`：sqlite 三张表替代 Berkeley DB：
  - `blockindex(hash, prev, height, pos)` —— 块索引（按高度有序加载，
    保证重启重建时父块先于子块）；
  - `txindex(txhash, blockhash, txn, spent)` —— `spent` 是 JSON 数组，
    每个输出一格，存花费者 txhash 或 null（对应 CTxIndex.vSpent）；
  - `kv` —— 只存 `hashBestChain`。
- `WalletFile`：明文 JSON（和 2009 年的 wallet.dat 一样不安全，这是特性）。

**核心代码讲解 — blockchain.py（精读）**

收块调用链（对照原版同名函数）：

```text
process_block (blockchain.py:209)        ← 入口；查重、孤块入库、递归接孤儿
 ├─ check_block (188)                    ← 无上下文：PoW、时间戳+2h、merkle、coinbase 唯一
 └─ accept_block (250)                   ← 有上下文：nBits==GetNextWorkRequired、
     │                                      时间戳 > 前 11 块中位数
     ├─ block_file.append                ← 先落盘
     └─ _add_to_block_index (264)
         └─ 高度 > 当前最佳 → _set_best_chain (284)
             ├─ 延伸最佳链 → connect_block (304)
             │    └─ connect_inputs (334)  ← 逐输入：存在性/未花费/成熟期/验签
             └─ 分叉超车   → reorganize (385)
```

- **按高度选主链**：`_add_to_block_index` 里只比较
  `height > self.best_height` —— 0.1.5 还没有累计工作量（chain-work），
  这是与现代比特币的本质差异之一。
- 孤块：`map_orphan_blocks` / `map_orphan_blocks_by_prev`，父块接上后
  `process_block` 尾部用工作队列递归收编（对照原版同样的 while 循环）。
- `connect_inputs` 的 **test_pool 模式**：块内交易要能花同块前面交易的
  输出，又不能提前写库 —— 所以用 `test_pool` dict 叠在 sqlite 之上做
  "草稿层"，全块验证通过后一次性落库。矿工 `create_new_block` 复用同一
  函数同一模式（第 4 站）。
- `reorganize`（流程图）：

```text
        old best ──┐                       ┌── new tip (更高)
                   ▼                       ▼
   ... ── fork ── A1 ── A2          fork ── B1 ── B2 ── B3
   1) 找 fork：双指针沿 pprev 后退至相遇
   2) 断开 A2、A1（倒序）：disconnect_block 逐块
      - 撤销 spent 标记、删 txindex、收集交易 → resurrect 列表
   3) 连接 B1、B2、B3（正序）：connect_block 逐块
   4) main_chain 列表截断+续接、pnext 指针重连
   5) resurrect 的交易塞回 mempool（accept(check_inputs=False)）
      B 链确认了的交易从 mempool 删除
```

- `get_next_work_required (164)`：保留原版 **off-by-one** ——
  回退 `nInterval-1` 块测 2015 个间隔，却除以完整两周。
  `tests/test_difficulty.py::test_retarget_keeps_difficulty_at_target_rate`
  把这个 2015/2016 因子写成了显式断言。

**核心代码讲解 — mempool.py**

`mapTransactions`（hash→tx）+ `map_next_tx`（outpoint→花费者）双表；
`accept (35)` 的检查顺序值得背下来：CheckTransaction → 非 coinbase →
查重（池内+链上）→ 池内冲突（双花）→ `_check_inputs`
（输入存在、未花费、coinbase 成熟、验签、费率）。

**配套测试**：`test_chain.py`（10 块链、双花、孤块、超发 coinbase、错 nBits、
重启恢复）、`test_reorg.py`（分叉超车 + **mempool 复活**）、
`test_mempool.py`、`test_difficulty.py`。

**动手实验**：照 `test_chain.py::test_double_spend_across_blocks_rejected`
的写法，自己构造一个双花区块喂给 `process_block`，把日志级别开到 DEBUG，
观察拒绝路径打出的是哪一条 `ERROR: ConnectInputs: ...`。

**自检问题**

1. 为什么块文件 append 发生在 `_add_to_block_index` 之前，顺序反过来会怎样？
2. 等高分叉（tie）会切换主链吗？依据是哪一行？
3. reorganize 里 resurrect 用 `accept(check_inputs=False)`，为什么可以跳过验签？
<details><summary>答案</summary>

1. 索引引用文件偏移 `pos`，必须先有 pos。反过来若中途崩溃会出现"索引指向不存在的数据"，现在最坏只是文件尾部有一段无索引的冗余字节（重启无害）。
2. 不会。`_add_to_block_index` 只在 `height > best_height` 时切换——先到者保持，这正是原版行为（也是自私挖矿讨论的起点）。
3. 这些交易曾在 A 链区块里通过过完整验证，签名不会因重组失效；可能失效的只是"输入是否已被 B 链花掉"，而这会在矿工下次 `create_new_block` 调 `connect_inputs` 时被过滤。原版 ResurrectMemoryPool 同样不复验。
</details>

---

## 6. 第 4 站：钱包与矿工 — `wallet.py` / `miner.py`

**为什么先读它**：共识层管"什么是合法"，钱包层管"哪些是我的钱"。
0.1.5 的钱包模型和现代钱包差异很大，不读源码容易拿现代直觉去误解。

**核心代码讲解 — wallet.py**

- **整笔 fSpent 模型**（`WalletTx`，wallet.py:34）：花费标记打在
  *整笔钱包交易*上，不是每个输出上（按输出记账是后来的事）。
  推论：`create_transaction (237)` 花一笔 UTXO 必须**整笔花掉**，
  多出的部分给自己找零 —— 这就是找零机制在 0.1.x 存在的原因。
- **没有 keypool**：找零、挖矿 coinbase 都现场 `generate_new_key (115)`。
- 余额语义 `get_balance (210)`：`!f_spent` 且 `blocks_to_maturity == 0`
  的钱包交易的 credit 之和。注意两点：
  - **0 确认收款计入余额**（2009 年原版如此，`test_send_money_with_change`
    里 B 立即看到 10 BTC）；
  - 挖矿所得等 `COINBASE_MATURITY + 20 = 120` 块（钱包比共识的 100 块
    多 20 块安全边距，`blocks_to_maturity (200)`，原版同款）。
- `create_transaction` 的 **fee 重试循环**：先按用户设置的 fee 组装+签名，
  签完算出真实字节数 → `get_min_fee` 若更高 → 提高 fee 重来一轮
  （原版 CreateTransaction 同构）。
- `select_coins (217)`：**简化点** —— 先找"最小的单枚够付"，否则从小到大
  累加；原版有子集和近似算法。review 时这里可以标注为有意简化。
- 记账入口 `add_to_wallet_if_mine (168)` 挂在两处监听器上：
  `chain.tx_listeners`（交易进块）和 `mempool.listeners`（0 确认）。

**核心代码讲解 — miner.py**

- `create_new_block (49)`：在 `chain.lock` 内快照 —— 从 mempool 拉交易并用
  `connect_inputs` + test_pool 过滤（天然排除块内双花、自动累计手续费），
  coinbase 付给新键、金额 `block_value(height, fees)`。
- `_mine_loop (85)`：每 20000 个 nonce 检查一次"最佳块是否变了"
  （变了就放弃重建，对应原版监听 hashBestChain 的逻辑）并刷新 nTime。

**配套测试**：`test_wallet.py` —— 成熟期日程表、转账+找零余额断言
（290 = 250 + 40 找零）、超额拒绝、钱包持久化、矿工收手续费
（coinbase = 50 BTC + 0.01 fee）。

**动手实验**：GUI 里把 Options → Transaction fee 设成 0.01，发一笔转账，
然后看矿工挖出的下一个块里 coinbase 是不是 50.01（交易列表 Generated 行）。

**自检问题**

1. A 有一枚 50 BTC 的 UTXO，发 10 BTC 后马上再发 45 BTC，会发生什么？
2. 为什么钱包成熟期是 120 而共识只要求 100？
3. `generate_new_key` 在矿工里每次建块都调用，钱包文件会不会越来越大？
<details><summary>答案</summary>

1. 第二笔失败（Insufficient funds）：50 已整笔标记 f_spent，40 找零是 0 确认且可用，但 45 > 40。等找零确认也一样——可用余额就是 40。
2. 防重组：若挖矿所得在第 100 块刚成熟就花掉，一次浅重组就能让它消失连累下游交易。原版加 20 块缓冲，本仓库照搬。
3. 会（每次尝试建块都会留下一个键）。原版用 CReserveKey 把没用上的键还回去——这是本仓库的有意简化，README 偏离表未列出但量级无害。
</details>

---

## 7. 第 5 站：P2P 网络 — `config.py` / `net.py` / `node.py`

**为什么先读它**：到这里单机已经完整，网络层让多个状态机收敛到同一条链。
本站重点是**时序**，建议对照 `test_two_node_sync.py` 读。

**核心代码讲解 — net.py（传输层）**

- `pack_message (76)`：**20 字节消息头** = 4 魔数 `F9BEB4D9` + 12 命令
  （NUL 填充）+ 4 长度。**没有校验和字段** —— 那是 0.2.x 才加的，
  本仓库严格不实现。
- `parse_messages (86)`：分帧器 —— 向前扫描魔数实现重同步（坏字节、
  半包都能恢复），毒头（size 超 MAX_SIZE）跳过该魔数继续扫。
  `test_net_handshake.py::test_parse_roundtrip_and_resync` 全覆盖。
- `CAddress (25)`：services(8) + 12 字节 IPv4-mapped 前缀
  （`00*10 + FF FF`）+ ip(4) + port(2)，**ip/port 是网络字节序**
  （别处全小端，仅此两个字段大端 —— 原版如此）。
- `CNode (113)`：每连接的收发缓冲 + 三件库存簿记：
  `inventory_known`（防回声）、`inventory_to_send`（待广播）、
  `ask_for`（待索取）。
- `NetEngine (156)`：线程清单对照原版 —— accept 循环（ThreadSocketHandler）、
  重连循环（ThreadOpenConnections）、每连接一个 recv 线程。

**核心代码讲解 — node.py（协议层）**

握手（注意：**没有 verack**）：

```text
A(已有 125 块)                         B(空链, -connect=A)
   │ ◄────────── TCP connect ────────── │
   │ ── version(105, services, time) ─► │   双方在 on_peer_connected
   │ ◄─ version(105, services, time) ── │   无条件先发 version
   │            （没有 verack！）        │
   │ ◄──────── getblocks(locator) ───── │   B 收到第一个 version 就要块
```

初始同步（IBD）：

```text
   │ ── inv(block×125) ───────────────► │   _msg_getblocks: 从 locator 分叉点
   │ ◄─ getdata(block×125) ──────────── │   _msg_inv → ask_for → 批量 getdata
   │ ── block ×125 ───────────────────► │   逐块 process_block
   （若超过 500 块：第 500 块记入 hash_continue，
     对方取到它时回敬一个 tip 的 inv，触发下一轮 getblocks —— _msg_getdata:171）
```

- `_msg_inv (135)`：没有的就排进 `ask_for`；**已有但是孤块**的，向对方
  `push_get_blocks(best, orphan_root)` 补祖先 —— 对照原版同款分支。
- `_send_messages_loop (242)`：0.1 秒节拍统一冲刷 inv/getdata，发 inv 前
  过滤 `inventory_known` —— 这就是"A 发给 B 的块，B 不会再 inv 回 A"的
  防回声机制。
- 交易转发：`mempool.listeners → _on_pool_tx (72) → relay_inventory`；
  孤儿交易 `orphan_txs` 上限 1000，父交易出现后 `_retry_orphan_txs (234)`。
- `config.py`：`-datadir -port -connect -addnode -nolisten -gen` +
  datadir 下的 `bitcoin.conf`，节点发现是**纯手动**的（私网偏离，无 IRC）。

**配套测试**：`test_net_handshake.py`（头部金样、CAddress 字节序、重同步）、
`test_two_node_sync.py`（**真 socket 双节点**：125 块 IBD → 转账广播 →
对端挖矿确认回传 → A 的 mempool 清空，一个测试走完全协议）。

**动手实验**：开两个终端跑 `run_node.py`（一个 `-gen`），tail 两边
datadir 里的 `debug.log`，把日志里 `received: ...` 的消息序列和上面的
时序图对一遍。

**自检问题**

1. 没有校验和，坏一个字节会发生什么？没有 verack，怎么知道对方"准备好了"？
2. B 同步完 125 块后，为什么不会把这 125 个块的 inv 再播回给 A？
3. 600 块的链，B 完整同步需要几轮 getblocks？hash_continue 在哪一刻起作用？
<details><summary>答案</summary>

1. TCP 自身保证传输完整性，应用层坏帧靠扫魔数重同步丢弃；"准备好"的判定就是收到对方的 version（`on_message` 里 n_version==0 时丢弃一切非 version 消息）。
2. 两道防线：B 的新最佳块确实会触发 relay，但 A 早已把这些哈希记入了发往 B 时的 `inventory_known`，`_send_messages_loop` 冲刷时过滤掉；即便漏网，A `_already_have` 也不会去 getdata。
3. 两轮（500 + 100）。第 500 块被 B getdata 取走时，A 的 `_msg_getdata` 发现 `inv.hash == peer.hash_continue`，回敬 tip 的 inv；B 发现 tip 的 prev 不认识 → 走孤块分支 `push_get_blocks` 开启第二轮。
</details>

---

## 8. 第 6 站：图形界面 — `qt/`

**为什么后读它**：纯消费层，不含共识逻辑；但**跨线程规则**值得 review。

**核心代码讲解**

- 架构是"**模型 + 轮询**"：`mainwindow.py` 的 QTimer 每 500ms 调 `_poll`
  刷状态栏/余额；钱包变更监听器 `_mark_dirty` **只置一个布尔脏标志**，
  由主线程在下个节拍刷新 —— 因为监听器是在网络/矿工线程里被回调的，
  **任何非主线程禁止碰 Qt 对象**，这是本站唯一的硬规则。
  确认数随块高变化，所以 `_poll` 在高度变化时也强制刷新列表。
- `models.py:TransactionModel`：把 WalletTx 翻译成
  Status | Date | Description | Debit | Credit 五列（与 ui.cpp 列名一致）；
  状态文案规则在 `_row_for`（immature/not accepted/N confirmations）。
- 控件对照 ui.cpp：工具栏只有 **Send Coins / Address Book** 两个按钮、
  Options 菜单的 **Generate Bitcoins 勾选项**直接 start/stop 矿工线程、
  状态栏 `N connections N blocks N transactions`。
  All/Sent/Received 三个标签页是 **0.2.x 才有的时代错置**（用户要求保留，
  README 已注明）。
- `senddialog.py` 的校验顺序：地址 Base58Check → 金额解析 →
  `wallet.send_money` 的业务错误透传弹窗。

**配套测试**：`test_gui_smoke.py`（`QT_QPA_PLATFORM=offscreen` 离屏跑：
建窗、状态栏断言、交易行数、换地址、发送对话框校验）。
[tools/screenshot_gui.py](../tools/screenshot_gui.py) 可随时离屏渲染一张
主窗口截图（README 那张就是它生成的）。

**自检问题**

1. 把 `_mark_dirty` 改成直接调 `model.refresh()` 会发生什么？
2. 关窗口时数据会丢吗？关闭路径是什么？
<details><summary>答案</summary>

1. 网络线程直接操作 Qt 模型 → 未定义行为（典型表现为偶发崩溃或视图错乱）。Qt 的线程亲和性要求所有 UI 操作在 GUI 线程。
2. 不丢。`closeEvent → node.stop()`：停矿工、关 socket、`wallet.save()`、`chain.close()`（sqlite commit+close）。
</details>

---

## 9. 终点站：装配 — `run_node.py` / `run_gui.py` / `tools/`

`node.py:Node.__init__` 是全系统的接线图，10 行看懂构造顺序：
Blockchain（开库/建创世）→ MemPool（自挂到 chain.mempool，重组复活要用）→
Wallet（订阅 chain + mempool）→ Miner → NetEngine（handler 指回 Node）。
`tools/mine_genesis.py` 是一次性工具：挖私网创世块并把
nonce/hash/merkle 回填进 `params.py`（已经跑过，常量已固化）。

---

## 10. 附录

### 10.1 关键调用链速查

| 场景 | 链路 |
|---|---|
| 收到新块 | `net._recv_loop → node._msg_block → chain.process_block → check/accept/connect → block_listeners → relay inv` |
| 用户转账 | `GUI SendDialog → wallet.send_money → create_transaction → commit_transaction → mempool.accept → listeners →(wallet 记账 + node relay)` |
| 初始同步 | `version → getblocks → inv×N → getdata → block×N (+hash_continue 续轮)` |
| 重组 | `_set_best_chain → reorganize → disconnect×n + connect×m → mempool resurrect/evict` |

### 10.2 已知简化清单（review 时的"已知问题"基线）

诚实列出与原版/生产质量的差距，review 到这些地方不必惊讶：

1. `wallet.select_coins`：贪心选币，非原版子集和近似；无 CReserveKey 回收。
2. **单把 `chain.lock`（RLock）** 代替原版 cs_main + 各细粒度锁；正确性优先于并发度。
3. `connect_inputs` 查前置交易要**从磁盘读整个区块**再取下标 —— O(区块大小) 的查找，玩具规模无碍，真实规模需要 tx 级偏移（原版 CDiskTxPos 就是干这个的）。
4. 孤儿交易处理比原版简单（无按依赖索引，只全量重试）。
5. sqlite 每块 commit；崩溃一致性依赖 sqlite 日志，块文件与索引间无两阶段提交。
6. `getaddr/addr` 仅最小实现，无地址库持久化（私网手动连接用不上）。
7. RFC 6979 确定性签名代替 OpenSSL 随机 k（安全性更好，非缺陷）。
8. 日志/错误处理偏教学风格：`_error` 打日志返回 False，无错误码体系。

### 10.3 进一步阅读

- 原版源码（对照阅读强烈推荐）：
  https://github.com/bitcoin/bitcoin/tree/v0.1.5 ——
  `main.cpp`（共识+钱包+消息处理全在这一个文件里，6000 行）、
  `script.cpp`、`net.cpp`、`serialize.h`。
- 演进考古（读完本仓库后看这些"后来才出现"的东西会非常清晰）：
  - **0.2.x**：消息头加 4 字节校验和；`verack`；
  - **0.3.x**：累计工作量（chain-work）取代按高度选链；keypool；
  - **2010-07（0.3.x）**：OP_CAT 等危险 opcode 禁用、"return 1" 漏洞修复、1 MB 区块上限 —— 每一条都对应本仓库里一个被忠实保留的"缺陷"。
