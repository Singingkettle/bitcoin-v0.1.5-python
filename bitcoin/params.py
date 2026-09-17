"""共识常量与网络常量（对应原版 main.h / net.h / serialize.h）。

除了标注【私网改动】的地方，其余数值都与中本聪 v0.1.5 完全一致。
私网改动的目的只有一个：让纯 Python 的 CPU 挖矿也能在一秒内出块，
并且不会和真正的比特币网络互相干扰。
"""

# ---------------------------------------------------------------- serialize.h
VERSION = 105                 # v0.1.5 的协议版本号，同时也是序列化版本号
MAX_SIZE = 0x02000000         # 32 MiB：反序列化时的"理智上限"，防止恶意超大长度
                              # （注意：0.1.5 还没有 1 MB 区块上限，那是 2010 年才加的）

# 序列化"模式"标志：同一个对象在不同场景下写出的字节可能不同
SER_NETWORK = 1 << 0          # 发到网络上
SER_DISK = 1 << 1             # 写到磁盘上
SER_GETHASH = 1 << 2          # 为了计算哈希而序列化

# --------------------------------------------------------------------- main.h
COIN = 100_000_000            # 1 BTC = 1 亿聪（satoshi）。全项目金额一律用"聪"的整数
CENT = 1_000_000              # 0.01 BTC，手续费体系的基本单位
COINBASE_MATURITY = 100       # 挖矿所得要等 100 个确认才能花（共识规则）

# 难度调整（main.cpp 的 GetNextWorkRequired）
TARGET_TIMESPAN = 14 * 24 * 60 * 60           # 两周
TARGET_SPACING = 10 * 60                      # 目标出块间隔：十分钟
INTERVAL = TARGET_TIMESPAN // TARGET_SPACING  # 2016：每隔这么多块调整一次难度

# ---------------------------------------------------------------------- net.h
MESSAGE_START = b"\xf9\xbe\xb4\xd9"   # 网络魔数，沿用主网原值
NODE_NETWORK = 1                      # 服务标志位：我是保存完整区块链的全节点
COMMAND_SIZE = 12                     # 消息头里命令名占 12 字节
MESSAGE_HEADER_SIZE = 4 + COMMAND_SIZE + 4   # 魔数 + 命令 + 长度 = 20 字节，没有校验和

MSG_TX = 1                    # 库存条目类型：交易
MSG_BLOCK = 2                 # 库存条目类型：区块

DEFAULT_PORT = 18444          # 【私网改动】原版主网端口是 8333

# ------------------------------------------------------------------ 工作量证明
# 原版：bnProofOfWorkLimit = ~uint256(0) >> 32（哈希的前 32 位必须为 0）
# 【私网改动】放宽到 >> 8，配合下面的创世难度，CPython 约 0.1 秒就能挖出一块
PROOF_OF_WORK_LIMIT = ((1 << 256) - 1) >> 8

# ---------------------------------------------- 创世块（【私网改动】整组都是）
# 这组常量由 tools/mine_genesis.py 挖出后回填，每个节点第一次启动时据此重建创世块
GENESIS_TIMESTAMP_TEXT = b"D:/Projects/BTC 10/Jun/2026 Python re-creation of Bitcoin v0.1.5"
GENESIS_PUBKEY = bytes.fromhex(
    "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
    "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f"
)  # 中本聪创世块的原公钥，作为致敬沿用（没人有对应私钥，这 50 BTC 永远花不掉）
GENESIS_VERSION = 1
GENESIS_TIME = 1780272000     # 2026-06-01 00:00:00 UTC（必须是过去的时间）
GENESIS_BITS = 0x1F00FFFF     # 创世难度：大约每 65536 次哈希命中一次
GENESIS_NONCE = 7913
GENESIS_HASH = "00001fce11f81d65c4044b674cfdae590a2a07a84716c0f59fe39afaf1dd033f"
GENESIS_MERKLE_ROOT = "cb9c17630861bb550b2962e860b65c9be5d3e9297654f3a6f978cb0a3a871146"


def block_value(n_best_height: int, fees: int) -> int:
    """GetBlockValue：区块奖励 = 补贴 + 手续费。

    补贴从 50 BTC 开始，每 21 万个块右移一位（减半）。
    注意原版用的是全局变量 nBestHeight（"当前最佳链高度"），而不是这个块
    自己的高度——所以调用方传进来的也必须是"当前最佳高度"。
    """
    subsidy = 50 * COIN
    subsidy >>= n_best_height // 210_000
    return subsidy + fees


def compact_to_target(bits: int) -> int:
    """CBigNum::SetCompact：把 32 位的紧凑难度 nBits 展开成 256 位目标值。

    nBits 是一种"以 256 为底的科学计数法"：最高字节是指数（目标值占几个字节），
    低 23 位是尾数。例如 0x1F00FFFF -> 0xFFFF 左移 8*(31-3) 位。
    """
    size = bits >> 24
    word = bits & 0x007FFFFF
    if size <= 3:
        return word >> (8 * (3 - size))
    return word << (8 * (size - 3))


def target_to_compact(target: int) -> int:
    """CBigNum::GetCompact：把 256 位目标值压缩回 nBits（有损，只保留约 3 字节精度）。"""
    size = (target.bit_length() + 7) // 8
    if size <= 3:
        word = target << (8 * (3 - size))
    else:
        word = target >> (8 * (size - 3))
    # 尾数的最高位是符号位（原版借用了 OpenSSL 的 MPI 格式），正数不能占用它，
    # 所以一旦最高位被占，就把尾数右移一个字节、指数加一来补偿。
    if word & 0x00800000:
        word >>= 8
        size += 1
    return word | (size << 24)
