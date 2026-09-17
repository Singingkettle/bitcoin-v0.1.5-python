"""实验脚本的公共小工具。每个 labXX_*.py 的第一行都会 import 它。"""

import logging
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

logging.disable(logging.DEBUG)          # 实验时不看调试日志
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def title(text: str):
    print()
    print("=" * 70)
    print(text)
    print("=" * 70)


def step(text: str):
    print()
    print(f"—— {text}")


def temp_datadir(name: str) -> str:
    """每次运行都用一个全新的临时数据目录，不会弄脏你的真实数据。"""
    return tempfile.mkdtemp(prefix=f"btc_lab_{name}_")


def mine(chain, n=1, key=None, txs=(), quiet=False, prev=None):
    """在 chain 的链尖上（或指定的 prev 之上）连续挖 n 个块并提交。
    奖励付给 key（不给就付给一把随机密钥）。返回挖出的区块列表。"""
    from bitcoin import params
    from bitcoin.block import CBlock
    from bitcoin.hashes import hash256
    from bitcoin.key import CKey
    from bitcoin.script import CScript, script_pubkey_for_pubkey
    from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut

    blocks = []
    for i in range(n):
        prev = chain.best_index if prev is None else prev
        k = key or CKey.generate()
        n_bits = chain.get_next_work_required(prev)
        mine.extra_nonce += 1
        coinbase = CTransaction(
            vin=[CTxIn(COutPoint(), CScript().push_int(n_bits).push_bignum(mine.extra_nonce))],
            vout=[CTxOut(params.block_value(chain.best_height, 0),
                         script_pubkey_for_pubkey(k.get_pubkey()))])
        block = CBlock(n_version=1, hash_prev_block=prev.hash, n_time=prev.n_time + 1,
                       n_bits=n_bits, vtx=[coinbase] + (list(txs) if i == 0 else []))
        block.hash_merkle_root = block.get_merkle_root()
        target = params.compact_to_target(n_bits)
        prefix = block.header_bytes()[:76]
        nonce = 0
        while int.from_bytes(hash256(prefix + nonce.to_bytes(4, "little")), "little") > target:
            nonce += 1
        block.n_nonce = nonce
        assert chain.process_block(block)
        blocks.append(block)
        prev = chain.map_block_index[block.get_hash()]      # 下一个块接在刚挖出的这个后面
    if not quiet:
        print(f"   （挖了 {n} 个块，当前高度 {chain.best_height}）")
    return blocks


mine.extra_nonce = 0
