"""脚本系统——对应原版 script.h / script.cpp。

比特币里"这笔钱归谁"不是写死的字段，而是一小段程序：
- 输出里的 scriptPubKey 是一把"锁"（花这笔钱要满足什么条件）；
- 输入里的 scriptSig 是"钥匙"（证明我满足条件）。
验证时把两段脚本拼起来，放进一台基于栈的小型虚拟机里执行，最后栈顶为"真"就算通过。

本文件忠实复刻了 0.1.x 时代的几个著名特征（包括后来被当作漏洞修掉的那些）：

* 脚本里的数字是 CBigNum：任意精度，小端"符号-幅值"编码。
* 验证时把两段脚本**拼接后只执行一次**：
  EvalScript(scriptSig + OP_CODESEPARATOR + scriptPubKey)。
* OP_RETURN 不是"立即失败"，而是"跳到脚本末尾"，结果由当时的栈顶决定。
  于是 scriptSig 写成 `OP_1 OP_RETURN` 就能花任何人的钱——这就是 2010 年
  被修掉的那个著名漏洞，这里为了忠实原样保留。
* 签名的最后一个字节是 hashtype；OP_CHECKSIG 会先把签名自己从脚本里删掉
  （FindAndDelete），再调用 SignatureHash() 算出要验证的摘要。
* SignatureHash() 在 nIn 越界时返回 1 而不是报错（"return 1" bug）。
* OP_CHECKMULTISIG 会多弹出一个栈元素（著名的 off-by-one）。
* 没有任何 opcode 被禁用：字符串拼接、乘除、移位等在 0.1.5 都还能用。
* 脚本结束时不检查 IF/ENDIF 是否配对。
* 0xF0 及以上的字节被当作"双字节 opcode"的前半部分（OP_PUBKEY 等模板占位符）。
"""

import struct

from . import params
from .hashes import hash160, hash256, ripemd160, sha1, sha256
from .key import CKey
from .serialize import DataStream
from .tx import COutPoint, CTransaction, CTxIn, CTxOut

# ---------------------------------------------------------------- 签名哈希类型
SIGHASH_ALL = 1              # 签名覆盖所有输入和所有输出（绝大多数交易）
SIGHASH_NONE = 2             # 不锁定任何输出："这钱我花了，给谁无所谓"
SIGHASH_SINGLE = 3           # 只锁定与本输入同序号的那一个输出
SIGHASH_ANYONECANPAY = 0x80  # 只锁定本输入，别人可以再往交易里添加输入

# ------------------------------------------------------------------- opcodes
# 以下数值由原版 script.h 的枚举逐项导出（tests/test_script.py 会逐个核对）
OP_0 = 0x00
OP_FALSE = 0x00
OP_PUSHDATA1 = 0x4C
OP_PUSHDATA2 = 0x4D
OP_PUSHDATA4 = 0x4E
OP_1NEGATE = 0x4F
OP_RESERVED = 0x50
OP_1 = 0x51
OP_TRUE = 0x51
OP_2 = 0x52
OP_3 = 0x53
OP_4 = 0x54
OP_5 = 0x55
OP_6 = 0x56
OP_7 = 0x57
OP_8 = 0x58
OP_9 = 0x59
OP_10 = 0x5A
OP_11 = 0x5B
OP_12 = 0x5C
OP_13 = 0x5D
OP_14 = 0x5E
OP_15 = 0x5F
OP_16 = 0x60
# 流程控制
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
# 栈操作
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
# 字符串拼接/截取
OP_CAT = 0x7E
OP_SUBSTR = 0x7F
OP_LEFT = 0x80
OP_RIGHT = 0x81
OP_SIZE = 0x82
# 位运算
OP_INVERT = 0x83
OP_AND = 0x84
OP_OR = 0x85
OP_XOR = 0x86
OP_EQUAL = 0x87
OP_EQUALVERIFY = 0x88
OP_RESERVED1 = 0x89
OP_RESERVED2 = 0x8A
# 数值运算
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
# 密码学
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
# 0xF0 起是"双字节 opcode"区；只定义了 Solver 用的两个模板占位符
OP_SINGLEBYTE_END = 0xF0
OP_DOUBLEBYTE_BEGIN = 0xF000
OP_PUBKEY = 0xF001
OP_PUBKEYHASH = 0xF002
OP_INVALIDOPCODE = 0xFFFF

