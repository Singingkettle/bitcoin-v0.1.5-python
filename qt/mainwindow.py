"""CMainFrame from ui.cpp, in Qt.

Layout follows the 2009 original: a Send Coins / Address Book toolbar,
Your Address line with Copy and New buttons, the balance, the transaction
list (with the All/Sent/Received tabs that strictly arrived in 0.2.x —
a deliberate, documented anachronism), and the status bar reading
"  N connections     N blocks     N transactions".
"""

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

ABOUT_TEXT = """<b>Bitcoin v0.1.5 ALPHA (Python re-creation)</b><br><br>
Copyright (c) 2009 Satoshi Nakamoto (the original).<br>
This is an educational pure-Python re-creation of the v0.1.5 codebase,
running its own private network. It is a toy: do not use it for anything
of value.<br><br>
Distributed under the MIT/X11 software license."""


class MainWindow(QMainWindow):
    def __init__(self, node):
        super().__init__()
        self.node = node
        self.wallet = node.wallet
        self.setWindowTitle("Bitcoin v0.1.5 (Python)")
        self.resize(760, 520)

        self._wallet_dirty = True  # repainted by the poll timer
        self.wallet.changed_listeners.append(self._mark_dirty)

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # the original UI refreshed off timers as well
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(500)
        self._poll()

    # ------------------------------------------------------------- chrome
    def _build_menu(self):
        menu_file = self.menuBar().addMenu("&File")
        action_exit = QAction("E&xit", self)
        action_exit.triggered.connect(self.close)
        menu_file.addAction(action_exit)

        menu_options = self.menuBar().addMenu("&Options")
        self.action_generate = QAction("&Generate Bitcoins", self)
        self.action_generate.setCheckable(True)
        self.action_generate.setChecked(self.node.miner.running)
        self.action_generate.toggled.connect(self._toggle_generate)
        menu_options.addAction(self.action_generate)
        action_change = QAction("&Change Your Address...", self)
        action_change.triggered.connect(self._change_address)
        menu_options.addAction(action_change)
        action_options = QAction("&Options...", self)
        action_options.triggered.connect(self._show_options)
        menu_options.addAction(action_options)

        menu_help = self.menuBar().addMenu("&Help")
        action_about = QAction("&About...", self)
        action_about.triggered.connect(
            lambda: QMessageBox.about(self, "About Bitcoin", ABOUT_TEXT))
        menu_help.addAction(action_about)

    def _build_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(toolbar)
        action_send = QAction("Send Coins", self)
        action_send.triggered.connect(self._show_send)
        toolbar.addAction(action_send)
        action_book = QAction("Address Book", self)
        action_book.triggered.connect(self._show_address_book)
        toolbar.addAction(action_book)

    def _build_central(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        # Your Address row
        row = QHBoxLayout()
        row.addWidget(QLabel("Your Bitcoin Address:"))
        self.edit_address = QLineEdit(self.wallet.get_default_address())
        self.edit_address.setReadOnly(True)
        row.addWidget(self.edit_address, 1)
        btn_copy = QPushButton("&Copy to Clipboard")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(
            self.edit_address.text()))
        row.addWidget(btn_copy)
        btn_new = QPushButton("&New...")
        btn_new.clicked.connect(self._change_address)
        row.addWidget(btn_new)
        layout.addLayout(row)

        # Balance
        balance_row = QHBoxLayout()
        balance_row.addWidget(QLabel("Balance:"))
        self.label_balance = QLabel("0.00")
        font = self.label_balance.font()
        font.setBold(True)
        self.label_balance.setFont(font)
        balance_row.addWidget(self.label_balance)
        balance_row.addStretch()
        layout.addLayout(balance_row)

        # transaction tabs (anachronism from 0.2.x, kept by request)
        self.tabs = QTabWidget()
        self.models = []
        for title, mode in (("All Transactions", ALL), ("Sent", SENT),
                            ("Received", RECEIVED)):
            model = TransactionModel(self.wallet, self.node.chain, mode)
            view = QTableView()
            view.setModel(model)
            view.setSelectionBehavior(QTableView.SelectRows)
            view.setEditTriggers(QTableView.NoEditTriggers)
            view.verticalHeader().setVisible(False)
            view.horizontalHeader().setSectionResizeMode(
                2, QHeaderView.Stretch)
            self.models.append(model)
            self.tabs.addTab(view, title)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def _build_statusbar(self):
        self.label_status = QLabel()
        self.statusBar().addWidget(self.label_status)
        self.label_generating = QLabel()
        self.statusBar().addPermanentWidget(self.label_generating)

    # ------------------------------------------------------------- actions
    def _show_send(self):
        SendDialog(self.wallet, self).exec()

    def _show_address_book(self):
        AddressBookDialog(self.wallet, self).exec()

    def _show_options(self):
        OptionsDialog(self.wallet, self).exec()

    def _change_address(self):
        self.wallet.set_new_default_key()
        self.edit_address.setText(self.wallet.get_default_address())

    def _toggle_generate(self, checked: bool):
        if checked:
            self.node.miner.start()
        else:
            self.node.miner.stop()

    # --------------------------------------------------------------- update
    def _mark_dirty(self):
        # called from network/miner threads: only flip a flag, never touch Qt
        self._wallet_dirty = True

    def _poll(self):
        st = self.node.status()
        self.label_status.setText(
            f"  {st['connections']} connection"
            f"{'s' if st['connections'] != 1 else ''}     "
            f"{st['blocks']} blocks     "
            f"{st['transactions']} transactions")
        self.label_generating.setText(
            "Generating" if st["generating"] else "")
        self.label_balance.setText(format_money(st["balance"]))
        # confirmation counts change with every block, so refresh the list
        # on new blocks as well as on wallet changes
        height_changed = st["blocks"] != getattr(self, "_last_height", -1)
        self._last_height = st["blocks"]
        if self._wallet_dirty or height_changed:
            self._wallet_dirty = False
            for model in self.models:
                model.refresh()

    def closeEvent(self, event):
        self.timer.stop()
        self.node.stop()
        event.accept()
