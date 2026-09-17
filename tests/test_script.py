"""脚本虚拟机：对 v0.1.5 的每一个 opcode 逐个验证行为，包括那些著名的历史怪癖。

写法说明：script(...) 里
  - 普通整数       = 一个 opcode
  - N(5)           = "压入数字 5"（相当于原版的 script << 5）
  - bytes          = "压入这段数据"
"""

import json
import pathlib

import pytest

from bitcoin import script as sc
from bitcoin.hashes import hash160, hash256, ripemd160, sha1, sha256
from bitcoin.script import CScript, bn_deserialize, bn_serialize, cast_to_bool, eval_script
from bitcoin.tx import CTransaction, CTxIn, CTxOut

from bitcoin.script import *  # noqa: F401,F403  —— 测试里直接用 OP_xxx 名字


class N(int):
    """标记"这是一个要压栈的数字"，以便和 opcode 区分开。"""


DUMMY_TX = CTransaction(vin=[CTxIn()], vout=[CTxOut(0)])


def script(*items) -> CScript:
    s = CScript()
    for it in items:
        if isinstance(it, N):
            s.push_int(int(it))
        elif isinstance(it, (bytes, bytearray)):
            s.push_data(bytes(it))
        else:
            s.push_opcode(it)
    return s


def ev(*items) -> bool:
    return eval_script(script(*items), DUMMY_TX, 0)


def ev_raw(raw: bytes) -> bool:
    return eval_script(CScript(raw), DUMMY_TX, 0)


def leaves_stack(prefix: list, expected: list) -> bool:
    """执行 prefix 之后，栈里（从底到顶）是否恰好是 expected。
    做法：从栈顶往下逐个 EQUALVERIFY，最后检查栈已经空了。"""
    items = list(prefix)
    for want in reversed(expected):
        items += [bytes(want) if not isinstance(want, int) else bn_serialize(want),
                  OP_EQUALVERIFY]
    items += [OP_DEPTH, N(0), OP_EQUAL]
    return ev(*items)


# =====================================================================
# 1. opcode 数值与原版头文件逐个一致
# =====================================================================
def test_every_opcode_value_matches_original_header():
    """tests/data/opcodes_v015.json 是用脚本从原版 script.h 的枚举里直接解析出来的。"""
    original = json.loads(
        (pathlib.Path(__file__).parent / "data" / "opcodes_v015.json").read_text())
    assert len(original) == 108
    for name, value in original.items():
        assert getattr(sc, name) == value, name


# =====================================================================
# 2. CBigNum 的字节编码
# =====================================================================
@pytest.mark.parametrize("n,encoded", [
    (0, ""), (1, "01"), (-1, "81"), (127, "7f"), (-127, "ff"),
    (128, "8000"), (-128, "8080"), (255, "ff00"), (-255, "ff80"),
    (256, "0001"), (-256, "0081"), (32767, "ff7f"), (32768, "008000"),
    (486604799, "ffff001d"),                # 主网创世块 scriptSig 里的那个数
    (2**64, "000000000000000001"),
])
def test_bignum_known_encodings(n, encoded):
    assert bn_serialize(n).hex() == encoded
    assert bn_deserialize(bytes.fromhex(encoded)) == n


@pytest.mark.parametrize("n", [0, 1, -1, 2**31, -(2**31), 2**200 + 12345, -(2**200) - 1])
def test_bignum_roundtrip_arbitrary_precision(n):
    assert bn_deserialize(bn_serialize(n)) == n


def test_bignum_non_canonical_inputs_decode_numerically():
    assert bn_deserialize(b"\x01\x00\x00") == 1        # 多余的高位 0
    assert bn_deserialize(b"\x00\x80") == 0            # 负零
    assert bn_deserialize(b"\x01\x80") == -1


@pytest.mark.parametrize("vch,truth", [
    (b"", False), (b"\x00", False), (b"\x00\x00", False),
    (b"\x80", False), (b"\x00\x80", False),            # 负零也是假
    (b"\x01", True), (b"\x81", True), (b"\x00\x01", True), (b"\x80\x00", True),
])
def test_cast_to_bool(vch, truth):
    assert cast_to_bool(vch) is truth


