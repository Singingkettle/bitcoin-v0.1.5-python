"""内存池——对应原版 main.cpp 里的 mapTransactions / mapNextTx 和 AcceptTransaction。

内存池 = "已经验证通过、但还没被打包进区块"的交易的候车室。
矿工从这里挑交易装进新区块；节点之间互相转发的也是这里面的交易。

两张表：
- map_transactions: 交易哈希 -> 交易
- map_next_tx:      (前序交易哈希, 输出序号) -> 花费它的那笔池内交易的哈希
                    （用来一眼看出"这笔钱是不是已经被池里的某笔交易花了"）

注意：v0.1.5 的内存池**不检查手续费**，手续费门槛是矿工打包时才执行的（见 miner.py）。
"""

from . import params
from .script import verify_signature
from .serialize import uint256_from_hex
from .tx import CTransaction


class MemPool:
    def __init__(self, chain):
        self.chain = chain
        # 直接共用区块链的那把大锁（原版里两者都受 cs_main 保护）。
        # 共用一把锁就不存在"先拿谁后拿谁"的问题，从根上杜绝死锁。
        self.lock = chain.lock
        self.map_transactions: dict[int, CTransaction] = {}
        self.map_next_tx: dict[tuple[int, int], int] = {}
        self.listeners = []             # f(tx)：有新交易进池——钱包、网络层订阅
        self.replaced_listeners = []    # f(旧交易)：旧交易被新版本替换掉了——钱包订阅
        chain.mempool = self

    def __contains__(self, txhash: int) -> bool:
        return txhash in self.map_transactions

    def __len__(self):
        return len(self.map_transactions)

    def get(self, txhash: int) -> CTransaction | None:
        return self.map_transactions.get(txhash)

    def accept(self, tx: CTransaction, check_inputs: bool = True) -> bool:
        """AcceptTransaction：一笔交易想进内存池要过的关。

        check_inputs=False 只在链重组"复活"交易时使用：那些交易以前已经完整验证过了。
        """
        with self.lock:
            if tx.is_coinbase():
                return self._err("AcceptTransaction: coinbase 只能出现在区块里，不能单独传播")
            if not tx.check_transaction():
                return self._err("AcceptTransaction: CheckTransaction 失败")

            h = tx.get_hash()
            if h in self.map_transactions:
                return False                            # 池里已经有了
            if check_inputs and self.chain.contains_tx(h):
                return False                            # 已经在区块里了

            # 和池里的交易有没有冲突（花了同一笔钱）？
            old_tx = None
            for i, txin in enumerate(tx.vin):
                key = (txin.prevout.hash, txin.prevout.n)
                if key in self.map_next_tx:
                    # 唯一允许的"冲突"：它是池里某笔交易的更新版本
                    # （花完全相同的输入、序列号更高；见 CTransaction.is_newer_than）
                    if i != 0:
                        return False
                    old_hash = self.map_next_tx[key]
                    old_tx = self.map_transactions[old_hash]
                    if not tx.is_newer_than(old_tx):
                        return self._err("AcceptTransaction: 与池内交易冲突（双花）")
                    for txin2 in tx.vin:
                        key2 = (txin2.prevout.hash, txin2.prevout.n)
                        if self.map_next_tx.get(key2) != old_hash:
                            return False
                    break

            if check_inputs and not self._check_inputs(tx):
                return False

            if old_tx is not None:
                self.log_debug("内存池：交易被新版本替换")
                self.remove(old_tx)
            self.map_transactions[h] = tx
            for txin in tx.vin:
                self.map_next_tx[(txin.prevout.hash, txin.prevout.n)] = h

        # 通知订阅者。回调里钱包会去拿 wallet.lock；此刻本线程要么没持锁，
        # 要么只持有 chain.lock——符合"先 chain 后 wallet"的加锁顺序，不会死锁。
        if old_tx is not None:
            for f in self.replaced_listeners:
                f(old_tx)
        for f in self.listeners:
            f(tx)
        return True

    def _check_inputs(self, tx: CTransaction) -> bool:
        """对应原版以"非区块、非矿工"模式调用的 ConnectInputs。"""
        value_in = 0
        for n_in, txin in enumerate(tx.vin):
            found = self.chain.load_tx(txin.prevout.hash)
            if found is not None:
                # 前序交易已经在区块里
                prev, rec = found
                if txin.prevout.n >= len(prev.vout):
                    return self._err("AcceptTransaction: prevout.n 越界")
                if prev.is_coinbase():
                    pindex = self.chain.map_block_index.get(
                        uint256_from_hex(rec["blockhash"]))
                    # 下一个区块的高度是 best_height+1，那时必须满 100 个确认
                    if (pindex is None or self.chain.best_height + 1
                            - pindex.n_height < params.COINBASE_MATURITY):
                        return self._err("AcceptTransaction: coinbase 尚未成熟")
                spent = rec["spent"][txin.prevout.n]
            else:
                # 前序交易也还在池里（花一笔尚未确认的钱）
                prev = self.map_transactions.get(txin.prevout.hash)
                if prev is None:
                    return self._err("AcceptTransaction: 找不到输入引用的交易")
                if txin.prevout.n >= len(prev.vout):
                    return self._err("AcceptTransaction: prevout.n 越界")
                spent = None
            if not verify_signature(prev, tx, n_in):
                return self._err("AcceptTransaction: 签名验证失败")
            if spent is not None:
                return self._err("AcceptTransaction: 输入已经被花过了")
            value_in += prev.vout[txin.prevout.n].n_value

        if value_in - tx.get_value_out() < 0:
            return self._err("AcceptTransaction: 输出总额大于输入总额")
        return True

    def remove(self, tx: CTransaction):
        """RemoveFromMemoryPool。"""
        with self.lock:
            h = tx.get_hash()
            for txin in tx.vin:
                key = (txin.prevout.hash, txin.prevout.n)
                if self.map_next_tx.get(key) == h:
                    del self.map_next_tx[key]
            self.map_transactions.pop(h, None)

    def transactions(self) -> list[CTransaction]:
        with self.lock:
            return list(self.map_transactions.values())

    def log_debug(self, msg: str):
        self.chain.log.debug(msg)

    def _err(self, msg: str) -> bool:
        self.chain.log.debug("ERROR: %s", msg)
        return False
