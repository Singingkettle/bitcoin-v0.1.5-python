"""选项对话框——对应原版的 COptionsDialog。v0.1.5 里只有一个可设置项：交易手续费。"""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
)

from bitcoin.util import format_money, parse_money


class OptionsDialog(QDialog):
    def __init__(self, wallet, parent=None):
        super().__init__(parent)
        self.wallet = wallet
        self.setWindowTitle("选项")
        grid = QGridLayout(self)
        grid.addWidget(QLabel(
            "可选的交易手续费：你自愿付给帮你打包交易的节点（矿工）的小费。"), 0, 0, 1, 2)
        grid.addWidget(QLabel("交易手续费："), 1, 0)
        self.edit_fee = QLineEdit(format_money(wallet.transaction_fee))
        grid.addWidget(self.edit_fee, 1, 1)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        grid.addWidget(box, 2, 0, 1, 2)

    def _save(self):
        try:
            fee = parse_money(self.edit_fee.text())
        except ValueError:
            QMessageBox.warning(self, "选项", "金额格式错误")
            return
        self.wallet.set_transaction_fee(fee)
        self.accept()
