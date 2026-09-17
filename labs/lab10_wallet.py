"""实验 10：钱包、内存池与手续费——“余额”到底是什么？
对应教程第 11、12 章。运行：python labs/lab10_wallet.py   （约需 20 秒）
"""
from _common import mine, step, temp_datadir, title

from bitcoin import base58, params
from bitcoin.blockchain import Blockchain
from bitcoin.mempool import MemPool
from bitcoin.miner import Miner
from bitcoin.script import script_to_asm, solver
from bitcoin.util import format_money
from bitcoin.wallet import Wallet

title("实验 10：钱包、内存池与手续费")
COIN, CENT = params.COIN, params.CENT

chain = Blockchain(temp_datadir("chain"))
pool = MemPool(chain)
alice = Wallet(temp_datadir("alice"), chain, pool)
bob = Wallet(temp_datadir("bob"), chain, pool)
miner = Miner(chain, pool, alice)

step("1. 新钱包：一把密钥、一个地址、余额为 0")
print(f"   Alice 的地址：{alice.get_default_address()}")
print(f"   钱包里的密钥数：{len(alice.keys)}，钱包交易数：{len(alice.map_wallet)}，余额：{format_money(alice.get_balance())}")

step("2. Alice 挖到一个区块：50 BTC 到手了吗？")
miner.mine_one_block()
wtx = next(iter(alice.map_wallet.values()))
print(f"   钱包里多了一笔“挖矿所得”交易，面值 {format_money(alice.get_credit(wtx.tx))}")
print(f"   但余额仍是 {format_money(alice.get_balance())}：还要再等 {alice.blocks_to_maturity(wtx)} 个区块才“成熟”")
print("   （共识规则要求 100 个确认；钱包自己更保守，要等 120 个）")

step("3. 再挖 124 个块")
for _ in range(124):
    miner.mine_one_block()
print(f"   当前高度 {chain.best_height}，钱包交易 {len(alice.map_wallet)} 笔，密钥 {len(alice.keys)} 把")
print(f"   余额 = {format_money(alice.get_balance())}  （只有最早的 {alice.get_balance() // (50 * COIN)} 个区块的奖励成熟了）")
print("   钱包里并没有一个叫“余额”的数字——它是把所有“属于我、已成熟、还没花”的输出现加出来的。")

step("4. Alice 付 12.34 BTC 给 Bob")
ok, txid = alice.send_money(bob.get_default_address(), 12 * COIN + 34 * CENT)
print(f"   发送成功：{ok}，交易 ID {txid[:16]}…")
tx = pool.transactions()[0]
print(f"   这笔交易有 {len(tx.vin)} 个输入、{len(tx.vout)} 个输出：")
for i, out in enumerate(tx.vout):
    kind, data = solver(out.script_pubkey)
    addr = base58.pubkey_to_address(data) if kind == "pubkey" else base58.hash160_to_address(data)
    whose = "Bob" if bob.is_mine_txout(out) else "Alice（找零）"
    print(f"      输出#{i}: {format_money(out.n_value):>6} -> {addr}  [{whose}]")
    print(f"               锁：{script_to_asm(out.script_pubkey)[:50]}…")
spent = alice.map_wallet[tx.vin[0].prevout.hash]
print(f"   被花掉的是一整枚 50 BTC 的挖矿所得（f_spent={spent.f_spent}）；找零付回了**同一个公钥**：")
print(f"   {bytes(tx.vout[1].script_pubkey) == bytes(spent.tx.vout[0].script_pubkey)}"
      "  —— 0.1.5 就是这么做的，和中本聪付给 Hal Finney 那笔真实交易一样")

step("5. 交易还没进区块，但双方的余额已经变了（零确认）")
print(f"   内存池里有 {len(pool)} 笔交易")
print(f"   Alice 余额 {format_money(alice.get_balance())}，Bob 余额 {format_money(bob.get_balance())}")
print("   注意：零确认的钱并不安全（见实验 9），原版界面会把它标为“0/unconfirmed”。")

step("6. 挖一个块把它确认")
block = miner.mine_one_block()
print(f"   新区块里有 {len(block.vtx)} 笔交易（1 笔 coinbase + 刚才那笔转账）")
print(f"   内存池剩 {len(pool)} 笔；Bob 那笔钱的确认数 = {bob.get_depth(next(iter(bob.map_wallet.values())))}")

step("7. 手续费：矿工的小费")
alice.set_transaction_fee(5 * CENT)
ok, _ = alice.send_money(bob.get_default_address(), 1 * COIN)
tx = pool.transactions()[0]
inputs_value = sum(alice.map_wallet[i.prevout.hash].tx.vout[i.prevout.n].n_value for i in tx.vin)
print(f"   输入合计 {format_money(inputs_value)}，输出合计 {format_money(tx.get_value_out())}，"
      f"差额 {format_money(inputs_value - tx.get_value_out())} 就是手续费")
print("   交易里没有“手续费”这个字段——输入比输出多出来的部分，谁挖到这个块就归谁。")
block, _ = miner.create_new_block()
print(f"   矿工组装的候选区块里，coinbase 的金额 = {format_money(block.vtx[0].vout[0].n_value)}（50 补贴 + 0.05 手续费）")
alice.set_transaction_fee(0)

step("8. 0.1.5 的手续费规则（矿工打包时执行，内存池不检查）")
print(f"   普通小交易（{tx.get_serialize_size()} 字节）：最低手续费 = {format_money(tx.get_min_fee(f_discount=True))}  （每个区块前 100 笔免费）")
print(f"   同一笔交易如果排在区块的第 100 笔之后：     {format_money(tx.get_min_fee(f_discount=False))}  （每千字节 0.01）")
print("   任何含有小于 0.01 BTC 输出（“粉尘”）的交易：  至少 0.01")

step("9. 钱包文件里有什么？")
print(f"   {len(alice.keys)} 把私钥（明文！）、{len(alice.map_wallet)} 笔钱包交易、地址簿 {alice.address_book}")
print("   2009 年的 wallet.dat 同样是明文保存私钥——钱包加密要到 2011 年才出现。")
alice.close()
bob.close()
chain.close()
print("\n实验 10 完成。")
