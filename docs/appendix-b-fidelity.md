# 附录 B　源码对照与忠实度清单

这份附录回答三个问题：

1. 本项目的每个文件对应中本聪原版的哪个文件、哪些函数？
2. 哪些地方逐行忠实于原版？哪些地方有意改了？
3. **这份代码被审查过吗？查出过什么问题？**

原版源码：<https://github.com/bitcoin/bitcoin/tree/v0.1.5>（约 15,000 行 C++）

## B.1　文件与函数对照

| 本项目 | 原版文件 | 主要的函数/类对应（本项目名 ← 原版名） |
|---|---|---|
| `params.py` | `main.h` `net.h` `serialize.h` | `COIN` `CENT` `COINBASE_MATURITY` `VERSION` `MAX_SIZE`；`block_value` ← `GetBlockValue`；`compact_to_target` ← `CBigNum::SetCompact`；`target_to_compact` ← `GetCompact` |
| `serialize.py` | `serialize.h` | `DataStream` ← `CDataStream`；`ser_compact_size` ← `WriteCompactSize` |
| `hashes.py` `ripemd160.py` | `util.h` `sha.cpp`、OpenSSL | `hash256` ← `Hash()`；`hash160` ← `Hash160()` |
| `base58.py` | `base58.h` | `encode`/`decode` ← `EncodeBase58`/`DecodeBase58`；`…_check`；`hash160_to_address` ← `Hash160ToAddress` |
| `key.py` | `key.h` | `CKey`：`generate` ← `MakeNewKey`；`get_pubkey`；`sign`；`verify_with_pubkey` ← `Verify` |
| `tx.py` | `main.h` | `COutPoint` `CTxIn` `CTxOut` `CTransaction`：`is_coinbase` `is_final` `is_newer_than` `check_transaction` `get_min_fee` `get_value_out` |
| `block.py` | `main.h` | `CBlock`：`build_merkle_tree` `get_hash`；`CBlockIndex`：`get_median_time_past`；`CBlockLocator` |
| `script.py` | `script.h` `script.cpp` `bignum.h` | `CScript`（`push_int` ← `operator<<(int)`，`push_bignum` ← `operator<<(CBigNum)`，`get_op` ← `GetOp`，`find_and_delete`）；`eval_script` ← `EvalScript`；`signature_hash` ← `SignatureHash`；`check_sig`；`solver`；`sign_signature`；`verify_signature`；`bn_serialize` ← `CBigNum::getvch` |
| `db.py` | `db.cpp` `db.h` | `BlockFile` ← `WriteToDisk`/`ReadFromDisk`；`BlockIndexDB` ← `CTxDB`；`WalletDB` ← `CWalletDB` |
| `blockchain.py` | `main.cpp` | `process_block` `check_block` `accept_block` `_add_to_block_index` `connect_block` `connect_inputs` `disconnect_block` `reorganize` `get_next_work_required` `get_orphan_root`；`_load_or_create` ← `LoadBlockIndex` |
| `mempool.py` | `main.cpp` | `accept` ← `AcceptTransaction`；`remove` ← `RemoveFromMemoryPool`；`map_transactions` `map_next_tx` |
| `wallet.py` | `main.cpp` | `add_key` ← `AddKey`；`add_to_wallet_if_mine`；`get_balance`；`select_coins`；`create_transaction`；`commit_transaction_spent`；`send_money`；`reaccept_wallet_transactions`；`relay_wallet_transactions`；`blocks_to_maturity` ← `GetBlocksToMaturity` |
| `miner.py` | `main.cpp` | `create_new_block` + `solve` + `found_block` ← `BitcoinMiner()` |
| `net.py` | `net.h` `net.cpp` | `pack_message` ← `CMessageHeader`；`parse_messages` ← `ProcessMessages` 的分帧部分；`CAddress`；`CInv`；`CNode`（`push_inventory` `ask_for`） |
| `node.py` | `main.cpp` | `on_message` + `_msg_*` ← `ProcessMessage`；`send_messages` ← `SendMessages`；`_already_have` ← `AlreadyHave` |
| `config.py` `util.py` | `util.h` `util.cpp` | `Config` ← `mapArgs`；`format_money` `parse_money` ← `FormatMoney` `ParseMoney` |
| `qt/` | `ui.cpp` `uibase.cpp` | `MainWindow` ← `CMainFrame`；`SendDialog` ← `CSendDialog`；`rows_for` ← `InsertTransaction`；`format_tx_status` ← `FormatTxStatus` |
| （未实现） | `irc.cpp` `market.cpp` | IRC 节点发现；未完成的交易市场；按 IP 直接付款 |

