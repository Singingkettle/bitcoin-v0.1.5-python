"""Consensus and network constants.

Faithful to v0.1.5 (main.h / net.h / serialize.h) except where marked
PRIVATE-NET: this re-creation runs its own network with a custom genesis
block and a much lower difficulty so pure-Python CPU mining finds blocks
in seconds.
"""

# --- serialize.h ---
VERSION = 105                 # protocol / serialization version of v0.1.5
MAX_SIZE = 0x02000000         # 32 MiB sanity limit (no 1 MB block limit in 0.1.5)

# serialization type flags
SER_NETWORK = 1 << 0
SER_DISK = 1 << 1
SER_GETHASH = 1 << 2

# --- main.h ---
COIN = 100_000_000
CENT = 1_000_000
COINBASE_MATURITY = 100

# difficulty retarget (main.cpp GetNextWorkRequired)
TARGET_TIMESPAN = 14 * 24 * 60 * 60   # two weeks
TARGET_SPACING = 10 * 60              # ten minutes
INTERVAL = TARGET_TIMESPAN // TARGET_SPACING  # 2016

# --- net.h ---
MESSAGE_START = b"\xf9\xbe\xb4\xd9"   # original mainnet magic, kept as-is
NODE_NETWORK = 1
COMMAND_SIZE = 12
MESSAGE_HEADER_SIZE = 4 + COMMAND_SIZE + 4   # magic + command + length, NO checksum

MSG_TX = 1
MSG_BLOCK = 2

# PRIVATE-NET: 18444 instead of the original 8333
DEFAULT_PORT = 18444

# --- proof of work ---
# original: bnProofOfWorkLimit = ~uint256(0) >> 32
# PRIVATE-NET: ~uint256(0) >> 8 so CPython finds blocks in well under a second
PROOF_OF_WORK_LIMIT = (1 << 256) - 1 >> 8

# --- genesis block (PRIVATE-NET, mined by tools/mine_genesis.py) ---
GENESIS_TIMESTAMP_TEXT = b"D:/Projects/BTC 10/Jun/2026 Python re-creation of Bitcoin v0.1.5"
GENESIS_PUBKEY = bytes.fromhex(
    "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
    "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f"
)  # Satoshi's genesis pubkey, reused as a tribute (output is unspendable for us)
GENESIS_VERSION = 1
GENESIS_TIME = 1781136000     # 2026-06-11 00:00:00 UTC
GENESIS_BITS = 0x1F00FFFF     # PRIVATE-NET easy difficulty (~1 in 65k hashes)
GENESIS_NONCE = 0             # filled in by tools/mine_genesis.py
GENESIS_HASH = None           # filled in by tools/mine_genesis.py (hex string, big-endian)
GENESIS_MERKLE_ROOT = None    # filled in by tools/mine_genesis.py

# block reward (main.cpp GetBlockValue)
def block_value(height: int, fees: int) -> int:
    subsidy = 50 * COIN
    subsidy >>= height // 210_000
    return subsidy + fees


def compact_to_target(bits: int) -> int:
    """CBigNum::SetCompact — nBits to a 256-bit target."""
    size = bits >> 24
    word = bits & 0x007FFFFF
    if size <= 3:
        return word >> (8 * (3 - size))
    return word << (8 * (size - 3))


def target_to_compact(target: int) -> int:
    """CBigNum::GetCompact."""
    size = (target.bit_length() + 7) // 8
    if size <= 3:
        word = target << (8 * (3 - size))
    else:
        word = target >> (8 * (size - 3))
    # avoid the sign bit of the mantissa
    if word & 0x00800000:
        word >>= 8
        size += 1
    return word | (size << 24)
