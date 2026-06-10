"""Shared fixtures: an in-process miner and a pre-mined 110-block chain.

Mining 110 blocks takes ~10 s, so it happens once per session into a seed
directory; each test that needs spendable coins gets a fresh copy of it.
"""

import itertools
import json
import shutil

import pytest

from bitcoin import params
from bitcoin.block import CBlock
from bitcoin.blockchain import Blockchain
from bitcoin.hashes import hash160
from bitcoin.key import CKey
from bitcoin.script import (
    CScript,
    SIGHASH_ALL,
    script_pubkey_for_hash160,
    script_pubkey_for_pubkey,
    sign_signature,
)
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut

_extra_nonce = itertools.count(1)


class KeyStore:
    def __init__(self, *keys):
        self.by_pubkey = {k.get_pubkey(): k for k in keys}
        self.by_h160 = {hash160(k.get_pubkey()): k for k in keys}

    def get_key_for_pubkey(self, pubkey):
        return self.by_pubkey.get(bytes(pubkey))

    def get_key_for_hash160(self, h160):
        return self.by_h160.get(bytes(h160))


def mine_block(chain: Blockchain, prev=None, txs=(), key=None, n_time=None,
               coinbase_value=None) -> CBlock:
    """Build and PoW-solve a block on top of `prev` (default: best)."""
    prev = prev or chain.best_index
    height = prev.n_height + 1
    key = key or CKey.generate()
    n_bits = chain.get_next_work_required(prev)
    script_sig = CScript().push_int(n_bits).push_int(next(_extra_nonce))
    coinbase = CTransaction(
        vin=[CTxIn(COutPoint(), script_sig)],
        vout=[CTxOut(coinbase_value if coinbase_value is not None
                     else params.block_value(height, 0),
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
    target = params.compact_to_target(block.n_bits)
    nonce = 0
    while True:
        block.n_nonce = nonce
        if block.get_hash() <= target:
            return block
        nonce += 1


def make_spend(prev_tx: CTransaction, n: int, key: CKey, value: int,
               to_h160: bytes | None = None) -> CTransaction:
    """Spend output `n` of prev_tx (owned by `key`) into one P2PKH output."""
    to_h160 = to_h160 if to_h160 is not None else hash160(CKey.generate().get_pubkey())
    spend = CTransaction(
        vin=[CTxIn(COutPoint(prev_tx.get_hash(), n))],
        vout=[CTxOut(value, script_pubkey_for_hash160(to_h160))],
    )
    assert sign_signature(KeyStore(key), prev_tx, spend, 0, SIGHASH_ALL)
    return spend


@pytest.fixture(scope="session")
def seed_chain_dir(tmp_path_factory):
    """110 blocks mined once per test session; coinbase keys saved alongside."""
    d = tmp_path_factory.mktemp("seedchain")
    chain = Blockchain(str(d))
    keys = {}
    for height in range(1, 111):
        key = CKey.generate()
        block = mine_block(chain, key=key)
        assert chain.process_block(block), f"seed mining failed at height {height}"
        keys[height] = key.get_secret().hex()
    assert chain.best_height == 110
    chain.close()
    (d / "keys.json").write_text(json.dumps(keys))
    return d


@pytest.fixture
def funded_chain(seed_chain_dir, tmp_path):
    """A private copy of the seed chain: (chain, {height: CKey})."""
    dst = tmp_path / "chain"
    shutil.copytree(seed_chain_dir, dst)
    chain = Blockchain(str(dst))
    keys = {
        int(h): CKey.from_secret(bytes.fromhex(sec))
        for h, sec in json.loads((dst / "keys.json").read_text()).items()
    }
    yield chain, keys
    chain.close()


@pytest.fixture
def fresh_chain(tmp_path):
    chain = Blockchain(str(tmp_path / "fresh"))
    yield chain
    chain.close()
