"""Node + PySide6 GUI.

  python run_gui.py -datadir=data\\nodeA -port=18444
  python run_gui.py -datadir=data\\nodeB -port=18445 -connect=127.0.0.1:18444
"""

import sys

from PySide6.QtWidgets import QApplication

from bitcoin.config import Config
from bitcoin.node import Node
from qt.mainwindow import MainWindow


def main():
    config = Config(sys.argv[1:])
    node = Node(config)
    node.start()
    app = QApplication(sys.argv)
    app.setApplicationName("Bitcoin v0.1.5 (Python)")
    window = MainWindow(node)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
