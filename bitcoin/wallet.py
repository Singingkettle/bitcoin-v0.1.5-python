"""钱包——对应原版 main.cpp 里与钱包有关的那一半，以及 db.cpp 的 CWalletDB。

区块链管的是"什么交易合法"，钱包管的是"哪些钱是我的、我还剩多少、怎么花"。
钱包本身不保存"余额"这个数字——余额永远是现算出来的：
把钱包里所有"属于我、还没花掉"的交易输出加起来。

忠实保留的 v0.1.5 特征：

* 整笔标记：CWalletTx 只有一个 fSpent 标志，管的是**整笔交易**，而不是每个输出
  （按输出记账是后来的版本才有的）。所以花钱时总是把选中的钱包交易里"属于我的输出"
  一次全花掉，多出来的部分作为"找零"付回给自己。
* 找零付回给**原来那枚币的同一个公钥**（原版注释里写着 todo：为了隐私应该换新密钥）。
* 没有密钥池：需要新密钥时现场生成一把。
* 挖矿所得在钱包里要等 COINBASE_MATURITY+20 = 120 个块才算可用
  （共识规则只要求 100，钱包自己多留了 20 个块的安全余量）。
* 零确认的收款也计入余额（2009 年的 GetBalance 就是这么算的）。

【加锁顺序】需要同时用到链和钱包时，一律先拿 chain.lock 再拿 self.lock。
"""

import random
import threading

from . import base58, params, util
from .db import WalletDB
from .hashes import hash160
from .key import CKey
from .script import (
    SIGHASH_ALL,
    TX_PUBKEY,
    script_pubkey_for_hash160,
    script_pubkey_for_pubkey,
    sign_signature,
    solver,
)
from .serialize import DataStream, uint256_from_hex, uint256_to_hex
from .tx import COutPoint, CTransaction, CTxIn, CTxOut


class WalletTx:
    """CWalletTx：一笔交易 + 钱包给它附加的信息。"""

    def __init__(self, tx: CTransaction, time_received: int,
                 f_from_me: bool = False, f_spent: bool = False,
                 hash_block: int = 0):
        self.tx = tx
        self.time_received = time_received  # 钱包第一次见到它的时间
        self.f_from_me = f_from_me          # 是不是我自己发出的
        self.f_spent = f_spent              # 这笔交易里属于我的钱是否已被我花掉
        self.hash_block = hash_block        # 它被打包进了哪个区块（0 = 还没进块）

    def to_record(self) -> dict:
        return {
            "tx": self.tx.serialized().hex(),
            "time": self.time_received,
            "from_me": self.f_from_me,
            "spent": self.f_spent,
            "hash_block": uint256_to_hex(self.hash_block),
        }

    @classmethod
    def from_record(cls, d: dict) -> "WalletTx":
        tx = CTransaction.deserialize(DataStream(bytes.fromhex(d["tx"])))
        hash_block = uint256_from_hex(d["hash_block"]) if d.get("hash_block") else 0
        return cls(tx, d["time"], d["from_me"], d["spent"], hash_block)


