"""图形界面测试。用 Qt 的 offscreen 平台运行，不需要真的显示器。"""

import os
import shutil
import json

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from bitcoin import params
from bitcoin.config import Config
from bitcoin.key import CKey
from bitcoin.node import Node
from tests.conftest import free_port, mine_block

COIN = params.COIN


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def rich_node(seed_chain_dir, tmp_path):
    """钱包里已经有 11 枚成熟 coinbase 的节点。"""
    datadir = tmp_path / "gui"
    shutil.copytree(seed_chain_dir, datadir)
    node = Node(Config([f"-datadir={datadir}", f"-port={free_port()}", "-nolisten"]))
    keys = json.loads((datadir / "keys.json").read_text())
    for h in range(1, 15):
        node.wallet.add_key(CKey.from_secret(bytes.fromhex(keys[str(h)])))
    node.wallet.rescan()
    node.start()
    yield node
    node.stop()


@pytest.fixture
def silent_boxes(monkeypatch):
    """把会阻塞的模态消息框换成"记录一下就返回"。"""
    shown = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda parent, title, text: shown.append(("warning", text))))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda parent, title, text: shown.append(("info", text))))
    return shown


def test_main_window_shows_status_balance_and_address(app, rich_node):
    from qt.mainwindow import MainWindow
    window = MainWindow(rich_node)
    try:
        window._poll()
        assert "0 个连接" in window.label_status.text()
        assert "130 个区块" in window.label_status.text()
        assert "14 笔交易" in window.label_status.text()
        assert window.label_balance.text() == "550.00"              # 11 枚成熟的 coinbase
        assert window.edit_address.text() == rich_node.wallet.get_default_address()
        assert window.edit_address.isReadOnly()
        assert [window.tabs.tabText(i) for i in range(3)] == ["全部交易", "已发送", "已接收"]
    finally:
        window.timer.stop()


