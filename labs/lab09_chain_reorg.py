"""实验 9：最长链、分叉、重组，以及“为什么要等 6 个确认”。
对应教程第 10 章。运行：python labs/lab09_chain_reorg.py   （约需 20 秒）
"""
import math

from _common import mine, step, temp_datadir, title

from bitcoin import params
from bitcoin.blockchain import Blockchain
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.mempool import MemPool
from bitcoin.script import script_pubkey_for_hash160, sign_signature
from bitcoin.serialize import uint256_to_hex
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut

title("实验 9：区块链、分叉与重组")


def show(chain, note=""):
    tips = " <- ".join(f"#{p.n_height}:{uint256_to_hex(p.hash)[4:10]}" for p in chain.main_chain[-5:])
    print(f"   主链（最近 5 块）：{tips}   {note}")


class KeyRing:
    def __init__(self, key):
        self.key = key

    def get_key_for_pubkey(self, pub):
        return self.key if self.key.get_pubkey() == bytes(pub) else None

    def get_key_for_hash160(self, h):
        return self.key if hash160(self.key.get_pubkey()) == bytes(h) else None


chain = Blockchain(temp_datadir("reorg"))
pool = MemPool(chain)

step("1. 一条新链从创世块开始")
show(chain)
print(f"   创世块哈希：{uint256_to_hex(chain.genesis_index.hash)}")

step("2. 攻击者先挖到一笔钱，并等它成熟（需要 100 个确认）")
attacker = CKey.generate()
first = mine(chain, 1, key=attacker, quiet=True)[0]
mine(chain, 100, quiet=True)
show(chain)

step("3. 每个区块都用哈希指向前一个区块：改动任何历史区块，后面所有区块的哈希全都对不上")
p = chain.best_index
for _ in range(3):
    print(f"   #{p.n_height} 哈希 {uint256_to_hex(p.hash)[:16]}…  它的“前一块”字段 = {uint256_to_hex(p.hash_prev_block)[:16]}…")
    p = p.pprev

step("4. 攻击者付 50 BTC 给商家；交易被打包进区块 A1，商家看到“1 个确认”就发货了")
merchant = CKey.generate()
coinbase = first.vtx[0]
pay = CTransaction(vin=[CTxIn(COutPoint(coinbase.get_hash(), 0))],
                   vout=[CTxOut(50 * params.COIN,
                                script_pubkey_for_hash160(hash160(merchant.get_pubkey())))])
assert sign_signature(KeyRing(attacker), coinbase, pay, 0)
fork_point = chain.best_index
a1 = mine(chain, 1, txs=[pay], quiet=True)[0]
show(chain, "<- A1 里有付给商家的交易")
print(f"   付款交易的确认数：{chain.get_tx_depth(pay.get_hash())}")

step("5. 与此同时，攻击者偷偷从 A1 之前的位置另挖一条链（B 链），里面没有那笔付款")
b = mine(chain, 1, prev=fork_point, quiet=True)
show(chain, "<- B1 和 A1 一样高：先到先得，主链不变")
print(f"   此刻索引里一共记着 {len(chain.map_block_index)} 个区块，其中 B1 在“支链”上")

step("6. 攻击者的 B 链又长了一块：B 链更高了，所有节点自动切换过去（链重组）")
mine(chain, 1, prev=chain.map_block_index[b[0].get_hash()], quiet=True)
show(chain, "<- 现在主链是 B1、B2")
print(f"   付款交易的确认数：{chain.get_tx_depth(pay.get_hash())}  —— 商家收到的钱“消失”了")
print(f"   那笔交易被放回了内存池，等待重新打包：{pay.get_hash() in pool}")

step("7. 攻击者在 B 链上把同一笔钱付给自己——商家那笔就永远无效了（双花成功）")
steal = CTransaction(vin=[CTxIn(COutPoint(coinbase.get_hash(), 0))],
                     vout=[CTxOut(50 * params.COIN,
                                  script_pubkey_for_hash160(hash160(attacker.get_pubkey())))])
assert sign_signature(KeyRing(attacker), coinbase, steal, 0)
mine(chain, 1, txs=[steal], quiet=True)
print(f"   攻击者付给自己的交易确认数：{chain.get_tx_depth(steal.get_hash())}")
ok = chain.connect_inputs(pay, {}, chain.best_height + 1)
print(f"   商家那笔交易现在还能被打包吗？{ok is not None}（它要花的那笔钱已经被花掉了）")
print("   教训：只有 1 个确认的交易，可能被一条更长的链整个抹掉。")

step("8. 那要等几个确认才安全？——白皮书第 11 节的计算")
print("   假设攻击者掌握全网算力的比例是 q，商家等了 z 个确认。攻击者从落后 z 块开始追，")
print("   最终能追上诚实链的概率是：")


def attacker_success(q: float, z: int) -> float:
    p = 1 - q
    lam = z * q / p                         # 诚实链挖 z 块期间，攻击者期望挖出的块数
    total = 1.0
    for k in range(z + 1):
        poisson = math.exp(-lam) * lam**k / math.factorial(k)
        total -= poisson * (1 - (q / p) ** (z - k))
    return max(total, 0.0)                  # 浮点误差可能算出 -0.0000000001 这样的数


print(f"\n   {'确认数 z':<10}" + "".join(f"q={q:<12.0%}" for q in (0.10, 0.30, 0.45)))
for z in (0, 1, 2, 3, 4, 5, 6, 10, 20, 50):
    print(f"   {z:<12}" + "".join(f"{attacker_success(q, z):<14.7f}" for q in (0.10, 0.30, 0.45)))
print("\n   q=10% 这一列和白皮书里的表格完全一致（z=5 时 0.0009137）。")
print("   读法：攻击者只有 10% 算力时，等 6 个确认，被双花的概率约万分之二；")
print("   但如果攻击者有 45% 算力，等 50 个确认仍有约 28% 的概率被逆转（白皮书算过：要降到千分之一")
print("   以下得等 340 个确认）；算力超过 50% 则迟早必然成功——这就是所谓的“51% 攻击”。")
print("   “6 个确认”只是一个经验值，它假设没有人控制接近一半的算力。")
chain.close()
print("\n实验 9 完成。")
