"""测试公用的工具和夹具（fixture）。

最耗时的事情是挖矿。所以整个测试会话只挖**一次** SEED_HEIGHT 个区块的"种子链"，
存到一个目录里；每个需要"有钱的链"的测试拿到的都是它的一份独立拷贝，互不影响。
"""

import itertools
import json
import logging
import shutil
import socket
import time

import pytest

from bitcoin import params
from bitcoin.block import CBlock
from bitcoin.blockchain import Blockchain
from bitcoin.config import Config
from bitcoin.hashes import hash160, hash256
from bitcoin.key import CKey
from bitcoin.script import (
    CScript,
    SIGHASH_ALL,
    script_pubkey_for_hash160,
    script_pubkey_for_pubkey,
    sign_signature,
)
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut

logging.disable(logging.DEBUG)      # 测试时不输出海量的调试日志

SEED_HEIGHT = 130                   # 种子链的高度
_extra_nonce = itertools.count(1)


class KeyStore:
    """最简单的"钥匙串"，实现 sign_signature 需要的两个查询方法。"""

    def __init__(self, *keys):
        self.by_pubkey = {k.get_pubkey(): k for k in keys}
        self.by_h160 = {hash160(k.get_pubkey()): k for k in keys}

    def get_key_for_pubkey(self, pubkey):
        return self.by_pubkey.get(bytes(pubkey))

    def get_key_for_hash160(self, h160):
        return self.by_h160.get(bytes(h160))


def solve_block(block: CBlock) -> CBlock:
    """不停换 nonce，直到区块哈希满足它自己声称的难度。"""
    target = params.compact_to_target(block.n_bits)
    prefix = block.header_bytes()[:76]
    nonce = 0
    while int.from_bytes(hash256(prefix + nonce.to_bytes(4, "little")), "little") > target:
        nonce += 1
    block.n_nonce = nonce
    return block


def mine_block(chain: Blockchain, prev=None, txs=(), key=None, n_time=None,
               coinbase_value=None) -> CBlock:
    """在 prev（默认是当前链尖）之上造一个合法的区块并挖出来，但**不**提交给链。"""
    prev = prev or chain.best_index
    key = key or CKey.generate()
    n_bits = chain.get_next_work_required(prev)
    script_sig = CScript().push_int(n_bits).push_bignum(next(_extra_nonce))
    coinbase = CTransaction(
        vin=[CTxIn(COutPoint(), script_sig)],
        vout=[CTxOut(coinbase_value if coinbase_value is not None
                     else params.block_value(prev.n_height, 0),
                     script_pubkey_for_pubkey(key.get_pubkey()))],
    )
    block = CBlock(
        n_version=1,
        hash_prev_block=prev.hash,
        n_time=n_time if n_time is not None else prev.n_time + 1,
        n_bits=n_bits,
        vtx=[coinbase] + list(txs),
    )
    block.hash_merkle_root = block.get_merkle_root()
    return solve_block(block)


def make_spend(prev_tx: CTransaction, n: int, key: CKey, value: int,
               to_h160: bytes | None = None, n_sequence: int = 0xFFFFFFFF,
               n_lock_time: int = 0) -> CTransaction:
    """花掉 prev_tx 的第 n 个输出（归 key 所有），付 value 给一个 P2PKH 地址。"""
    to_h160 = to_h160 if to_h160 is not None else hash160(CKey.generate().get_pubkey())
    spend = CTransaction(
        vin=[CTxIn(COutPoint(prev_tx.get_hash(), n), n_sequence=n_sequence)],
        vout=[CTxOut(value, script_pubkey_for_hash160(to_h160))],
        n_lock_time=n_lock_time,
    )
    assert sign_signature(KeyStore(key), prev_tx, spend, 0, SIGHASH_ALL)
    return spend


def coinbase_of(chain: Blockchain, height: int) -> CTransaction:
    return chain.load_block(chain.main_chain[height]).vtx[0]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until(predicate, timeout=30, what="条件"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"等待超时：{what}")


# --------------------------------------------------------------------- 夹具
@pytest.fixture(scope="session")
def seed_chain_dir(tmp_path_factory):
    """整个会话只挖一次的种子链；每个 coinbase 的私钥保存在 keys.json 里。"""
    d = tmp_path_factory.mktemp("seedchain")
    chain = Blockchain(str(d))
    keys = {}
    for height in range(1, SEED_HEIGHT + 1):
        key = CKey.generate()
        assert chain.process_block(mine_block(chain, key=key)), f"种子链在高度 {height} 挖矿失败"
        keys[height] = key.get_secret().hex()
    assert chain.best_height == SEED_HEIGHT
    chain.close()
    (d / "keys.json").write_text(json.dumps(keys))
    return d


@pytest.fixture
def funded_chain(seed_chain_dir, tmp_path):
    """种子链的一份私有拷贝：返回 (chain, {高度: 该高度 coinbase 的 CKey})。"""
    dst = tmp_path / "chain"
    shutil.copytree(seed_chain_dir, dst)
    chain = Blockchain(str(dst))
    keys = {int(h): CKey.from_secret(bytes.fromhex(sec))
            for h, sec in json.loads((dst / "keys.json").read_text()).items()}
    yield chain, keys
    chain.close()


@pytest.fixture
def fresh_chain(tmp_path):
    """只有创世块的新链。"""
    chain = Blockchain(str(tmp_path / "fresh"))
    yield chain
    chain.close()


@pytest.fixture
def make_node(tmp_path):
    """工厂夹具：make_node("A", "-connect=...") 创建一个完整节点，测试结束时自动关闭。"""
    from bitcoin.node import Node

    nodes = []

    def factory(name: str, *extra_args, port: int | None = None, datadir=None):
        port = port or free_port()
        datadir = datadir or (tmp_path / name)
        node = Node(Config([f"-datadir={datadir}", f"-port={port}", *extra_args]))
        node.test_port = port
        nodes.append(node)
        return node

    yield factory
    for node in reversed(nodes):
        try:
            node.stop()
        except Exception:
            pass