# =====================================================================
# 3. 压栈指令与脚本解析
# =====================================================================
def test_push_int_encoding():
    assert bytes(CScript().push_int(-1)) == bytes([OP_1NEGATE])
    assert bytes(CScript().push_int(0)) == b"\x00"
    for n in range(1, 17):
        assert bytes(CScript().push_int(n)) == bytes([OP_1 + n - 1])
    assert bytes(CScript().push_int(17)) == b"\x01\x11"
    assert bytes(CScript().push_int(-2)) == b"\x01\x82"


def test_push_bignum_is_always_a_data_push():
    assert bytes(CScript().push_bignum(4)) == b"\x01\x04"      # 而 push_int(4) 是 OP_4
    assert bytes(CScript().push_int(4)) == bytes([OP_4])


@pytest.mark.parametrize("size,prefix", [
    (0, "00"), (1, "01"), (75, "4b"),
    (76, "4c4c"), (255, "4cff"),
    (256, "4d0001"), (65535, "4dffff"),
    (65536, "4e00000100"),
])
def test_push_data_size_prefixes(size, prefix):
    s = CScript().push_data(b"\xaa" * size)
    assert bytes(s).hex().startswith(prefix)
    assert len(s) == len(bytes.fromhex(prefix)) + size
    assert list(s.ops()) == [(bytes.fromhex(prefix)[0], b"\xaa" * size)]


def test_small_int_opcodes_push_their_value():
    assert ev(OP_1NEGATE, bn_serialize(-1), OP_EQUAL)
    for n in range(1, 17):
        assert ev(OP_1 + n - 1, bn_serialize(n), OP_EQUAL)
    assert ev(OP_0, b"", OP_EQUAL)


@pytest.mark.parametrize("raw", [
    "01",               # 声称压 1 字节，但后面没数据了
    "4c",               # PUSHDATA1 缺长度字节
    "4c05aabb",         # PUSHDATA1 声称 5 字节，只有 2 字节
    "4d01",             # PUSHDATA2 长度字段不完整
    "4dffff00",         # PUSHDATA2 数据不够
    "4e010000",         # PUSHDATA4 长度字段不完整
    "4e0500000001",     # PUSHDATA4 数据不够
])
def test_truncated_pushes_fail(raw):
    assert not ev_raw(bytes.fromhex("51" + raw))        # 前面先压个 1，确认失败不是因为空栈


def test_truncated_push_fails_even_inside_unexecuted_branch():
    """不执行的分支里 opcode 会被跳过，但解析失败依然是失败。"""
    assert not ev_raw(bytes([OP_1, OP_0, OP_IF, 0x05, 0xAA, OP_ENDIF]))


def test_double_byte_opcodes_consume_two_bytes():
    # 0xF0 0x68 是**一条**双字节指令；如果被当成两条单字节指令，0x68(ENDIF) 会提前闭合 IF
    assert ev_raw(bytes([OP_0, OP_IF, 0xF0, OP_ENDIF, OP_ENDIF, OP_1]))
    # 末尾孤零零一个 0xF0：指令不完整
    assert not ev_raw(bytes([OP_1, 0xF0]))
    # 被执行到的双字节 opcode（包括模板占位符 OP_PUBKEY）都是未知指令
    assert not ev_raw(bytes([OP_1, 0xF0, 0x01]))
    assert bytes(CScript().push_opcode(OP_PUBKEYHASH)) == b"\xf0\x02"


# =====================================================================
# 4. 流程控制
# =====================================================================
def test_empty_script_is_false():
    assert not ev()


def test_result_is_top_of_stack():
    assert ev(N(1))
    assert not ev(N(0))
    assert ev(N(0), N(1))
    assert not ev(N(1), N(0))


def test_nop():
    assert ev(N(1), OP_NOP)


