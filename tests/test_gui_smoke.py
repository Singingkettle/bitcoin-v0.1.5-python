"""GUI smoke test on the offscreen Qt platform: the main window builds,
polls status, lists transactions, and shuts the node down cleanly."""

import os
import socket

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from bitcoin import params
from bitcoin.config import Config
from bitcoin.node import Node
from tests.conftest import mine_block


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_mainwindow_smoke(app, tmp_path):
    from qt.mainwindow import MainWindow

    node = Node(Config([f"-datadir={tmp_path / 'gui'}", f"-port={_free_port()}"]))
    # a little history so the transaction list has rows
    for _ in range(3):
        key = node.wallet.generate_new_key(save=False)
        assert node.chain.process_block(mine_block(node.chain, key=key))
    node.start()

    window = MainWindow(node)
    window._poll()
    assert "3 blocks" in window.label_status.text()
    assert "0 connections" in window.label_status.text()
    # three generated (immature) transactions in the All tab
    assert window.models[0].rowCount() == 3
    assert window.models[0].rows[0][2] == "Generated"
    # immature generation -> zero balance
    assert window.label_balance.text() == "0.00"

    old_address = window.edit_address.text()
    window._change_address()
    assert window.edit_address.text() != old_address

    window.close()  # closeEvent stops the node


def test_send_dialog_validation(app, tmp_path):
    from qt.senddialog import SendDialog

    node = Node(Config([f"-datadir={tmp_path / 'gui2'}",
                        f"-port={_free_port()}", "-nolisten"]))
    dialog = SendDialog(node.wallet)
    dialog.edit_address.setText("not-an-address")
    dialog.edit_amount.setText("1.0")
    # invalid address: dialog stays open (no accept), no crash
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))

    # suppress the modal warning box for the test
    from PySide6.QtWidgets import QMessageBox

    orig = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    try:
        dialog._send()
        assert not accepted
        # a valid address but more than the balance: still rejected
        dialog.edit_address.setText(node.wallet.get_default_address())
        dialog._send()
        assert not accepted
    finally:
        QMessageBox.warning = orig
        node.wallet.save()
        node.chain.close()
