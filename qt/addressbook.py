"""地址簿——对应原版的 CAddressBookDialog。
"地址簿"页是别人的地址（给它们起名字方便转账）；"我的地址"页是钱包里自己的收款地址。"""

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from bitcoin import base58


class AddressBookDialog(QDialog):
    def __init__(self, wallet, parent=None, picking=False):
        super().__init__(parent)
        self.wallet = wallet
        self.picking = picking              # 是不是从"发送"对话框里打开、用来挑地址的
        self.selected_address: str | None = None
        self.setWindowTitle("地址簿")
        self.resize(560, 380)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.table_book = self._make_table()
        self.table_own = self._make_table()
        self.tabs.addTab(self.table_book, "地址簿")
        self.tabs.addTab(self.table_own, "我的地址")
        layout.addWidget(self.tabs)

        buttons = QHBoxLayout()
        btn_new = QPushButton("新建(&N)...")
        btn_new.clicked.connect(self._new_entry)
        btn_copy = QPushButton("复制到剪贴板(&C)")
        btn_copy.clicked.connect(self._copy)
        buttons.addWidget(btn_new)
        buttons.addWidget(btn_copy)
        buttons.addStretch()
        layout.addLayout(buttons)

        box = QDialogButtonBox(QDialogButtonBox.Ok)
        box.accepted.connect(self._ok)
        layout.addWidget(box)

        if picking:
            self.table_book.doubleClicked.connect(lambda _: self._ok())
        self.refresh()

    def _make_table(self) -> QTableWidget:
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["名称", "地址"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 180)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        return table

    def refresh(self):
        own_addresses = {base58.pubkey_to_address(pub) for pub in self.wallet.keys}
        book = [(label, addr) for addr, label in
                sorted(self.wallet.address_book.items(), key=lambda kv: kv[1])
                if addr not in own_addresses]
        default = self.wallet.get_default_address()
        own = sorted(((self.wallet.address_book.get(addr, ""), addr)
                      for addr in own_addresses),
                     key=lambda row: (row[1] != default, row[1]))  # 当前收款地址排第一
        for table, rows in ((self.table_book, book), (self.table_own, own)):
            table.setRowCount(len(rows))
            for r, (label, addr) in enumerate(rows):
                table.setItem(r, 0, QTableWidgetItem(label))
                table.setItem(r, 1, QTableWidgetItem(addr))

    def _current_address(self) -> str | None:
        table = self.table_own if self.tabs.currentIndex() == 1 else self.table_book
        row = table.currentRow()
        return table.item(row, 1).text() if row >= 0 else None

    def _new_entry(self):
        if self.tabs.currentIndex() == 1:
            label, ok = QInputDialog.getText(self, "新建收款地址", "备注名：")
            if not ok:
                return
            key = self.wallet.generate_new_key()
            self.wallet.set_address_label(
                base58.pubkey_to_address(key.get_pubkey()), label)
        else:
            addr, ok = QInputDialog.getText(self, "新建地址", "比特币地址：")
            if not ok or not base58.is_valid_address(addr.strip()):
                return
            label, ok = QInputDialog.getText(self, "新建地址", "名称：")
            if not ok:
                return
            self.wallet.set_address_label(addr.strip(), label)
        self.refresh()

    def _copy(self):
        addr = self._current_address()
        if addr:
            QApplication.clipboard().setText(addr)

    def _ok(self):
        if self.picking:
            self.selected_address = self._current_address()
        self.accept()