def test_op_ver_pushes_protocol_version():
    assert ev(OP_VER, N(105), OP_EQUAL)


@pytest.mark.parametrize("cond,want", [(1, 10), (0, 20), (-1, 10), (5, 10)])
def test_if_else_endif(cond, want):
    assert ev(N(cond), OP_IF, N(10), OP_ELSE, N(20), OP_ENDIF, N(want), OP_EQUAL)


def test_notif():
    assert ev(N(0), OP_NOTIF, N(1), OP_ELSE, N(0), OP_ENDIF)
    assert not ev(N(1), OP_NOTIF, N(1), OP_ELSE, N(0), OP_ENDIF)


def test_if_without_else():
    assert ev(N(1), N(1), OP_IF, OP_DROP, N(7), OP_ENDIF, N(7), OP_EQUAL)
    assert ev(N(9), N(0), OP_IF, OP_DROP, N(7), OP_ENDIF, N(9), OP_EQUAL)


def test_nested_if():
    def run(a, b):
        return [x for x in (11, 12, 21, 22) if ev(
            N(a), OP_IF,
                N(b), OP_IF, N(11), OP_ELSE, N(12), OP_ENDIF,
            OP_ELSE,
                N(b), OP_IF, N(21), OP_ELSE, N(22), OP_ENDIF,
            OP_ENDIF, N(x), OP_EQUAL)]
    assert run(1, 1) == [11]
    assert run(1, 0) == [12]
    assert run(0, 1) == [21]
    assert run(0, 0) == [22]


def test_unknown_opcodes_are_ignored_in_unexecuted_branch():
    assert ev(N(0), OP_IF, OP_RESERVED, 0xBB, 0xEE, OP_ENDIF, N(1))
    assert not ev(N(1), OP_IF, OP_RESERVED, OP_ENDIF, N(1))


def test_verif_and_vernotif():
    """OP_VERIF：协议版本 >= 栈顶的数 才进入分支——中本聪为"按版本升级脚本"留的机制。"""
    assert ev(N(105), OP_VERIF, N(1), OP_ELSE, N(0), OP_ENDIF)
    assert ev(N(1), OP_VERIF, N(1), OP_ELSE, N(0), OP_ENDIF)
    assert not ev(N(106), OP_VERIF, N(1), OP_ELSE, N(0), OP_ENDIF)
    assert ev(N(106), OP_VERNOTIF, N(1), OP_ELSE, N(0), OP_ENDIF)


def test_else_or_endif_without_if_fails():
    assert not ev(N(1), OP_ELSE)
    assert not ev(N(1), OP_ENDIF)


def test_if_with_empty_stack_fails():
    assert not ev(OP_IF, OP_ENDIF, N(1))


def test_unbalanced_if_is_not_checked_at_end():
    """原版 0.1.5 在脚本结束时**不检查** IF 是否闭合（后来的版本才加）。"""
    assert ev(N(1), N(1), OP_IF)                # IF 弹掉一个 1，栈里还剩一个 1 -> 真
    assert ev(N(1), N(0), OP_IF, N(0))          # 分支不执行，N(0) 被跳过，栈顶仍是 1 -> 真
    assert not ev(N(0), N(0), OP_IF, N(1))      # 同理，栈顶仍是 0 -> 假


def test_verify():
    assert ev(N(1), N(1), OP_VERIFY)
    assert not ev(N(1), OP_VERIFY)              # 通过了，但栈也空了
    assert not ev(N(1), N(0), OP_VERIFY)
    assert not ev(OP_VERIFY)


def test_failed_verify_jumps_to_end_instead_of_aborting():
    """VERIFY 失败后是"跳到末尾"：后面哪怕有非法指令也不会执行到。结果为假是因为栈顶是 0。"""
    assert not ev(N(0), OP_VERIFY, OP_RESERVED)


