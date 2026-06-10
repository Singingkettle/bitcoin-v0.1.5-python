"""main.cpp consensus half: ProcessBlock / CheckBlock / AcceptBlock /
ConnectBlock / DisconnectBlock / Reorganize, the orphan-block maps and
GetNextWorkRequired.

A Blockchain instance owns what were the globals mapBlockIndex,
pindexBest, hashBestChain and mapOrphanBlocks, so tests can run several
independent chains in one process. self.lock stands in for cs_main.
"""

import threading
from collections import defaultdict

from . import params, util
from .block import CBlock, CBlockIndex, CBlockLocator
from .db import BlockFile, BlockIndexDB
from .script import CScript, OP_CHECKSIG, bn_serialize, verify_signature
from .serialize import uint256_from_hex, uint256_to_hex
from .tx import COutPoint, CTransaction, CTxIn, CTxOut


def build_genesis_block(n_time: int | None = None, n_nonce: int | None = None) -> CBlock:
    """The private-net genesis, built exactly the way main.cpp builds the
    mainnet one (CBigNum(4) data push and all)."""
    script_sig = (CScript().push_int(486604799)
                  .push_data(bn_serialize(4))
                  .push_data(params.GENESIS_TIMESTAMP_TEXT))
    script_pubkey = (CScript().push_data(params.GENESIS_PUBKEY)
                     .push_opcode(OP_CHECKSIG))
    txnew = CTransaction(
        vin=[CTxIn(COutPoint(), script_sig)],
        vout=[CTxOut(50 * params.COIN, script_pubkey)],
    )
    block = CBlock(
        n_version=params.GENESIS_VERSION,
        hash_prev_block=0,
        n_time=params.GENESIS_TIME if n_time is None else n_time,
        n_bits=params.GENESIS_BITS,
        n_nonce=params.GENESIS_NONCE if n_nonce is None else n_nonce,
        vtx=[txnew],
    )
    block.hash_merkle_root = block.get_merkle_root()
    return block