MAX_SHIFT_BITS = 2048    # 见 eval_script 里 OP_LSHIFT 的说明（唯一一处安全性改动）

# opcode 数值 -> 名字（对应原版的 GetOpName），打印脚本时用
OPCODE_NAMES = {value: name for name, value in list(globals().items())
                if name.startswith("OP_") and name not in ("OP_FALSE", "OP_TRUE")}


# ------------------------------------------------ CBigNum 的字节编码（bignum.h）
def bn_serialize(n: int) -> bytes:
    """CBigNum::getvch：整数 -> 字节。

    规则：幅值按小端排列；最后一个字节的最高位是符号位（1 表示负数）。
    如果幅值本身就用到了那个最高位，就再补一个字节专门放符号。
    例：0 -> 空；1 -> 01；-1 -> 81；128 -> 80 00；-128 -> 80 80。
    """
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
    """CBigNum::setvch：字节 -> 整数（bn_serialize 的逆运算）。"""
    if not b:
        return 0
    neg = bool(b[-1] & 0x80)
    mag = bytearray(b)
    mag[-1] &= 0x7F
    n = int.from_bytes(mag, "little")
    return -n if neg else n


def cast_to_bool(b: bytes) -> bool:
    """CastToBool：原版就是 CBigNum(vch) != 0。空串、全 0、"负零"都算假。"""
    return bn_deserialize(b) != 0


def _bn_shift_right(n: int, bits: int) -> int:
    """OpenSSL 的 BN_rshift 是对**幅值**移位、符号不变；
    而 Python 的 >> 对负数是向下取整（-3 >> 1 == -2）。这里按 OpenSSL 的语义来。"""
    return (abs(n) >> bits) * (-1 if n < 0 else 1)


def _bn_div(a: int, b: int) -> int:
    """BN_div：商向零取整（C 语言的习惯），而 Python 的 // 是向下取整。"""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


class CScript(bytearray):
    """脚本就是一串字节；这个类提供原版 CScript 的 operator<< 系列"往里写"的方法。"""

    def push_opcode(self, opcode: int) -> "CScript":
        if opcode <= OP_SINGLEBYTE_END:
            self.append(opcode)
        else:                                  # 双字节 opcode：高字节在前
            self.append(opcode >> 8)
            self.append(opcode & 0xFF)
        return self

    def push_data(self, data: bytes) -> "CScript":
        """operator<<(vector)：写入"把这段数据压栈"的指令。
        长度 < 76：直接用长度当 opcode；否则用 OP_PUSHDATA1/2/4 + 长度字段。"""
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
        """operator<<(int)：-1 和 1..16 有专门的单字节 opcode，其余整数当作 CBigNum 数据压栈。"""
        if n == -1 or 1 <= n <= 16:
            self.append(n + (OP_1 - 1))
        else:
            self.push_data(bn_serialize(n))
        return self

    def push_bignum(self, n: int) -> "CScript":
        """operator<<(CBigNum)：**总是**按数据压栈，哪怕 n 在 1..16 之间。
        主网创世块的 scriptSig 里那个 `01 04` 就是 CBigNum(4) 这样来的。"""
        return self.push_data(bn_serialize(n))

    def get_op(self, pc: int):
        """GetOp：从位置 pc 读出一条指令，返回 (新的pc, opcode, 压栈数据或None)。
        读到末尾抛 IndexError；指令被截断（数据不够长）抛 ValueError。"""
        if pc >= len(self):
            raise IndexError("已到脚本末尾")
        opcode = self[pc]
        pc += 1
        if opcode >= OP_SINGLEBYTE_END:        # 双字节 opcode
            if pc + 1 > len(self):
                raise ValueError("双字节 opcode 被截断")
            opcode = (opcode << 8) | self[pc]
            pc += 1

        data = None
        if opcode <= OP_PUSHDATA4:
            if opcode < OP_PUSHDATA1:
                n = opcode
            elif opcode == OP_PUSHDATA1:
                if pc + 1 > len(self):
                    raise ValueError("PUSHDATA1 被截断")
                n = self[pc]
                pc += 1
            elif opcode == OP_PUSHDATA2:
                if pc + 2 > len(self):
                    raise ValueError("PUSHDATA2 被截断")
                n = struct.unpack_from("<H", self, pc)[0]
                pc += 2
            else:
                if pc + 4 > len(self):
                    raise ValueError("PUSHDATA4 被截断")
                n = struct.unpack_from("<I", self, pc)[0]
                pc += 4
            if pc + n > len(self):
                raise ValueError("压栈数据超出脚本末尾")
            data = bytes(self[pc:pc + n])
            pc += n
        return pc, opcode, data

    def ops(self):
        """逐条迭代 (opcode, data)。"""
        pc = 0
        while pc < len(self):
            pc, opcode, data = self.get_op(pc)
            yield opcode, data

    def find_and_delete(self, b: bytes) -> int:
        """FindAndDelete：删除脚本中所有与 b 字节完全相同的片段。
        只在"指令边界"上比较（和原版的 do-while 循环一样），返回删除的次数。"""
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


