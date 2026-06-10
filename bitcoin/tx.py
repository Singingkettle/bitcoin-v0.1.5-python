"""COutPoint / CTxIn / CTxOut / CTransaction from main.h.

Script fields are stored as raw bytes here; script.py wraps them in
CScript when it needs to parse them (avoids a circular import, same
split as main.h including script.h).
"""

from . import params
from .hashes import hash256
from .serialize import DataStream


class COutPoint:
    def __init__(self, hash_: int = 0, n: int = 0xFFFFFFFF):
        self.hash = hash_      # uint256 as int
        self.n = n             # uint32; -1 stored as 0xFFFFFFFF

    def is_null(self) -> bool:
        return self.hash == 0 and self.n == 0xFFFFFFFF

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
    def __init__(self, prevout: COutPoint | None = None,
                 script_sig: bytes = b"", n_sequence: int = 0xFFFFFFFF):
        self.prevout = prevout if prevout is not None else COutPoint()
        self.script_sig = script_sig
        self.n_sequence = n_sequence

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
    def __init__(self, n_value: int = -1, script_pubkey: bytes = b""):
        self.n_value = n_value
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
        self.n_lock_time = n_lock_time

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
        """uint256 as int; display with uint256_to_hex()."""
        return int.from_bytes(hash256(self.serialized()), "little")

    def get_serialize_size(self) -> int:
        return len(self.serialized())

    def is_coinbase(self) -> bool:
        return len(self.vin) == 1 and self.vin[0].prevout.is_null()

    def get_value_out(self) -> int:
        total = 0
        for txout in self.vout:
            if txout.n_value < 0:
                raise ValueError("GetValueOut() : negative value")
            total += txout.n_value
        return total

    def get_min_fee(self, f_discount: bool = False) -> int:
        """CTransaction::GetMinFee — 1 cent per started kilobyte; small
        transactions can go free; sub-cent outputs always pay."""
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
        """CTransaction::CheckTransaction — context-free checks."""
        if not self.vin or not self.vout:
            return False
        for txout in self.vout:
            if txout.n_value < 0:
                return False
        if self.is_coinbase():
            if not 2 <= len(self.vin[0].script_sig) <= 100:
                return False
        else:
            for txin in self.vin:
                if txin.prevout.is_null():
                    return False
        return True
