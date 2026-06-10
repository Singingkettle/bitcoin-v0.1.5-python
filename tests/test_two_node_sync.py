"""Two real nodes over localhost sockets: initial block download,
transaction broadcast, remote mining, confirmation propagation."""

import socket
import time

import pytest

from bitcoin import params
from bitcoin.config import Config
from bitcoin.node import Node
from tests.conftest import mine_block


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(predicate, timeout=30, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


@pytest.fixture
def two_nodes(tmp_path):
    port_a, port_b = _free_port(), _free_port()
    node_a = Node(Config([f"-datadir={tmp_path / 'A'}", f"-port={port_a}"]))
    node_b = Node(Config([f"-datadir={tmp_path / 'B'}", f"-port={port_b}",
                          f"-connect=127.0.0.1:{port_a}"]))
    yield node_a, node_b
    node_b.stop()
    node_a.stop()


def test_initial_sync_tx_broadcast_and_remote_confirm(two_nodes):
    node_a, node_b = two_nodes

    # A mines 125 blocks to its own wallet before networking starts
    # (125 so the earliest coinbases pass the wallet's 120-conf maturity)
    for _ in range(125):
        key = node_a.wallet.generate_new_key(save=False)
        assert node_a.chain.process_block(mine_block(node_a.chain, key=key))
    assert node_a.chain.best_height == 125
    assert node_a.wallet.get_balance() >= 5 * 50 * params.COIN

    node_a.start()
    node_b.start()

    # initial block download: B catches up over the socket
    _wait(lambda: node_b.chain.best_height == 125, what="B to sync 125 blocks")
    assert (node_b.chain.best_index.hash == node_a.chain.best_index.hash)
    _wait(lambda: node_a.net.connection_count() == 1, what="A to see B")

    # A pays B; the tx gossips into B's pool and B's wallet sees the credit
    ok, txid = node_a.wallet.send_money(node_b.wallet.get_default_address(),
                                        10 * params.COIN)
    assert ok, txid
    _wait(lambda: len(node_b.mempool) == 1, what="tx to reach B's mempool")
    assert node_b.wallet.get_balance() == 10 * params.COIN

    # B mines the confirmation; the block gossips back to A
    block, _ = node_b.miner.create_new_block()
    assert len(block.vtx) == 2
    target = params.compact_to_target(block.n_bits)
    nonce = 0
    while True:
        block.n_nonce = nonce
        if block.get_hash() <= target:
            break
        nonce += 1
    assert node_b.chain.process_block(block)

    _wait(lambda: node_a.chain.best_height == 126, what="A to receive B's block")
    # the confirmed tx is in the chain on both sides and out of A's pool
    from bitcoin.serialize import uint256_from_hex
    assert node_a.chain.get_tx_depth(uint256_from_hex(txid)) == 1
    assert node_b.chain.get_tx_depth(uint256_from_hex(txid)) == 1
    _wait(lambda: len(node_a.mempool) == 0, what="A's pool to drop the confirmed tx")
