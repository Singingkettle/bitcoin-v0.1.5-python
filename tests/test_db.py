"""存储层：区块文件、索引数据库的事务、钱包数据库。"""

import json

import pytest

from bitcoin import params
from bitcoin.blockchain import build_genesis_block
from bitcoin.db import BlockFile, BlockIndexDB, WalletDB


def test_block_file_format_and_roundtrip(tmp_path):
    bf = BlockFile(str(tmp_path))
    block = build_genesis_block()
    raw = block.serialized()
    pos1 = bf.append(raw)
    pos2 = bf.append(raw)
    assert pos1 == 0 and pos2 == 8 + len(raw)               # 每条记录 = 魔数4 + 长度4 + 区块
    content = (tmp_path / "blk0001.dat").read_bytes()
    assert content[:4] == params.MESSAGE_START
    assert int.from_bytes(content[4:8], "little") == len(raw)
    assert content[8:8 + len(raw)] == raw
    assert bf.read(pos1).get_hash() == bf.read(pos2).get_hash() == block.get_hash()


def test_block_file_detects_bad_offset(tmp_path):
    bf = BlockFile(str(tmp_path))
    bf.append(build_genesis_block().serialized())
    with pytest.raises(IOError):
        bf.read(3)


def test_block_file_is_append_only_across_reopen(tmp_path):
    raw = build_genesis_block().serialized()
    BlockFile(str(tmp_path)).append(raw)
    assert BlockFile(str(tmp_path)).append(raw) == 8 + len(raw)


def test_index_db_commit_and_rollback(tmp_path):
    db = BlockIndexDB(str(tmp_path))
    db.write_block_index("aa", "00", 1, 100)
    db.write_tx_index("t1", "aa", 0, [None, None])
    db.write_best_chain("aa")
    db.commit()

    # 一批未提交的修改……
    db.write_block_index("bb", "aa", 2, 200)
    db.write_tx_index("t1", "aa", 0, ["spender", None])
    db.erase_tx_index("t1")
    db.write_best_chain("bb")
    # ……回滚之后应当像从未发生过
    db.rollback()
    assert [r[0] for r in db.load_block_index()] == ["aa"]
    assert db.read_tx_index("t1") == {"blockhash": "aa", "txn": 0, "spent": [None, None]}
    assert db.read_best_chain() == "aa"
    db.close()


def test_index_db_persists_across_reopen(tmp_path):
    db = BlockIndexDB(str(tmp_path))
    db.write_block_index("h2", "h1", 2, 20)
    db.write_block_index("h1", "h0", 1, 10)
    db.write_tx_index("t", "h1", 3, [None, "x"])
    db.write_best_chain("h2")
    db.commit()
    db.close()
    db = BlockIndexDB(str(tmp_path))
    assert [r[0] for r in db.load_block_index()] == ["h1", "h2"]    # 按高度排序
    assert db.read_tx_index("t")["spent"] == [None, "x"]
    assert db.read_tx_index("missing") is None
    assert db.read_best_chain() == "h2"
    db.erase_block_index("h2")
    db.commit()
    assert [r[0] for r in db.load_block_index()] == ["h1"]
    db.close()
    db.close()                                                      # 重复关闭不报错


def test_wallet_db_roundtrip(tmp_path):
    db = WalletDB(str(tmp_path))
    db.write_key("pub1", "sec1")
    db.write_key("pub2", "sec2")
    db.write_tx("tx1", {"tx": "00", "time": 1})
    db.write_tx("tx1", {"tx": "00", "time": 2})                     # 覆盖
    db.write_tx("tx2", {"tx": "11", "time": 3})
    db.erase_tx("tx2")
    db.write_name("addr", "小明")
    db.write_setting("fee", 1000000)
    db.close()

    db = WalletDB(str(tmp_path))
    assert sorted(db.load_keys()) == ["sec1", "sec2"]
    assert db.load_txs() == [{"tx": "00", "time": 2}]
    assert db.load_address_book() == {"addr": "小明"}
    assert db.read_setting("fee") == 1000000
    assert db.read_setting("nope", "默认") == "默认"
    db.close()


def test_wallet_db_migrates_legacy_json(tmp_path):
    """早期版本的 wallet.json 会被自动导入一次，然后改名留档。"""
    from bitcoin.key import CKey
    key = CKey.generate()
    (tmp_path / "wallet.json").write_text(json.dumps({
        "keys": [key.get_secret().hex()],
        "defaultkey": key.get_pubkey().hex(),
        "address_book": {"1abc": "老朋友"},
        "txs": [],
    }), encoding="utf-8")
    db = WalletDB(str(tmp_path))
    assert db.load_keys() == [key.get_secret().hex()]
    assert db.load_address_book() == {"1abc": "老朋友"}
    assert db.read_setting("defaultkey") == key.get_pubkey().hex()
    assert not (tmp_path / "wallet.json").exists()
    assert (tmp_path / "wallet.json.migrated").exists()
    db.close()
