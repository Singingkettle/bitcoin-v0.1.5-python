"""带图形界面的节点。

  python run_gui.py -datadir=data\\nodeA -port=18444
  python run_gui.py -datadir=data\\nodeB -port=18445 -connect=127.0.0.1:18444
"""

import logging
import sys

from PySide6.QtWidgets import QApplication

from bitcoin.config import Config
from bitcoin.node import Node
from qt.mainwindow import MainWindow


def main():
    config = Config(sys.argv[1:])
    node = Node(config)
    # 图形界面下不往控制台刷日志（debug.log 里仍然有完整记录）
    for handler in node.log.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler):
            handler.setLevel(logging.WARNING)
    node.start()
    app = QApplication(sys.argv)
    window = MainWindow(node)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