def script_to_asm(script: bytes) -> str:
    """把脚本翻译成人能读的"汇编"形式（对应原版 CScript::ToString），
    例如：OP_DUP OP_HASH160 62e907b1...8f18 OP_EQUALVERIFY OP_CHECKSIG"""
    parts = []
    try:
        for opcode, data in CScript(script).ops():
            if data is not None:
                parts.append(data.hex() if data else "0")
            else:
                parts.append(OPCODE_NAMES.get(opcode, f"OP_UNKNOWN_{opcode:#x}"))
    except (IndexError, ValueError):
        parts.append("[解析错误]")
    return " ".join(parts)


def signature_hash(script_code: CScript, tx_to: CTransaction, n_in: int,
                   hash_type: int) -> int:
    """SignatureHash：算出"签名到底签的是什么"——一个 256 位摘要。

    思路：签名不可能签到自己身上，所以要先造一份交易的"副本"：
    1. 把所有输入的 scriptSig 清空；
    2. 把当前这个输入的 scriptSig 换成它要解的那把锁（script_code）；
    3. 按 hashtype 决定保留/抹掉哪些输出和其它输入；
    4. 序列化这份副本，末尾再接 4 字节的 hashtype，做两次 SHA-256。
    """
    if n_in >= len(tx_to.vin):
        return 1            # 著名的 "return 1" bug：越界不报错，摘要变成常数 1

    # 两段脚本拼接后可能出现多余的 OP_CODESEPARATOR，原版在这里统一删掉
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
        txtmp.vout = []                         # 收款人随意
        for i, txin in enumerate(txtmp.vin):
            if i != n_in:
                txin.n_sequence = 0             # 其它输入允许以后更新
    elif base_type == SIGHASH_SINGLE:
        if n_in >= len(txtmp.vout):
            return 1                            # 同样的 "return 1" bug
        txtmp.vout = txtmp.vout[:n_in + 1]
        for i in range(n_in):
            txtmp.vout[i] = CTxOut(-1, CScript())   # SetNull()
        for i, txin in enumerate(txtmp.vin):
            if i != n_in:
                txin.n_sequence = 0

    if hash_type & SIGHASH_ANYONECANPAY:
        txtmp.vin = [txtmp.vin[n_in]]           # 完全不管其它输入

    s = DataStream(n_type=params.SER_GETHASH, n_version=params.VERSION)
    txtmp.serialize(s)
    s.write_int32(hash_type)
    return int.from_bytes(hash256(s.getvalue()), "little")


