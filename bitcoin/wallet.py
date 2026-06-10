"""The wallet half of main.cpp / db.cpp.

v0.1.5 semantics kept on purpose:

* no key pool — a brand-new key is generated for every change output and
  every generated block;
* CWalletTx has a single fSpent flag for the whole transaction (per-output
  tracking arrived later), so the wallet always spends outputs whole and
  sends itself change;
* generated coins wait COINBASE_MATURITY+20 (=120) blocks in the wallet
  even though consensus only demands 100;
* zero-conf receives count toward the balance, exactly like GetBalance()
  did in 2009.
"""

import threading

from . import base58, params, util
from .db import WalletFile
from .hashes import hash160
from .key import CKey
from .script import (
    SIGHASH_ALL,
    TX_PUBKEY,
    TX_PUBKEYHASH,
    script_pubkey_for_hash160,
    sign_signature,
    solver,
)
from .serialize import DataStream, uint256_to_hex
from .tx import COutPoint, CTransaction, CTxIn, CTxOut


class WalletTx:
    """CWalletTx — a transaction plus wallet-side metadata."""

    def __init__(self, tx: CTransaction, time_received: int,
                 f_from_me: bool = False, f_spent: bool = False):
        self.tx = tx
        self.time_received = time_received
        self.f_from_me = f_from_me
        self.f_spent = f_spent

    def to_json(self) -> dict:
        return {
            "tx": self.tx.serialized().hex(),
            "time": self.time_received,
            "from_me": self.f_from_me,
            "spent": self.f_spent,
        }

    @classmethod
    def from_json(cls, d: dict) -> "WalletTx":
        tx = CTransaction.deserialize(DataStream(bytes.fromhex(d["tx"])))
        return cls(tx, d["time"], d["from_me"], d["spent"])


