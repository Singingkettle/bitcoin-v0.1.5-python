"""交易列表的数据模型——对应原版 ui.cpp 里 CMainFrame::InsertTransaction 的那一大段逻辑。

五列与原版一致：状态 | 日期 | 描述 | 支出 | 收入。
一笔钱包交易怎么显示，取决于它对我来说是"净收入"、"付给自己"还是"付给别人"。
"""

import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from bitcoin import base58
from bitcoin.script import TX_PUBKEY, solver
from bitcoin.util import format_money

COLUMNS = ["状态", "日期", "描述", "支出", "收入"]

ALL, SENT, RECEIVED = range(3)


def txout_address(txout) -> str:
    """从输出脚本里取出收款地址（两种标准模板都支持）。"""
    match = solver(txout.script_pubkey)
    if match is None:
        return "(非标准脚本)"
    kind, data = match
    if kind == TX_PUBKEY:
        return base58.pubkey_to_address(data)
    return base58.hash160_to_address(data)


def format_tx_status(wallet, wtx) -> str:
    """FormatTxStatus。"""
    depth = wallet.get_depth(wtx)
    if not wallet.is_final(wtx):
        return f"还需 {wtx.tx.n_lock_time - wallet.chain.best_height} 个区块才定稿"
    if depth < 6:
        return f"{depth}/未确认"
    return f"{depth} 个确认"


def rows_for(wallet, wtx) -> list[tuple]:
    """一笔钱包交易 -> 若干显示行，每行是 (状态, 日期, 描述, 支出, 收入, 是否支出)。"""
    tx = wtx.tx
    credit = wallet.get_available_credit(wtx)
    debit = wallet.get_debit(tx)
    net = credit - debit
    status = format_tx_status(wallet, wtx)
    date = time.strftime("%Y-%m-%d %H:%M", time.localtime(wtx.time_received))
    book = wallet.address_book

    if net > 0 or tx.is_coinbase():
        # ---- 收入 ----
        if tx.is_coinbase():
            description = "挖矿所得"
            if credit == 0:
                unmatured = wallet.get_credit(tx)
                if wallet.get_depth(wtx) > 0:
                    description += (f"（{format_money(unmatured)} 将在 "
                                    f"{wallet.blocks_to_maturity(wtx)} 个区块后成熟）")
                else:
                    description += "（未被网络接受）"
        else:
            description = "收款"
            for txout in tx.vout:
                if wallet.is_mine_txout(txout):
                    addr = txout_address(txout)
                    description = f"来自：未知，收款地址：{addr}"
                    if book.get(addr):
                        description += f"（{book[addr]}）"
                    break
        return [(status, date, description, "", format_money(net, True), False)]

    all_from_me = all(wallet.is_mine_txin(txin) for txin in tx.vin)
    all_to_me = all(wallet.is_mine_txout(txout) for txout in tx.vout)

    if all_from_me and all_to_me:
        value = tx.vout[0].n_value
        return [(status, date, "付给自己",
                 format_money(net - value, True), format_money(value, True), True)]

    if all_from_me:
        # ---- 支出：每个付给别人的输出单独一行；手续费算在第一行里 ----
        fee = debit - tx.get_value_out()
        rows = []
        for n_out, txout in enumerate(tx.vout):
            if wallet.is_mine_txout(txout):
                continue            # 找零，不显示
            addr = txout_address(txout)
            description = "付给："
            if book.get(addr):
                description += book[addr] + " "
            description += addr
            value = txout.n_value
            if n_out == 0 and fee > 0:
                value += fee
            rows.append((status, date, description,
                         format_money(-value, True), "", True))
        return rows

    # ---- 混合情况：输入里只有一部分是我的，没法拆开显示 ----
    return [(status, date, "（混合交易）", format_money(net, True), "", True)]


class TransactionModel(QAbstractTableModel):
    def __init__(self, wallet, mode=ALL, parent=None):
        super().__init__(parent)
        self.wallet = wallet
        self.mode = mode
        self.rows: list[tuple] = []
        self.refresh()

    def _sort_key(self, wtx):
        """和原版一样：还没进块的排最上面，其余按区块高度从新到旧。"""
        pindex = self.wallet.chain.map_block_index.get(wtx.hash_block)
        height = pindex.n_height if pindex is not None else 2**31
        return (height, 1 if wtx.tx.is_coinbase() else 0, wtx.time_received)

    def refresh(self):
        rows = []
        wtxs = sorted(self.wallet.transactions_newest_first(),
                      key=self._sort_key, reverse=True)
        for wtx in wtxs:
            for row in rows_for(self.wallet, wtx):
                if self.mode == SENT and not row[5]:
                    continue
                if self.mode == RECEIVED and row[5]:
                    continue
                rows.append(row[:5])
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    # -------------------------------------------- QAbstractTableModel 接口
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