def test_op_return_jumps_to_end_the_famous_bug():
    """v0.1.x 的 OP_RETURN 只是"提前结束"，结果看栈顶。
    于是任何人都可以用 scriptSig = `OP_1 OP_RETURN` 花掉任何人的钱——
    这个漏洞直到 2010 年 7 月才被发现并修复。这里忠实保留，见 test_sighash.py 里的演示。"""
    assert ev(N(1), OP_RETURN)
    assert not ev(N(0), OP_RETURN)
    assert not ev(OP_RETURN)
    assert ev(N(1), OP_RETURN, OP_RESERVED, 0xBB)       # 后面的内容根本不会被看到


def test_reserved_and_unknown_opcodes_fail():
    for op in (OP_RESERVED, OP_RESERVED1, OP_RESERVED2, 0xB0, 0xEF):
        assert not ev(N(1), op)


# =====================================================================
# 5. 栈操作
# =====================================================================
A, B, C, D, E, F = b"a", b"b", b"c", b"d", b"e", b"f"


@pytest.mark.parametrize("before,op,after", [
    ([A, B], OP_2DROP, []),
    ([A, B], OP_2DUP, [A, B, A, B]),
    ([A, B, C], OP_3DUP, [A, B, C, A, B, C]),
    ([A, B, C, D], OP_2OVER, [A, B, C, D, A, B]),
    ([A, B, C, D, E, F], OP_2ROT, [C, D, E, F, A, B]),
    ([A, B, C, D], OP_2SWAP, [C, D, A, B]),
    ([A], OP_DROP, []),
    ([A], OP_DUP, [A, A]),
    ([A, B], OP_NIP, [B]),
    ([A, B], OP_OVER, [A, B, A]),
    ([A, B, C], OP_ROT, [B, C, A]),
    ([A, B], OP_SWAP, [B, A]),
    ([A, B], OP_TUCK, [B, A, B]),
    ([A], OP_IFDUP, [A, A]),
    ([b""], OP_IFDUP, [b""]),
])
def test_stack_ops(before, op, after):
    assert leaves_stack(list(before) + [op], after)


def test_depth():
    assert leaves_stack([OP_DEPTH], [0])
    assert leaves_stack([A, B, C, OP_DEPTH], [A, B, C, 3])


def test_pick_and_roll():
    assert leaves_stack([A, B, C, N(0), OP_PICK], [A, B, C, C])
    assert leaves_stack([A, B, C, N(2), OP_PICK], [A, B, C, A])
    assert leaves_stack([A, B, C, N(0), OP_ROLL], [A, B, C])
    assert leaves_stack([A, B, C, N(2), OP_ROLL], [B, C, A])
    assert not ev(A, B, C, N(3), OP_PICK)               # 越界
    assert not ev(A, B, C, N(-1), OP_ROLL)


def test_altstack():
    assert leaves_stack([A, B, OP_TOALTSTACK, C, OP_FROMALTSTACK], [A, C, B])
    assert not ev(N(1), OP_FROMALTSTACK)                # 副栈是空的


@pytest.mark.parametrize("op,needed", [
    (OP_2DROP, 2), (OP_2DUP, 2), (OP_3DUP, 3), (OP_2OVER, 4), (OP_2ROT, 6),
    (OP_2SWAP, 4), (OP_DROP, 1), (OP_DUP, 1), (OP_NIP, 2), (OP_OVER, 2),
    (OP_ROT, 3), (OP_SWAP, 2), (OP_TUCK, 2), (OP_IFDUP, 1), (OP_TOALTSTACK, 1),
    (OP_PICK, 2), (OP_ROLL, 2), (OP_CAT, 2), (OP_SUBSTR, 3), (OP_LEFT, 2),
    (OP_RIGHT, 2), (OP_SIZE, 1), (OP_INVERT, 1), (OP_AND, 2), (OP_EQUAL, 2),
    (OP_1ADD, 1), (OP_ADD, 2), (OP_WITHIN, 3), (OP_SHA256, 1), (OP_CHECKSIG, 2),
])
def test_stack_underflow_fails(op, needed):
    """栈里的元素比指令需要的少一个 -> 失败（而不是崩溃）。"""
    items = [N(1)] * (needed - 1) + [op, N(1)]
    assert not ev(*items)


