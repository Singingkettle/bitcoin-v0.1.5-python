"""CSendDialog. The From/Message fields of the original served IP-to-IP
payments and were greyed out for bitcoin addresses — omitted here."""

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from bitcoin import base58
from bitcoin.util import parse_money


class SendDialog(QDialog):
    def __init__(self, wallet, parent=None):
        super().__init__(parent)
        self.wallet = wallet
        self.setWindowTitle("Send Coins")
        self.setMinimumWidth(450)

        grid = QGridLayout(self)
        grid.addWidget(QLabel("Pay To:"), 0, 0)
        self.edit_address = QLineEdit()
        grid.addWidget(self.edit_address, 0, 1)

        buttons = QHBoxLayout()
        btn_paste = QPushButton("&Paste")
        btn_paste.clicked.connect(self._paste)
        btn_book = QPushButton("Address &Book...")
        btn_book.clicked.connect(self._pick_from_book)
        buttons.addWidget(btn_paste)
        buttons.addWidget(btn_book)
        buttons.addStretch()
        grid.addLayout(buttons, 1, 1)

        grid.addWidget(QLabel("Amount:"), 2, 0)
        self.edit_amount = QLineEdit()
        self.edit_amount.setPlaceholderText("0.00")
        grid.addWidget(self.edit_amount, 2, 1)

        box = QDialogButtonBox()
        self.btn_send = box.addButton("&Send", QDialogButtonBox.AcceptRole)
        box.addButton(QDialogButtonBox.Cancel)
        box.accepted.connect(self._send)
        box.rejected.connect(self.reject)
        grid.addWidget(box, 3, 0, 1, 2)

    def _paste(self):
        self.edit_address.setText(QApplication.clipboard().text().strip())

    def _pick_from_book(self):
        from .addressbook import AddressBookDialog

        dialog = AddressBookDialog(self.wallet, self, picking=True)
        if dialog.exec() == QDialog.Accepted and dialog.selected_address:
            self.edit_address.setText(dialog.selected_address)

    def _send(self):
        address = self.edit_address.text().strip()
        if not base58.is_valid_address(address):
            QMessageBox.warning(self, "Send Coins", "Invalid bitcoin address")
            return
        try:
            amount = parse_money(self.edit_amount.text())
        except ValueError:
            QMessageBox.warning(self, "Send Coins", "Error parsing amount")
            return
        ok, result = self.wallet.send_money(address, amount)
        if not ok:
            QMessageBox.warning(self, "Send Coins", result)
            return
        QMessageBox.information(self, "Send Coins", "Payment sent!")
        self.accept()