class Wallet:
    def __init__(self, datadir: str, chain, mempool, logger=None):
        self.lock = threading.RLock()
        self.log = logger or util.setup_logging(None)
        self.chain = chain
        self.mempool = mempool
        self.db = WalletDB(datadir)

        self.keys: dict[bytes, CKey] = {}           # mapKeys：公钥 -> 密钥
        self.keys_by_h160: dict[bytes, CKey] = {}   # mapPubKeys：公钥哈希 -> 密钥
        self.default_key: CKey | None = None        # 界面上"你的地址"对应的密钥
        self.map_wallet: dict[int, WalletTx] = {}   # mapWallet：交易哈希 -> 钱包交易
        self.address_book: dict[str, str] = {}      # mapAddressBook：地址 -> 备注名
        self.transaction_fee = 0                    # nTransactionFee：用户自愿多付的手续费

        self.changed_listeners = []     # f()：钱包有变化——界面订阅
        self.relay_listeners = []       # f(tx)：请把这笔交易再广播一次——网络层订阅
        self._last_relay_time = 0

        self._load()
        chain.tx_listeners.append(self._on_chain_tx)
        mempool.listeners.append(self._on_mempool_tx)
        mempool.replaced_listeners.append(self.erase_from_wallet)

    # ------------------------------------------------------------ 读写磁盘
    def _load(self):
        for secret_hex in self.db.load_keys():
            self._register_key(CKey.from_secret(bytes.fromhex(secret_hex)))
        for record in self.db.load_txs():
            wtx = WalletTx.from_record(record)
            self.map_wallet[wtx.tx.get_hash()] = wtx
        self.address_book = self.db.load_address_book()
        self.transaction_fee = int(self.db.read_setting("fee", 0))

        default_pub = self.db.read_setting("defaultkey")
        if default_pub and bytes.fromhex(default_pub) in self.keys:
            self.default_key = self.keys[bytes.fromhex(default_pub)]
        else:
            # 第一次运行：生成"你的地址"
            self.default_key = self.generate_new_key()
            self.db.write_setting("defaultkey", self.default_key.get_pubkey().hex())

    def _write_wtx(self, wtx: WalletTx):
        self.db.write_tx(uint256_to_hex(wtx.tx.get_hash()), wtx.to_record())

    def save(self):
        """每条记录在变化时就已经单独落盘了，这里只是为了兼容旧接口。"""

    def close(self):
        with self.lock:
            self.db.close()

    # ---------------------------------------------------------------- 密钥
    def _register_key(self, key: CKey) -> CKey:
        pub = key.get_pubkey()
        self.keys[pub] = key
        self.keys_by_h160[hash160(pub)] = key
        return key

    def add_key(self, key: CKey) -> CKey:
        """AddKey：登记一把密钥并**立刻**写盘。"""
        with self.lock:
            self._register_key(key)
            self.db.write_key(key.get_pubkey().hex(), key.get_secret().hex())
            return key

    def generate_new_key(self) -> CKey:
        """GenerateNewKey。"""
        return self.add_key(CKey.generate())

    def get_default_address(self) -> str:
        return base58.pubkey_to_address(self.default_key.get_pubkey())

    def set_new_default_key(self):
        """界面上的"换一个新地址"。旧地址依然有效，钱包里所有密钥都能收款。"""
        with self.lock:
            self.default_key = self.generate_new_key()
            self.db.write_setting("defaultkey", self.default_key.get_pubkey().hex())

    def set_transaction_fee(self, fee: int):
        with self.lock:
            self.transaction_fee = fee
            self.db.write_setting("fee", fee)

    # 供 sign_signature 使用的"钥匙串"接口
    def get_key_for_pubkey(self, pubkey: bytes) -> CKey | None:
        return self.keys.get(bytes(pubkey))

    def get_key_for_hash160(self, h160: bytes) -> CKey | None:
        return self.keys_by_h160.get(bytes(h160))

    # ---------------------------------------------------------- 是不是我的
    def is_mine_txout(self, txout: CTxOut) -> bool:
        """CTxOut::IsMine：这把"锁"是标准模板，而且对应的私钥在我手里。"""
        match = solver(txout.script_pubkey)
        if match is None:
            return False
        kind, data = match
        if kind == TX_PUBKEY:
            return bytes(data) in self.keys
        return bytes(data) in self.keys_by_h160

    def extract_pubkey_mine(self, txout: CTxOut) -> bytes | None:
        """ExtractPubKey(fMineOnly=true)：取出这个输出对应的、属于我的公钥。"""
        match = solver(txout.script_pubkey)
        if match is None:
            return None
        kind, data = match
        if kind == TX_PUBKEY:
            return bytes(data) if bytes(data) in self.keys else None
        key = self.keys_by_h160.get(bytes(data))
        return key.get_pubkey() if key is not None else None

    def is_mine(self, tx: CTransaction) -> bool:
        """CTransaction::IsMine：只要有一个输出是我的。"""
        return any(self.is_mine_txout(o) for o in tx.vout)

    def is_mine_txin(self, txin: CTxIn) -> bool:
        return self._debit_of(txin) is not None

    def _debit_of(self, txin: CTxIn):
        prev = self.map_wallet.get(txin.prevout.hash)
        if prev is not None and txin.prevout.n < len(prev.tx.vout):
            txout = prev.tx.vout[txin.prevout.n]
            if self.is_mine_txout(txout):
                return txout.n_value
        return None

    def get_credit(self, tx: CTransaction) -> int:
        """CTransaction::GetCredit：这笔交易付给我的总额（不考虑是否成熟）。"""
        return sum(o.n_value for o in tx.vout if self.is_mine_txout(o))

    def get_debit(self, tx: CTransaction) -> int:
        """CTransaction::GetDebit：这笔交易从我这里花掉的总额。"""
        return sum(self._debit_of(txin) or 0 for txin in tx.vin)

    # ------------------------------------------------------------ 收录交易
    def add_to_wallet_if_mine(self, tx: CTransaction, pindex=None,
                              f_from_me: bool = False):
        """AddToWalletIfMine：和我有关的交易才收录进钱包。"""
        with self.lock:
            h = tx.get_hash()
            if not (self.is_mine(tx) or h in self.map_wallet
                    or self.get_debit(tx) > 0):
                return
            wtx = self.map_wallet.get(h)
            if wtx is None:
                wtx = WalletTx(tx, util.get_adjusted_time(), f_from_me)
                self.map_wallet[h] = wtx
                updated = True
                # 【扩展】原版的 fSpent 只在本机发起转账时才会设置；这里额外处理
                # "重新扫描区块链"时遇到的、花了我的钱的历史交易，否则余额会算多。
                for txin in tx.vin:
                    if self._debit_of(txin) is not None:
                        prev = self.map_wallet[txin.prevout.hash]
                        if not prev.f_spent:
                            prev.f_spent = True
                            self._write_wtx(prev)
            else:
                updated = False                 # 已经有了：只合并新信息
                if f_from_me and not wtx.f_from_me:
                    wtx.f_from_me = True
                    updated = True
            if pindex is not None and wtx.hash_block != pindex.hash:
                wtx.hash_block = pindex.hash    # 记下它进了哪个区块
                updated = True
            if updated:
                self._write_wtx(wtx)
        self._notify()

    def erase_from_wallet(self, tx: CTransaction):
        """EraseFromWallet：交易被更新版本替换掉了，旧版本从钱包里删除。"""
        with self.lock:
            h = tx.get_hash()
            if self.map_wallet.pop(h, None) is not None:
                self.db.erase_tx(uint256_to_hex(h))
        self._notify()

    def _on_chain_tx(self, tx, pindex, f_connect):
        if f_connect:
            self.add_to_wallet_if_mine(tx, pindex)
        elif tx.get_hash() in self.map_wallet:
            self._notify()      # 区块被断开（链重组）：确认数变了，让界面刷新

    def _on_mempool_tx(self, tx):
        self.add_to_wallet_if_mine(tx)

    # ---------------------------------------------------------------- 余额
    def get_depth(self, wtx: WalletTx) -> int:
        """GetDepthInMainChain：确认数。纯内存查询，不碰数据库。"""
        if wtx.hash_block == 0:
            return 0
        return self.chain.get_block_depth(wtx.hash_block)

    def blocks_to_maturity(self, wtx: WalletTx) -> int:
        """GetBlocksToMaturity：挖矿所得还要再等几个块才能花。"""
        if not wtx.tx.is_coinbase():
            return 0
        return max(0, (params.COINBASE_MATURITY + 20) - self.get_depth(wtx))

    def is_final(self, wtx: WalletTx) -> bool:
        return wtx.tx.is_final(self.chain.best_height)

    def get_available_credit(self, wtx: WalletTx) -> int:
        """CWalletTx::GetCredit：未成熟的挖矿所得按 0 算。"""
        if wtx.tx.is_coinbase() and self.blocks_to_maturity(wtx) > 0:
            return 0
        return self.get_credit(wtx.tx)

    def get_balance(self) -> int:
        """GetBalance：所有"已定稿、未花掉"的钱包交易的可用金额之和。"""
        with self.chain.lock, self.lock:
            return sum(self.get_available_credit(w)
                       for w in self.map_wallet.values()
                       if self.is_final(w) and not w.f_spent)

    # ---------------------------------------------------------------- 花钱
    def select_coins(self, target: int) -> list[WalletTx] | None:
        """SelectCoins：挑哪几笔钱来凑够 target。逐行移植自原版：

        1. 有恰好等于 target 的，直接用它；
        2. 把比 target 小的放一堆，同时记住"比 target 大的里面最小的那个"；
        3. 小的全加起来都不够 -> 只能用那个大的；
        4. 否则用随机试探法（最多 1000 轮）找一个总和 >= target 且尽量接近的子集；
        5. 如果那个"大的"比找到的子集更接近 target，就用大的。
        """
        with self.chain.lock, self.lock:
            lowest_larger: tuple[int, WalletTx] | None = None
            v_value: list[tuple[int, WalletTx]] = []
            total_lower = 0
            for wtx in self.map_wallet.values():
                if not self.is_final(wtx) or wtx.f_spent:
                    continue
                n = self.get_available_credit(wtx)
                if n <= 0:
                    continue
                if n < target:
                    v_value.append((n, wtx))
                    total_lower += n
                elif n == target:
                    return [wtx]
                elif lowest_larger is None or n < lowest_larger[0]:
                    lowest_larger = (n, wtx)

            if total_lower < target:
                return [lowest_larger[1]] if lowest_larger else None

            # 用随机逼近法解"子集和"问题
            v_value.sort(key=lambda item: item[0], reverse=True)
            best_mask = [True] * len(v_value)
            best_total = total_lower
            for _ in range(1000):
                if best_total == target:
                    break
                included = [False] * len(v_value)
                total = 0
                reached = False
                for n_pass in range(2):
                    if reached:
                        break
                    for i, (value, _) in enumerate(v_value):
                        # 第一遍随机取；第二遍把第一遍没取的补上
                        take = random.random() < 0.5 if n_pass == 0 else not included[i]
                        if take:
                            total += value
                            included[i] = True
                            if total >= target:
                                reached = True
                                if total < best_total:
                                    best_total = total
                                    best_mask = list(included)
                                total -= value
                                included[i] = False

            if lowest_larger and lowest_larger[0] - target <= best_total - target:
                return [lowest_larger[1]]
            return [wtx for (_, wtx), keep in zip(v_value, best_mask) if keep]

    def create_transaction(self, script_pubkey: bytes, value: int):
        """CreateTransaction：组装并签好一笔交易。
        成功返回 (WalletTx, 实际手续费)；失败返回 (None, 需要的手续费)。"""
        if value < 0:
            return None, 0
        with self.chain.lock, self.lock:
            fee = self.transaction_fee
            while True:
                # 注：原版在这个循环里有个小 bug——重试时把上一轮的手续费也加进了
                # 付给对方的金额里。它只在"用户设的手续费 < 最低手续费"时才会触发，
                # 属于无心之失而非设计，这里没有复刻。
                total_needed = value + fee
                coins = self.select_coins(total_needed)
                if coins is None:
                    return None, fee
                value_in = sum(self.get_available_credit(w) for w in coins)

                tx = CTransaction()
                tx.vout.append(CTxOut(value, script_pubkey))        # vout[0]：付给对方

                if value_in > total_needed:
                    # vout[1]：找零。付回给第一枚币的同一个公钥
                    change_pubkey = None
                    for txout in coins[0].tx.vout:
                        if self.is_mine_txout(txout):
                            change_pubkey = self.extract_pubkey_mine(txout)
                            if change_pubkey:
                                break
                    if not change_pubkey:
                        return None, fee
                    tx.vout.append(CTxOut(value_in - total_needed,
                                          script_pubkey_for_pubkey(change_pubkey)))

                # 输入：选中的每笔钱包交易里，所有属于我的输出
                prevs = []
                for w in coins:
                    for n, txout in enumerate(w.tx.vout):
                        if self.is_mine_txout(txout):
                            tx.vin.append(CTxIn(COutPoint(w.tx.get_hash(), n)))
                            prevs.append(w.tx)
                for n_in, prev_tx in enumerate(prevs):
                    if not sign_signature(self, prev_tx, tx, n_in, SIGHASH_ALL):
                        return None, fee

                # 签完名才知道交易有多大，才能算出最低手续费；不够就加钱重来
                min_fee = tx.get_min_fee(f_discount=True)
                if fee < min_fee:
                    fee = min_fee
                    continue
                return WalletTx(tx, util.get_adjusted_time(), f_from_me=True), fee

    def commit_transaction_spent(self, wtx: WalletTx):
        """CommitTransactionSpent：把新交易记入钱包，并把它花掉的那些钱包交易标记为已花。"""
        with self.lock:
            self.map_wallet[wtx.tx.get_hash()] = wtx
            self._write_wtx(wtx)
            for prev_hash in {txin.prevout.hash for txin in wtx.tx.vin}:
                prev = self.map_wallet.get(prev_hash)
                if prev is not None:
                    prev.f_spent = True
                    self._write_wtx(prev)
        self._notify()

    def send_money(self, address: str, value: int):
        """SendMoney（外加发送对话框里的那几项检查）。
        返回 (True, 交易哈希) 或 (False, 给用户看的错误信息)。"""
        if value <= 0:
            return False, "金额错误"
        if not base58.is_valid_address(address):
            return False, "无效的比特币地址"
        with self.chain.lock, self.lock:
            balance = self.get_balance()
            if value > balance:
                return False, "金额超过了你的余额"
            if value + self.transaction_fee > balance:
                return False, (f"加上 {util.format_money(self.transaction_fee)} "
                               "的手续费后，总额超过了你的余额")

            script_pubkey = script_pubkey_for_hash160(base58.address_to_hash160(address))
            wtx, fee = self.create_transaction(bytes(script_pubkey), value)
            if wtx is None:
                if value + fee > balance:
                    return False, (f"这是一笔超大的交易，需要 "
                                   f"{util.format_money(fee)} 的手续费")
                return False, "创建交易失败"

            self.commit_transaction_spent(wtx)
            if not self.mempool.accept(wtx.tx):
                # 原版在这里直接抛异常（"这不应该失败"）。我们改成把钱包状态恢复原样。
                self._undo_commit(wtx)
                return False, "交易无效，未能广播"
            if address not in self.address_book:
                self.set_address_label(address, "")     # 原版：发送过的地址自动进地址簿
            return True, uint256_to_hex(wtx.tx.get_hash())

    def _undo_commit(self, wtx: WalletTx):
        with self.lock:
            h = wtx.tx.get_hash()
            self.map_wallet.pop(h, None)
            self.db.erase_tx(uint256_to_hex(h))
            for prev_hash in {txin.prevout.hash for txin in wtx.tx.vin}:
                prev = self.map_wallet.get(prev_hash)
                if prev is not None:
                    prev.f_spent = False
                    self._write_wtx(prev)
        self._notify()

    # ------------------------------------------------------ 重播与重新扫描
    def _unconfirmed_own_txs(self) -> list[WalletTx]:
        return sorted((w for w in self.map_wallet.values()
                       if not w.tx.is_coinbase()
                       and not self.chain.contains_tx(w.tx.get_hash())),
                      key=lambda w: w.time_received)

    def reaccept_wallet_transactions(self):
        """ReacceptWalletTransactions：节点重启后内存池是空的，
        把钱包里还没进块的交易重新放回内存池，否则它们就永远没人打包了。"""
        with self.chain.lock, self.lock:
            pending = self._unconfirmed_own_txs()
        for wtx in pending:
            self.mempool.accept(wtx.tx, check_inputs=False)

    def relay_wallet_transactions(self):
        """RelayWalletTransactions：每出现新的最佳区块时调用；最多每 10 分钟一次，
        把还没进块的钱包交易再向全网广播一遍（比如当初发送时一个邻居都没连上）。"""
        now = util.get_time()
        if now - self._last_relay_time < 10 * 60:
            return
        self._last_relay_time = now
        with self.chain.lock, self.lock:
            pending = self._unconfirmed_own_txs()
        for wtx in pending:
            for f in self.relay_listeners:
                f(wtx.tx)

    def rescan(self):
        """【扩展】从头扫描整条主链，把和我有关的交易都收录进来。
        原版 0.1.5 没有这个功能（后来的版本才加了 -rescan），导入密钥后需要它。"""
        with self.chain.lock, self.lock:
            for pindex in self.chain.main_chain:
                for tx in self.chain.load_block(pindex).vtx:
                    self.add_to_wallet_if_mine(tx, pindex)

    # ---------------------------------------------------------------- 其他
    def transactions_newest_first(self) -> list[WalletTx]:
        with self.lock:
            return sorted(self.map_wallet.values(),
                          key=lambda w: w.time_received, reverse=True)

    def set_address_label(self, address: str, label: str):
        with self.lock:
            self.address_book[address] = label
            self.db.write_name(address, label)
        self._notify()

    def _notify(self):
        for f in self.changed_listeners:
            f()
