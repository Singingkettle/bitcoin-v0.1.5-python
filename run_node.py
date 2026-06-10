"""Headless node.

  python run_node.py -datadir=data\\nodeA -port=18444 [-gen]
  python run_node.py -datadir=data\\nodeB -port=18445 -connect=127.0.0.1:18444
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
    print(f"node listening on port {config.port}, datadir={config.datadir}")
    print(f"your address: {node.wallet.get_default_address()}")
    try:
        while True:
            time.sleep(5)
            st = node.status()
            print(f"  {st['connections']} connections     {st['blocks']} blocks     "
                  f"{st['transactions']} transactions     "
                  f"balance {format_money(st['balance'])}"
                  f"{'     generating' if st['generating'] else ''}")
    except KeyboardInterrupt:
        print("shutting down...")
        node.stop()


if __name__ == "__main__":
    main()
