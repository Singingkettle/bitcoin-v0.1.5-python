# Bitcoin v0.1.5 —— 纯 Python 复刻版（附完整中文教程）

用纯 Python 忠实复刻中本聪 2009 年发布的
[Bitcoin v0.1.5 ALPHA](https://github.com/bitcoin/bitcoin/tree/v0.1.5)，
原版的 wxWidgets 界面用 Qt（PySide6）重做。它运行在自己的私有网络上：
你可以在一台电脑上启动几个节点，挖矿、转账、看区块在节点之间传播——
把 2009 年的那套协议从头到尾亲手跑一遍。

**这个项目首先是一份教材。** 约 4000 行带中文注释的源码 + 16 章从高中知识讲起的教程 +
12 个动手实验 + 650 多个测试，目标是让你彻底弄懂比特币的每一个零件。

![主窗口](screenshot.png)

> ⚠️ **这是学习用的玩具。** 私有网络、极低的难度、明文保存私钥的钱包，
> 还原样保留了 2009 年代码里那些后来被修复的安全漏洞。绝对不要用它处理任何有价值的东西。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

pytest                          # 650+ 个测试，约 3 分钟
python labs\lab02_hash.py       # 跑一个实验试试

# 启动两个带界面的节点（或者直接运行 demo_two_nodes.ps1）
python run_gui.py -datadir=data\nodeA -port=18444
python run_gui.py -datadir=data\nodeB -port=18445 -connect=127.0.0.1:18444
```

在节点 A 的菜单里勾选 **选项 → 生成比特币（挖矿）**，两个窗口状态栏里的区块数会一起上涨。
挖矿所得要等 120 个区块才能花（私网出块很快，十几秒就够了）。然后复制节点 B 的地址，
在 A 上点 **发送比特币**：B 那边立刻显示"0/未确认"，下一个区块出来后变成"1/未确认"。

`run_node.py` 是不带界面的版本；加 `-gen` 参数启动后立即挖矿。

## 从哪里开始学

**→ [docs/ch00-roadmap.md](docs/ch00-roadmap.md)：学习路线图**

| 你想…… | 去看 |
|---|---|
| 从零开始系统地学 | [教程目录](docs/ch00-roadmap.md)，按章节顺序 |
| 先动手玩一玩 | [labs/](labs/) 里的 12 个实验，每个都能直接运行 |
| 对照原版 C++ 读源码 | [附录 B：源码对照与忠实度清单](docs/appendix-b-fidelity.md) |
| 知道这份复刻到底靠不靠谱 | [附录 C：测试地图](docs/appendix-c-tests.md) |

## 凭什么说它"复刻得对"

最硬的证据在 [tests/test_mainnet_vectors.py](tests/test_mainnet_vectors.py)：
**中本聪 2009 年 1 月 12 日付给 Hal Finney 的那笔真实交易（史上第一笔比特币转账），
用本项目的脚本引擎验签通过**；把金额、收款人、签名中的任何一个比特改掉，验证立刻失败。
要让这个测试通过，序列化、双 SHA-256、脚本的拼接执行、FindAndDelete、SignatureHash、
ECDSA 验签必须**全部**与原版一致。

此外：

* 按原版源码里的写法逐字段重建**主网创世块**，285 字节一个不差，
  哈希 `000000000019d668…` 与默克尔根 `4a5e1e4b…` 完全吻合；
* 108 个 opcode 的数值由脚本从原版 `script.h` 的枚举里解析出来，逐个核对；
* 白皮书第 11 节的攻击者成功概率表，[实验 9](labs/lab09_chain_reorg.py) 算出来的数字与原文一致。

## 忠实复刻了什么

全部对照 v0.1.5 的原始 C++ 源码逐函数核对过（详见[附录 B](docs/appendix-b-fidelity.md)）：

* **网络协议（版本 105）**：20 字节消息头——魔数 `F9 BE B4 D9` + 12 字节命令 + 长度，
  **没有校验和**；没有 `verack`、没有 `ping`；消息只有
  `version addr inv getdata getblocks tx block getaddr`；`version` 里带的是**对方**的地址；
  `getblocks` 没有"每次 500 个"的限制；同一份数据被多个邻居通告时只向一个索取（2 分钟后才换人）。
* **共识**：80 字节区块头、双 SHA-256；50 BTC 起每 21 万块减半；每 2016 块调整难度，
  单次最多 4 倍，并保留了原版"只数了 2015 个间隔"的小失误；
  **按高度选最佳链**（"累计工作量"是后来才有的）；孤块暂存与回补；coinbase 100 个确认才能花；
  `GetBlockValue` 用的是全局的 `nBestHeight`。
* **脚本**：v0.1.5 的全部 opcode（当时还没有任何 opcode 被禁用），任意精度的 CBigNum 数字；
  `scriptSig + OP_CODESEPARATOR + scriptPubKey` **拼接后只执行一次**；
  `OP_RETURN` 是"跳到末尾"而不是"立即失败"（于是 `OP_1 OP_RETURN` 能花任何人的钱——
  2010 年才修复的著名漏洞）；`SignatureHash` 的 "return 1" bug；
  `OP_CHECKMULTISIG` 多弹一个栈元素；双字节 opcode；脚本结束时不检查 IF 是否闭合。
* **交易与内存池**：`GetMinFee` 逐行一致；手续费门槛由**矿工**执行（每个区块前 100 笔免费），
  内存池不检查；`nSequence`/`nLockTime` 的"可替换交易"；孤儿交易。
* **钱包**：整笔交易一个 `fSpent` 标志；找零付回**原币的同一个公钥**；
  原版的随机逼近选币算法；零确认的收款计入余额；挖矿所得等 120 个块；
  矿工的收款密钥只在挖到块时才存盘；重启后把未确认交易放回内存池；每 10 分钟重播一次。

## 有意的改动

| 方面 | 原版 | 本项目 |
|---|---|---|
| 网络 | 公开主网，靠 IRC 频道发现节点 | 私有网络，用 `-connect` / `-addnode` 手动互连 |
| 创世块 | 《泰晤士报》2009-01-03 头版标题 | 自己的创世块（[tools/mine_genesis.py](tools/mine_genesis.py)），收款公钥沿用中本聪的作为致敬 |
| 难度 | 工作量上限 `~0 >> 32` | `~0 >> 8`，创世难度 `0x1f00ffff`，Python 约 0.05 秒挖一个块 |
| 端口 | 8333 | 18444 |
| 挖矿 | 没有邻居时不挖 | 允许单机挖矿 |
| 存储 | Berkeley DB | sqlite（`blk0001.dat` 的格式与原版相同） |
| 密码学 | OpenSSL | 纯 Python 的 `ecdsa` 库、hashlib、自带的 RIPEMD-160；签名用 RFC 6979 确定性随机数 |
| 界面 | wxWidgets，英文 | PySide6，中文；"全部/已发送/已接收"标签页其实是 0.2 版才有的 |
| 安全加固 | —— | `OP_LSHIFT` 的移位量上限 2048、孤儿交易最多 1000 笔（原版无上限，可被用来耗尽内存） |
| 未实现 | `market.cpp`（未完成的交易市场）、按 IP 地址直接付款、IRC | 相关消息被当作未知命令忽略 |

## 目录结构

```
bitcoin/      核心库，模块划分与原版 C++ 文件一一对应（见 bitcoin/__init__.py）
qt/           图形界面
labs/         12 个动手实验
tests/        测试（含真实主网数据 tests/data/）
docs/         教程
tools/        挖创世块、生成截图的小工具
run_node.py   无界面节点        run_gui.py   带界面节点
```

## 许可证

MIT/X11，与原版相同。见 [LICENSE](LICENSE)。