def test_transaction_list_rows(app, rich_node, silent_boxes):
    from qt.models import ALL, RECEIVED, SENT, TransactionModel
    wallet = rich_node.wallet
    model = TransactionModel(wallet, ALL)
    assert model.columnCount() == 5
    assert [model.headerData(i, __import__("PySide6.QtCore").QtCore.Qt.Horizontal)
            for i in range(5)] == ["状态", "日期", "描述", "支出", "收入"]
    assert model.rowCount() == 14
    rows = {r[2]: r for r in model.rows}
    immature = [r for r in model.rows if "成熟" in r[2]]
    mature = [r for r in model.rows if r[2] == "挖矿所得"]
    assert len(immature) == 3 and len(mature) == 11
    assert all(r[4] == "+50.00" and r[3] == "" for r in mature)
    assert all(r[4] == "0.00" for r in immature)                    # 未成熟：暂不计入
    assert any("50.00 将在 1 个区块后成熟" in r[2] for r in immature)
    assert all("个确认" in r[0] for r in model.rows)

    # 付一笔钱给外部地址
    ok, _ = wallet.send_money("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", 10 * COIN)
    assert ok
    model.refresh()
    top = model.rows[0]                                             # 还没进块的排最上面
    assert top[0] == "0/未确认"
    assert top[2] == "付给：1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    assert (top[3], top[4]) == ("-10.00", "")
    assert TransactionModel(wallet, SENT).rowCount() == 1
    assert TransactionModel(wallet, RECEIVED).rowCount() == 14

    # 给地址起个名字，描述里会显示出来
    wallet.set_address_label("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "中本聪")
    model.refresh()
    assert model.rows[0][2] == "付给：中本聪 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"

    # 付给自己
    ok, _ = wallet.send_money(wallet.get_default_address(), 3 * COIN)
    assert ok
    model.refresh()
    assert any(r[2] == "付给自己" and r[4] == "+3.00" for r in model.rows)

    # 确认之后状态变化
    rich_node.miner.mine_one_block()
    model.refresh()
    sent_row = next(r for r in model.rows if r[2].startswith("付给：中本聪"))
    assert sent_row[0] == "1/未确认"                                 # 6 个确认以内仍标注"未确认"
    for _ in range(5):
        rich_node.miner.mine_one_block()
    model.refresh()
    sent_row = next(r for r in model.rows if r[2].startswith("付给：中本聪"))
    assert sent_row[0] == "6 个确认"


def test_received_payment_row(app, rich_node, tmp_path):
    from qt.models import RECEIVED, TransactionModel
    other = Node(Config([f"-datadir={tmp_path / 'other'}", f"-port={free_port()}", "-nolisten"]))
    try:
        addr = other.wallet.get_default_address()
        ok, _ = rich_node.wallet.send_money(addr, 12 * COIN)
        assert ok
        tx = rich_node.mempool.transactions()[0]
        other.wallet.add_to_wallet_if_mine(tx)                      # 模拟 other 从网络上收到这笔交易
        model = TransactionModel(other.wallet, RECEIVED)
        assert model.rowCount() == 1
        assert model.rows[0][2] == f"来自：未知，收款地址：{addr}"
        assert model.rows[0][4] == "+12.00"
    finally:
        other.stop()


def test_change_address_button(app, rich_node):
    from qt.mainwindow import MainWindow
    window = MainWindow(rich_node)
    try:
        old = window.edit_address.text()
        window._change_address()
        assert window.edit_address.text() != old
        assert window.edit_address.text() == rich_node.wallet.get_default_address()
    finally:
        window.timer.stop()


def test_generate_menu_toggles_the_miner(app, rich_node):
    from qt.mainwindow import MainWindow
    window = MainWindow(rich_node)
    try:
        assert not window.action_generate.isChecked()
        window.action_generate.setChecked(True)
        assert rich_node.miner.running
        window._poll()
        assert window.label_generating.text() == "正在挖矿"
        window.action_generate.setChecked(False)
        assert not rich_node.miner.running
    finally:
        window.timer.stop()


def test_wallet_callbacks_only_set_a_flag(app, rich_node):
    """钱包的回调发生在别的线程里，所以界面在回调中只允许改一个布尔标志。"""
    from qt.mainwindow import MainWindow
    window = MainWindow(rich_node)
    try:
        window._poll()
        window._wallet_dirty = False
        window._last_refresh = 0
        rich_node.wallet.set_address_label("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "x")
        assert window._wallet_dirty is True
        window._poll()
        assert window._wallet_dirty is False
    finally:
        window.timer.stop()


def test_send_dialog_validation_and_success(app, rich_node, silent_boxes):
    from qt.senddialog import SendDialog
    dialog = SendDialog(rich_node.wallet)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))

    for address, amount, fragment in [
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "abc", "金额格式"),
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "1.234", "金额格式"),
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "0", "金额"),
        ("bad-address", "1", "无效"),
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "99999", "余额"),
    ]:
        silent_boxes.clear()
        dialog.edit_address.setText(address)
        dialog.edit_amount.setText(amount)
        dialog._send()
        assert not accepted
        assert silent_boxes and silent_boxes[0][0] == "warning" and fragment in silent_boxes[0][1]
    assert len(rich_node.mempool) == 0

    silent_boxes.clear()
    dialog.edit_address.setText("  1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa ")
    dialog.edit_amount.setText("1,000.00".replace("1,000", "10"))
    dialog._send()
    assert accepted == [True]
    assert silent_boxes == [("info", "付款已发出")]
    assert len(rich_node.mempool) == 1


def test_options_dialog_saves_fee(app, rich_node, silent_boxes):
    from qt.optionsdialog import OptionsDialog
    dialog = OptionsDialog(rich_node.wallet)
    assert dialog.edit_fee.text() == "0.00"
    dialog.edit_fee.setText("junk")
    dialog._save()
    assert silent_boxes and rich_node.wallet.transaction_fee == 0
    dialog.edit_fee.setText("0.01")
    dialog._save()
    assert rich_node.wallet.transaction_fee == params.CENT
    assert OptionsDialog(rich_node.wallet).edit_fee.text() == "0.01"


def test_address_book_dialog(app, rich_node):
    from qt.addressbook import AddressBookDialog
    wallet = rich_node.wallet
    wallet.set_address_label("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "中本聪")
    dialog = AddressBookDialog(wallet, picking=True)
    assert dialog.table_book.rowCount() == 1
    assert dialog.table_book.item(0, 0).text() == "中本聪"
    assert dialog.table_own.rowCount() == len(wallet.keys)
    assert dialog.table_own.item(0, 1).text() == wallet.get_default_address()   # 当前地址排第一
    dialog.table_book.selectRow(0)
    dialog._ok()
    assert dialog.selected_address == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


def test_close_event_stops_the_node(app, tmp_path):
    from qt.mainwindow import MainWindow
    node = Node(Config([f"-datadir={tmp_path / 'closing'}", f"-port={free_port()}"]))
    node.start()
    assert node.chain.process_block(mine_block(node.chain))
    window = MainWindow(node)
    window.close()
    assert node.stop_event.is_set()
    assert node.chain.db.conn is None and node.wallet.db.conn is None
