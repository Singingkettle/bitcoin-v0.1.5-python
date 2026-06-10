"""COptionsDialog — v0.1.5 had exactly one setting: the transaction fee."""

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
        self.setWindowTitle("Options")
        grid = QGridLayout(self)
        grid.addWidget(QLabel(
            "Optional transaction fee you give to the nodes that process "
            "your transactions."), 0, 0, 1, 2)
        grid.addWidget(QLabel("Transaction fee:"), 1, 0)
        self.edit_fee = QLineEdit(format_money(int(wallet.settings.get("fee", 0))))
        grid.addWidget(self.edit_fee, 1, 1)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        grid.addWidget(box, 2, 0, 1, 2)

    def _save(self):
        try:
            fee = parse_money(self.edit_fee.text())
        except ValueError:
            QMessageBox.warning(self, "Options", "Error parsing amount")
            return
        self.wallet.settings["fee"] = fee
        self.wallet.save()
        self.accept()
