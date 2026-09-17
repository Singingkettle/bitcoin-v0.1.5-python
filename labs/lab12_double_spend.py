"""实验 12：在真实的 P2P 网络上尝试“双花”——比特币要解决的核心问题。
对应教程第 14 章。运行：python labs/lab12_double_spend.py   （约需 25 秒）

剧情：Mallory（坏人）有一枚 50 BTC 的币。她同时做了两笔交易：
  交易甲：把这 50 BTC 付给 Bob；  交易乙：把**同一枚币**付给 Carol。
她把甲直接塞给 Bob 的节点，把乙直接塞给 Carol 的节点。网络会怎么处理？
"""
import socket
import time

from _common import mine, step, temp_datadir, title

from bitcoin import base58, params
from bitcoin.config import Config
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.node import Node
from bitcoin.script import script_pubkey_for_hash160, sign_signature
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut
from bitcoin.util import format_money

title("实验 12：双花攻击与“先到先得 + 最长链”")
COIN = params.COIN


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(predicate, timeout=30):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.05)
    assert predicate(), "等待超时"


class KeyRing:
    def __init__(self, key):
        self.key = key

    def get_key_for_pubkey(self, pub):
        return self.key if self.key.get_pubkey() == bytes(pub) else None

    def get_key_for_hash160(self, h):
        return None


port_b, port_c = free_port(), free_port()
bob = Node(Config([f"-datadir={temp_datadir('bob')}", f"-port={port_b}"]))
carol = Node(Config([f"-datadir={temp_datadir('carol')}", f"-port={port_c}",
                     f"-connect=127.0.0.1:{port_b}"]))

step("0. 准备：Mallory 挖到一枚币并等它成熟；Bob、Carol 两个诚实节点联网同步")
mallory = CKey.generate()
coin = mine(bob.chain, 1, key=mallory, quiet=True)[0].vtx[0]
mine(bob.chain, 100, quiet=True)
bob.start()
carol.start()
wait(lambda: carol.chain.best_height == bob.chain.best_height == 101)
print(f"   两个节点高度都是 {bob.chain.best_height}；Mallory 的币：{format_money(coin.vout[0].n_value)} BTC")


def pay(to_address: str) -> CTransaction:
    tx = CTransaction(vin=[CTxIn(COutPoint(coin.get_hash(), 0))],
                      vout=[CTxOut(50 * COIN,
                                   script_pubkey_for_hash160(base58.address_to_hash160(to_address)))])
    assert sign_signature(KeyRing(mallory), coin, tx, 0)
    return tx


step("1. Mallory 用同一枚币签了两笔交易——两笔的签名都是完全合法的")
tx_bob = pay(bob.wallet.get_default_address())
tx_carol = pay(carol.wallet.get_default_address())
print(f"   交易甲（付给 Bob）  ：{tx_bob.get_hash():064x}"[:60] + "…")
print(f"   交易乙（付给 Carol）：{tx_carol.get_hash():064x}"[:60] + "…")
print("   密码学只能证明“这确实是 Mallory 签的”，证明不了“她没有把同一枚币再签给别人”。")
print("   这就是“双花问题”——数字货币 30 年来最大的难题，也是比特币真正解决的问题。")

step("2. 她同时把甲塞给 Bob 的节点、把乙塞给 Carol 的节点")
print(f"   Bob 的节点接受交易甲：  {bob.mempool.accept(tx_bob)}")
print(f"   Carol 的节点接受交易乙：{carol.mempool.accept(tx_carol)}")
time.sleep(1.5)                 # 给两个节点一点时间互相转发
print("   两个节点都把自己收到的交易转发给了对方，但是——")
print(f"   Bob 的内存池：  {['甲' if t.get_hash() == tx_bob.get_hash() else '乙' for t in bob.mempool.transactions()]}")
print(f"   Carol 的内存池：{['甲' if t.get_hash() == tx_bob.get_hash() else '乙' for t in carol.mempool.transactions()]}")
print("   每个节点只认**自己先看到的那一笔**，后到的那笔因为“花了同一枚币”被拒之门外。")
print(f"   此刻 Bob 的钱包显示余额 {format_money(bob.wallet.get_balance())}，"
      f"Carol 的钱包也显示 {format_money(carol.wallet.get_balance())} —— 两个人都以为自己收到钱了！")

step("3. 谁说了算？——下一个挖出区块的人。这次是 Carol 挖到了")
block = carol.miner.mine_one_block()
wait(lambda: bob.chain.best_height == 102)
print(f"   Carol 的区块包含交易{'乙' if any(t.get_hash() == tx_carol.get_hash() for t in block.vtx) else '甲'}；"
      f"Bob 的节点验证后接受了这个区块（高度 {bob.chain.best_height}）")
print(f"   交易乙的确认数：{bob.chain.get_tx_depth(tx_carol.get_hash())}；"
      f"交易甲的确认数：{bob.chain.get_tx_depth(tx_bob.get_hash())}")

step("4. 交易甲从此成了“死交易”")
candidate, _ = bob.miner.create_new_block()
print(f"   Bob 自己挖矿时也没法打包交易甲了：候选区块里只有 {len(candidate.vtx)} 笔交易（coinbase）")
print("   ——因为它要花的那枚币，在区块链上已经被交易乙花掉了。")
print(f"   可是 Bob 的钱包仍然显示余额 {format_money(bob.wallet.get_balance())}（零确认也计入余额，0.1.5 就是如此）。")
print("   如果 Bob 在第 2 步看到余额就发了货，他就被骗了。")

step("5. 结论")
print("   ① 零确认的交易不可信：先到先得只是各个节点自己的临时看法，全网并没有达成一致。")
print("   ② 区块链就是全网对“交易先后顺序”的唯一共识：被写进最长链的那一笔才算数。")
print("   ③ 想推翻它，必须重做这个区块以及它之后所有区块的工作量证明（见实验 9）。")
print("   白皮书第 2 节的核心意思正是：收款人需要确信，这枚币之前的主人没有把它先签给过别人。")

carol.stop()
bob.stop()
print("\n实验 12 完成。")
