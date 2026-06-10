"""One-shot: mine the private-net genesis block and patch params.py.

Run once:  python tools/mine_genesis.py
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitcoin import params
from bitcoin.blockchain import build_genesis_block
from bitcoin.serialize import uint256_to_hex


def main():
    target = params.compact_to_target(params.GENESIS_BITS)
    block = build_genesis_block()
    print(f"mining genesis: nBits={params.GENESIS_BITS:#010x} nTime={block.n_time}")
    t0 = time.time()
    nonce = 0
    while True:
        block.n_nonce = nonce
        if block.get_hash() <= target:
            break
        nonce += 1
    elapsed = time.time() - t0
    ghash = uint256_to_hex(block.get_hash())
    merkle = uint256_to_hex(block.hash_merkle_root)
    print(f"found nonce={nonce} in {elapsed:.2f}s ({nonce / max(elapsed, 1e-9):.0f} h/s)")
    print(f"hash   = {ghash}")
    print(f"merkle = {merkle}")

    params_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "bitcoin", "params.py")
    with open(params_path, "r", encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r"GENESIS_NONCE = .*", f"GENESIS_NONCE = {nonce}", src, count=1)
    src = re.sub(r"GENESIS_HASH = .*", f'GENESIS_HASH = "{ghash}"', src, count=1)
    src = re.sub(r"GENESIS_MERKLE_ROOT = .*",
                 f'GENESIS_MERKLE_ROOT = "{merkle}"', src, count=1)
    with open(params_path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"patched {params_path}")


if __name__ == "__main__":
    main()
