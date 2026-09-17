"""实验 5：解剖史上第一笔比特币转账（中本聪 -> Hal Finney，2009-01-12）。
对应教程第 6 章。运行：python labs/lab05_transaction.py
"""
import json
import pathlib

from _common import ROOT, step, title

from bitcoin import base58
from bitcoin.script import script_to_asm, solver, verify_signature
from bitcoin.serialize import DataStream, uint256_to_hex
from bitcoin.tx import CTransaction
from bitcoin.util import format_money

title("实验 5：解剖一笔真实的交易")

vectors = json.loads((pathlib.Path(ROOT) / "tests" / "data" / "mainnet_vectors.json")
                     .read_text(encoding="utf-8"))
raw = bytes.fromhex(vectors["block170_first_payment"]["hex"])
prev_raw = bytes.fromhex(vectors["block9_coinbase"]["hex"])

step(f"1. 这笔交易的全部内容：{len(raw)} 个字节")
print("  ", raw.hex())

step("2. 拿着“手术刀”（读游标）从头往后切")
s = DataStream(raw)
print(f"   [4 字节] 版本号 nVersion          = {s.read_int32()}")
n_in = s.read_compact_size()
print(f"   [1 字节] 输入个数                 = {n_in}")
prev_hash = s.read_uint256()
print(f"   [32字节] 输入#0 引用的交易哈希    = {uint256_to_hex(prev_hash)}")
print(f"   [4 字节] 输入#0 引用那笔交易的第几个输出 = {s.read_uint32()}")
script_sig = s.read_string()
print(f"   [1+{len(script_sig)}字节] 输入#0 的 scriptSig（解锁脚本）：")
print(f"            {script_to_asm(script_sig)[:60]}...  （一个签名）")
print(f"   [4 字节] 输入#0 的序列号 nSequence = {s.read_uint32():#x}")
n_out = s.read_compact_size()
print(f"   [1 字节] 输出个数                 = {n_out}")
for i in range(n_out):
    value = s.read_int64()
    script_pubkey = s.read_string()
    print(f"   [8 字节] 输出#{i} 的金额            = {value} 聪 = {format_money(value)} BTC")
    print(f"   [1+{len(script_pubkey)}字节] 输出#{i} 的 scriptPubKey（锁定脚本）：")
    print(f"            {script_to_asm(script_pubkey)[:40]}... OP_CHECKSIG")
print(f"   [4 字节] 锁定时间 nLockTime       = {s.read_uint32()}")
print(f"   还剩 {s.remaining()} 字节 —— 刚好切完。")

step("3. 用项目里的 CTransaction 类做同样的事")
tx = CTransaction.deserialize(DataStream(raw))
prev = CTransaction.deserialize(DataStream(prev_raw))
print(f"   交易 ID（整笔交易做两次 SHA-256）= {uint256_to_hex(tx.get_hash())}")
print("   去任何区块浏览器搜这个 ID，都能看到这笔交易。")

step("4. 钱从哪里来？——输入指向了第 9 号区块的 coinbase（中本聪自己挖到的 50 BTC）")
print(f"   被引用的交易 ID = {uint256_to_hex(prev.get_hash())}")
print(f"   它是 coinbase 吗？{prev.is_coinbase()}；金额 = {format_money(prev.vout[0].n_value)} BTC")

step("5. 钱到哪里去？")
for i, out in enumerate(tx.vout):
    _, pubkey = solver(out.script_pubkey)
    print(f"   输出#{i}: {format_money(out.n_value):>6} BTC -> {base58.pubkey_to_address(pubkey)}")
print("   第一个是 Hal Finney；第二个地址和第 9 号区块 coinbase 的收款地址相同——")
print("   这就是“找零”：50 的整钞付 10，剩下 40 付回给自己。")

step("6. 手续费 = 输入总额 - 输出总额")
fee = prev.vout[0].n_value - tx.get_value_out()
print(f"   {format_money(prev.vout[0].n_value)} - {format_money(tx.get_value_out())} = {format_money(fee)} BTC（2009 年，免费）")

step("7. 验证：这笔交易真的有权花那 50 BTC 吗？")
print(f"   verify_signature -> {verify_signature(prev, tx, 0)}")
tx.vout[0].n_value += 1
print(f"   把付给 Hal 的金额偷偷加 1 聪再验 -> {verify_signature(prev, tx, 0)}")
print("   签名把“付给谁、付多少”都锁死了，矿工和转发的节点谁也改不了。")
print("\n实验 5 完成。")