def check_sig(sig: bytes, pubkey: bytes, script_code: CScript,
              tx_to: CTransaction, n_in: int, hash_type: int) -> bool:
    """CheckSig：hashtype 是挂在 DER 签名末尾的一个字节，验签前要先摘下来。"""
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
                hash_type: int = 0, trace=None) -> bool:
    """EvalScript：脚本虚拟机。逐条执行指令，结束时栈非空且栈顶为真 -> True。

    原版里很多"失败"不是立刻 return false，而是 `pc = pend`（跳到末尾），
    最终结果仍由栈顶决定。这里用 `pc = pend` 复刻同样的控制流。

    trace 是教学/调试用的钩子：每执行完一条指令就调用 trace(opcode, data, 栈的副本)。
    （原版对应的调试手段是 pvStackRet 参数。）
    """
    stack: list[bytes] = []
    altstack: list[bytes] = []
    vf_exec: list[bool] = []        # IF/ELSE 嵌套的执行状态栈
    pc = 0
    pend = len(script)
    begincodehash = 0               # 最近一个 OP_CODESEPARATOR 之后的位置
    pending_trace = None

    def need(n: int):
        if len(stack) < n:
            raise IndexError("栈元素不足")

    def popn(n: int) -> list[bytes]:
        need(n)
        vals = stack[-n:]
        del stack[-n:]
        return vals

    def push_bn(n: int):
        stack.append(bn_serialize(n))

    def top_bn(i: int) -> int:
        return bn_deserialize(stack[i])

    try:
        while pc < pend:
            f_exec = all(vf_exec)                       # 所有外层 IF 都为真才执行
            pc, opcode, data = script.get_op(pc)        # 截断的指令 -> ValueError -> False

            if trace is not None and pending_trace is not None:
                trace(*pending_trace, list(stack))      # 汇报上一条指令执行后的栈
            pending_trace = (opcode, data)

            if f_exec and opcode <= OP_PUSHDATA4:
                stack.append(data)
                continue
            if not (f_exec or OP_IF <= opcode <= OP_ENDIF):
                continue                                # 处在不执行的分支里：跳过

            # ---------------------------------------------------- 压入小整数
            if opcode == OP_1NEGATE or OP_1 <= opcode <= OP_16:
                push_bn(opcode - (OP_1 - 1))

            # ------------------------------------------------------ 流程控制
            elif opcode == OP_NOP:
                pass
            elif opcode == OP_VER:
                push_bn(params.VERSION)
            elif opcode in (OP_IF, OP_NOTIF, OP_VERIF, OP_VERNOTIF):
                value = False
                if f_exec:
                    need(1)
                    vch = stack.pop()
                    if opcode in (OP_VERIF, OP_VERNOTIF):
                        value = params.VERSION >= bn_deserialize(vch)
                    else:
                        value = cast_to_bool(vch)
                    if opcode in (OP_NOTIF, OP_VERNOTIF):
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
                need(1)
                if cast_to_bool(stack[-1]):
                    stack.pop()
                else:
                    pc = pend
            elif opcode == OP_RETURN:
                pc = pend                   # 不是失败！只是提前结束（见文件头说明）

            # -------------------------------------------------------- 栈操作
            elif opcode == OP_TOALTSTACK:
                altstack.append(popn(1)[0])
            elif opcode == OP_FROMALTSTACK:
                if not altstack:
                    return False
                stack.append(altstack.pop())
            elif opcode == OP_2DROP:
                popn(2)
            elif opcode == OP_2DUP:
                need(2)
                stack.extend(stack[-2:])
            elif opcode == OP_3DUP:
                need(3)
                stack.extend(stack[-3:])
            elif opcode == OP_2OVER:
                need(4)
                stack.extend(stack[-4:-2])
            elif opcode == OP_2ROT:
                six = popn(6)
                stack.extend(six[2:] + six[:2])
            elif opcode == OP_2SWAP:
                four = popn(4)
                stack.extend(four[2:] + four[:2])
            elif opcode == OP_IFDUP:
                need(1)
                if cast_to_bool(stack[-1]):
                    stack.append(stack[-1])
            elif opcode == OP_DEPTH:
                push_bn(len(stack))
            elif opcode == OP_DROP:
                popn(1)
            elif opcode == OP_DUP:
                need(1)
                stack.append(stack[-1])
            elif opcode == OP_NIP:
                need(2)
                del stack[-2]
            elif opcode == OP_OVER:
                need(2)
                stack.append(stack[-2])
            elif opcode in (OP_PICK, OP_ROLL):
                need(2)
                n = bn_deserialize(stack.pop())
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
                need(2)
                stack.insert(-2, stack[-1])

            # ------------------------------------------------ 字符串拼接/截取
            elif opcode == OP_CAT:
                b1, b2 = popn(2)
                stack.append(b1 + b2)
            elif opcode == OP_SUBSTR:
                need(3)
                n_begin = top_bn(-2)
                n_end = n_begin + top_bn(-1)
                if n_begin < 0 or n_end < n_begin:
                    return False
                value, _, _ = popn(3)
                stack.append(value[n_begin:n_end])      # 切片越界会自动截到末尾，和原版的钳制一致
            elif opcode in (OP_LEFT, OP_RIGHT):
                need(2)
                n_size = top_bn(-1)
                if n_size < 0:
                    return False
                value, _ = popn(2)
                n_size = min(n_size, len(value))
                stack.append(value[:n_size] if opcode == OP_LEFT
                             else value[len(value) - n_size:])
            elif opcode == OP_SIZE:
                need(1)
                push_bn(len(stack[-1]))

            # -------------------------------------------------------- 位运算
            elif opcode == OP_INVERT:
                (value,) = popn(1)
                stack.append(bytes(~b & 0xFF for b in value))
            elif opcode in (OP_AND, OP_OR, OP_XOR):
                b1, b2 = popn(2)
                n = max(len(b1), len(b2))               # MakeSameSize：短的在末尾补 0
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
                equal = b1 == b2                        # 逐字节比较，不是按数值比较
                push_bn(1 if equal else 0)
                if opcode == OP_EQUALVERIFY:
                    if equal:
                        stack.pop()
                    else:
                        pc = pend

            # ------------------------------------------------------ 数值运算
            elif opcode in (OP_1ADD, OP_1SUB, OP_2MUL, OP_2DIV, OP_NEGATE,
                            OP_ABS, OP_NOT, OP_0NOTEQUAL):
                need(1)
                n = bn_deserialize(stack.pop())
                if opcode == OP_1ADD:
                    n += 1
                elif opcode == OP_1SUB:
                    n -= 1
                elif opcode == OP_2MUL:
                    n <<= 1
                elif opcode == OP_2DIV:
                    n = _bn_shift_right(n, 1)
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
                need(2)
                a = top_bn(-2)
                b = top_bn(-1)
                if opcode == OP_ADD:
                    r = a + b
                elif opcode == OP_SUB:
                    r = a - b
                elif opcode == OP_MUL:
                    r = a * b
                elif opcode == OP_DIV:
                    if b == 0:
                        return False
                    r = _bn_div(a, b)
                elif opcode == OP_MOD:
                    if b == 0:
                        return False
                    r = a - _bn_div(a, b) * b           # 余数的符号跟被除数走
                elif opcode in (OP_LSHIFT, OP_RSHIFT):
                    if b < 0:
                        return False
                    # 【安全性改动】原版对移位量不设上限，一条 `1 <巨大的数> OP_LSHIFT`
                    # 就能让节点去申请几百 MB 内存。这里限制在 2048 位以内。
                    if b > MAX_SHIFT_BITS:
                        return False
                    r = a << b if opcode == OP_LSHIFT else _bn_shift_right(a, b)
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
                popn(2)
                push_bn(r)
                if opcode == OP_NUMEQUALVERIFY:
                    if cast_to_bool(stack[-1]):
                        stack.pop()
                    else:
                        pc = pend
            elif opcode == OP_WITHIN:
                # (x min max -- 结果)：min <= x < max
                need(3)
                x, lo, hi = top_bn(-3), top_bn(-2), top_bn(-1)
                popn(3)
                push_bn(1 if lo <= x < hi else 0)

            # -------------------------------------------------------- 密码学
            elif opcode in (OP_RIPEMD160, OP_SHA1, OP_SHA256, OP_HASH160, OP_HASH256):
                (value,) = popn(1)
                fn = {OP_RIPEMD160: ripemd160, OP_SHA1: sha1, OP_SHA256: sha256,
                      OP_HASH160: hash160, OP_HASH256: hash256}[opcode]
                stack.append(fn(value))
            elif opcode == OP_CODESEPARATOR:
                begincodehash = pc          # 之后的签名只覆盖从这里开始的脚本
            elif opcode in (OP_CHECKSIG, OP_CHECKSIGVERIFY):
                # (签名 公钥 -- 真/假)
                need(2)
                sig, pubkey = stack[-2], stack[-1]
                script_code = CScript(bytes(script[begincodehash:]))
                # 签名无法签到自己身上，所以先把它从脚本里删掉
                script_code.find_and_delete(bytes(CScript().push_data(sig)))
                ok = check_sig(sig, pubkey, script_code, tx_to, n_in, hash_type)
                popn(2)
                push_bn(1 if ok else 0)
                if opcode == OP_CHECKSIGVERIFY:
                    if ok:
                        stack.pop()
                    else:
                        pc = pend
            elif opcode in (OP_CHECKMULTISIG, OP_CHECKMULTISIGVERIFY):
                # ([签名...] 签名数 [公钥...] 公钥数 -- 真/假)
                # 下面的下标计算逐行照搬原版，包括那个"多要一个元素"的 off-by-one
                i = 1
                need(i)
                n_keys = top_bn(-i)
                if n_keys < 0:
                    return False
                i += 1
                ikey = i
                i += n_keys
                need(i)
                n_sigs = top_bn(-i)
                if n_sigs < 0 or n_sigs > n_keys:
                    return False
                i += 1
                isig = i
                i += n_sigs
                need(i)                     # <- 这里多要了 1 个：著名的 bug

                script_code = CScript(bytes(script[begincodehash:]))
                for k in range(n_sigs):
                    script_code.find_and_delete(
                        bytes(CScript().push_data(stack[-isig - k])))

                ok = True
                while ok and n_sigs > 0:
                    if check_sig(stack[-isig], stack[-ikey], script_code,
                                 tx_to, n_in, hash_type):
                        isig += 1
                        n_sigs -= 1
                    ikey += 1
                    n_keys -= 1
                    # 剩下的签名比剩下的公钥还多 -> 不可能全部通过了
                    if n_sigs > n_keys:
                        ok = False

                popn(i)
                push_bn(1 if ok else 0)
                if opcode == OP_CHECKMULTISIGVERIFY:
                    if ok:
                        stack.pop()
                    else:
                        pc = pend
            else:
                return False                # 未知 / 保留的 opcode
    except (IndexError, ValueError):
        return False                        # 栈元素不足，或指令被截断

    if trace is not None and pending_trace is not None:
        trace(*pending_trace, list(stack))
    # 注意：原版在这里**不检查** IF/ENDIF 是否配对
    return bool(stack) and cast_to_bool(stack[-1])