## B.2　逐行忠实的地方

下列内容对照原版源码逐行核对过，并有测试锁定：

**线路格式**
* 所有整数小端；变长整数的四档编码；`MAX_SIZE` = 32 MiB
* 交易、区块、区块头、`COutPoint`、`CInv`、`CAddress`、`CBlockLocator` 的字节布局
* 20 字节消息头，无校验和；命令名必须是可见 ASCII 且 0 之后全 0
* `version` 载荷：版本、服务、时间、**对方的**地址（46 字节）；无 `verack`
* `blk0001.dat`：魔数 + 长度 + 区块

**脚本**
* 108 个 opcode 的数值（由原版头文件解析得到）；`0xF0` 以上为双字节 opcode
* `CBigNum` 的小端符号-幅值编码；任意精度
* `operator<<(int)` 与 `operator<<(CBigNum)` 的不同编码
* `EvalScript` 每个 opcode 的语义，包括：`OP_VERIFY`/`OP_RETURN`/`…VERIFY` 失败时是"跳到末尾"而非立即返回；
  `OP_VER`/`OP_VERIF`/`OP_VERNOTIF`；`OP_SUBSTR`/`OP_LEFT`/`OP_RIGHT` 的越界钳制；
  除法向零取整、余数符号随被除数、右移作用于幅值；`OP_CHECKMULTISIG` 的下标算法与多弹一个元素；
  结束时不检查 IF 配对
* 拼接执行；`FindAndDelete`；`SignatureHash`（含两处 "return 1"、四种 hashtype）；`CheckSig` 从签名末尾取 hashtype
* `Solver` 的两个模板（公钥长度 > 32；哈希长度 = 20）

**共识**
* `CheckBlock` / `AcceptBlock` 的每一项检查
* `ConnectInputs` 的检查项与 coinbase 成熟度的边界（花费区块高度 − coinbase 高度 ≥ 100）
* `GetNextWorkRequired`：2016 块、往回走 2015 步、[1/4, 4×] 钳制、工作量上限
* `GetBlockValue` 使用全局 `nBestHeight`
* 按高度（而非累计工作量）选最佳链；等高时先到先得
* 孤块的暂存、连锁接入；`GetOrphanRoot` 返回最老孤块**自己**的哈希
* `Reorganize` 找分叉点的算法；失败时回滚并清除无效分支
* 创世块不经过 `ConnectBlock`，其 coinbase 不可花费

**内存池与矿工**
* `AcceptTransaction` 的检查顺序；`IsNewerThan` 的交易替换逻辑；内存池不检查手续费
* `GetMinFee` 逐行一致
* 矿工：前 100 笔交易享受免费；多遍扫描以处理池内依赖；每笔交易在草稿副本上试；区块大小上限 `MAX_SIZE/2`；
  跳过未定稿的交易；coinbase `scriptSig << nBits << ++bnExtraNonce`；
  收款密钥仅在挖到块时存盘；每 0x40000 个 nonce 检查一次链尖是否变化

**钱包**
* 整笔 `fSpent`；找零付回原币的同一公钥（P2PK）；`SelectCoins` 的随机逼近算法
* `GetBalance`：已定稿 + 未花；零确认计入；coinbase 等 `COINBASE_MATURITY + 20`
* 发送后地址自动进地址簿；重启后 `ReacceptWalletTransactions`；`RelayWalletTransactions` 的 10 分钟节流
* `FormatMoney`/`ParseMoney`：只到"分"、千位逗号

