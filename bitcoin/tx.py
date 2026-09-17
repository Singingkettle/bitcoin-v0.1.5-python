"""交易的数据结构——对应原版 main.h 里的 COutPoint / CTxIn / CTxOut / CTransaction。

一笔交易 = 若干"输入"（我要花哪几笔以前收到的钱）+ 若干"输出"（这些钱现在给谁）。

本文件里脚本字段只是普通的 bytes；需要解析脚本时由 script.py 把它包成 CScript。
这样分层和原版一致（main.h 只是"拿着"脚本，script.h 才"理解"脚本），
也避免了两个模块互相 import。
"""

from . import params
from .hashes import hash256
from .serialize import DataStream

UINT_MAX = 0xFFFFFFFF


class COutPoint:
    """"某笔交易的第 n 个输出"——用 (交易哈希, 序号) 精确指向一笔钱。"""

    def __init__(self, hash_: int = 0, n: int = UINT_MAX):
        self.hash = hash_      # 前一笔交易的哈希（uint256，用 int 表示）
        self.n = n             # 那笔交易的第几个输出；原版的 -1 在这里就是 0xFFFFFFFF

    def is_null(self) -> bool:
        """空指针：只有 coinbase（凭空造币）交易的输入才会这样。"""
        return self.hash == 0 and self.n == UINT_MAX

    def serialize(self, s: DataStream):
        s.write_uint256(self.hash)
        s.write_uint32(self.n)

    @classmethod
    def deserialize(cls, s: DataStream) -> "COutPoint":
        return cls(s.read_uint256(), s.read_uint32())

    def __eq__(self, other):
        return self.hash == other.hash and self.n == other.n

    def __hash__(self):
        return hash((self.hash, self.n))

    def __repr__(self):
        return f"COutPoint({self.hash:064x}, {self.n})"


class CTxIn:
    """交易输入：指向一笔以前的输出（prevout），并出示"我有权花它"的证明（scriptSig）。"""

    def __init__(self, prevout: COutPoint | None = None,
                 script_sig: bytes = b"", n_sequence: int = UINT_MAX):
        self.prevout = prevout if prevout is not None else COutPoint()
        self.script_sig = script_sig
        self.n_sequence = n_sequence    # 序列号：配合 nLockTime 实现"可替换的交易"

    def is_final(self) -> bool:
        """序列号拉满表示"这个输入已经定稿，不会再有新版本"。"""
        return self.n_sequence == UINT_MAX

    def serialize(self, s: DataStream):
        self.prevout.serialize(s)
        s.write_string(bytes(self.script_sig))
        s.write_uint32(self.n_sequence)

    @classmethod
    def deserialize(cls, s: DataStream) -> "CTxIn":
        prevout = COutPoint.deserialize(s)
        script_sig = s.read_string()
        n_sequence = s.read_uint32()
        return cls(prevout, script_sig, n_sequence)


class CTxOut:
    """交易输出：一笔金额 + 一把"锁"（scriptPubKey，规定谁能花这笔钱）。"""

    def __init__(self, n_value: int = -1, script_pubkey: bytes = b""):
        self.n_value = n_value              # 金额，单位是聪
        self.script_pubkey = script_pubkey

    def is_null(self) -> bool:
        return self.n_value == -1

    def serialize(self, s: DataStream):
        s.write_int64(self.n_value)
        s.write_string(bytes(self.script_pubkey))

    @classmethod
    def deserialize(cls, s: DataStream) -> "CTxOut":
        n_value = s.read_int64()
        script_pubkey = s.read_string()
        return cls(n_value, script_pubkey)


