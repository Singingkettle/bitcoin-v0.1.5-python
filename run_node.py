"""无界面节点。

  python run_node.py -datadir=data\\nodeA -port=18444 -gen
  python run_node.py -datadir=data\\nodeB -port=18445 -connect=127.0.0.1:18444

按 Ctrl+C 退出。
"""

import sys
import time

from bitcoin.config import Config
from bitcoin.node import Node
from bitcoin.util import format_money


def main():
    config = Config(sys.argv[1:])
    node = Node(config)
    node.start()
    print(f"节点已启动：端口 {config.port}，数据目录 {config.datadir}")
    print(f"你的地址：{node.wallet.get_default_address()}")
    try:
        while True:
            time.sleep(5)
            st = node.status()
            print(f"  {st['connections']} 个连接     {st['blocks']} 个区块     "
                  f"{st['transactions']} 笔交易     余额 {format_money(st['balance'])}"
                  f"{'     正在挖矿' if st['generating'] else ''}")
    except KeyboardInterrupt:
        print("正在关闭...")
        node.stop()


if __name__ == "__main__":
    main()
