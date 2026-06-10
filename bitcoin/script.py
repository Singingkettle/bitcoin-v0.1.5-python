"""script.h / script.cpp — opcodes, CScript, EvalScript, Solver, signing.

The defining 0.1.x quirks reproduced here:

* Script numbers are CBigNum — arbitrary precision, little-endian
  sign-magnitude encoding.
* Verification concatenates the scripts and evaluates ONCE:
  EvalScript(scriptSig + OP_CODESEPARATOR + scriptPubKey, txTo, nIn).
* OP_CHECKSIG takes the hashtype byte off the end of the DER signature,
  FindAndDelete()s the signature push from the subscript, and hashes the
  transaction with SignatureHash().
* SignatureHash() returns 1 (not an error) when nIn is out of range —
  the famous "return one" bug, kept for fidelity.
* OP_CHECKMULTISIG pops one extra value off the stack.
* No opcodes are disabled — the splice/bitwise ops still work in 0.1.5.
"""

import struct

from . import params
from .hashes import hash160, hash256, sha1, sha256
from .key import CKey
from .serialize import DataStream
from .tx import CTransaction, CTxIn, CTxOut, COutPoint

# --- signature hash types ---
SIGHASH_ALL = 1
SIGHASH_NONE = 2
SIGHASH_SINGLE = 3
SIGHASH_ANYONECANPAY = 0x80

# --- opcodes (script.h enum, complete for v0.1.5) ---
OP_0 = 0x00
OP_PUSHDATA1 = 0x4C
OP_PUSHDATA2 = 0x4D
OP_PUSHDATA4 = 0x4E
OP_1NEGATE = 0x4F
OP_RESERVED = 0x50
OP_1 = 0x51
OP_2, OP_3, OP_4, OP_5, OP_6, OP_7, OP_8 = range(0x52, 0x59)
OP_9, OP_10, OP_11, OP_12, OP_13, OP_14, OP_15, OP_16 = range(0x59, 0x61)
OP_NOP = 0x61
OP_VER = 0x62
OP_IF = 0x63
OP_NOTIF = 0x64
OP_VERIF = 0x65
OP_VERNOTIF = 0x66
OP_ELSE = 0x67
OP_ENDIF = 0x68
OP_VERIFY = 0x69
OP_RETURN = 0x6A
OP_TOALTSTACK = 0x6B
OP_FROMALTSTACK = 0x6C
OP_2DROP = 0x6D
OP_2DUP = 0x6E
OP_3DUP = 0x6F
OP_2OVER = 0x70
OP_2ROT = 0x71
OP_2SWAP = 0x72
OP_IFDUP = 0x73
OP_DEPTH = 0x74
OP_DROP = 0x75
OP_DUP = 0x76
OP_NIP = 0x77
OP_OVER = 0x78
OP_PICK = 0x79
OP_ROLL = 0x7A
OP_ROT = 0x7B
OP_SWAP = 0x7C
OP_TUCK = 0x7D
OP_CAT = 0x7E
OP_SUBSTR = 0x7F
OP_LEFT = 0x80
OP_RIGHT = 0x81
OP_SIZE = 0x82
OP_INVERT = 0x83
OP_AND = 0x84
OP_OR = 0x85
OP_XOR = 0x86
OP_EQUAL = 0x87
OP_EQUALVERIFY = 0x88
OP_RESERVED1 = 0x89
OP_RESERVED2 = 0x8A
OP_1ADD = 0x8B
OP_1SUB = 0x8C
OP_2MUL = 0x8D
OP_2DIV = 0x8E
OP_NEGATE = 0x8F
OP_ABS = 0x90
OP_NOT = 0x91
OP_0NOTEQUAL = 0x92
OP_ADD = 0x93
OP_SUB = 0x94
OP_MUL = 0x95
OP_DIV = 0x96
OP_MOD = 0x97
OP_LSHIFT = 0x98
OP_RSHIFT = 0x99
OP_BOOLAND = 0x9A
OP_BOOLOR = 0x9B
OP_NUMEQUAL = 0x9C
OP_NUMEQUALVERIFY = 0x9D
OP_NUMNOTEQUAL = 0x9E
OP_LESSTHAN = 0x9F
OP_GREATERTHAN = 0xA0
OP_LESSTHANOREQUAL = 0xA1
OP_GREATERTHANOREQUAL = 0xA2
OP_MIN = 0xA3
OP_MAX = 0xA4
OP_WITHIN = 0xA5
OP_RIPEMD160 = 0xA6
OP_SHA1 = 0xA7
OP_SHA256 = 0xA8
OP_HASH160 = 0xA9
OP_HASH256 = 0xAA
OP_CODESEPARATOR = 0xAB
OP_CHECKSIG = 0xAC
OP_CHECKSIGVERIFY = 0xAD
OP_CHECKMULTISIG = 0xAE
OP_CHECKMULTISIGVERIFY = 0xAF