**网络行为**
* 只向第一个邻居发初始 `getblocks`；`getblocks` 无数量上限，且绕过"对方已知"过滤
* `AskFor`：同一数据被多个邻居通告时，后来者的请求推迟 2 分钟
* 收到孤块或已知孤块的 `inv` 时，回补请求的截止哈希为孤块根

## B.3　有意的改动

| 类别 | 改动 | 理由 |
|---|---|---|
| 私网 | 自己的创世块；工作量上限 `~0>>8`；创世难度 `0x1f00ffff`；端口 18444 | 让纯 Python 能在几十毫秒内出块；与真实网络隔离 |
| 私网 | 手动 `-connect` 代替 IRC | 私网没有 IRC 频道 |
| 私网 | 允许没有邻居时挖矿（原版会等待） | 单机实验需要 |
| 依赖 | sqlite 代替 Berkeley DB；`ecdsa` 库代替 OpenSSL | 纯 Python、标准库 |
| 安全 | 签名使用 RFC 6979 确定性 k | 线格式不变；消除 k 重复导致私钥泄露的风险 |
| 安全 | `OP_LSHIFT`/`OP_RSHIFT` 移位量上限 2048 | 原版无上限，一行脚本可耗尽内存 |
| 安全 | 孤儿交易最多缓存 1000 笔 | 原版无上限 |
| 安全 | 广播失败时恢复钱包状态（原版抛异常） | 避免钱包与内存池不一致 |
| 未复刻的原版 bug | `CreateTransaction` 重试循环里把上一轮手续费累加进付款金额 | 无心之失而非设计；只在"设置的手续费 < 最低手续费"时触发 |
| 扩展 | `wallet.rescan()`；收录交易时顺带标记被它花掉的钱包交易 | 导入密钥后需要；原版 0.1.5 无此功能 |
| 扩展 | `eval_script` 的 `trace` 钩子；`script_to_asm` | 教学用 |
| 界面 | PySide6；中文；"全部/已发送/已接收"标签页（0.2.x 才有） | —— |
| 简化 | `getaddr`/`addr` 只做最小实现，不持久化地址库；`getdata` 的交易从内存池取（原版从 `mapRelay` 转发缓存取） | 私网不需要节点发现 |
| 简化 | 时间校正 `GetAdjustedTime` 直接返回本机时间 | 私网节点都在同一台机器上 |

## B.4　代码审查记录

这份代码在初版完成之后，做过一次**对照原版 C++ 源码的逐函数审查**。
方法是把原版的 `main.cpp`、`script.cpp`、`net.h` 等文件取到本地，一个函数一个函数地与 Python 版比对。
审查发现了 4 个真正的 bug 和十几处"凭记忆写的、与原版不符"的地方，全部已修复并补了测试。

把它们列在这里，既是为了诚实，也因为**它们本身就是很好的学习材料**——每一条都说明了某个细节为什么重要。

### 真正的 bug

| # | 问题 | 后果 | 现在由哪个测试把关 |
|---|---|---|---|
| 1 | `get_orphan_root` 返回了"缺失的父块"的哈希；原版返回的是"最老的孤块**自己**"的哈希 | 回补请求的截止位置差了一个块，恰好要不到缺的那一块；节点一旦收到孤块就永远卡住 | `test_orphan_block_waits_for_its_parent`、`test_orphan_block_triggers_getblocks_with_orphan_root_as_stop`、`test_orphan_backfill_over_the_network` |
| 2 | 加锁顺序不一致：`get_balance` 先钱包锁后链锁，区块连接时先链锁后回调钱包；内存池与链之间也有同样的问题 | 界面刷新、转账与挖矿同时进行时可能**死锁** | `tests/test_concurrency.py`（5 个线程同时折腾一个节点） |
| 3 | 链重组中途失败（新分支里有无效区块）时，数据库里已做的"断开"没有回滚 | 交易索引被写坏：旧分支的交易在账本上"消失"，但链尖还指着旧分支 | `test_failed_reorg_is_rolled_back_completely`、`test_failed_block_leaves_no_trace` |
| 4 | `OP_DIV`/`OP_MOD` 用了浮点除法 `int(a / b)`；`OP_2DIV`/`OP_RSHIFT` 用了 Python 的 `>>` | 大数除法结果错误（浮点只有 53 位精度）；负数右移的结果与 OpenSSL 不同（−7>>1 应为 −3 而非 −4） | `test_binary_numeric`、`test_unary_numeric` 里的大数与负数用例 |