# ------------------------------------------------------------ 标准模板（Solver）
TX_PUBKEY = "pubkey"            # <公钥> OP_CHECKSIG                 —— 挖矿所得用这种
TX_PUBKEYHASH = "pubkeyhash"    # OP_DUP OP_HASH160 <公钥哈希> OP_EQUALVERIFY OP_CHECKSIG —— 转账到地址


def solver(script_pubkey: bytes):
    """Solver：判断一把"锁"是不是两种标准模板之一。

    返回 (TX_PUBKEY, 公钥) 或 (TX_PUBKEYHASH, 20字节公钥哈希)；都不匹配返回 None。
    钱包靠它判断"这笔钱是不是我的"，以及该怎么生成对应的 scriptSig。
    """
    try:
        ops = list(CScript(script_pubkey).ops())
    except (IndexError, ValueError):
        return None
    # 模板一：OP_PUBKEY OP_CHECKSIG（原版要求公钥长度 > 32 字节）
    if (len(ops) == 2 and ops[0][1] is not None and len(ops[0][1]) > 32
            and ops[1][0] == OP_CHECKSIG):
        return TX_PUBKEY, ops[0][1]
    # 模板二：OP_DUP OP_HASH160 OP_PUBKEYHASH OP_EQUALVERIFY OP_CHECKSIG
    if (len(ops) == 5 and ops[0][0] == OP_DUP and ops[1][0] == OP_HASH160
            and ops[2][1] is not None and len(ops[2][1]) == 20
            and ops[3][0] == OP_EQUALVERIFY and ops[4][0] == OP_CHECKSIG):
        return TX_PUBKEYHASH, ops[2][1]
    return None