# --- CBigNum vector encoding (bignum.h getvch/setvch) ---
def bn_serialize(n: int) -> bytes:
    """Little-endian magnitude, sign bit in the high bit of the last byte."""
    if n == 0:
        return b""
    neg = n < 0
    mag = abs(n)
    out = bytearray()
    while mag:
        out.append(mag & 0xFF)
        mag >>= 8
    if out[-1] & 0x80:
        out.append(0x80 if neg else 0x00)
    elif neg:
        out[-1] |= 0x80
    return bytes(out)


def bn_deserialize(b: bytes) -> int:
    if not b:
        return 0
    neg = bool(b[-1] & 0x80)
    mag = bytearray(b)
    mag[-1] &= 0x7F
    n = int.from_bytes(mag, "little")
    return -n if neg else n


def cast_to_bool(b: bytes) -> bool:
    """CastToBool — false for empty, all-zero, and negative zero."""
    for i, byte in enumerate(b):
        if byte != 0:
            if i == len(b) - 1 and byte == 0x80:
                return False  # negative zero
            return True
    return False


class CScript(bytearray):
    """Script byte vector with the CScript operator<< push helpers."""

    def push_opcode(self, opcode: int) -> "CScript":
        self.append(opcode)
        return self

    def push_data(self, data: bytes) -> "CScript":
        n = len(data)
        if n < OP_PUSHDATA1:
            self.append(n)
        elif n <= 0xFF:
            self.append(OP_PUSHDATA1)
            self.append(n)
        elif n <= 0xFFFF:
            self.append(OP_PUSHDATA2)
            self += struct.pack("<H", n)
        else:
            self.append(OP_PUSHDATA4)
            self += struct.pack("<I", n)
        self += data
        return self

    def push_int(self, n: int) -> "CScript":
        """operator<<(int64) — small values get OP_1NEGATE/OP_1..OP_16,
        everything else is a CBigNum push (this is exactly how the genesis
        coinbase encoded 486604799 and 4)."""
        if n == -1 or 1 <= n <= 16:
            self.append(n + (OP_1 - 1))
        else:
            self.push_data(bn_serialize(n))
        return self

    def get_op(self, pc: int):
        """GetOp — returns (new_pc, opcode, pushed_bytes_or_None);
        raises IndexError past the end, ValueError on a truncated push."""
        if pc >= len(self):
            raise IndexError("end of script")
        opcode = self[pc]
        pc += 1
        data = None
        if opcode <= OP_PUSHDATA4:
            if opcode < OP_PUSHDATA1:
                n = opcode
            elif opcode == OP_PUSHDATA1:
                if pc + 1 > len(self):
                    raise ValueError("truncated PUSHDATA1")
                n = self[pc]
                pc += 1
            elif opcode == OP_PUSHDATA2:
                if pc + 2 > len(self):
                    raise ValueError("truncated PUSHDATA2")
                n = struct.unpack_from("<H", self, pc)[0]
                pc += 2
            else:
                if pc + 4 > len(self):
                    raise ValueError("truncated PUSHDATA4")
                n = struct.unpack_from("<I", self, pc)[0]
                pc += 4
            if pc + n > len(self):
                raise ValueError("push past end of script")
            data = bytes(self[pc:pc + n])
            pc += n
        return pc, opcode, data

    def ops(self):
        pc = 0
        while pc < len(self):
            pc, opcode, data = self.get_op(pc)
            yield opcode, data

    def find_and_delete(self, b: bytes) -> int:
        """FindAndDelete — remove byte-exact occurrences of `b`, checked at
        op boundaries, exactly like the original loop."""
        if not b:
            return 0
        found = 0
        pc = 0
        while True:
            while len(self) - pc >= len(b) and self[pc:pc + len(b)] == b:
                del self[pc:pc + len(b)]
                found += 1
            try:
                pc, _, _ = self.get_op(pc)
            except (IndexError, ValueError):
                break
        return found

    def __add__(self, other) -> "CScript":
        return CScript(bytes(self) + bytes(other))


