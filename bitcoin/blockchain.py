"""区块链的共识核心——对应原版 main.cpp 里的
ProcessBlock / CheckBlock / AcceptBlock / AddToBlockIndex /
ConnectBlock / DisconnectBlock / Reorganize / GetNextWorkRequired。

一个 Blockchain 对象持有原版里的那些全局变量（mapBlockIndex、pindexBest、
hashBestChain、mapOrphanBlocks……），这样测试里可以在同一个进程中跑多条互相独立的链。
self.lock 对应原版的全局大锁 cs_main：凡是读写链状态的操作都要先拿到它。

【加锁顺序约定】全项目统一为：先 chain.lock，后 wallet.lock。
任何线程都不允许反过来拿，否则两个线程会互相等待对方手里的锁——死锁。
"""

import threading
from collections import defaultdict

from . import params, util
from .block import CBlock, CBlockIndex, CBlockLocator
from .db import BlockFile, BlockIndexDB
from .script import CScript, OP_CHECKSIG, verify_signature
from .serialize import uint256_from_hex, uint256_to_hex
from .tx import COutPoint, CTransaction, CTxIn, CTxOut


def build_genesis_block(n_time: int | None = None, n_nonce: int | None = None) -> CBlock:
    """构造私网的创世块——写法与原版 LoadBlockIndex() 里构造主网创世块完全一致：
    scriptSig = CScript() << 486604799 << CBigNum(4) << 时间戳文字。"""
    script_sig = (CScript().push_int(486604799)
                  .push_bignum(4)
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

        self.map_block_index: dict[int, CBlockIndex] = {}       # 区块哈希 -> 索引项
        self.map_orphan_blocks: dict[int, CBlock] = {}          # 孤块：父块还没到的区块
        self.map_orphan_blocks_by_prev: dict[int, list[CBlock]] = defaultdict(list)

        self.genesis_index: CBlockIndex | None = None
        self.best_index: CBlockIndex | None = None              # pindexBest：最佳链的链尖
        self.main_chain: list[CBlockIndex] = []                 # 最佳链：下标就是高度

        self.mempool = None        # 由 MemPool 自己挂上来（重组时要用）
        self.tx_listeners = []     # f(tx, pindex或None, 是否连接)——钱包订阅
        self.block_listeners = []  # f(pindex)：出现新的最佳区块——网络层/界面订阅

        self._load_or_create()

    # ---------------------------------------------------------- 启动加载
    def _load_or_create(self):
        rows = self.db.load_block_index()
        if not rows:
            # 全新的数据目录：写入创世块。
            # 注意创世块**不会**经过 connect_block，所以它的那笔 coinbase 不在交易索引里，
            # 永远无法被花费——和原版（以及真实的比特币）一模一样。
            genesis = build_genesis_block()
            ghash = genesis.get_hash()
            assert uint256_to_hex(ghash) == params.GENESIS_HASH, (
                "params.py 里的创世块常量对不上，请重新运行 tools/mine_genesis.py")
            assert genesis.check_proof_of_work(), "创世块不满足工作量证明"
            pos = self.block_file.append(genesis.serialized())
            pindex = CBlockIndex(genesis, ghash, pos)
            self.map_block_index[ghash] = pindex
            self.genesis_index = self.best_index = pindex
            self.main_chain = [pindex]
            self.db.write_block_index(uint256_to_hex(ghash), uint256_to_hex(0), 0, pos)
            self.db.write_best_chain(uint256_to_hex(ghash))
            self.db.commit()
            return

        # 从磁盘重建 mapBlockIndex（行按高度排序，所以父块一定先出现）
        for hash_hex, prev_hex, height, pos in rows:
            block = self.block_file.read(pos)
            h = uint256_from_hex(hash_hex)
            pindex = CBlockIndex(block, h, pos)
            pindex.n_height = height
            prev = uint256_from_hex(prev_hex)
            if prev:
                pindex.pprev = self.map_block_index.get(prev)
            self.map_block_index[h] = pindex
            if height == 0:
                self.genesis_index = pindex

        self.best_index = self.map_block_index[uint256_from_hex(self.db.read_best_chain())]
        # 从链尖沿 pprev 走回创世块，得到最佳链，再顺着设置 pnext
        chain = []
        pindex = self.best_index
        while pindex is not None:
            chain.append(pindex)
            pindex = pindex.pprev
        chain.reverse()
        for a, b in zip(chain, chain[1:]):
            a.pnext = b
        self.main_chain = chain

    # ------------------------------------------------------------ 链状态查询
    @property
    def best_height(self) -> int:
        """nBestHeight：最佳链的高度（创世块是 0）。"""
        return self.best_index.n_height if self.best_index else -1

    def is_in_main_chain(self, pindex: CBlockIndex) -> bool:
        return (pindex.n_height < len(self.main_chain)
                and self.main_chain[pindex.n_height] is pindex)

    def get_locator(self, pindex: CBlockIndex | None = None) -> CBlockLocator:
        return CBlockLocator.from_index(pindex or self.best_index,
                                        self.genesis_index.hash)

    def find_fork_by_locator(self, locator: CBlockLocator) -> CBlockIndex:
        """CBlockLocator::GetBlockIndex：对方列出的哈希里，第一个在我主链上的就是分叉点。"""
        for h in locator.v_have:
            pindex = self.map_block_index.get(h)
            if pindex is not None and self.is_in_main_chain(pindex):
                return pindex
        return self.genesis_index

    def load_block(self, pindex: CBlockIndex) -> CBlock:
        return self.block_file.read(pindex.n_file_pos)

    def load_tx(self, txhash: int):
        """通过交易索引找到一笔已上链的交易。返回 (交易, 索引记录) 或 None。"""
        with self.lock:
            rec = self.db.read_tx_index(uint256_to_hex(txhash))
            if rec is None:
                return None
            pindex = self.map_block_index.get(uint256_from_hex(rec["blockhash"]))
            if pindex is None:
                return None
            return self.load_block(pindex).vtx[rec["txn"]], rec

    def contains_tx(self, txhash: int) -> bool:
        """txdb.ContainsTx：这笔交易是否已经在最佳链的某个区块里。"""
        with self.lock:
            return self.db.read_tx_index(uint256_to_hex(txhash)) is not None

    def get_block_depth(self, block_hash: int) -> int:
        """某个区块在主链上的"深度"（确认数）：链尖自己是 1。不在主链上返回 0。"""
        with self.lock:
            pindex = self.map_block_index.get(block_hash)
            if pindex is None or not self.is_in_main_chain(pindex):
                return 0
            return self.best_height - pindex.n_height + 1

    def get_tx_depth(self, txhash: int) -> int:
        """GetDepthInMainChain：一笔交易的确认数；不在主链上返回 0。"""
        with self.lock:
            rec = self.db.read_tx_index(uint256_to_hex(txhash))
            if rec is None:
                return 0
            return self.get_block_depth(uint256_from_hex(rec["blockhash"]))

    # -------------------------------------------------------------- 难度调整
    def get_next_work_required(self, pindex_last: CBlockIndex | None) -> int:
        """GetNextWorkRequired：下一个区块应该满足的难度（nBits）。

        每 2016 个块调整一次：看这 2016 个块实际花了多久，
        比两周短就调难、比两周长就调易，但单次调整幅度限制在 4 倍以内。

        原版有个保留至今的小失误：它往回走的是 nInterval-1 = 2015 步，
        量到的其实是 2015 个间隔，却拿去和完整的两周相比。这里原样保留。
        """
        if pindex_last is None:
            return params.target_to_compact(params.PROOF_OF_WORK_LIMIT)
        if (pindex_last.n_height + 1) % params.INTERVAL != 0:
            return pindex_last.n_bits           # 不到调整点：沿用上一个块的难度

        pindex_first = pindex_last
        for _ in range(params.INTERVAL - 1):
            if pindex_first.pprev is None:
                break
            pindex_first = pindex_first.pprev

        actual_timespan = pindex_last.n_time - pindex_first.n_time
        actual_timespan = max(actual_timespan, params.TARGET_TIMESPAN // 4)
        actual_timespan = min(actual_timespan, params.TARGET_TIMESPAN * 4)

        # 新目标 = 旧目标 × 实际耗时 ÷ 期望耗时（目标值越大越容易）
        new_target = (params.compact_to_target(pindex_last.n_bits)
                      * actual_timespan // params.TARGET_TIMESPAN)
        new_target = min(new_target, params.PROOF_OF_WORK_LIMIT)
        return params.target_to_compact(new_target)

    # ---------------------------------------------------------- 接收新区块
    def check_block(self, block: CBlock) -> bool:
        """CheckBlock：不依赖上下文的检查（连孤块在入库暂存之前也要先过这一关）。"""
        if not block.vtx:
            return self._error("CheckBlock: 区块里没有交易")
        if len(block.serialized()) > params.MAX_SIZE:
            return self._error("CheckBlock: 区块过大")
        if block.n_time > util.get_adjusted_time() + 2 * 60 * 60:
            return self._error("CheckBlock: 时间戳比现在超前了两小时以上")
        if not block.vtx[0].is_coinbase():
            return self._error("CheckBlock: 第一笔交易不是 coinbase")
        if any(tx.is_coinbase() for tx in block.vtx[1:]):
            return self._error("CheckBlock: 出现了不止一笔 coinbase")
        for tx in block.vtx:
            if not tx.check_transaction():
                return self._error("CheckBlock: CheckTransaction 失败")
        if not block.check_proof_of_work():
            return self._error("CheckBlock: 工作量证明不成立")
        if block.hash_merkle_root != block.get_merkle_root():
            return self._error("CheckBlock: 默克尔根对不上")
        return True

    def process_block(self, block: CBlock) -> bool:
        """ProcessBlock：收到一个区块（不管是自己挖的还是别人发来的）的总入口。
        区块被接受，或者被当作孤块暂存，都返回 True（与原版的返回约定一致）。"""
        with self.lock:
            h = block.get_hash()
            if h in self.map_block_index:
                return self._error(f"ProcessBlock: 已经有这个区块了 {uint256_to_hex(h)[:16]}")
            if h in self.map_orphan_blocks:
                return self._error("ProcessBlock: 已经有这个区块了（孤块）")
            if not self.check_block(block):
                return self._error("ProcessBlock: CheckBlock 失败")

            # 父块还没到：先放进"孤块"暂存区，等父块来了再处理
            if block.hash_prev_block not in self.map_block_index:
                self.log.debug("ProcessBlock: 孤块，父块=%s",
                               uint256_to_hex(block.hash_prev_block)[:16])
                self.map_orphan_blocks[h] = block
                self.map_orphan_blocks_by_prev[block.hash_prev_block].append(block)
                return True

            if not self.accept_block(block):
                return self._error("ProcessBlock: AcceptBlock 失败")

            # 这个块到了，之前在等它的孤块（以及孤块的孤块……）现在都可以处理了
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
        """GetOrphanRoot：顺着孤块链往回走，返回**最老的那个孤块自己的哈希**。

        它被用作 getblocks 请求里的 hashStop："请把我的链尖之后、直到这个块之前的
        所有区块发给我"。对方发到这个哈希就停（不含它本身，因为我已经有了）。
        """
        while block.hash_prev_block in self.map_orphan_blocks:
            block = self.map_orphan_blocks[block.hash_prev_block]
        return block.get_hash()

    def accept_block(self, block: CBlock) -> bool:
        """AcceptBlock：依赖上下文的检查，通过后写盘并登记索引。"""
        if block.get_hash() in self.map_block_index:
            return self._error("AcceptBlock: 区块已在索引中")
        prev = self.map_block_index.get(block.hash_prev_block)
        if prev is None:
            return self._error("AcceptBlock: 找不到父块")
        if block.n_time <= prev.get_median_time_past():
            return self._error("AcceptBlock: 时间戳太早")
        if block.n_bits != self.get_next_work_required(prev):
            return self._error("AcceptBlock: 难度值不对")

        pos = self.block_file.append(block.serialized())
        return self._add_to_block_index(block, pos, prev)

    def _add_to_block_index(self, block: CBlock, pos: int, prev: CBlockIndex) -> bool:
        """AddToBlockIndex：登记索引；如果这个块让某条链变得比当前最佳链更高，就切换过去。"""
        h = block.get_hash()
        pindex = CBlockIndex(block, h, pos)
        pindex.pprev = prev
        pindex.n_height = prev.n_height + 1
        self.map_block_index[h] = pindex
        self.db.write_block_index(uint256_to_hex(h),
                                  uint256_to_hex(block.hash_prev_block),
                                  pindex.n_height, pos)

        # 0.1.5 选最佳链只比高度（"累计工作量"是后来版本才引入的）
        if pindex.n_height <= self.best_height:
            self.db.commit()
            return True         # 只是某条较短支链上的块：记下来就行

        events = []             # 待通知钱包的事件，等一切成功后再统一发出
        if block.hash_prev_block == self.best_index.hash:
            # 情况一：直接接在当前最佳链的末尾
            ok = self.connect_block(block, pindex, events)
            if not ok:
                self.db.rollback()
                del self.map_block_index[h]
                return self._error("AddToBlockIndex: ConnectBlock 失败")
            removed_from_pool = [tx for tx in block.vtx[1:]]
            resurrect = []
            self.best_index.pnext = pindex
            self.main_chain.append(pindex)
        else:
            # 情况二：另一条支链超过了当前最佳链 -> 重组
            result = self.reorganize(block, pindex, events)
            if result is None:
                self.map_block_index.pop(h, None)
                return self._error("AddToBlockIndex: Reorganize 失败")
            resurrect, removed_from_pool = result

        self.db.write_best_chain(uint256_to_hex(h))
        self.db.commit()
        self.best_index = pindex
        self.log.debug("AddToBlockIndex: 新的最佳区块 高度=%d 哈希=%s",
                       pindex.n_height, uint256_to_hex(h)[:20])

        # ---- 以下是"提交成功之后"才做的事 ----
        if self.mempool is not None:
            for tx in resurrect:                # 被断开的区块里的交易放回内存池
                self.mempool.accept(tx, check_inputs=False)
            for tx in removed_from_pool:        # 已经上链的交易从内存池删除
                self.mempool.remove(tx)
        for tx, tx_pindex, f_connect in events:
            for f in self.tx_listeners:
                f(tx, tx_pindex, f_connect)
        for f in self.block_listeners:
            f(pindex)
        return True

    # ------------------------------------------------------ 连接 / 断开区块
    def connect_block(self, block: CBlock, pindex: CBlockIndex, events: list) -> bool:
        """ConnectBlock：让一个区块"生效"——花掉它引用的输入，把它的交易登记进交易索引，
        并检查 coinbase 没有多拿钱。全部检查通过后才一次性写库。"""
        test_pool: dict[str, dict] = {}     # 本区块内的"草稿"：改动先记在这里
        mem_txs: dict[str, CTransaction] = {}   # 本区块里已经处理过的交易
        fees = 0
        for n, tx in enumerate(block.vtx):
            if not tx.is_coinbase():
                fee = self.connect_inputs(tx, test_pool, pindex.n_height,
                                          mem_txs=mem_txs)
                if fee is None:
                    return False
                fees += fee
            # 登记本交易，这样同一区块里排在后面的交易就可以花它的输出
            txhash_hex = uint256_to_hex(tx.get_hash())
            test_pool[txhash_hex] = {
                "blockhash": uint256_to_hex(pindex.hash),
                "txn": n,
                "spent": [None] * len(tx.vout),
            }
            mem_txs[txhash_hex] = tx
        # 原版 GetBlockValue 用的是全局的 nBestHeight（此时还是"旧"的最佳高度）
        if block.vtx[0].get_value_out() > params.block_value(self.best_height, fees):
            return self._error("ConnectBlock: coinbase 拿的钱超过了 补贴+手续费")

        for txhash_hex, rec in test_pool.items():
            self.db.write_tx_index(txhash_hex, rec["blockhash"], rec["txn"], rec["spent"])
        events.extend((tx, pindex, True) for tx in block.vtx)
        return True

    def connect_inputs(self, tx: CTransaction, test_pool: dict,
                       spend_height: int, min_fee: int = 0,
                       mem_txs: dict | None = None) -> int | None:
        """ConnectInputs：逐个检查交易的输入，成功返回这笔交易的手续费，失败返回 None。

        对每个输入：前序交易存在 -> 序号不越界 -> 如果是 coinbase 必须已成熟
        -> 签名有效 -> 这笔钱还没被花过 -> 标记为已花。

        test_pool 是调用者传进来的"草稿本"：对交易索引的修改都先写在这里。
        失败时调用者应当丢弃整个草稿（矿工就是每笔交易先复制一份草稿再试）。

        mem_txs 是"还不在磁盘索引里、但此刻可以被引用"的交易（哈希hex -> 交易）：
        连接区块时是同一区块里排在前面的交易，矿工打包时是内存池里的交易。
        """
        txhash_hex = uint256_to_hex(tx.get_hash())
        value_in = 0
        for n_in, txin in enumerate(tx.vin):
            prev_hex = uint256_to_hex(txin.prevout.hash)
            rec = test_pool.get(prev_hex) or self.db.read_tx_index(prev_hex)
            if rec is None:
                return self._error_none(f"ConnectInputs: 找不到前序交易 {prev_hex[:16]}")
            rec = {**rec, "spent": list(rec["spent"])}      # 复制一份再改，别动到原件
            if mem_txs is not None and prev_hex in mem_txs:
                prev_tx = mem_txs[prev_hex]                 # 前序交易还在内存里
                prev_height = spend_height
            else:
                prev_pindex = self.map_block_index.get(
                    uint256_from_hex(rec["blockhash"]) if rec["blockhash"] else 0)
                if prev_pindex is None:
                    return self._error_none("ConnectInputs: 前序交易所在区块的索引缺失")
                prev_tx = self.load_block(prev_pindex).vtx[rec["txn"]]
                prev_height = prev_pindex.n_height
            if txin.prevout.n >= len(prev_tx.vout) or txin.prevout.n >= len(rec["spent"]):
                return self._error_none("ConnectInputs: prevout.n 越界")
            # coinbase 必须等 100 个确认才能花
            if prev_tx.is_coinbase():
                if spend_height - prev_height < params.COINBASE_MATURITY:
                    return self._error_none("ConnectInputs: 试图花费尚未成熟的 coinbase")
            if not verify_signature(prev_tx, tx, n_in):
                return self._error_none("ConnectInputs: 签名验证失败")
            if rec["spent"][txin.prevout.n] is not None:
                return self._error_none("ConnectInputs: 这笔输出已经被花过了（双花）")
            rec["spent"][txin.prevout.n] = txhash_hex
            test_pool[prev_hex] = rec
            value_in += prev_tx.vout[txin.prevout.n].n_value

        fee = value_in - tx.get_value_out()
        if fee < 0:
            return self._error_none("ConnectInputs: 输出总额大于输入总额")
        if fee < min_fee:
            return None         # 手续费不够（只有矿工打包时才会传 min_fee）
        return fee

    def disconnect_block(self, block: CBlock, events: list) -> bool:
        """DisconnectBlock：connect_block 的逆操作——倒序撤销每笔交易：
        把它花掉的输出重新标成"未花费"，并把它自己从交易索引里删掉。"""
        for tx in reversed(block.vtx):
            if not tx.is_coinbase():
                for txin in tx.vin:
                    prev_hex = uint256_to_hex(txin.prevout.hash)
                    rec = self.db.read_tx_index(prev_hex)
                    if rec is None:
                        return self._error("DisconnectInputs: 读不到前序交易的索引")
                    if txin.prevout.n >= len(rec["spent"]):
                        return self._error("DisconnectInputs: prevout.n 越界")
                    rec["spent"][txin.prevout.n] = None
                    self.db.write_tx_index(prev_hex, rec["blockhash"],
                                           rec["txn"], rec["spent"])
            self.db.erase_tx_index(uint256_to_hex(tx.get_hash()))
        events.extend((tx, None, False) for tx in block.vtx)
        return True

    def reorganize(self, block_new: CBlock, pindex_new: CBlockIndex, events: list):
        """Reorganize：最佳链从旧分支切换到以 pindex_new 结尾的新分支。

        成功返回 (要放回内存池的交易, 要从内存池删掉的交易)；失败返回 None，
        并且数据库回滚、内存状态原封不动——就像这次重组从未发生过。
        """
        self.log.debug("*** REORGANIZE 链重组 ***")
        # 1. 找分叉点：两个指针分别从新旧链尖往回走，直到相遇
        pfork = self.best_index
        plonger = pindex_new
        while pfork is not plonger:
            pfork = pfork.pprev
            if pfork is None:
                return self._error_none("Reorganize: pfork->pprev 为空")
            while plonger.n_height > pfork.n_height:
                plonger = plonger.pprev
                if plonger is None:
                    return self._error_none("Reorganize: plonger->pprev 为空")

        # 2. 列出要断开的（旧分支，从链尖到分叉点）和要连接的（新分支，从分叉点到新链尖）
        v_disconnect = []
        pindex = self.best_index
        while pindex is not pfork:
            v_disconnect.append(pindex)
            pindex = pindex.pprev
        v_connect = []
        pindex = pindex_new
        while pindex is not pfork:
            v_connect.append(pindex)
            pindex = pindex.pprev
        v_connect.reverse()

        # 3. 断开旧分支
        pending_events = []
        v_resurrect: list[CTransaction] = []
        for pindex in v_disconnect:
            block = self.load_block(pindex)
            if not self.disconnect_block(block, pending_events):
                self.db.rollback()
                return self._error_none("Reorganize: DisconnectBlock 失败")
            v_resurrect.extend(t for t in block.vtx if not t.is_coinbase())

        # 4. 连接新分支
        v_delete: list[CTransaction] = []
        for i, pindex in enumerate(v_connect):
            block = block_new if pindex is pindex_new else self.load_block(pindex)
            if not self.connect_block(block, pindex, pending_events):
                # 新分支里有无效区块：撤销一切，并把这个块及其后面的块从索引里清掉
                self.db.rollback()
                for bad in v_connect[i:]:
                    self.map_block_index.pop(bad.hash, None)
                    self.db.erase_block_index(uint256_to_hex(bad.hash))
                self.db.commit()
                return self._error_none("Reorganize: ConnectBlock 失败")
            v_delete.extend(t for t in block.vtx if not t.is_coinbase())

        # 5. 数据库部分全部成功，现在才修改内存里的链结构
        for pindex in v_disconnect:
            if pindex.pprev is not None:
                pindex.pprev.pnext = None
        for pindex in v_connect:
            if pindex.pprev is not None:
                pindex.pprev.pnext = pindex
        self.main_chain = self.main_chain[:pfork.n_height + 1] + v_connect

        events.extend(pending_events)
        return v_resurrect, v_delete

    # ------------------------------------------------------------------ 其他
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
