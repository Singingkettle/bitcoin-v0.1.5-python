"""并发压力测试：挖矿线程、"界面轮询"线程、转账线程、网络消息线程同时折腾同一个节点。

要抓的是**死锁**：A 线程拿着链锁等钱包锁，B 线程拿着钱包锁等链锁，两个都永远等下去。
本项目的规矩是"先 chain.lock，后 wallet.lock"，这里用真刀真枪的多线程来验证没有人违反它。
"""

import json
import shutil
import threading
import time

import pytest

from bitcoin import params
from bitcoin.config import Config
from bitcoin.key import CKey
from bitcoin.node import Node
from tests.conftest import free_port, mine_block

COIN = params.COIN
RUN_SECONDS = 6


@pytest.fixture
def busy_node(seed_chain_dir, tmp_path):
    datadir = tmp_path / "node"
    shutil.copytree(seed_chain_dir, datadir)
    node = Node(Config([f"-datadir={datadir}", f"-port={free_port()}", "-nolisten"]))
    keys = json.loads((datadir / "keys.json").read_text())
    for h in range(1, 12):                                          # 这 11 个 coinbase 已对钱包成熟
        node.wallet.add_key(CKey.from_secret(bytes.fromhex(keys[str(h)])))
    node.wallet.rescan()
    assert node.wallet.get_balance() == 11 * 50 * COIN
    yield node
    node.stop()


def test_no_deadlock_under_concurrent_mining_polling_and_sending(busy_node):
    from qt.models import rows_for                                  # 界面刷新时真正执行的那段逻辑

    node = busy_node
    stop = threading.Event()
    errors: list[BaseException] = []
    counters = {"polls": 0, "sends": 0, "blocks_fed": 0}
    sent_txids = []

    def guarded(fn):
        def run():
            try:
                while not stop.is_set():
                    fn()
            except BaseException as e:                              # noqa: BLE001
                errors.append(e)
        return run

    def gui_poll():                                                 # 模拟界面定时器
        node.status()
        for wtx in node.wallet.transactions_newest_first()[:50]:
            rows_for(node.wallet, wtx)
        counters["polls"] += 1

    def sender():                                                   # 模拟用户不停转账
        addr = node.wallet.get_default_address()
        ok, result = node.wallet.send_money(addr, COIN)
        if ok:
            sent_txids.append(result)
            counters["sends"] += 1
        time.sleep(0.02)

    def block_feeder():                                             # 模拟网络线程送来别人挖的区块
        block = mine_block(node.chain)
        with node.chain.lock:
            node.chain.process_block(block)                         # 可能因为链尖已变而成为支链，无所谓
        counters["blocks_fed"] += 1

    threads = [threading.Thread(target=guarded(f), name=f.__name__, daemon=True)
               for f in (gui_poll, gui_poll, sender, block_feeder)]
    start_height = node.chain.best_height
    node.miner.start()
    for t in threads:
        t.start()
    time.sleep(RUN_SECONDS)
    stop.set()
    node.miner.stop()
    for t in threads:
        t.join(timeout=20)

    stuck = [t.name for t in threads if t.is_alive()]
    assert not stuck, f"线程卡死（疑似死锁）：{stuck}"
    assert not node.miner.running
    assert not errors, errors
    # 每类线程都确实干了活（数量不重要——五个线程在抢同一把 Python 全局解释器锁）
    assert counters["polls"] >= 2 and counters["sends"] >= 2 and counters["blocks_fed"] >= 2, counters
    assert node.chain.best_height > start_height + 5

    # 收尾：再挖一个块，所有发出去的交易都应当已经上链，内存池清空
    node.miner.mine_one_block()
    assert len(node.mempool) == 0
    from bitcoin.serialize import uint256_from_hex
    assert all(node.chain.get_tx_depth(uint256_from_hex(t)) >= 1 for t in sent_txids)
    # 守恒检查：所有转账都是付给自己、手续费为 0，所以不管中间倒了多少手，
    # 余额必须恰好等于"已成熟的 coinbase 个数 × 50"
    mature = [w for w in node.wallet.map_wallet.values()
              if w.tx.is_coinbase() and node.wallet.blocks_to_maturity(w) == 0]
    assert node.wallet.get_balance() == len(mature) * 50 * COIN


def test_index_and_disk_are_consistent_after_the_storm(busy_node):
    """折腾一阵之后重启：从磁盘重建出来的链必须和内存里的完全一致。"""
    from bitcoin.blockchain import Blockchain

    node = busy_node
    node.miner.start()
    deadline = time.time() + 4
    while time.time() < deadline:
        node.chain.process_block(mine_block(node.chain))
    node.miner.stop()
    tip, height = node.chain.best_index.hash, node.chain.best_height
    n_index = len(node.chain.map_block_index)
    node.chain.db.commit()

    datadir_copy = node.config.datadir + "_copy"
    shutil.copytree(node.config.datadir, datadir_copy,
                    ignore=shutil.ignore_patterns("debug.log"))
    reloaded = Blockchain(datadir_copy)
    assert (reloaded.best_index.hash, reloaded.best_height) == (tip, height)
    assert len(reloaded.map_block_index) == n_index
    assert [p.hash for p in reloaded.main_chain] == [p.hash for p in node.chain.main_chain]
    reloaded.close()