def signature_hash(script_code: CScript, tx_to: CTransaction, n_in: int,
                   hash_type: int) -> int:
    """SignatureHash() from script.cpp, including the 'return 1' bug."""
    if n_in >= len(tx_to.vin):
        return 1

    # in case concatenating two scripts ends up with two codeseparators,
    # or an extra one at the end, this prevents all those possible incompatibilities
    script_code = CScript(bytes(script_code))
    script_code.find_and_delete(bytes([OP_CODESEPARATOR]))

    txtmp = CTransaction(
        n_version=tx_to.n_version,
        vin=[CTxIn(COutPoint(i.prevout.hash, i.prevout.n), CScript(),
                   i.n_sequence) for i in tx_to.vin],
        vout=[CTxOut(o.n_value, CScript(bytes(o.script_pubkey)))
              for o in tx_to.vout],
        n_lock_time=tx_to.n_lock_time,
    )
    txtmp.vin[n_in].script_sig = script_code

    base_type = hash_type & 0x1F
    if base_type == SIGHASH_NONE:
        # wildcard payee
        txtmp.vout = []
        for i, txin in enumerate(txtmp.vin):
            if i != n_in:
                txin.n_sequence = 0
    elif base_type == SIGHASH_SINGLE:
        if n_in >= len(txtmp.vout):
            return 1
        txtmp.vout = txtmp.vout[:n_in + 1]
        for i in range(n_in):
            txtmp.vout[i] = CTxOut(-1, CScript())
        for i, txin in enumerate(txtmp.vin):
            if i != n_in:
                txin.n_sequence = 0

    if hash_type & SIGHASH_ANYONECANPAY:
        txtmp.vin = [txtmp.vin[n_in]]

    s = DataStream(n_type=params.SER_GETHASH, n_version=params.VERSION)
    txtmp.serialize(s)
    s.write_int32(hash_type)
    return int.from_bytes(hash256(s.getvalue()), "little")


def check_sig(sig: bytes, pubkey: bytes, script_code: CScript,
              tx_to: CTransaction, n_in: int, hash_type: int) -> bool:
    """CheckSig() — hashtype byte rides on the end of the DER signature."""
    if not sig:
        return False
    if hash_type == 0:
        hash_type = sig[-1]
    elif hash_type != sig[-1]:
        return False
    sig = sig[:-1]
    digest = signature_hash(script_code, tx_to, n_in, hash_type)
    return CKey.verify_with_pubkey(pubkey, digest.to_bytes(32, "little"), sig)


