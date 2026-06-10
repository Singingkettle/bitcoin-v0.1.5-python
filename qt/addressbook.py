"""CAddressBookDialog — stored names for other people's addresses, plus a
'Your Addresses' view of the wallet's own receiving addresses."""

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
        self.picking = picking
        self.selected_address: str | None = None
        self.setWindowTitle("Address Book")
        self.resize(520, 380)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.table_book = self._make_table()
        self.table_own = self._make_table()
        self.tabs.addTab(self.table_book, "Address Book")
        self.tabs.addTab(self.table_own, "Your Addresses")
        layout.addWidget(self.tabs)

        buttons = QHBoxLayout()
        btn_new = QPushButton("&New Address...")
        btn_new.clicked.connect(self._new_entry)
        btn_copy = QPushButton("&Copy to Clipboard")
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
        table.setHorizontalHeaderLabels(["Name", "Address"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 180)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        return table

    def refresh(self):
        own_addresses = {
            base58.pubkey_to_address(pub) for pub in self.wallet.keys
        }
        book = [(label, addr) for addr, label in
                sorted(self.wallet.address_book.items(), key=lambda kv: kv[1])
                if addr not in own_addresses]
        own = [(self.wallet.address_book.get(addr, ""), addr)
               for addr in sorted(own_addresses)]
        # the default receiving address first
        default = self.wallet.get_default_address()
        own.sort(key=lambda row: row[1] != default)
        for table, rows in ((self.table_book, book), (self.table_own, own)):
            table.setRowCount(len(rows))
            for r, (label, addr) in enumerate(rows):
                table.setItem(r, 0, QTableWidgetItem(label))
                table.setItem(r, 1, QTableWidgetItem(addr))

    def _current_table(self) -> QTableWidget:
        return self.table_own if self.tabs.currentIndex() == 1 else self.table_book

    def _current_address(self) -> str | None:
        table = self._current_table()
        row = table.currentRow()
        return table.item(row, 1).text() if row >= 0 else None

    def _new_entry(self):
        if self.tabs.currentIndex() == 1:
            # a brand-new receiving key, labelled
            label, ok = QInputDialog.getText(self, "New Receiving Address",
                                             "Label:")
            if not ok:
                return
            key = self.wallet.generate_new_key()
            addr = base58.pubkey_to_address(key.get_pubkey())
            if label:
                self.wallet.set_address_label(addr, label)
        else:
            addr, ok = QInputDialog.getText(self, "New Address",
                                            "Bitcoin address:")
            if not ok or not base58.is_valid_address(addr.strip()):
                return
            label, ok = QInputDialog.getText(self, "New Address", "Name:")
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