# =====================================================================
# 6. 字符串拼接/截取（0.1.5 里还没有被禁用）
# =====================================================================
def test_cat():
    assert leaves_stack([b"abc", b"def", OP_CAT], [b"abcdef"])
    assert leaves_stack([b"", b"x", OP_CAT], [b"x"])


@pytest.mark.parametrize("begin,size,want", [
    (0, 3, b"abc"), (2, 3, b"cde"), (0, 0, b""), (4, 100, b"ef"),
    (6, 1, b""), (100, 5, b""),             # 越界会被钳制到末尾，而不是失败
])
def test_substr(begin, size, want):
    assert leaves_stack([b"abcdef", N(begin), N(size), OP_SUBSTR], [want])


def test_substr_negative_arguments_fail():
    assert not ev(b"abcdef", N(-1), N(2), OP_SUBSTR)
    assert not ev(b"abcdef", N(1), N(-1), OP_SUBSTR)


@pytest.mark.parametrize("op,n,want", [
    (OP_LEFT, 2, b"ab"), (OP_LEFT, 0, b""), (OP_LEFT, 99, b"abcdef"),
    (OP_RIGHT, 2, b"ef"), (OP_RIGHT, 0, b""), (OP_RIGHT, 99, b"abcdef"),
])
def test_left_right(op, n, want):
    assert leaves_stack([b"abcdef", N(n), op], [want])


def test_left_right_negative_fails():
    assert not ev(b"abcdef", N(-1), OP_LEFT)
    assert not ev(b"abcdef", N(-1), OP_RIGHT)


def test_size_keeps_the_operand():
    assert leaves_stack([b"abcde", OP_SIZE], [b"abcde", 5])
    assert leaves_stack([b"", OP_SIZE], [b"", 0])


# =====================================================================
# 7. 位运算与相等
# =====================================================================
def test_invert():
    assert leaves_stack([bytes([0x0F, 0xF0, 0x00]), OP_INVERT], [bytes([0xF0, 0x0F, 0xFF])])


def test_and_or_xor():
    x, y = bytes([0b1100, 0xFF]), bytes([0b1010, 0x0F])
    assert leaves_stack([x, y, OP_AND], [bytes([0b1000, 0x0F])])
    assert leaves_stack([x, y, OP_OR], [bytes([0b1110, 0xFF])])
    assert leaves_stack([x, y, OP_XOR], [bytes([0b0110, 0xF0])])


def test_bitwise_pads_shorter_operand_with_trailing_zeros():
    assert leaves_stack([b"\xff\xff\xff", b"\x0f", OP_AND], [b"\x0f\x00\x00"])
    assert leaves_stack([b"\x0f", b"\xf0\xff", OP_OR], [b"\xff\xff"])


def test_equal_is_bytewise_not_numeric():
    """中本聪在源码注释里解释过：1 可以写成 01、0100、010000……数值相同但字节不同。
    OP_EQUAL 比的是字节，OP_NUMEQUAL 比的才是数值。"""
    assert ev(b"\x01", b"\x01", OP_EQUAL)
    assert not ev(b"\x01", b"\x01\x00", OP_EQUAL)
    assert ev(b"\x01", b"\x01\x00", OP_NUMEQUAL)


def test_equalverify():
    assert ev(N(1), A, A, OP_EQUALVERIFY)
    assert not ev(N(1), A, B, OP_EQUALVERIFY)
    assert not ev(A, A, OP_EQUALVERIFY)                 # 通过了但栈空了


