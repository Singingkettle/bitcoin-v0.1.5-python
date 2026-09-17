"""存储层——替代原版的 db.cpp。

原版用追加写的 blk0001.dat 存区块，用 Berkeley DB 存索引和钱包。这里：

* blk0001.dat    —— 与原版同样的格式：魔数 + 长度 + 序列化的区块，只追加不修改；
                    读取时按文件偏移直接定位。
* blkindex.sqlite —— 区块索引、交易索引、"当前最佳链"指针（对应原版 CTxDB / blkindex.dat）。
* wallet.sqlite   —— 私钥、钱包交易、地址簿、设置（对应原版 CWalletDB / wallet.dat）。
                    和 2009 年一样是**明文**存私钥，谁拿到这个文件谁就拿到了钱。

为什么用 sqlite：Berkeley DB 本质上是一个支持事务的"键值对"数据库，
sqlite 是 Python 标准库里最接近的东西——同样能"要么全部写入、要么全部撤销"。
"""

import json
import os
import sqlite3
import struct

from . import params
from .block import CBlock
from .serialize import DataStream


class BlockFile:
    """blk0001.dat——对应 CBlock::WriteToDisk / ReadFromDisk。"""

    def __init__(self, datadir: str):
        os.makedirs(datadir, exist_ok=True)
        self.path = os.path.join(datadir, "blk0001.dat")
        if not os.path.exists(self.path):
            open(self.path, "wb").close()

    def append(self, block_bytes: bytes) -> int:
        """追加一个区块，返回这条记录在文件里的起始偏移（指向魔数）。"""
        with open(self.path, "ab") as f:
            pos = f.tell()
            f.write(params.MESSAGE_START)
            f.write(struct.pack("<I", len(block_bytes)))
            f.write(block_bytes)
        return pos

    def read(self, pos: int) -> CBlock:
        with open(self.path, "rb") as f:
            f.seek(pos)
            if f.read(4) != params.MESSAGE_START:
                raise IOError("区块文件里的魔数不对，文件可能已损坏")
            (size,) = struct.unpack("<I", f.read(4))
            data = f.read(size)
        return CBlock.deserialize(DataStream(data))


