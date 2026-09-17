"""实验 6：脚本虚拟机——看着栈一步一步变化。
对应教程第 7 章。运行：python labs/lab06_script.py
"""
from _common import step, title

from bitcoin import params
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.script import (
    OP_1,
    OP_ADD,
    OP_CODESEPARATOR,
    OP_EQUAL,
    OP_RETURN,
    OPCODE_NAMES,
    CScript,
    eval_script,
    script_pubkey_for_hash160,
    script_to_asm,
    sign_signature,
    verify_signature,
)
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut

title("实验 6：脚本虚拟机")


def short(b: bytes) -> str:
    h = b.hex()
    return h if len(h) <= 16 else f"{h[:8]}…{h[-4:]}({len(b)}字节)"


def run(script, tx, n_in=0):
    """执行脚本并打印每一步之后栈的样子（栈顶在右边）。"""
    def trace(opcode, data, stack):
        name = f"压入 {short(data)}" if data is not None else OPCODE_NAMES.get(opcode, hex(opcode))
        print(f"      {name:<28} 栈: [{', '.join(short(x) or '空' for x in stack)}]")
    result = eval_script(CScript(script), tx, n_in, trace=trace)
    print(f"      => 脚本结果：{result}")
    return result


dummy = CTransaction(vin=[CTxIn()], vout=[CTxOut(0)])

step("1. 热身：2 + 3 == 5 ?   脚本：OP_2 OP_3 OP_ADD OP_5 OP_EQUAL")
run(CScript().push_int(2).push_int(3).push_opcode(OP_ADD).push_int(5).push_opcode(OP_EQUAL), dummy)
print("   逆波兰式（后缀表达式）：先放操作数，再放运算符。没有变量，没有循环，只有一个栈。")

step("2. 造一笔真正的“付到地址”（P2PKH）交易")
alice, bob = CKey.generate(), CKey.generate()
# 一笔“以前的交易”：50 BTC 锁给 Alice 的地址
prev = CTransaction(vin=[CTxIn(COutPoint(), CScript().push_int(0).push_int(7))],
                    vout=[CTxOut(50 * params.COIN,
                                 script_pubkey_for_hash160(hash160(alice.get_pubkey())))])
print(f"   锁（scriptPubKey）：{script_to_asm(prev.vout[0].script_pubkey)}")
print("   翻译成人话：“谁能出示一个公钥，它的哈希等于这 20 字节，并且能用对应的私钥签名，钱就归谁。”")


class KeyRing:
    def __init__(self, *keys):
        self.keys = keys

    def get_key_for_pubkey(self, pub):
        return next((k for k in self.keys if k.get_pubkey() == bytes(pub)), None)

    def get_key_for_hash160(self, h):
        return next((k for k in self.keys if hash160(k.get_pubkey()) == bytes(h)), None)


spend = CTransaction(vin=[CTxIn(COutPoint(prev.get_hash(), 0))],
                     vout=[CTxOut(50 * params.COIN,
                                  script_pubkey_for_hash160(hash160(bob.get_pubkey())))])
assert sign_signature(KeyRing(alice), prev, spend, 0)
print(f"   钥匙（scriptSig）：<签名 {len(list(CScript(spend.vin[0].script_sig).ops())[0][1])} 字节> <公钥 65 字节>")

step("3. 验证 = 把“钥匙 + 分隔符 + 锁”拼成一段脚本，执行一遍")
combined = (CScript(spend.vin[0].script_sig) + CScript([OP_CODESEPARATOR])
            + CScript(prev.vout[0].script_pubkey))
run(combined, spend)

step("4. Bob 想冒充 Alice：用自己的私钥签名、附上自己的公钥")
forged = CTransaction(vin=[CTxIn(COutPoint(prev.get_hash(), 0))],
                      vout=[CTxOut(50 * params.COIN,
                                   script_pubkey_for_hash160(hash160(bob.get_pubkey())))])
bob_view = CTransaction(vin=[CTxIn(COutPoint(), b"\x00\x00")],
                        vout=[CTxOut(0, script_pubkey_for_hash160(hash160(bob.get_pubkey())))])
forged_in = CTransaction(vin=[CTxIn(COutPoint(bob_view.get_hash(), 0))], vout=forged.vout)
assert sign_signature(KeyRing(bob), bob_view, forged_in, 0)
forged.vin[0].script_sig = forged_in.vin[0].script_sig       # 把 Bob 的“钥匙”插到 Alice 的“锁”上
combined = (CScript(forged.vin[0].script_sig) + CScript([OP_CODESEPARATOR])
            + CScript(prev.vout[0].script_pubkey))
run(combined, forged)
print("   卡在 OP_EQUALVERIFY：Bob 公钥的哈希和锁里写的不一样。")

step("5. 0.1.x 的著名漏洞：不需要任何私钥，scriptSig 写 `OP_1 OP_RETURN` 就能开任何锁")
theft = CTransaction(vin=[CTxIn(COutPoint(prev.get_hash(), 0), bytes([OP_1, OP_RETURN]))],
                     vout=[CTxOut(50 * params.COIN,
                                  script_pubkey_for_hash160(hash160(bob.get_pubkey())))])
combined = (CScript(theft.vin[0].script_sig) + CScript([OP_CODESEPARATOR])
            + CScript(prev.vout[0].script_pubkey))
run(combined, theft)
print(f"   verify_signature -> {verify_signature(prev, theft, 0)}")
print("   OP_RETURN 让执行在到达“锁”之前就结束了，而此时栈顶是 1（真）。")
print("   这个漏洞 2010 年 7 月被发现并修复：改成先单独执行 scriptSig、再执行 scriptPubKey，")
print("   并且 OP_RETURN 改为“立即失败”。本项目忠实复刻 v0.1.5，所以漏洞也原样保留。")
print("\n实验 6 完成。")