另外还有一个矿工里的隐患：一笔交易的前几个输入验证通过、后一个输入失败时，前几个输入已经在"草稿"里被标成了已花，
会导致后面本来合法的交易被误拒。原版的做法是每笔交易在草稿的**副本**上试。已照此修正
（`test_failed_candidate_does_not_poison_later_ones`）。

### 与原版不符的地方（已改为忠实）

| 方面 | 初版的写法 | 原版的实际行为 |
|---|---|---|
| `OP_RETURN` | 立即失败 | 跳到脚本末尾，结果由栈顶决定（著名漏洞） |
| 脚本结束 | 检查 IF 是否闭合 | 不检查 |
| `OP_VER` `OP_VERIF` `OP_VERNOTIF` | 当作无效指令 | 有实际功能 |
| 双字节 opcode | 未实现 | `0xF0` 以上的字节与下一字节合成一条指令 |
| `OP_SUBSTR` 越界 | 失败 | 钳制到末尾 |
| 找零 | 每次生成新密钥，P2PKH | 付回原币的同一公钥，P2PK |
| 选币 | 简单贪心 | 随机逼近子集和 |
| 手续费门槛 | 内存池检查，矿工不检查 | 内存池不检查，矿工检查（前 100 笔免费） |
| 交易替换（`nSequence`）与 `IsFinal` | 未实现 | 有 |
| 矿工收款密钥 | 每次组装区块都生成并存盘 | 只在挖到块时存盘 |
| `getblocks` | 每次最多 500 个 + `hashContinue` | 无上限（500 的限制是后来版本的） |
| `version` 里的地址 | 自己的地址 | 对方的地址 |
| 重复下载 | 每个通告的邻居都去要 | `AskFor`：2 分钟后才换下一个邻居 |
| 区块奖励 | 用区块自己的高度 | 用全局 `nBestHeight` |
| 区块定位器 | 末尾不重复追加创世块 | 无条件追加 |
| 钱包重启/重播 | 未实现 | `ReacceptWalletTransactions`、`RelayWalletTransactions` |
| 金额显示 | 8 位小数 | 只到"分"，带千位逗号 |
| 消息头检查 | 只查长度 | 还检查命令名的字符合法性 |
| 发送线程 | 处理消息的线程直接 `sendall` | 先入发送缓冲区，由专门的线程发送 |

### 审查之后的验证手段

* **真实主网数据交叉验证**（`tests/test_mainnet_vectors.py`）：中本聪→Hal Finney 的真实交易验签通过；
  任意篡改均被识破；第 170 号区块的默克尔根与真实区块头一致；真实区块头的哈希与工作量证明。
* **主网创世块逐字节重建**（`tests/test_genesis_vector.py`）。
* **原版枚举自动比对**：opcode 数值由脚本从 `script.h` 解析，存于 `tests/data/opcodes_v015.json`。
* **独立实现交叉验证**（`labs/lab03_elliptic_curve.py`）：几十行手写的椭圆曲线/ECDSA 代码与 `ecdsa` 库互相验证，并验证了中本聪的真实签名。
* **白皮书数值比对**（`labs/lab09_chain_reorg.py`）：攻击者成功概率表与白皮书第 11 节一致。

[← 附录 A](appendix-a-glossary.md)　|　[附录 C：测试地图 →](appendix-c-tests.md)
