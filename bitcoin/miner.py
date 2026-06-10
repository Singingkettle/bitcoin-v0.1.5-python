"""BitcoinMiner / CreateNewBlock from main.cpp.

A plain Python thread scanning nonces — viable because the private net's
difficulty (0x1f00ffff) needs ~65k double-SHA256 per block and hashlib
manages a few hundred thousand per second.
"""

import itertools
import threading

from . import params, util
from .block import CBlock
from .script import CScript
from .tx import COutPoint, CTransaction, CTxIn, CTxOut
from .script import script_pubkey_for_pubkey

_extra_nonce = itertools.count(1)


class Miner:
    def __init__(self, chain, mempool, wallet, logger=None):
        self.chain = chain
        self.mempool = mempool
        self.wallet = wallet
        self.log = logger or util.setup_logging(None)
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.blocks_found = 0

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._mine_loop,
                                       name="ThreadBitcoinMiner", daemon=True)
        self.thread.start()
        self.log.debug("BitcoinMiner started")

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.log.debug("BitcoinMiner stopped")

    def create_new_block(self) -> tuple[CBlock, int]:
        """CreateNewBlock — coinbase to a fresh key + paying mempool txs."""
        with self.chain.lock:
            prev = self.chain.best_index
            height = prev.n_height + 1
            n_bits = self.chain.get_next_work_required(prev)

            # collect mempool transactions, validating against a test pool so
            # in-block double-spends are impossible
            test_pool: dict = {}
            txs, fees = [], 0
            for tx in self.mempool.transactions():
                fee = self.chain.connect_inputs(tx, test_pool, height)
                if fee is None:
                    continue
                txs.append(tx)
                fees += fee

            key = self.wallet.generate_new_key()
            script_sig = CScript().push_int(n_bits).push_int(next(_extra_nonce))
            coinbase = CTransaction(
                vin=[CTxIn(COutPoint(), script_sig)],
                vout=[CTxOut(params.block_value(height, fees),
                             script_pubkey_for_pubkey(key.get_pubkey()))],
            )
            block = CBlock(
                n_version=1,
                hash_prev_block=prev.hash,
                n_time=max(util.get_adjusted_time(),
                           prev.get_median_time_past() + 1),
                n_bits=n_bits,
                vtx=[coinbase] + txs,
            )
            block.hash_merkle_root = block.get_merkle_root()
            return block, prev.hash

    def _mine_loop(self):
        while not self.stop_event.is_set():
            block, prev_hash = self.create_new_block()
            target = params.compact_to_target(block.n_bits)
            nonce = 0
            solved = False
            while not self.stop_event.is_set():
                block.n_nonce = nonce
                if block.get_hash() <= target:
                    solved = True
                    break
                nonce += 1
                if nonce % 20000 == 0:
                    # refresh: new best block or new mempool txs -> rebuild
                    if self.chain.best_index.hash != prev_hash:
                        break
                    block.n_time = max(block.n_time, util.get_adjusted_time())
            if solved:
                self.blocks_found += 1
                self.log.debug("BitcoinMiner: proof-of-work found, nonce=%d", nonce)
                self.chain.process_block(block)
