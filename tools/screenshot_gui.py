"""Render the main window offscreen and save a PNG (used for the README).

  python tools/screenshot_gui.py [outfile]
"""

import os
import socket
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from PySide6.QtWidgets import QApplication

from bitcoin import params
from bitcoin.config import Config
from bitcoin.node import Node
from conftest import mine_block


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "screenshot.png")
    datadir = tempfile.mkdtemp(prefix="btc_shot_")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    node = Node(Config([f"-datadir={datadir}", f"-port={port}", "-nolisten"]))
    node.log.setLevel("WARNING")
    for _ in range(125):
        key = node.wallet.generate_new_key(save=False)
        assert node.chain.process_block(mine_block(node.chain, key=key))
    ok, result = node.wallet.send_money("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
                                        10 * params.COIN)
    print("send:", ok, result)

    app = QApplication([])
    from qt.mainwindow import MainWindow

    window = MainWindow(node)
    window.resize(860, 560)
    window._poll()
    window.grab().save(out)
    print("saved", out)
    node.stop()


if __name__ == "__main__":
    main()
