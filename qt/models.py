"""Transaction list model — the Status | Date | Description | Debit |
Credit columns of CMainFrame's wxListCtrl, as a Qt table model."""

import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from bitcoin import base58, params
from bitcoin.script import TX_PUBKEY, TX_PUBKEYHASH, solver
from bitcoin.util import format_money

COLUMNS = ["Status", "Date", "Description", "Debit", "Credit"]

ALL, SENT, RECEIVED = range(3)


def _txout_address(txout) -> str:
    match = solver(txout.script_pubkey)
    if match is None:
        return "(unknown)"
    kind, data = match
    if kind == TX_PUBKEY:
        return base58.pubkey_to_address(data)
    if kind == TX_PUBKEYHASH:
        return base58.hash160_to_address(data)
    return "(unknown)"


class TransactionModel(QAbstractTableModel):
    def __init__(self, wallet, chain, mode=ALL, parent=None):
        super().__init__(parent)
        self.wallet = wallet
        self.chain = chain
        self.mode = mode
        self.rows: list[tuple] = []
        self.refresh()

    # --- data assembly -------------------------------------------------
    def _row_for(self, wtx):
        tx = wtx.tx
        credit = self.wallet.get_credit(tx)
        debit = self.wallet.get_debit(tx)
        depth = self.wallet.get_depth(wtx)

        if tx.is_coinbase():
            maturity = self.wallet.blocks_to_maturity(wtx)
            if depth == 0:
                status = "(not accepted)"
            elif maturity > 0:
                status = f"(immature, {maturity} more blocks)"
            else:
                status = f"{depth} confirmations"
            description = "Generated"
        else:
            if depth == 0:
                status = "0/unconfirmed"
            else:
                status = f"{depth} confirmations"
            if debit > 0:
                # our send: show the first output that isn't ours (the payee)
                payee = next((o for o in tx.vout
                              if not self.wallet.is_mine_txout(o)), None)
                description = (f"To: {_txout_address(payee)}"
                               if payee is not None else "Payment to yourself")
            else:
                ours = next((o for o in tx.vout
                             if self.wallet.is_mine_txout(o)), None)
                description = (f"Received with: {_txout_address(ours)}"
                               if ours is not None else "Received")

        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(wtx.time_received))
        debit_str = format_money(-debit + (credit if debit > 0 else 0)) \
            if debit > 0 else ""
        credit_str = format_money(credit) if debit == 0 and credit > 0 else ""
        is_sent = debit > 0
        return (status, date, description, debit_str, credit_str, is_sent)

    def refresh(self):
        rows = []
        for wtx in self.wallet.transactions_newest_first():
            row = self._row_for(wtx)
            if self.mode == SENT and not row[5]:
                continue
            if self.mode == RECEIVED and row[5]:
                continue
            rows.append(row[:5])
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    # --- QAbstractTableModel -------------------------------------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.DisplayRole:
            return self.rows[index.row()][index.column()]
        if role == Qt.TextAlignmentRole and index.column() >= 3:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None