# =====================================================================
# 8. 数值运算（任意精度）
# =====================================================================
@pytest.mark.parametrize("a,op,want", [
    (5, OP_1ADD, 6), (-1, OP_1ADD, 0), (5, OP_1SUB, 4), (0, OP_1SUB, -1),
    (7, OP_2MUL, 14), (-7, OP_2MUL, -14),
    (7, OP_2DIV, 3), (-7, OP_2DIV, -3), (-1, OP_2DIV, 0),    # 对幅值移位：-7 -> -3，不是 -4
    (5, OP_NEGATE, -5), (-5, OP_NEGATE, 5), (0, OP_NEGATE, 0),
    (-5, OP_ABS, 5), (5, OP_ABS, 5),
    (0, OP_NOT, 1), (1, OP_NOT, 0), (7, OP_NOT, 0), (-7, OP_NOT, 0),
    (0, OP_0NOTEQUAL, 0), (7, OP_0NOTEQUAL, 1), (-7, OP_0NOTEQUAL, 1),
    (2**100, OP_1ADD, 2**100 + 1),
])
def test_unary_numeric(a, op, want):
    assert leaves_stack([bn_serialize(a), op], [want])


BIG = 2**200 + 12345


@pytest.mark.parametrize("a,b,op,want", [
    (2, 3, OP_ADD, 5), (-2, 3, OP_ADD, 1), (BIG, 1, OP_ADD, BIG + 1),
    (2, 3, OP_SUB, -1), (BIG, BIG, OP_SUB, 0),
    (6, 7, OP_MUL, 42), (-6, 7, OP_MUL, -42), (BIG, BIG, OP_MUL, BIG * BIG),
    # 除法向零取整（C 语言习惯），不是 Python 的向下取整
    (7, 2, OP_DIV, 3), (-7, 2, OP_DIV, -3), (7, -2, OP_DIV, -3), (-7, -2, OP_DIV, 3),
    # 大数除法必须是精确的整数运算（用浮点会丢精度）
    (BIG * 7 + 3, 7, OP_DIV, BIG), (BIG * 7 + 3, 7, OP_MOD, 3),
    # 余数的符号跟着被除数
    (7, 3, OP_MOD, 1), (-7, 3, OP_MOD, -1), (7, -3, OP_MOD, 1), (-7, -3, OP_MOD, -1),
    (1, 10, OP_LSHIFT, 1024), (-3, 1, OP_LSHIFT, -6), (5, 0, OP_LSHIFT, 5),
    (1024, 3, OP_RSHIFT, 128), (-3, 1, OP_RSHIFT, -1), (1, 5, OP_RSHIFT, 0),
    (1, 1, OP_BOOLAND, 1), (1, 0, OP_BOOLAND, 0), (0, 0, OP_BOOLAND, 0), (-1, 5, OP_BOOLAND, 1),
    (1, 0, OP_BOOLOR, 1), (0, 0, OP_BOOLOR, 0),
    (5, 5, OP_NUMEQUAL, 1), (5, 6, OP_NUMEQUAL, 0),
    (5, 6, OP_NUMNOTEQUAL, 1), (5, 5, OP_NUMNOTEQUAL, 0),
    (5, 6, OP_LESSTHAN, 1), (6, 5, OP_LESSTHAN, 0), (5, 5, OP_LESSTHAN, 0),
    (6, 5, OP_GREATERTHAN, 1), (5, 5, OP_GREATERTHAN, 0),
    (5, 5, OP_LESSTHANOREQUAL, 1), (6, 5, OP_LESSTHANOREQUAL, 0),
    (5, 5, OP_GREATERTHANOREQUAL, 1), (4, 5, OP_GREATERTHANOREQUAL, 0),
    (3, 9, OP_MIN, 3), (-3, -9, OP_MIN, -9), (3, 9, OP_MAX, 9), (-3, -9, OP_MAX, -3),
])
def test_binary_numeric(a, b, op, want):
    assert leaves_stack([bn_serialize(a), bn_serialize(b), op], [want])


def test_division_by_zero_fails():
    assert not ev(N(1), N(7), N(0), OP_DIV)
    assert not ev(N(1), N(7), N(0), OP_MOD)