def script_pubkey_for_pubkey(pubkey: bytes) -> CScript:
    """scriptPubKey << 公钥 << OP_CHECKSIG"""
    return CScript().push_data(pubkey).push_opcode(OP_CHECKSIG)


def script_pubkey_for_hash160(h160: bytes) -> CScript:
    """scriptPubKey << OP_DUP << OP_HASH160 << 公钥哈希 << OP_EQUALVERIFY << OP_CHECKSIG"""
    s = CScript()
    s.push_opcode(OP_DUP)
    s.push_opcode(OP_HASH160)
    s.push_data(h160)
    s.push_opcode(OP_EQUALVERIFY)
    s.push_opcode(OP_CHECKSIG)
    return s


def sign_signature(keystore, tx_from: CTransaction, tx_to: CTransaction,
                   n_in: int, hash_type: int = SIGHASH_ALL) -> bool:
    """SignSignature：为 tx_to 的第 n_in 个输入生成 scriptSig。

    keystore 需要提供 get_key_for_pubkey(公钥) 和 get_key_for_hash160(公钥哈希)，
    找不到时返回 None（钱包 Wallet 就实现了这两个方法）。
    """
    assert n_in < len(tx_to.vin)
    txin = tx_to.vin[n_in]
    assert txin.prevout.n < len(tx_from.vout)
    txout = tx_from.vout[txin.prevout.n]
    script_pubkey = CScript(bytes(txout.script_pubkey))

    match = solver(script_pubkey)
    if match is None:
        return False
    kind, data = match
    key = (keystore.get_key_for_pubkey(data) if kind == TX_PUBKEY
           else keystore.get_key_for_hash160(data))
    if key is None:
        return False

    digest = signature_hash(script_pubkey, tx_to, n_in, hash_type)
    sig = key.sign(digest.to_bytes(32, "little")) + bytes([hash_type])
    if kind == TX_PUBKEY:
        txin.script_sig = CScript().push_data(sig)                  # 只要签名
    else:
        txin.script_sig = CScript().push_data(sig).push_data(key.get_pubkey())  # 签名 + 公钥

    # 和原版一样，签完立刻自己验一遍
    combined = (CScript(bytes(txin.script_sig))
                + CScript(bytes([OP_CODESEPARATOR])) + script_pubkey)
    return eval_script(combined, tx_to, n_in)


def verify_signature(tx_from: CTransaction, tx_to: CTransaction,
                     n_in: int, hash_type: int = 0) -> bool:
    """VerifySignature：验证 tx_to 的第 n_in 个输入确实有权花 tx_from 的对应输出。"""
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
