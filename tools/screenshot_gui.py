"""离屏渲染主窗口并保存成 PNG（README 里的截图就是用它生成的）。

    python tools/screenshot_gui.py [输出文件]

Windows 上想得到带正常字体的截图，请先设置环境变量 QT_QPA_PLATFORM=windows。
"""

import logging
import os
import socket
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication

from bitcoin import params
from bitcoin.config import Config
from bitcoin.node import Node
from tests.conftest import mine_block


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "screenshot.png")
    logging.disable(logging.DEBUG)
    datadir = tempfile.mkdtemp(prefix="btc_shot_")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    node = Node(Config([f"-datadir={datadir}", f"-port={port}", "-nolisten"]))
    for _ in range(125):                        # 挖 125 个块，让最早的几笔挖矿所得成熟
        key = node.wallet.generate_new_key()
        assert node.chain.process_block(mine_block(node.chain, key=key))
    node.wallet.set_address_label("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "中本聪的创世地址")
    print("发送：", node.wallet.send_money("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", 10 * params.COIN))

    app = QApplication([])
    from qt.mainwindow import MainWindow

    window = MainWindow(node)
    window.resize(900, 560)
    window._poll()
    window.grab().save(out)
    print("已保存", out)
    window.timer.stop()
    node.stop()


if __name__ == "__main__":
    main()
