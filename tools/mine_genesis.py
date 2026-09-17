"""一次性工具：挖出私网的创世块，并把结果回填进 bitcoin/params.py。

    python tools/mine_genesis.py

只有在你修改了 params.py 里的创世块参数（时间戳文字、时间、难度）之后才需要重新运行。
注意：换了创世块 = 换了一条全新的链，旧的数据目录必须删掉。
"""

import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bitcoin import params
from bitcoin.blockchain import build_genesis_block
from bitcoin.serialize import uint256_to_hex


def main():
    target = params.compact_to_target(params.GENESIS_BITS)
    block = build_genesis_block()
    print(f"开始挖创世块：nBits={params.GENESIS_BITS:#010x} nTime={block.n_time}")
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
    print(f"找到 nonce={nonce}，用时 {elapsed:.2f} 秒（约 {nonce / max(elapsed, 1e-9):.0f} 次哈希/秒）")
    print(f"区块哈希   = {ghash}")
    print(f"默克尔根   = {merkle}")

    params_path = os.path.join(ROOT, "bitcoin", "params.py")
    with open(params_path, "r", encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r"GENESIS_NONCE = .*", f"GENESIS_NONCE = {nonce}", src, count=1)
    src = re.sub(r"GENESIS_HASH = .*", f'GENESIS_HASH = "{ghash}"', src, count=1)
    src = re.sub(r"GENESIS_MERKLE_ROOT = .*",
                 f'GENESIS_MERKLE_ROOT = "{merkle}"', src, count=1)
    with open(params_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print(f"已回填 {params_path}")


if __name__ == "__main__":
    main()