def eval_script(script: CScript, tx_to: CTransaction, n_in: int,
                hash_type: int = 0) -> bool:
    """EvalScript() — single evaluation of the concatenated script."""
    stack: list[bytes] = []
    altstack: list[bytes] = []
    vf_exec: list[bool] = []  # if/else execution state
    pc = 0
    begincodehash = 0  # offset just past the most recent OP_CODESEPARATOR

    def popn(n):
        if len(stack) < n:
            raise IndexError("stack underflow")
        vals = stack[-n:]
        del stack[-n:]
        return vals

    def push_bn(n: int):
        stack.append(bn_serialize(n))

    def pop_bn() -> int:
        return bn_deserialize(popn(1)[0])

    try:
        while pc < len(script):
            f_exec = all(vf_exec)
            pc, opcode, data = script.get_op(pc)

            if data is not None:
                if f_exec:
                    stack.append(data)
                continue
            if not f_exec and not (OP_IF <= opcode <= OP_ENDIF):
                continue

            # --- push value ---
            if opcode == OP_1NEGATE or OP_1 <= opcode <= OP_16:
                push_bn(opcode - (OP_1 - 1))

            # --- control ---
            elif opcode == OP_NOP:
                pass
            elif opcode in (OP_VER, OP_VERIF, OP_VERNOTIF,
                            OP_RESERVED, OP_RESERVED1, OP_RESERVED2):
                return False
            elif opcode in (OP_IF, OP_NOTIF):
                value = False
                if f_exec:
                    value = cast_to_bool(popn(1)[0])
                    if opcode == OP_NOTIF:
                        value = not value
                vf_exec.append(value)
            elif opcode == OP_ELSE:
                if not vf_exec:
                    return False
                vf_exec[-1] = not vf_exec[-1]
            elif opcode == OP_ENDIF:
                if not vf_exec:
                    return False
                vf_exec.pop()
            elif opcode == OP_VERIFY:
                if not cast_to_bool(stack[-1]):
                    return False
                stack.pop()
            elif opcode == OP_RETURN:
                return False

            # --- stack ops ---
            elif opcode == OP_TOALTSTACK:
                altstack.append(popn(1)[0])
            elif opcode == OP_FROMALTSTACK:
                if not altstack:
                    return False
                stack.append(altstack.pop())
            elif opcode == OP_2DROP:
                popn(2)
            elif opcode == OP_2DUP:
                if len(stack) < 2:
                    return False
                stack.extend(stack[-2:])
            elif opcode == OP_3DUP:
                if len(stack) < 3:
                    return False
                stack.extend(stack[-3:])
            elif opcode == OP_2OVER:
                if len(stack) < 4:
                    return False
                stack.extend(stack[-4:-2])
            elif opcode == OP_2ROT:
                if len(stack) < 6:
                    return False
                six = popn(6)
                stack.extend(six[2:] + six[:2])
            elif opcode == OP_2SWAP:
                four = popn(4)
                stack.extend(four[2:] + four[:2])
            elif opcode == OP_IFDUP:
                if not stack:
                    return False
                if cast_to_bool(stack[-1]):
                    stack.append(stack[-1])
            elif opcode == OP_DEPTH:
                push_bn(len(stack))
            elif opcode == OP_DROP:
                popn(1)
            elif opcode == OP_DUP:
                if not stack:
                    return False
                stack.append(stack[-1])
            elif opcode == OP_NIP:
                if len(stack) < 2:
                    return False
                del stack[-2]
            elif opcode == OP_OVER:
                if len(stack) < 2:
                    return False
                stack.append(stack[-2])
            elif opcode in (OP_PICK, OP_ROLL):
                n = pop_bn()
                if n < 0 or n >= len(stack):
                    return False
                value = stack[-n - 1]
                if opcode == OP_ROLL:
                    del stack[-n - 1]
                stack.append(value)
            elif opcode == OP_ROT:
                three = popn(3)
                stack.extend(three[1:] + three[:1])
            elif opcode == OP_SWAP:
                two = popn(2)
                stack.extend(two[::-1])
            elif opcode == OP_TUCK:
                if len(stack) < 2:
                    return False
                stack.insert(-2, stack[-1])

            # --- splice ops (still enabled in 0.1.5) ---
            elif opcode == OP_CAT:
                b1, b2 = popn(2)
                stack.append(b1 + b2)
            elif opcode == OP_SUBSTR:
                npos2 = pop_bn()
                npos = pop_bn()
                (value,) = popn(1)
                if npos < 0 or npos2 < 0 or npos > len(value):
                    return False
                stack.append(value[npos:npos + npos2])
            elif opcode in (OP_LEFT, OP_RIGHT):
                n = pop_bn()
                (value,) = popn(1)
                if n < 0:
                    return False
                stack.append(value[:n] if opcode == OP_LEFT else value[max(0, len(value) - n):])
            elif opcode == OP_SIZE:
                if not stack:
                    return False
                push_bn(len(stack[-1]))

            # --- bitwise (operate on equal-length padding like CBigNum-ish) ---
            elif opcode == OP_INVERT:
                (value,) = popn(1)
                stack.append(bytes(~b & 0xFF for b in value))
            elif opcode in (OP_AND, OP_OR, OP_XOR):
                b1, b2 = popn(2)
                n = max(len(b1), len(b2))
                b1 = b1.ljust(n, b"\x00")
                b2 = b2.ljust(n, b"\x00")
                if opcode == OP_AND:
                    stack.append(bytes(x & y for x, y in zip(b1, b2)))
                elif opcode == OP_OR:
                    stack.append(bytes(x | y for x, y in zip(b1, b2)))
                else:
                    stack.append(bytes(x ^ y for x, y in zip(b1, b2)))
            elif opcode in (OP_EQUAL, OP_EQUALVERIFY):
                b1, b2 = popn(2)
                equal = b1 == b2
                if opcode == OP_EQUAL:
                    push_bn(1 if equal else 0)
                elif not equal:
                    return False

            # --- numeric (CBigNum: arbitrary precision) ---
            elif opcode in (OP_1ADD, OP_1SUB, OP_2MUL, OP_2DIV, OP_NEGATE,
                            OP_ABS, OP_NOT, OP_0NOTEQUAL):
                n = pop_bn()
                if opcode == OP_1ADD:
                    n += 1
                elif opcode == OP_1SUB:
                    n -= 1
                elif opcode == OP_2MUL:
                    n <<= 1
                elif opcode == OP_2DIV:
                    n >>= 1
                elif opcode == OP_NEGATE:
                    n = -n
                elif opcode == OP_ABS:
                    n = abs(n)
                elif opcode == OP_NOT:
                    n = int(n == 0)
                else:
                    n = int(n != 0)
                push_bn(n)
            elif opcode in (OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD,
                            OP_LSHIFT, OP_RSHIFT, OP_BOOLAND, OP_BOOLOR,
                            OP_NUMEQUAL, OP_NUMEQUALVERIFY, OP_NUMNOTEQUAL,
                            OP_LESSTHAN, OP_GREATERTHAN, OP_LESSTHANOREQUAL,
                            OP_GREATERTHANOREQUAL, OP_MIN, OP_MAX):
                b = pop_bn()
                a = pop_bn()
                if opcode == OP_ADD:
                    r = a + b
                elif opcode == OP_SUB:
                    r = a - b
                elif opcode == OP_MUL:
                    r = a * b
                elif opcode == OP_DIV:
                    if b == 0:
                        return False
                    r = int(a / b)  # C++ division truncates toward zero
                elif opcode == OP_MOD:
                    if b == 0:
                        return False
                    r = a - int(a / b) * b
                elif opcode == OP_LSHIFT:
                    if b < 0 or b > 2048:
                        return False
                    r = a << b
                elif opcode == OP_RSHIFT:
                    if b < 0 or b > 2048:
                        return False
                    r = a >> b
                elif opcode == OP_BOOLAND:
                    r = int(a != 0 and b != 0)
                elif opcode == OP_BOOLOR:
                    r = int(a != 0 or b != 0)
                elif opcode in (OP_NUMEQUAL, OP_NUMEQUALVERIFY):
                    r = int(a == b)
                elif opcode == OP_NUMNOTEQUAL:
                    r = int(a != b)
                elif opcode == OP_LESSTHAN:
                    r = int(a < b)
                elif opcode == OP_GREATERTHAN:
                    r = int(a > b)
                elif opcode == OP_LESSTHANOREQUAL:
                    r = int(a <= b)
                elif opcode == OP_GREATERTHANOREQUAL:
                    r = int(a >= b)
                elif opcode == OP_MIN:
                    r = min(a, b)
                else:
                    r = max(a, b)
                push_bn(r)
                if opcode == OP_NUMEQUALVERIFY:
                    if not cast_to_bool(stack[-1]):
                        return False
                    stack.pop()
            elif opcode == OP_WITHIN:
                top = pop_bn()
                mid = pop_bn()
                low = pop_bn()
                push_bn(1 if mid <= low < top else 0)

            # --- crypto ---
            elif opcode == OP_RIPEMD160:
                from .ripemd160 import ripemd160 as _r160
                (value,) = popn(1)
                stack.append(_r160(value))
            elif opcode == OP_SHA1:
                (value,) = popn(1)
                stack.append(sha1(value))
            elif opcode == OP_SHA256:
                (value,) = popn(1)
                stack.append(sha256(value))
            elif opcode == OP_HASH160:
                (value,) = popn(1)
                stack.append(hash160(value))
            elif opcode == OP_HASH256:
                (value,) = popn(1)
                stack.append(hash256(value))
            elif opcode == OP_CODESEPARATOR:
                begincodehash = pc
            elif opcode in (OP_CHECKSIG, OP_CHECKSIGVERIFY):
                pubkey, = popn(1)
                sig, = popn(1) if stack else (None,)
                if sig is None:
                    return False
                script_code = CScript(bytes(script[begincodehash:]))
                # drop the signature: a signature can't sign itself
                sig_push = CScript().push_data(sig)
                script_code.find_and_delete(bytes(sig_push))
                ok = check_sig(sig, pubkey, script_code, tx_to, n_in, hash_type)
                if opcode == OP_CHECKSIG:
                    push_bn(1 if ok else 0)
                elif not ok:
                    return False
            elif opcode in (OP_CHECKMULTISIG, OP_CHECKMULTISIGVERIFY):
                n_keys = pop_bn()
                if n_keys < 0 or n_keys > 20:
                    return False
                pubkeys = popn(n_keys)[::-1]
                n_sigs = pop_bn()
                if n_sigs < 0 or n_sigs > n_keys:
                    return False
                sigs = popn(n_sigs)[::-1]
                popn(1)  # the famous extra-value bug, faithfully popped
                script_code = CScript(bytes(script[begincodehash:]))
                for sig in sigs:
                    sig_push = CScript().push_data(sig)
                    script_code.find_and_delete(bytes(sig_push))
                ok = True
                isig = 0
                ikey = 0
                while n_sigs > 0:
                    if check_sig(sigs[isig], pubkeys[ikey], script_code,
                                 tx_to, n_in, hash_type):
                        isig += 1
                        n_sigs -= 1
                    ikey += 1
                    n_keys -= 1
                    if n_sigs > n_keys:
                        ok = False
                        break
                if opcode == OP_CHECKMULTISIG:
                    push_bn(1 if ok else 0)
                elif not ok:
                    return False
            else:
                return False  # unknown opcode
    except (IndexError, ValueError):
        return False

    if vf_exec:
        return False
    return bool(stack) and cast_to_bool(stack[-1])


