"""矿工——对应原版 main.cpp 的 BitcoinMiner()。

挖矿就是一个"猜数字"的体力活：
1. 把内存池里的交易装进一个候选区块，第一笔放上付给自己的 coinbase；
2. 不停地改区块头里的 nonce，每改一次算一次区块哈希；
3. 哪次哈希值小于等于难度目标，这个区块就"挖到"了，立刻广播出去。

私网的难度很低（平均约 65536 次哈希出一个块），而 Python 的 hashlib 每秒能算
几十万次双 SHA-256，所以一个普通线程就够用了。

【与原版的一处有意不同】原版在一个邻居都没连上时不挖矿（while (vNodes.empty()) Sleep）。
私网经常是单机自己玩，所以这里允许单机挖矿。
"""

import threading

from . import params, util
from .block import CBlock
from .hashes import hash256
from .key import CKey
from .script import CScript, script_pubkey_for_pubkey
from .serialize import uint256_to_hex
from .tx import COutPoint, CTransaction, CTxIn, CTxOut

NONCE_CHECK_MASK = 0x3FFFF      # 原版：每试 262144 个 nonce 抬头看一眼外面的情况


class Miner:
    def __init__(self, chain, mempool, wallet, logger=None):
        self.chain = chain
        self.mempool = mempool
        self.wallet = wallet
        self.log = logger or util.setup_logging(None)
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.blocks_found = 0
        # 和原版一样：收款密钥先只放在矿工手里，**挖到块时**才存进钱包并换一把新的。
        # 这样没挖到的尝试不会在钱包里留下一堆没用的密钥。
        self.key = CKey.generate()
        self.extra_nonce = 0            # bnExtraNonce：保证每次尝试的 coinbase 都不一样

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
        self.log.debug("BitcoinMiner 启动")

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.log.debug("BitcoinMiner 停止")

    def create_new_block(self) -> tuple[CBlock, int]:
        """组装候选区块（对应 BitcoinMiner 里 "Create new block" 那一段）。
        返回 (区块, 它所基于的父块哈希)。"""
        with self.chain.lock:
            prev = self.chain.best_index
            n_bits = self.chain.get_next_work_required(prev)

            # coinbase：scriptSig << nBits << ++bnExtraNonce（后者是 CBigNum，按数据压栈）
            self.extra_nonce += 1
            script_sig = CScript().push_int(n_bits).push_bignum(self.extra_nonce)
            coinbase = CTransaction(
                vin=[CTxIn(COutPoint(), script_sig)],
                vout=[CTxOut(0, script_pubkey_for_pubkey(self.key.get_pubkey()))],
            )
            vtx = [coinbase]

            # 从内存池里挑交易。要反复扫描多遍：因为池里的交易 B 可能花的是交易 A 的输出，
            # 如果先遇到 B，得等 A 被选中之后的下一遍才能选上 B。
            pool_txs = self.mempool.transactions()
            mem_txs = {uint256_to_hex(tx.get_hash()): tx for tx in pool_txs}
            test_pool: dict = {}
            fees = 0
            block_size = 0
            added: set[int] = set()
            found_something = True
            while found_something and block_size < params.MAX_SIZE // 2:
                found_something = False
                for tx in pool_txs:
                    h = tx.get_hash()
                    if h in added:
                        continue
                    if tx.is_coinbase() or not tx.is_final(self.chain.best_height):
                        continue
                    # 手续费门槛（主要是为了防止有人用垃圾交易灌满区块）：
                    # 每个区块的前 100 笔交易，只要小于 10K 字节就免费；否则每千字节 0.01
                    min_fee = tx.get_min_fee(f_discount=len(vtx) < 100)
                    # 每笔交易在"草稿的副本"上试，失败了就整份丢弃，不污染正式草稿
                    test_pool_tmp = dict(test_pool)
                    fee = self.chain.connect_inputs(
                        tx, test_pool_tmp, prev.n_height + 1, min_fee, mem_txs)
                    if fee is None:
                        continue
                    # 登记本交易：只有**已经被选进本区块**的池内交易，它的输出才能被后面的交易花
                    test_pool_tmp[uint256_to_hex(h)] = {
                        "blockhash": None,          # 还没上链
                        "txn": -1,
                        "spent": [None] * len(tx.vout),
                    }
                    test_pool = test_pool_tmp
                    fees += fee
                    vtx.append(tx)
                    block_size += tx.get_serialize_size()
                    added.add(h)
                    found_something = True

            coinbase.vout[0].n_value = params.block_value(self.chain.best_height, fees)
            block = CBlock(
                n_version=1,
                hash_prev_block=prev.hash,
                n_time=max(prev.get_median_time_past() + 1, util.get_adjusted_time()),
                n_bits=n_bits,
                n_nonce=1,
                vtx=vtx,
            )
            block.hash_merkle_root = block.get_merkle_root()
            return block, prev.hash

    def solve(self, block: CBlock, prev_hash: int | None = None) -> bool:
        """反复改 nonce 直到哈希达标。返回 True 表示挖到了；
        返回 False 表示该放弃这个候选区块、重新组装一个了。"""
        target = params.compact_to_target(block.n_bits)
        start_time = util.get_time()
        pool_size_at_start = len(self.mempool)
        # 区块头 80 字节里只有最后 4 字节（nonce）在变，前 76 字节先算好，省得每次重新序列化
        prefix = block.header_bytes()[:76]
        while True:
            header = prefix + block.n_nonce.to_bytes(4, "little")
            if int.from_bytes(hash256(header), "little") <= target:
                return True
            block.n_nonce = (block.n_nonce + 1) & 0xFFFFFFFF
            if block.n_nonce & NONCE_CHECK_MASK == 0:
                if block.n_nonce == 0:
                    return False            # 40 多亿个 nonce 全试完了
                if prev_hash is not None and self.chain.best_index.hash != prev_hash:
                    return False            # 别人先挖到了，链尖变了
                if (len(self.mempool) != pool_size_at_start
                        and util.get_time() - start_time > 60):
                    return False            # 有新交易，而且已经挖了一分钟：重新打包
                if self.stop_event.is_set():
                    return False
                # 顺便刷新时间戳（时间戳在前 76 字节里，所以前缀要重算）
                block.n_time = max(block.n_time, util.get_adjusted_time())
                prefix = block.header_bytes()[:76]

    def found_block(self, block: CBlock) -> bool:
        """挖到之后：先把收款密钥存进钱包（这样钱包才认得这笔 coinbase 是自己的），
        换一把新密钥，然后像处理别人发来的区块一样处理它。"""
        self.wallet.add_key(self.key)
        self.key = CKey.generate()
        self.blocks_found += 1
        ok = self.chain.process_block(block)
        if not ok:
            self.log.debug("BitcoinMiner 出错：自己挖的区块没被接受")
        return ok

    def mine_one_block(self) -> CBlock:
        """同步地挖出一个区块（测试和教学用）。"""
        while True:
            block, prev_hash = self.create_new_block()
            if self.solve(block, prev_hash):
                self.found_block(block)
                return block

    def _mine_loop(self):
        while not self.stop_event.is_set():
            block, prev_hash = self.create_new_block()
            if self.solve(block, prev_hash) and not self.stop_event.is_set():
                self.log.debug("BitcoinMiner: 找到工作量证明，nonce=%d", block.n_nonce)
                self.found_block(block)
