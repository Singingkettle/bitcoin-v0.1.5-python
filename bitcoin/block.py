"""区块相关的数据结构——对应原版 main.h 的 CBlock / CBlockIndex / CBlockLocator。

区块 = 80 字节的区块头 + 一串交易。
区块头里的 hashPrevBlock 指向上一个区块，于是所有区块串成一条"链"；
hashMerkleRoot 是本区块全部交易的"指纹"，改动任何一笔交易都会让它变化。
"""

from . import params
from .hashes import hash256
from .serialize import DataStream
from .tx import CTransaction


class CBlock:
    def __init__(self, n_version: int = 1, hash_prev_block: int = 0,
                 hash_merkle_root: int = 0, n_time: int = 0,
                 n_bits: int = 0, n_nonce: int = 0,
                 vtx: list[CTransaction] | None = None):
        # ---- 区块头（共 80 字节）----
        self.n_version = n_version                  # 4 字节：版本
        self.hash_prev_block = hash_prev_block      # 32 字节：上一个区块的哈希
        self.hash_merkle_root = hash_merkle_root    # 32 字节：交易默克尔树的根
        self.n_time = n_time                        # 4 字节：时间戳
        self.n_bits = n_bits                        # 4 字节：难度目标（紧凑编码）
        self.n_nonce = n_nonce                      # 4 字节：矿工反复尝试的那个随机数
        # ---- 区块体 ----
        self.vtx: list[CTransaction] = vtx if vtx is not None else []

    # ------------------------------------------------------------ 序列化
    def serialize_header(self, s: DataStream):
        s.write_int32(self.n_version)
        s.write_uint256(self.hash_prev_block)
        s.write_uint256(self.hash_merkle_root)
        s.write_uint32(self.n_time)
        s.write_uint32(self.n_bits)
        s.write_uint32(self.n_nonce)

    def serialize(self, s: DataStream):
        self.serialize_header(s)
        s.write_vector(self.vtx, lambda tx: tx.serialize(s))

    @classmethod
    def deserialize(cls, s: DataStream) -> "CBlock":
        b = cls()
        b.n_version = s.read_int32()
        b.hash_prev_block = s.read_uint256()
        b.hash_merkle_root = s.read_uint256()
        b.n_time = s.read_uint32()
        b.n_bits = s.read_uint32()
        b.n_nonce = s.read_uint32()
        b.vtx = s.read_vector(lambda: CTransaction.deserialize(s))
        return b

    def serialized(self) -> bytes:
        s = DataStream()
        self.serialize(s)
        return s.getvalue()

    def header_bytes(self) -> bytes:
        s = DataStream()
        self.serialize_header(s)
        return s.getvalue()

    def get_hash(self) -> int:
        """区块哈希 = 只对 80 字节的区块头做两次 SHA-256（与区块里有多少交易无关，
        交易是通过 hashMerkleRoot 间接被"签"进去的）。"""
        return int.from_bytes(hash256(self.header_bytes()), "little")

    # ------------------------------------------------------------ 默克尔树
    def build_merkle_tree(self) -> list[int]:
        """BuildMerkleTree：交易哈希两两配对再哈希，一层层往上，直到只剩一个根。

        某一层是奇数个时，最后一个和**它自己**配对（min(i+1, size-1)）。
        返回整棵树（一维数组：先是所有叶子，再是上一层……最后一个元素就是根）。
        """
        tree: list[int] = [tx.get_hash() for tx in self.vtx]
        j = 0                       # 当前这一层在数组里的起始下标
        size = len(self.vtx)        # 当前这一层的节点数
        while size > 1:
            for i in range(0, size, 2):
                i2 = min(i + 1, size - 1)
                combined = (tree[j + i].to_bytes(32, "little")
                            + tree[j + i2].to_bytes(32, "little"))
                tree.append(int.from_bytes(hash256(combined), "little"))
            j += size
            size = (size + 1) // 2
        return tree

    def get_merkle_root(self) -> int:
        tree = self.build_merkle_tree()
        return tree[-1] if tree else 0

    def check_proof_of_work(self) -> bool:
        """工作量证明：区块哈希（当成一个 256 位整数）必须不大于难度目标。"""
        target = params.compact_to_target(self.n_bits)
        if target <= 0 or target > params.PROOF_OF_WORK_LIMIT:
            return False            # 声称的难度比允许的最低难度还低
        return self.get_hash() <= target


class CBlockIndex:
    """区块索引：每个已知区块在内存里的一条"目录项"（只记区块头和它在磁盘上的位置）。

    pprev 指向父块，把所有目录项连成一棵树（有分叉时是树，不是链）；
    pnext 只沿着"最佳链"设置，指向链上的下一个块。
    """

    def __init__(self, block: CBlock, hash_: int, n_file_pos: int = 0):
        self.hash = hash_
        self.pprev: CBlockIndex | None = None
        self.pnext: CBlockIndex | None = None
        self.n_height = 0
        self.n_file_pos = n_file_pos        # 区块在 blk0001.dat 里的偏移
        # 区块头的副本
        self.n_version = block.n_version
        self.hash_prev_block = block.hash_prev_block
        self.hash_merkle_root = block.hash_merkle_root
        self.n_time = block.n_time
        self.n_bits = block.n_bits
        self.n_nonce = block.n_nonce

    def get_median_time_past(self, span: int = 11) -> int:
        """GetMedianTimePast：最近 11 个块的时间戳的中位数。
        新区块的时间必须晚于它——单个矿工乱填时间戳也没法把时间往回拨。"""
        times = []
        pindex = self
        for _ in range(span):
            if pindex is None:
                break
            times.append(pindex.n_time)
            pindex = pindex.pprev
        times.sort()
        return times[len(times) // 2]

    def __repr__(self):
        return f"CBlockIndex(height={self.n_height}, hash={self.hash:064x})"


class CBlockLocator:
    """区块定位器：用很少的几个哈希向对方描述"我的链长什么样"。

    从链尖往回列哈希：前 10 个一步一个，之后步长每次翻倍（1,1,...,2,4,8...），
    最后总是附上创世块。对方从前往后找到第一个自己主链上也有的哈希，
    那就是两条链的分叉点，从那里开始把后面的块发过来就行了。
    哪怕链有几十万个块，定位器也只有几十个哈希。
    """

    def __init__(self, v_have: list[int] | None = None):
        self.v_have: list[int] = v_have if v_have is not None else []

    @classmethod
    def from_index(cls, pindex: CBlockIndex | None, genesis_hash: int) -> "CBlockLocator":
        v_have = []
        step = 1
        while pindex is not None:
            v_have.append(pindex.hash)
            for _ in range(step):               # 往回走 step 步
                if pindex is None:
                    break
                pindex = pindex.pprev
            if len(v_have) > 10:
                step *= 2
        v_have.append(genesis_hash)             # 原版无条件追加（即使重复）
        return cls(v_have)

    def serialize(self, s: DataStream):
        s.write_int32(params.VERSION)
        s.write_vector(self.v_have, s.write_uint256)

    @classmethod
    def deserialize(cls, s: DataStream) -> "CBlockLocator":
        s.read_int32()      # nVersion，用不到
        return cls(s.read_vector(s.read_uint256))