# --- templates (Solver) ---
TX_PUBKEY = "pubkey"
TX_PUBKEYHASH = "pubkeyhash"


def solver(script_pubkey: CScript):
    """Solver() — match the two standard templates of 0.1.5.

    Returns (TX_PUBKEY, pubkey) | (TX_PUBKEYHASH, hash160) | None.
    """
    try:
        ops = list(CScript(script_pubkey).ops())
    except (IndexError, ValueError):
        return None
    # <pubkey> OP_CHECKSIG
    if (len(ops) == 2 and ops[0][1] is not None and len(ops[0][1]) >= 33
            and ops[1][0] == OP_CHECKSIG):
        return TX_PUBKEY, ops[0][1]
    # OP_DUP OP_HASH160 <h160> OP_EQUALVERIFY OP_CHECKSIG
    if (len(ops) == 5 and ops[0][0] == OP_DUP and ops[1][0] == OP_HASH160
            and ops[2][1] is not None and len(ops[2][1]) == 20
            and ops[3][0] == OP_EQUALVERIFY and ops[4][0] == OP_CHECKSIG):
        return TX_PUBKEYHASH, ops[2][1]
    return None


def script_pubkey_for_pubkey(pubkey: bytes) -> CScript:
    """scriptPubKey << pubkey << OP_CHECKSIG (generation outputs)."""
    return CScript().push_data(pubkey).push_opcode(OP_CHECKSIG)


