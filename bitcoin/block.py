"""CBlock / merkle tree / CBlockIndex / CBlockLocator from main.h."""

from . import params
from .hashes import hash256
from .serialize import DataStream
from .tx import CTransaction


class CBlock:
    def __init__(self, n_version: int = 1, hash_prev_block: int = 0,
                 hash_merkle_root: int = 0, n_time: int = 0,
                 n_bits: int = 0, n_nonce: int = 0,
                 vtx: list[CTransaction] | None = None):
        self.n_version = n_version
        self.hash_prev_block = hash_prev_block
        self.hash_merkle_root = hash_merkle_root
        self.n_time = n_time
        self.n_bits = n_bits
        self.n_nonce = n_nonce
        self.vtx: list[CTransaction] = vtx if vtx is not None else []

    # --- serialization ---
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
        """Block hash = SHA256d of the 80-byte header only."""
        return int.from_bytes(hash256(self.header_bytes()), "little")

    # --- merkle ---
    def build_merkle_tree(self) -> list[int]:
        """BuildMerkleTree — odd levels duplicate the last entry."""
        tree: list[int] = [tx.get_hash() for tx in self.vtx]
        j = 0
        size = len(self.vtx)
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
        target = params.compact_to_target(self.n_bits)
        if target <= 0 or target > params.PROOF_OF_WORK_LIMIT:
            return False
        return self.get_hash() <= target


class CBlockIndex:
    """In-memory index entry, one per known block (main.h CBlockIndex)."""

    def __init__(self, block: CBlock, hash_: int):
        self.hash = hash_
        self.pprev: CBlockIndex | None = None
        self.pnext: CBlockIndex | None = None  # set along the best chain
        self.n_height = 0
        # header copy
        self.n_version = block.n_version
        self.hash_merkle_root = block.hash_merkle_root
        self.n_time = block.n_time
        self.n_bits = block.n_bits
        self.n_nonce = block.n_nonce
        self.hash_prev_block = block.hash_prev_block

    def get_median_time_past(self, span: int = 11) -> int:
        """GetMedianTimePast over the last `span` blocks."""
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
    """CBlockLocator — exponentially thinning list of hashes back to genesis."""

    def __init__(self, v_have: list[int] | None = None):
        self.v_have: list[int] = v_have if v_have is not None else []

    @classmethod
    def from_index(cls, pindex: CBlockIndex | None, genesis_hash: int) -> "CBlockLocator":
        v_have = []
        step = 1
        while pindex is not None:
            v_have.append(pindex.hash)
            for _ in range(step):
                if pindex is None:
                    break
                pindex = pindex.pprev
            if len(v_have) > 10:
                step *= 2
        if not v_have or v_have[-1] != genesis_hash:
            v_have.append(genesis_hash)
        return cls(v_have)

    def serialize(self, s: DataStream):
        s.write_int32(params.VERSION)
        s.write_vector(self.v_have, s.write_uint256)

    @classmethod
    def deserialize(cls, s: DataStream) -> "CBlockLocator":
        s.read_int32()  # nVersion, unused
        return cls(s.read_vector(s.read_uint256))
