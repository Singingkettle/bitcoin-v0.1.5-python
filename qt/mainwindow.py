"""主窗口——用 Qt 复刻原版 ui.cpp 里的 CMainFrame。

布局照着 2009 年的原版来：工具栏只有"发送"和"地址簿"两个按钮；一行"你的比特币地址"
带复制/新建按钮；余额；交易列表；状态栏显示"N 个连接  N 个区块  N 笔交易"。

【线程安全的唯一硬规则】钱包和区块链的监听器是在网络线程 / 矿工线程里被回调的，
而 Qt 的界面对象只允许在主线程里操作。所以回调里**只设置一个布尔标志**，
真正刷新界面的事由主线程的定时器来做。

（"全部 / 已发送 / 已接收"三个标签页严格来说是 0.2.x 才有的，这里按需求保留。）
"""

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableView,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from bitcoin.util import format_money

from .addressbook import AddressBookDialog
from .models import ALL, RECEIVED, SENT, TransactionModel
from .optionsdialog import OptionsDialog
from .senddialog import SendDialog

ABOUT_TEXT = """<b>Bitcoin v0.1.5 ALPHA（Python 复刻版）</b><br><br>
原版版权所有 (c) 2009 Satoshi Nakamoto（中本聪）。<br>
这是对 v0.1.5 源码的纯 Python 教学复刻，运行在自己的私有网络上。<br>
它是一个学习用的玩具，请勿用于任何有价值的用途。<br><br>
以 MIT/X11 许可证发布。"""


class MainWindow(QMainWindow):
    def __init__(self, node):
        super().__init__()
        self.node = node
        self.wallet = node.wallet
        self.setWindowTitle("Bitcoin v0.1.5（Python 复刻版）")
        self.resize(820, 540)

        self._wallet_dirty = True
        self._last_height = -1
        self._last_refresh = 0.0
        self.wallet.changed_listeners.append(self._mark_dirty)

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(500)
        self._poll()

    # ------------------------------------------------------------ 界面搭建
    def _build_menu(self):
        menu_file = self.menuBar().addMenu("文件(&F)")
        action_exit = QAction("退出(&X)", self)
        action_exit.triggered.connect(self.close)
        menu_file.addAction(action_exit)

        menu_options = self.menuBar().addMenu("选项(&O)")
        self.action_generate = QAction("生成比特币（挖矿）(&G)", self)
        self.action_generate.setCheckable(True)
        self.action_generate.setChecked(self.node.miner.running)
        self.action_generate.toggled.connect(self._toggle_generate)
        menu_options.addAction(self.action_generate)
        action_change = QAction("更换你的地址(&C)...", self)
        action_change.triggered.connect(self._change_address)
        menu_options.addAction(action_change)
        action_options = QAction("选项(&O)...", self)
        action_options.triggered.connect(lambda: OptionsDialog(self.wallet, self).exec())
        menu_options.addAction(action_options)

        menu_help = self.menuBar().addMenu("帮助(&H)")
        action_about = QAction("关于(&A)...", self)
        action_about.triggered.connect(
            lambda: QMessageBox.about(self, "关于 Bitcoin", ABOUT_TEXT))
        menu_help.addAction(action_about)

    def _build_toolbar(self):
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(toolbar)
        action_send = QAction("发送比特币", self)
        action_send.triggered.connect(lambda: SendDialog(self.wallet, self).exec())
        toolbar.addAction(action_send)
        action_book = QAction("地址簿", self)
        action_book.triggered.connect(lambda: AddressBookDialog(self.wallet, self).exec())
        toolbar.addAction(action_book)

    def _build_central(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        row = QHBoxLayout()
        row.addWidget(QLabel("你的比特币地址："))
        self.edit_address = QLineEdit(self.wallet.get_default_address())
        self.edit_address.setReadOnly(True)
        row.addWidget(self.edit_address, 1)
        btn_copy = QPushButton("复制到剪贴板(&C)")
        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.edit_address.text()))
        row.addWidget(btn_copy)
        btn_new = QPushButton("新建(&N)...")
        btn_new.clicked.connect(self._change_address)
        row.addWidget(btn_new)
        layout.addLayout(row)

        balance_row = QHBoxLayout()
        balance_row.addWidget(QLabel("余额："))
        self.label_balance = QLabel("0.00")
        font = self.label_balance.font()
        font.setBold(True)
        self.label_balance.setFont(font)
        balance_row.addWidget(self.label_balance)
        balance_row.addStretch()
        layout.addLayout(balance_row)

        self.tabs = QTabWidget()
        self.models = []
        for title, mode in (("全部交易", ALL), ("已发送", SENT), ("已接收", RECEIVED)):
            model = TransactionModel(self.wallet, mode)
            view = QTableView()
            view.setModel(model)
            view.setSelectionBehavior(QTableView.SelectRows)
            view.setEditTriggers(QTableView.NoEditTriggers)
            view.verticalHeader().setVisible(False)
            view.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
            view.setColumnWidth(0, 110)
            view.setColumnWidth(1, 130)
            self.models.append(model)
            self.tabs.addTab(view, title)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def _build_statusbar(self):
        self.label_status = QLabel()
        self.statusBar().addWidget(self.label_status)
        self.label_generating = QLabel()
        self.statusBar().addPermanentWidget(self.label_generating)

    # ---------------------------------------------------------------- 动作
    def _change_address(self):
        self.wallet.set_new_default_key()
        self.edit_address.setText(self.wallet.get_default_address())

    def _toggle_generate(self, checked: bool):
        if checked:
            self.node.miner.start()
        else:
            self.node.miner.stop()

    # ---------------------------------------------------------------- 刷新
    def _mark_dirty(self):
        # 这个函数会在网络线程 / 矿工线程里被调用：只改标志，绝不碰任何 Qt 对象
        self._wallet_dirty = True

    def _poll(self):
        st = self.node.status()
        self.label_status.setText(
            f"  {st['connections']} 个连接     {st['blocks']} 个区块     "
            f"{st['transactions']} 笔交易")
        self.label_generating.setText("正在挖矿" if st["generating"] else "")
        self.label_balance.setText(format_money(st["balance"]))

        # 每来一个新区块，所有交易的确认数都会变，所以高度变化时也要刷新列表；
        # 但挖矿时区块来得很快，刷新频率限制在每秒一次
        height_changed = st["blocks"] != self._last_height
        if ((self._wallet_dirty or height_changed)
                and time.monotonic() - self._last_refresh >= 1.0):
            self._wallet_dirty = False
            self._last_height = st["blocks"]
            self._last_refresh = time.monotonic()
            for model in self.models:
                model.refresh()

    def closeEvent(self, event):
        self.timer.stop()
        self.node.stop()
        event.accept()