def test_shift_limits():
    assert not ev(N(1), N(1), N(-1), OP_LSHIFT, OP_DROP)
    assert not ev(N(1), N(1), N(-1), OP_RSHIFT, OP_DROP)
    assert ev(N(1), N(2048), OP_LSHIFT)
    assert not ev(N(1), N(1), N(2049), OP_LSHIFT, OP_DROP)      # 本项目加的安全上限


def test_numequalverify():
    assert ev(N(1), N(5), N(5), OP_NUMEQUALVERIFY)
    assert not ev(N(1), N(5), N(6), OP_NUMEQUALVERIFY)


@pytest.mark.parametrize("x,lo,hi,want", [
    (5, 5, 10, 1), (9, 5, 10, 1), (10, 5, 10, 0), (4, 5, 10, 0), (-1, -5, 0, 1),
])
def test_within_is_half_open_interval(x, lo, hi, want):
    assert leaves_stack([bn_serialize(x), bn_serialize(lo), bn_serialize(hi), OP_WITHIN], [want])


def test_numeric_ops_accept_non_canonical_operands():
    assert leaves_stack([b"\x02\x00\x00", b"\x03\x00", OP_ADD], [5])


# =====================================================================
# 9. 哈希指令
# =====================================================================
@pytest.mark.parametrize("op,fn", [
    (OP_RIPEMD160, ripemd160), (OP_SHA1, sha1), (OP_SHA256, sha256),
    (OP_HASH160, hash160), (OP_HASH256, hash256),
])
@pytest.mark.parametrize("data", [b"", b"abc", b"\x00" * 100])
def test_hash_opcodes(op, fn, data):
    assert leaves_stack([data, op], [fn(data)])


# =====================================================================
# 10. FindAndDelete 与 Solver
# =====================================================================
def test_find_and_delete_removes_all_occurrences_at_op_boundaries():
    sig = CScript().push_data(b"\xAA\xBB")
    s = script(OP_DUP) + sig + script(OP_HASH160) + sig + sig + script(OP_CHECKSIG)
    assert s.find_and_delete(bytes(sig)) == 3
    assert bytes(s) == bytes([OP_DUP, OP_HASH160, OP_CHECKSIG])


def test_find_and_delete_ignores_matches_inside_push_data():
    """字节序列 02 AA BB 出现在另一段数据的**内部**（不在指令边界上）时不应被删除。"""
    pattern = bytes(CScript().push_data(b"\xAA\xBB"))                   # 02 AA BB
    s = CScript().push_data(b"\x99" + pattern)                          # 04 99 02 AA BB
    assert s.find_and_delete(pattern) == 0
    assert bytes(s) == b"\x04\x99\x02\xaa\xbb"


def test_find_and_delete_nothing_to_delete():
    s = script(OP_DUP, OP_HASH160)
    assert s.find_and_delete(b"\x02\xaa\xbb") == 0
    assert s.find_and_delete(b"") == 0


def test_solver_recognises_the_two_standard_templates():
    from bitcoin.key import CKey
    pub = CKey.generate().get_pubkey()
    assert sc.solver(sc.script_pubkey_for_pubkey(pub)) == (sc.TX_PUBKEY, pub)
    assert sc.solver(sc.script_pubkey_for_hash160(hash160(pub))) == (
        sc.TX_PUBKEYHASH, hash160(pub))


@pytest.mark.parametrize("bad", [
    script(OP_DUP),
    script(b"\x01" * 32, OP_CHECKSIG),                      # "公钥"只有 32 字节：太短
    script(OP_DUP, OP_HASH160, b"\x01" * 19, OP_EQUALVERIFY, OP_CHECKSIG),   # 哈希不是 20 字节
    script(OP_DUP, OP_HASH160, b"\x01" * 20, OP_EQUAL, OP_CHECKSIG),         # 指令不对
    script(b"\x04" * 65, OP_CHECKSIG, OP_NOP),              # 多了一条指令
    CScript(b"\x05\x01"),                                   # 根本解析不了
    CScript(),
])
def test_solver_rejects_non_standard_scripts(bad):
    assert sc.solver(bad) is None
