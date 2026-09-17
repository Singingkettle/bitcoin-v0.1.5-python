"""发送对话框——对应原版的 CSendDialog。
原版还有"发件人 / 留言"两栏，那是给"按 IP 地址直接付款"用的，本项目没有实现该功能，故省略。"""

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

from bitcoin.util import parse_money


class SendDialog(QDialog):
    def __init__(self, wallet, parent=None):
        super().__init__(parent)
        self.wallet = wallet
        self.setWindowTitle("发送比特币")
        self.setMinimumWidth(480)

        grid = QGridLayout(self)
        grid.addWidget(QLabel("收款地址："), 0, 0)
        self.edit_address = QLineEdit()
        grid.addWidget(self.edit_address, 0, 1)

        buttons = QHBoxLayout()
        btn_paste = QPushButton("粘贴(&P)")
        btn_paste.clicked.connect(self._paste)
        btn_book = QPushButton("地址簿(&B)...")
        btn_book.clicked.connect(self._pick_from_book)
        buttons.addWidget(btn_paste)
        buttons.addWidget(btn_book)
        buttons.addStretch()
        grid.addLayout(buttons, 1, 1)

        grid.addWidget(QLabel("金额："), 2, 0)
        self.edit_amount = QLineEdit()
        self.edit_amount.setPlaceholderText("0.00")
        grid.addWidget(self.edit_amount, 2, 1)

        box = QDialogButtonBox()
        box.addButton("发送(&S)", QDialogButtonBox.AcceptRole)
        box.addButton("取消", QDialogButtonBox.RejectRole)
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
        try:
            amount = parse_money(self.edit_amount.text())
        except ValueError:
            QMessageBox.warning(self, "发送比特币", "金额格式错误")
            return
        ok, result = self.wallet.send_money(self.edit_address.text().strip(), amount)
        if not ok:
            QMessageBox.warning(self, "发送比特币", result)
            return
        QMessageBox.information(self, "发送中...", "付款已发出")
        self.accept()