class BlockIndexDB:
    """blkindex.sqlite——区块索引 + 交易索引 + 最佳链指针。

    所有写操作先停留在一个未提交的事务里：
    commit() 对应原版的 TxnCommit，rollback() 对应 TxnAbort。
    这样"连接一个区块"或"链重组"要么完整生效，要么像没发生过一样。
    """

    def __init__(self, datadir: str):
        os.makedirs(datadir, exist_ok=True)
        self.conn = sqlite3.connect(
            os.path.join(datadir, "blkindex.sqlite"), check_same_thread=False
        )
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS blockindex(
                hash   TEXT PRIMARY KEY,   -- 区块哈希（大端十六进制）
                prev   TEXT NOT NULL,      -- 父块哈希
                height INTEGER NOT NULL,   -- 高度
                pos    INTEGER NOT NULL    -- 在 blk0001.dat 里的偏移
            );
            CREATE TABLE IF NOT EXISTS txindex(
                txhash    TEXT PRIMARY KEY,
                blockhash TEXT NOT NULL,     -- 这笔交易在哪个区块里
                txn       INTEGER NOT NULL,  -- 是该区块的第几笔交易
                spent     TEXT NOT NULL      -- JSON 数组，每个输出一格：null=未花费，否则是花费者的交易哈希
            );
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            """
        )
        self.conn.commit()

    # ------------------------------------------------------------ 区块索引
    def write_block_index(self, hash_hex: str, prev_hex: str, height: int, pos: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO blockindex VALUES (?,?,?,?)",
            (hash_hex, prev_hex, height, pos),
        )

    def erase_block_index(self, hash_hex: str):
        self.conn.execute("DELETE FROM blockindex WHERE hash=?", (hash_hex,))

    def load_block_index(self):
        """按高度从低到高返回全部索引行——保证重建时父块总是先于子块出现。"""
        return self.conn.execute(
            "SELECT hash, prev, height, pos FROM blockindex ORDER BY height"
        ).fetchall()

    # ----------------------------------------------- 交易索引（原版 CTxIndex）
    def read_tx_index(self, txhash_hex: str):
        row = self.conn.execute(
            "SELECT blockhash, txn, spent FROM txindex WHERE txhash=?",
            (txhash_hex,),
        ).fetchone()
        if row is None:
            return None
        return {"blockhash": row[0], "txn": row[1], "spent": json.loads(row[2])}

    def write_tx_index(self, txhash_hex: str, blockhash_hex: str, txn: int,
                       spent: list):
        self.conn.execute(
            "INSERT OR REPLACE INTO txindex VALUES (?,?,?,?)",
            (txhash_hex, blockhash_hex, txn, json.dumps(spent)),
        )

    def erase_tx_index(self, txhash_hex: str):
        self.conn.execute("DELETE FROM txindex WHERE txhash=?", (txhash_hex,))

    # -------------------------------------- 最佳链指针（WriteHashBestChain）
    def read_best_chain(self) -> str | None:
        row = self.conn.execute("SELECT v FROM kv WHERE k='hashBestChain'").fetchone()
        return row[0] if row else None

    def write_best_chain(self, hash_hex: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO kv VALUES ('hashBestChain', ?)", (hash_hex,)
        )

    # ---------------------------------------------------------------- 事务
    def commit(self):
        if self.conn is not None:
            self.conn.commit()

    def rollback(self):
        if self.conn is not None:
            self.conn.rollback()

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


class WalletDB:
    """wallet.sqlite——对应原版 CWalletDB。每条记录单独写入并立即提交，
    和原版"挖到块就马上把新私钥写盘"的行为一致：丢私钥 = 丢钱，不能攒着批量写。"""

    def __init__(self, datadir: str):
        os.makedirs(datadir, exist_ok=True)
        self.datadir = datadir
        self.conn = sqlite3.connect(
            os.path.join(datadir, "wallet.sqlite"), check_same_thread=False
        )
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS keys(pubkey TEXT PRIMARY KEY, secret TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS txs(txhash TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS addressbook(address TEXT PRIMARY KEY, label TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            """
        )
        self.conn.commit()
        self._migrate_from_json()

    def _migrate_from_json(self):
        """早期版本把钱包存成 wallet.json；如果发现旧文件就导入一次。"""
        old = os.path.join(self.datadir, "wallet.json")
        if not os.path.exists(old):
            return
        if self.conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0] == 0:
            with open(old, "r", encoding="utf-8") as f:
                data = json.load(f)
            from .key import CKey
            from .serialize import uint256_to_hex
            from .tx import CTransaction

            for secret_hex in data.get("keys", []):
                pubkey = CKey.from_secret(bytes.fromhex(secret_hex)).get_pubkey()
                self.conn.execute("INSERT OR REPLACE INTO keys VALUES (?,?)",
                                  (pubkey.hex(), secret_hex))
            for d in data.get("txs", []):
                tx = CTransaction.deserialize(DataStream(bytes.fromhex(d["tx"])))
                self.conn.execute("INSERT OR REPLACE INTO txs VALUES (?,?)",
                                  (uint256_to_hex(tx.get_hash()), json.dumps(d)))
            for addr, label in data.get("address_book", {}).items():
                self.conn.execute("INSERT OR REPLACE INTO addressbook VALUES (?,?)",
                                  (addr, label))
            if data.get("defaultkey"):
                self.conn.execute("INSERT OR REPLACE INTO kv VALUES ('defaultkey', ?)",
                                  (data["defaultkey"],))
            self.conn.commit()
        os.replace(old, old + ".migrated")

    # ------------------------------------------------------------------ 写
    def write_key(self, pubkey_hex: str, secret_hex: str):
        self.conn.execute("INSERT OR REPLACE INTO keys VALUES (?,?)",
                          (pubkey_hex, secret_hex))
        self.conn.commit()

    def write_tx(self, txhash_hex: str, record: dict):
        self.conn.execute("INSERT OR REPLACE INTO txs VALUES (?,?)",
                          (txhash_hex, json.dumps(record)))
        self.conn.commit()

    def erase_tx(self, txhash_hex: str):
        self.conn.execute("DELETE FROM txs WHERE txhash=?", (txhash_hex,))
        self.conn.commit()

    def write_name(self, address: str, label: str):
        self.conn.execute("INSERT OR REPLACE INTO addressbook VALUES (?,?)",
                          (address, label))
        self.conn.commit()

    def write_setting(self, key: str, value):
        self.conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)",
                          (key, json.dumps(value)))
        self.conn.commit()

    # ------------------------------------------------------------------ 读
    def load_keys(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT secret FROM keys")]

    def load_txs(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.conn.execute("SELECT data FROM txs")]

    def load_address_book(self) -> dict[str, str]:
        return dict(self.conn.execute("SELECT address, label FROM addressbook"))

    def read_setting(self, key: str, default=None):
        row = self.conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return row[0]           # 旧数据迁移过来的裸字符串

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