class CTransaction:
    def __init__(self, n_version: int = 1, vin: list[CTxIn] | None = None,
                 vout: list[CTxOut] | None = None, n_lock_time: int = 0):
        self.n_version = n_version
        self.vin: list[CTxIn] = vin if vin is not None else []
        self.vout: list[CTxOut] = vout if vout is not None else []
        self.n_lock_time = n_lock_time      # 锁定时间：在这个区块高度之前交易不算"定稿"

    # ------------------------------------------------------------ 序列化
    def serialize(self, s: DataStream):
        s.write_int32(self.n_version)
        s.write_vector(self.vin, lambda i: i.serialize(s))
        s.write_vector(self.vout, lambda o: o.serialize(s))
        s.write_uint32(self.n_lock_time)

    @classmethod
    def deserialize(cls, s: DataStream) -> "CTransaction":
        tx = cls()
        tx.n_version = s.read_int32()
        tx.vin = s.read_vector(lambda: CTxIn.deserialize(s))
        tx.vout = s.read_vector(lambda: CTxOut.deserialize(s))
        tx.n_lock_time = s.read_uint32()
        return tx

    def serialized(self) -> bytes:
        s = DataStream()
        self.serialize(s)
        return s.getvalue()

    def get_hash(self) -> int:
        """交易哈希（交易 ID）= 对整笔交易的序列化字节做两次 SHA-256。
        返回 int；要显示给人看请用 uint256_to_hex()。"""
        return int.from_bytes(hash256(self.serialized()), "little")

    def get_serialize_size(self) -> int:
        return len(self.serialized())

    # -------------------------------------------------------------- 判断
    def is_coinbase(self) -> bool:
        """coinbase 交易：只有一个输入且该输入指向"空"。每个区块的第一笔交易，凭空造出区块奖励。"""
        return len(self.vin) == 1 and self.vin[0].prevout.is_null()

    def is_final(self, n_best_height: int) -> bool:
        """IsFinal：交易是否已经"定稿"。

        nLockTime 为 0，或者已经小于当前最佳高度 -> 定稿；
        否则要求每个输入的序列号都拉满才算定稿。
        未定稿的交易可以被同一批输入、序列号更高的新版本替换，矿工也不会打包它。
        """
        if self.n_lock_time == 0 or self.n_lock_time < n_best_height:
            return True
        return all(txin.is_final() for txin in self.vin)

    def is_newer_than(self, old: "CTransaction") -> bool:
        """IsNewerThan：self 是不是 old 的"更新版本"（花的是完全相同的输入，序列号更高）。

        这段逻辑逐行照搬原版：在两边序列号不同的那些输入里，找出最小的序列号，
        最小值出现在 old 那边，就说明 self 更新。
        """
        if len(self.vin) != len(old.vin):
            return False
        for mine, theirs in zip(self.vin, old.vin):
            if mine.prevout != theirs.prevout:
                return False

        f_newer = False
        n_lowest = UINT_MAX
        for mine, theirs in zip(self.vin, old.vin):
            if mine.n_sequence != theirs.n_sequence:
                if mine.n_sequence <= n_lowest:
                    f_newer = False
                    n_lowest = mine.n_sequence
                if theirs.n_sequence < n_lowest:
                    f_newer = True
                    n_lowest = theirs.n_sequence
        return f_newer

    # -------------------------------------------------------------- 金额
    def get_value_out(self) -> int:
        total = 0
        for txout in self.vout:
            if txout.n_value < 0:
                raise ValueError("GetValueOut() : 输出金额为负")
            total += txout.n_value
        return total

    def get_min_fee(self, f_discount: bool = False) -> int:
        """GetMinFee：这笔交易至少该付多少手续费（逐行对应原版）。

        - 基础费率：每"开始的"1000 字节 0.01 BTC；
        - f_discount 为真且小于 10000 字节：免费
          （矿工打包时，每个区块的前 100 笔交易享受这个优惠）；
        - 防"粉尘"：只要有任何一个输出小于 0.01 BTC，就至少收 0.01 BTC。
        """
        n_bytes = self.get_serialize_size()
        min_fee = (1 + n_bytes // 1000) * params.CENT
        if f_discount and n_bytes < 10000:
            min_fee = 0
        if min_fee < params.CENT:
            for txout in self.vout:
                if txout.n_value < params.CENT:
                    min_fee = params.CENT
                    break
        return min_fee

    def check_transaction(self) -> bool:
        """CheckTransaction：不依赖任何上下文就能做的基本检查。"""
        if not self.vin or not self.vout:
            return False                    # 输入和输出都不能为空
        for txout in self.vout:
            if txout.n_value < 0:
                return False                # 金额不能为负
        if self.is_coinbase():
            if not 2 <= len(self.vin[0].script_sig) <= 100:
                return False                # coinbase 的 scriptSig 必须是 2~100 字节
        else:
            for txin in self.vin:
                if txin.prevout.is_null():
                    return False            # 普通交易的输入不能指向"空"
        return True