class Wallet:
    def __init__(self, datadir: str, chain, mempool, logger=None):
        self.lock = threading.RLock()
        self.log = logger or util.setup_logging(None)
        self.chain = chain
        self.mempool = mempool
        self.file = WalletFile(datadir)

        self.keys: dict[bytes, CKey] = {}          # pubkey -> key
        self.keys_by_h160: dict[bytes, CKey] = {}
        self.default_key: CKey | None = None
        self.map_wallet: dict[int, WalletTx] = {}  # txhash -> WalletTx
        self.address_book: dict[str, str] = {}     # address -> label
        self.settings: dict = {"fee": 0}           # nTransactionFee
        self.changed_listeners = []                # f() — UI refresh hook

        self._load()
        chain.tx_listeners.append(self._on_chain_tx)
        mempool.listeners.append(self._on_mempool_tx)

    # ------------------------------------------------------------ persistence
    def _load(self):
        data = self.file.load()
        for sec_hex in data.get("keys", []):
            self._register_key(CKey.from_secret(bytes.fromhex(sec_hex)))
        default_pub = data.get("defaultkey")
        if default_pub and bytes.fromhex(default_pub) in self.keys:
            self.default_key = self.keys[bytes.fromhex(default_pub)]
        elif self.keys:
            self.default_key = next(iter(self.keys.values()))
        else:
            self.default_key = self.generate_new_key(save=False)
        self.address_book = data.get("address_book", {})
        self.settings.update(data.get("settings", {}))
        for d in data.get("txs", []):
            wtx = WalletTx.from_json(d)
            self.map_wallet[wtx.tx.get_hash()] = wtx
        if not data:
            self.save()

    def save(self):
        with self.lock:
            self.file.save({
                "keys": [k.get_secret().hex() for k in self.keys.values()],
                "defaultkey": self.default_key.get_pubkey().hex(),
                "address_book": self.address_book,
                "settings": self.settings,
                "txs": [w.to_json() for w in self.map_wallet.values()],
            })

    # ------------------------------------------------------------------ keys
    def _register_key(self, key: CKey) -> CKey:
        pub = key.get_pubkey()
        self.keys[pub] = key
        self.keys_by_h160[hash160(pub)] = key
        return key

    def generate_new_key(self, save: bool = True) -> CKey:
        """GenerateNewKey — fresh key per use, the 0.1.x way."""
        with self.lock:
            key = self._register_key(CKey.generate())
            if save:
                self.save()
            return key

    def get_default_address(self) -> str:
        return base58.pubkey_to_address(self.default_key.get_pubkey())

    def set_new_default_key(self):
        with self.lock:
            self.default_key = self.generate_new_key()
            self.save()

    # KeyStore protocol for sign_signature
    def get_key_for_pubkey(self, pubkey: bytes) -> CKey | None:
        return self.keys.get(bytes(pubkey))

    def get_key_for_hash160(self, h160: bytes) -> CKey | None:
        return self.keys_by_h160.get(bytes(h160))

    # -------------------------------------------------------------- ownership
    def is_mine_txout(self, txout: CTxOut) -> bool:
        match = solver(txout.script_pubkey)
        if match is None:
            return False
        kind, data = match
        if kind == TX_PUBKEY:
            return bytes(data) in self.keys
        if kind == TX_PUBKEYHASH:
            return bytes(data) in self.keys_by_h160
        return False

    def get_credit(self, tx: CTransaction) -> int:
        return sum(o.n_value for o in tx.vout if self.is_mine_txout(o))

    def get_debit(self, tx: CTransaction) -> int:
        """Value of inputs that spend our own wallet transactions."""
        total = 0
        for txin in tx.vin:
            prev = self.map_wallet.get(txin.prevout.hash)
            if prev is not None and txin.prevout.n < len(prev.tx.vout):
                txout = prev.tx.vout[txin.prevout.n]
                if self.is_mine_txout(txout):
                    total += txout.n_value
        return total

    def is_mine(self, tx: CTransaction) -> bool:
        return self.get_credit(tx) > 0 or self.get_debit(tx) > 0

    # ------------------------------------------------------------- tx intake
    def add_to_wallet_if_mine(self, tx: CTransaction, f_from_me: bool = False):
        """AddToWalletIfMine."""
        with self.lock:
            if not self.is_mine(tx):
                return
            h = tx.get_hash()
            if h not in self.map_wallet:
                self.map_wallet[h] = WalletTx(tx, util.get_time(), f_from_me)
                # receiving spends nothing of ours, but if any input is ours
                # this tx marks those wallet txs spent (e.g. loaded from net)
                for txin in tx.vin:
                    prev = self.map_wallet.get(txin.prevout.hash)
                    if (prev is not None
                            and txin.prevout.n < len(prev.tx.vout)
                            and self.is_mine_txout(prev.tx.vout[txin.prevout.n])):
                        prev.f_spent = True
                self.save()
            self._notify()

    def _on_chain_tx(self, tx, pindex, f_connect):
        if f_connect:
            self.add_to_wallet_if_mine(tx)
        elif tx.get_hash() in self.map_wallet:
            self._notify()  # depth changed (reorg) — UI refresh only

    def _on_mempool_tx(self, tx):
        self.add_to_wallet_if_mine(tx)

    # --------------------------------------------------------------- balance
    def get_depth(self, wtx: WalletTx) -> int:
        return self.chain.get_tx_depth(wtx.tx.get_hash())

    def blocks_to_maturity(self, wtx: WalletTx) -> int:
        """GetBlocksToMaturity — the original's extra +20 margin included."""
        if not wtx.tx.is_coinbase():
            return 0
        return max(0, (params.COINBASE_MATURITY + 20) - self.get_depth(wtx))

    def is_tx_available(self, wtx: WalletTx) -> bool:
        # blocks_to_maturity also rules out orphaned generation (depth 0)
        return not wtx.f_spent and self.blocks_to_maturity(wtx) == 0

    def get_balance(self) -> int:
        """GetBalance — unspent credit, 0-conf included like the original."""
        with self.lock:
            return sum(self.get_credit(w.tx) for w in self.map_wallet.values()
                       if self.is_tx_available(w))

    # ---------------------------------------------------------------- sending
    def select_coins(self, target: int):
        """SelectCoins, simplified: smallest sufficient single coin, else
        accumulate smallest-first. Returns a list of WalletTx or None."""
        with self.lock:
            candidates = sorted(
                (w for w in self.map_wallet.values() if self.is_tx_available(w)
                 and self.get_credit(w.tx) > 0),
                key=lambda w: self.get_credit(w.tx),
            )
            for w in candidates:
                if self.get_credit(w.tx) >= target:
                    return [w]
            picked, total = [], 0
            for w in candidates:
                picked.append(w)
                total += self.get_credit(w.tx)
                if total >= target:
                    return picked
            return None

    def create_transaction(self, script_pubkey: bytes, value: int):
        """CreateTransaction — returns (WalletTx, fee) or (None, error_str)."""
        if value < 0:
            return None, "Amount must be positive"
        with self.lock, self.chain.lock:
            fee = int(self.settings.get("fee", 0))
            while True:
                tx = CTransaction()
                total_needed = value + fee
                coins = self.select_coins(total_needed)
                if coins is None:
                    return None, "Insufficient funds"
                value_in = sum(self.get_credit(w.tx) for w in coins)

                tx.vout = [CTxOut(value, script_pubkey)]
                change = value_in - total_needed
                if change > 0:
                    # new key for the change, no key pool in 0.1.x
                    change_key = self.generate_new_key(save=False)
                    tx.vout.append(CTxOut(
                        change,
                        script_pubkey_for_hash160(hash160(change_key.get_pubkey())),
                    ))

                # fill and sign the inputs (whole wallet txs, every owned output)
                for w in coins:
                    for n, txout in enumerate(w.tx.vout):
                        if self.is_mine_txout(txout):
                            tx.vin.append(CTxIn(COutPoint(w.tx.get_hash(), n)))
                prevs = {w.tx.get_hash(): w.tx for w in coins}
                for n_in, txin in enumerate(tx.vin):
                    if not sign_signature(self, prevs[txin.prevout.hash],
                                          tx, n_in, SIGHASH_ALL):
                        return None, "Signing transaction failed"

                min_fee = tx.get_min_fee(f_discount=True)
                if fee < min_fee:
                    fee = min_fee
                    continue
                return WalletTx(tx, util.get_time(), f_from_me=True), fee

    def commit_transaction(self, wtx: WalletTx) -> bool:
        """CommitTransaction — mark inputs spent, pool it, save."""
        with self.lock, self.chain.lock:
            for txin in wtx.tx.vin:
                prev = self.map_wallet.get(txin.prevout.hash)
                if prev is not None:
                    prev.f_spent = True
            self.map_wallet[wtx.tx.get_hash()] = wtx
            if not self.mempool.accept(wtx.tx):
                self.log.debug("CommitTransaction: AcceptTransaction failed")
                return False
            self.save()
            self._notify()
            return True

    def send_money(self, address: str, value: int):
        """SendMoney — returns (True, txhash_hex) or (False, error)."""
        if not base58.is_valid_address(address):
            return False, "Invalid bitcoin address"
        if value < params.CENT:
            return False, "Send amount too small"
        if value + int(self.settings.get("fee", 0)) > self.get_balance():
            return False, "Insufficient funds"
        script_pubkey = script_pubkey_for_hash160(base58.address_to_hash160(address))
        wtx, fee_or_err = self.create_transaction(bytes(script_pubkey), value)
        if wtx is None:
            return False, fee_or_err
        if not self.commit_transaction(wtx):
            return False, ("The transaction was rejected. This might happen if "
                           "some of your coins were already spent.")
        return True, uint256_to_hex(wtx.tx.get_hash())

    def rescan(self):
        """ScanForWalletTransactions — walk the whole main chain."""
        with self.lock, self.chain.lock:
            for pindex in self.chain.main_chain:
                for tx in self.chain.load_block(pindex).vtx:
                    self.add_to_wallet_if_mine(tx)

    # ------------------------------------------------------------------ misc
    def transactions_newest_first(self) -> list[WalletTx]:
        with self.lock:
            return sorted(self.map_wallet.values(),
                          key=lambda w: w.time_received, reverse=True)

    def set_address_label(self, address: str, label: str):
        with self.lock:
            self.address_book[address] = label
            self.save()
            self._notify()

    def _notify(self):
        for f in self.changed_listeners:
            f()
