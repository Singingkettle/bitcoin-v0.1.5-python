"""main.cpp's mapTransactions / mapNextTx — the memory pool.

AcceptTransaction's checks from v0.1.5: context-free CheckTransaction,
no coinbases, no transactions we already have (pool or chain), no
conflicts with in-pool spends, inputs must exist / be unspent / be
mature / verify, and the fee must cover GetMinFee(fDiscount=True).
"""

import threading

from . import params
from .script import verify_signature
from .serialize import uint256_from_hex, uint256_to_hex
from .tx import CTransaction


class MemPool:
    def __init__(self, chain):
        self.chain = chain
        self.lock = threading.RLock()
        self.map_transactions: dict[int, CTransaction] = {}
        self.map_next_tx: dict[tuple[int, int], int] = {}  # prevout -> spender hash
        self.listeners = []  # f(tx) on every accepted transaction — wallet/net
        chain.mempool = self

    def __contains__(self, txhash: int) -> bool:
        return txhash in self.map_transactions

    def __len__(self):
        return len(self.map_transactions)

    def get(self, txhash: int) -> CTransaction | None:
        return self.map_transactions.get(txhash)

    def accept(self, tx: CTransaction, check_inputs: bool = True) -> bool:
        """AcceptTransaction."""
        with self.lock, self.chain.lock:
            if not tx.check_transaction():
                return self._err("AcceptTransaction: CheckTransaction failed")
            if tx.is_coinbase():
                return self._err("AcceptTransaction: coinbase as individual tx")
            h = tx.get_hash()
            if h in self.map_transactions:
                return False
            if self.chain.db.read_tx_index(uint256_to_hex(h)) is not None:
                return False  # already in a block

            # check for conflicts with in-pool transactions
            for txin in tx.vin:
                key = (txin.prevout.hash, txin.prevout.n)
                if key in self.map_next_tx:
                    return self._err("AcceptTransaction: conflicting in-pool spend")

            if check_inputs and not self._check_inputs(tx):
                return False

            self.map_transactions[h] = tx
            for txin in tx.vin:
                self.map_next_tx[(txin.prevout.hash, txin.prevout.n)] = h
            for f in self.listeners:
                f(tx)
            return True

    def _check_inputs(self, tx: CTransaction) -> bool:
        value_in = 0
        for n_in, txin in enumerate(tx.vin):
            prev = self.map_transactions.get(txin.prevout.hash)
            if prev is not None:
                # spending an unconfirmed pool transaction
                if txin.prevout.n >= len(prev.vout):
                    return self._err("AcceptTransaction: prevout.n out of range")
            else:
                found = self.chain.load_tx(txin.prevout.hash)
                if found is None:
                    return self._err("AcceptTransaction: missing inputs")
                prev, rec = found
                if txin.prevout.n >= len(prev.vout):
                    return self._err("AcceptTransaction: prevout.n out of range")
                if rec["spent"][txin.prevout.n] is not None:
                    return self._err("AcceptTransaction: input already spent")
                if prev.is_coinbase():
                    pindex = self.chain.map_block_index.get(
                        uint256_from_hex(rec["blockhash"]))
                    if (pindex is None or self.chain.best_height + 1
                            - pindex.n_height < params.COINBASE_MATURITY):
                        return self._err("AcceptTransaction: immature coinbase spend")
            if not verify_signature(prev, tx, n_in):
                return self._err("AcceptTransaction: VerifySignature failed")
            value_in += prev.vout[txin.prevout.n].n_value

        fee = value_in - tx.get_value_out()
        if fee < 0:
            return self._err("AcceptTransaction: value in < value out")
        if fee < tx.get_min_fee(f_discount=True):
            return self._err("AcceptTransaction: not enough fees")
        return True

    def remove(self, tx: CTransaction):
        """RemoveFromMemoryPool."""
        with self.lock:
            h = tx.get_hash()
            for txin in tx.vin:
                self.map_next_tx.pop((txin.prevout.hash, txin.prevout.n), None)
            self.map_transactions.pop(h, None)

    def transactions(self) -> list[CTransaction]:
        with self.lock:
            return list(self.map_transactions.values())

    def _err(self, msg: str) -> bool:
        self.chain.log.debug("ERROR: %s", msg)
        return False