def script_pubkey_for_hash160(h160: bytes) -> CScript:
    """OP_DUP OP_HASH160 <h160> OP_EQUALVERIFY OP_CHECKSIG (sends to address)."""
    s = CScript()
    s.push_opcode(OP_DUP)
    s.push_opcode(OP_HASH160)
    s.push_data(h160)
    s.push_opcode(OP_EQUALVERIFY)
    s.push_opcode(OP_CHECKSIG)
    return s


def sign_signature(keystore, tx_from: CTransaction, tx_to: CTransaction,
                   n_in: int, hash_type: int = SIGHASH_ALL) -> bool:
    """SignSignature() — fill in tx_to.vin[n_in].scriptSig.

    keystore must provide get_key_for_pubkey(pubkey) and
    get_key_for_hash160(h160), both returning a CKey or None.
    """
    txin = tx_to.vin[n_in]
    assert txin.prevout.n < len(tx_from.vout)
    assert txin.prevout.hash == tx_from.get_hash()
    txout = tx_from.vout[txin.prevout.n]
    script_pubkey = CScript(bytes(txout.script_pubkey))

    match = solver(script_pubkey)
    if match is None:
        return False
    kind, data = match

    digest = signature_hash(script_pubkey, tx_to, n_in, hash_type)
    if kind == TX_PUBKEY:
        key = keystore.get_key_for_pubkey(data)
        if key is None:
            return False
        sig = key.sign(digest.to_bytes(32, "little")) + bytes([hash_type])
        txin.script_sig = CScript().push_data(sig)
        return True
    if kind == TX_PUBKEYHASH:
        key = keystore.get_key_for_hash160(data)
        if key is None:
            return False
        sig = key.sign(digest.to_bytes(32, "little")) + bytes([hash_type])
        txin.script_sig = CScript().push_data(sig).push_data(key.get_pubkey())
        return True
    return False


def verify_signature(tx_from: CTransaction, tx_to: CTransaction,
                     n_in: int, hash_type: int = 0) -> bool:
    """VerifySignature() — the concatenated 0.1.x evaluation."""
    assert n_in < len(tx_to.vin)
    txin = tx_to.vin[n_in]
    if txin.prevout.n >= len(tx_from.vout):
        return False
    if txin.prevout.hash != tx_from.get_hash():
        return False
    txout = tx_from.vout[txin.prevout.n]
    combined = (CScript(bytes(txin.script_sig))
                + CScript(bytes([OP_CODESEPARATOR]))
                + CScript(bytes(txout.script_pubkey)))
    return eval_script(combined, tx_to, n_in, hash_type)