class Blockchain:
    def __init__(self, datadir: str, logger=None):
        self.lock = threading.RLock()          # cs_main
        self.log = logger or util.setup_logging(None)
        self.block_file = BlockFile(datadir)
        self.db = BlockIndexDB(datadir)

        self.map_block_index: dict[int, CBlockIndex] = {}
        self.map_orphan_blocks: dict[int, CBlock] = {}
        self.map_orphan_blocks_by_prev: dict[int, list[CBlock]] = defaultdict(list)

        self.genesis_index: CBlockIndex | None = None
        self.best_index: CBlockIndex | None = None
        self.main_chain: list[CBlockIndex] = []   # height -> index, best chain

        self.mempool = None       # wired up by the node (needed by Reorganize)
        self.tx_listeners = []    # f(tx, pindex_or_None, f_connect) — wallet
        self.block_listeners = [] # f(pindex) on every new best block — net/ui

        self._load_or_create()

    # ------------------------------------------------------------------ load
    def _load_or_create(self):
        rows = self.db.load_block_index()
        if not rows:
            genesis = build_genesis_block()
            ghash = genesis.get_hash()
            if params.GENESIS_HASH is not None:
                assert uint256_to_hex(ghash) == params.GENESIS_HASH, (
                    "params.py genesis constants do not match - "
                    "run tools/mine_genesis.py"
                )
            assert genesis.check_proof_of_work(), "genesis fails PoW - run tools/mine_genesis.py"
            pos = self.block_file.append(genesis.serialized())
            pindex = CBlockIndex(genesis, ghash)
            pindex.n_file_pos = pos
            self.map_block_index[ghash] = pindex
            self.genesis_index = self.best_index = pindex
            self.main_chain = [pindex]
            self.db.write_block_index(uint256_to_hex(ghash), uint256_to_hex(0), 0, pos)
            self.db.write_best_chain(uint256_to_hex(ghash))
            self.db.commit()
            return

        # rebuild mapBlockIndex from disk (rows are height-ordered, so
        # parents always come first)
        for hash_hex, prev_hex, height, pos in rows:
            block = self.block_file.read(pos)
            h = uint256_from_hex(hash_hex)
            pindex = CBlockIndex(block, h)
            pindex.n_file_pos = pos
            pindex.n_height = height
            prev = uint256_from_hex(prev_hex)
            if prev:
                pindex.pprev = self.map_block_index.get(prev)
            self.map_block_index[h] = pindex
            if height == 0:
                self.genesis_index = pindex

        best_hex = self.db.read_best_chain()
        self.best_index = self.map_block_index[uint256_from_hex(best_hex)]
        # rebuild pnext / main_chain along the best chain
        chain = []
        pindex = self.best_index
        while pindex is not None:
            chain.append(pindex)
            pindex = pindex.pprev
        chain.reverse()
        for a, b in zip(chain, chain[1:]):
            a.pnext = b
        self.main_chain = chain

    # ----------------------------------------------------------- chain state
    @property
    def best_height(self) -> int:
        return self.best_index.n_height if self.best_index else -1

    def is_in_main_chain(self, pindex: CBlockIndex) -> bool:
        return (pindex.n_height < len(self.main_chain)
                and self.main_chain[pindex.n_height] is pindex)

    def get_locator(self, pindex: CBlockIndex | None = None) -> CBlockLocator:
        pindex = pindex or self.best_index
        return CBlockLocator.from_index(pindex, self.genesis_index.hash)

    def find_fork_by_locator(self, locator: CBlockLocator) -> CBlockIndex:
        """First main-chain block the locator names (CBlockLocator::GetBlockIndex)."""
        for h in locator.v_have:
            pindex = self.map_block_index.get(h)
            if pindex is not None and self.is_in_main_chain(pindex):
                return pindex
        return self.genesis_index

    def load_block(self, pindex: CBlockIndex) -> CBlock:
        return self.block_file.read(pindex.n_file_pos)

    def load_tx(self, txhash: int):
        """Look a transaction up via the tx index. Returns (tx, txindex) or None."""
        rec = self.db.read_tx_index(uint256_to_hex(txhash))
        if rec is None:
            return None
        pindex = self.map_block_index.get(uint256_from_hex(rec["blockhash"]))
        if pindex is None:
            return None
        block = self.load_block(pindex)
        return block.vtx[rec["txn"]], rec

    def get_tx_depth(self, txhash: int) -> int:
        """GetDepthInMainChain — 0 if not in a main-chain block."""
        with self.lock:
            rec = self.db.read_tx_index(uint256_to_hex(txhash))
            if rec is None:
                return 0
            pindex = self.map_block_index.get(uint256_from_hex(rec["blockhash"]))
            if pindex is None or not self.is_in_main_chain(pindex):
                return 0
            return self.best_height - pindex.n_height + 1

    # --------------------------------------------------------------- PoW
    def get_next_work_required(self, pindex_last: CBlockIndex | None) -> int:
        """GetNextWorkRequired, with the original nInterval-1 walk-back."""
        limit_compact = params.target_to_compact(params.PROOF_OF_WORK_LIMIT)
        if pindex_last is None:
            return limit_compact
        if (pindex_last.n_height + 1) % params.INTERVAL != 0:
            return pindex_last.n_bits

        pindex_first = pindex_last
        for _ in range(params.INTERVAL - 1):
            if pindex_first.pprev is None:
                break
            pindex_first = pindex_first.pprev

        actual_timespan = pindex_last.n_time - pindex_first.n_time
        actual_timespan = max(actual_timespan, params.TARGET_TIMESPAN // 4)
        actual_timespan = min(actual_timespan, params.TARGET_TIMESPAN * 4)

        new_target = (params.compact_to_target(pindex_last.n_bits)
                      * actual_timespan // params.TARGET_TIMESPAN)
        new_target = min(new_target, params.PROOF_OF_WORK_LIMIT)
        return params.target_to_compact(new_target)

    # --------------------------------------------------------- validation
    def check_block(self, block: CBlock) -> bool:
        """CheckBlock — context-free checks."""
        if not block.vtx:
            return self._error("CheckBlock: vtx empty")
        if len(block.serialized()) > params.MAX_SIZE:
            return self._error("CheckBlock: size limits failed")
        if not block.check_proof_of_work():
            return self._error("CheckBlock: proof of work failed")
        if block.n_time > util.get_adjusted_time() + 2 * 60 * 60:
            return self._error("CheckBlock: block timestamp too far in the future")
        if not block.vtx[0].is_coinbase():
            return self._error("CheckBlock: first tx is not coinbase")
        if any(tx.is_coinbase() for tx in block.vtx[1:]):
            return self._error("CheckBlock: more than one coinbase")
        for tx in block.vtx:
            if not tx.check_transaction():
                return self._error("CheckBlock: CheckTransaction failed")
        if block.hash_merkle_root != block.get_merkle_root():
            return self._error("CheckBlock: hashMerkleRoot mismatch")
        return True

    def process_block(self, block: CBlock) -> bool:
        """ProcessBlock. Returns True if the block was accepted or stored
        as an orphan (matching the original's return convention)."""
        with self.lock:
            h = block.get_hash()
            if h in self.map_block_index:
                return self._error(f"ProcessBlock: already have block {uint256_to_hex(h)[:16]}")
            if h in self.map_orphan_blocks:
                return self._error("ProcessBlock: already have block (orphan)")
            if not self.check_block(block):
                return False

            # If don't already have its previous block, shunt it off to
            # holding area until we get it
            if block.hash_prev_block not in self.map_block_index:
                self.log.debug("ProcessBlock: ORPHAN BLOCK, prev=%s",
                               uint256_to_hex(block.hash_prev_block)[:16])
                self.map_orphan_blocks[h] = block
                self.map_orphan_blocks_by_prev[block.hash_prev_block].append(block)
                return True

            if not self.accept_block(block):
                return self._error("ProcessBlock: AcceptBlock FAILED")

            # Recursively process any orphan blocks that depended on this one
            work_queue = [h]
            while work_queue:
                prev = work_queue.pop(0)
                for orphan in self.map_orphan_blocks_by_prev.pop(prev, []):
                    ohash = orphan.get_hash()
                    del self.map_orphan_blocks[ohash]
                    if self.accept_block(orphan):
                        work_queue.append(ohash)
            return True

    def get_orphan_root(self, block: CBlock) -> int:
        """GetOrphanRoot — walk orphans back to the oldest missing parent."""
        while block.hash_prev_block in self.map_orphan_blocks:
            block = self.map_orphan_blocks[block.hash_prev_block]
        return block.hash_prev_block

    def accept_block(self, block: CBlock) -> bool:
        """AcceptBlock — contextual checks, then write to disk and index."""
        prev = self.map_block_index.get(block.hash_prev_block)
        if prev is None:
            return self._error("AcceptBlock: prev block not found")
        height = prev.n_height + 1
        if block.n_bits != self.get_next_work_required(prev):
            return self._error("AcceptBlock: incorrect proof of work")
        if block.n_time <= prev.get_median_time_past():
            return self._error("AcceptBlock: block's timestamp is too early")

        pos = self.block_file.append(block.serialized())
        return self._add_to_block_index(block, pos, height, prev)

    def _add_to_block_index(self, block: CBlock, pos: int, height: int,
                            prev: CBlockIndex) -> bool:
        h = block.get_hash()
        pindex = CBlockIndex(block, h)
        pindex.n_file_pos = pos
        pindex.pprev = prev
        pindex.n_height = height
        self.map_block_index[h] = pindex
        self.db.write_block_index(uint256_to_hex(h),
                                  uint256_to_hex(block.hash_prev_block),
                                  height, pos)

        # new best chain? (0.1.5 compares plain height, not chain work)
        if height > self.best_height:
            if not self._set_best_chain(block, pindex):
                return False
        self.db.commit()
        return True

    # ------------------------------------------------------- best chain
    def _set_best_chain(self, block: CBlock, pindex: CBlockIndex) -> bool:
        if block.hash_prev_block == self.best_index.hash:
            if not self.connect_block(block, pindex):
                # invalid block: drop it from the in-memory index again
                del self.map_block_index[pindex.hash]
                return self._error("SetBestChain: ConnectBlock failed")
            self.best_index.pnext = pindex
            self.main_chain.append(pindex)
        else:
            if not self.reorganize(block, pindex):
                return self._error("SetBestChain: Reorganize failed")

        self.best_index = pindex
        self.db.write_best_chain(uint256_to_hex(pindex.hash))
        self.log.debug("SetBestChain: new best height=%d hash=%s",
                       pindex.n_height, uint256_to_hex(pindex.hash)[:20])
        for f in self.block_listeners:
            f(pindex)
        return True

    def connect_block(self, block: CBlock, pindex: CBlockIndex) -> bool:
        """ConnectBlock — spend inputs, write the tx index, check subsidy."""
        test_pool: dict[str, dict] = {}
        fees = 0
        for n, tx in enumerate(block.vtx):
            if not tx.is_coinbase():
                fee = self.connect_inputs(tx, test_pool, pindex.n_height)
                if fee is None:
                    return False
                fees += fee
            test_pool[uint256_to_hex(tx.get_hash())] = {
                "blockhash": uint256_to_hex(pindex.hash),
                "txn": n,
                "spent": [None] * len(tx.vout),
            }
        if block.vtx[0].get_value_out() > params.block_value(pindex.n_height, fees):
            return self._error("ConnectBlock: coinbase pays too much")

        for txhash_hex, rec in test_pool.items():
            self.db.write_tx_index(txhash_hex, rec["blockhash"], rec["txn"],
                                   rec["spent"])
        # delete redundant memory transactions
        if self.mempool is not None:
            for tx in block.vtx[1:]:
                self.mempool.remove(tx)
        for tx in block.vtx:
            for f in self.tx_listeners:
                f(tx, pindex, True)
        return True

    def connect_inputs(self, tx: CTransaction, test_pool: dict,
                       spend_height: int) -> int | None:
        """ConnectInputs — returns the fee, or None on any failure."""
        txhash_hex = uint256_to_hex(tx.get_hash())
        value_in = 0
        for n_in, txin in enumerate(tx.vin):
            prev_hex = uint256_to_hex(txin.prevout.hash)
            rec = test_pool.get(prev_hex) or self.db.read_tx_index(prev_hex)
            if rec is None:
                return self._error_none(f"ConnectInputs: prev tx not found {prev_hex[:16]}")
            prev_pindex = self.map_block_index.get(uint256_from_hex(rec["blockhash"]))
            if prev_pindex is None:
                return self._error_none("ConnectInputs: prev block index missing")
            prev_tx = self.load_block(prev_pindex).vtx[rec["txn"]]
            if txin.prevout.n >= len(prev_tx.vout):
                return self._error_none("ConnectInputs: prevout.n out of range")
            # If prev is coinbase, check that it's matured
            if prev_tx.is_coinbase():
                if spend_height - prev_pindex.n_height < params.COINBASE_MATURITY:
                    return self._error_none("ConnectInputs: tried to spend immature coinbase")
            if rec["spent"][txin.prevout.n] is not None:
                return self._error_none("ConnectInputs: prev tx already spent")
            if not verify_signature(prev_tx, tx, n_in):
                return self._error_none("ConnectInputs: VerifySignature failed")
            rec["spent"][txin.prevout.n] = txhash_hex
            test_pool[prev_hex] = rec
            value_in += prev_tx.vout[txin.prevout.n].n_value

        fee = value_in - tx.get_value_out()
        if fee < 0:
            return self._error_none("ConnectInputs: value in < value out")
        return fee

    def disconnect_block(self, block: CBlock) -> bool:
        """DisconnectBlock — un-spend inputs, drop the block's tx index."""
        for tx in reversed(block.vtx):
            for txin in tx.vin:
                if txin.prevout.is_null():
                    continue
                prev_hex = uint256_to_hex(txin.prevout.hash)
                rec = self.db.read_tx_index(prev_hex)
                if rec is not None:
                    rec["spent"][txin.prevout.n] = None
                    self.db.write_tx_index(prev_hex, rec["blockhash"],
                                           rec["txn"], rec["spent"])
            self.db.erase_tx_index(uint256_to_hex(tx.get_hash()))
        for tx in block.vtx:
            for f in self.tx_listeners:
                f(tx, None, False)
        return True

    def reorganize(self, block_new: CBlock, pindex_new: CBlockIndex) -> bool:
        """Reorganize — switch the best chain to the branch ending at pindex_new."""
        self.log.debug("REORGANIZE")
        # find the fork point
        fork = self.best_index
        longer = pindex_new
        while fork is not longer:
            if longer.n_height > fork.n_height:
                longer = longer.pprev
            else:
                fork = fork.pprev
            if fork is None or longer is None:
                return self._error("Reorganize: no fork point")

        # disconnect the old branch (best down to, not including, fork)
        resurrect: list[CTransaction] = []
        pindex = self.best_index
        while pindex is not fork:
            block = self.load_block(pindex)
            if not self.disconnect_block(block):
                return self._error("Reorganize: DisconnectBlock failed")
            resurrect.extend(t for t in block.vtx if not t.is_coinbase())
            pindex = pindex.pprev

        # connect the new branch (fork+1 up to pindex_new)
        branch = []
        pindex = pindex_new
        while pindex is not fork:
            branch.append(pindex)
            pindex = pindex.pprev
        branch.reverse()

        delete_from_mempool: list[CTransaction] = []
        for pindex in branch:
            block = block_new if pindex is pindex_new else self.load_block(pindex)
            if not self.connect_block(block, pindex):
                return self._error("Reorganize: ConnectBlock failed")
            delete_from_mempool.extend(t for t in block.vtx if not t.is_coinbase())

        # relink pnext / main_chain
        self.main_chain = self.main_chain[:fork.n_height + 1]
        fork.pnext = None
        for pindex in branch:
            self.main_chain[-1].pnext = pindex
            self.main_chain.append(pindex)
        pindex_new.pnext = None

        # resurrect disconnected transactions, evict newly-confirmed ones
        if self.mempool is not None:
            for tx in resurrect:
                self.mempool.accept(tx, check_inputs=False)
            for tx in delete_from_mempool:
                self.mempool.remove(tx)
        return True

    # ----------------------------------------------------------------- misc
    def close(self):
        with self.lock:
            self.db.commit()
            self.db.close()

    def _error(self, msg: str) -> bool:
        self.log.debug("ERROR: %s", msg)
        return False

    def _error_none(self, msg: str):
        self.log.debug("ERROR: %s", msg)
        return None
