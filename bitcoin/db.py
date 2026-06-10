"""db.cpp replacement.

The original kept blocks in append-only blk0001.dat files and the
index/wallet in Berkeley DB. Here:

* blk0001.dat — identical spirit: magic + size + raw serialized block,
  appended; blocks are re-read by file offset.
* blkindex.sqlite — block index, tx index and the best-chain pointer
  (was CTxDB / blkindex.dat).
* wallet.json — keys, address book, settings (was CWalletDB / wallet.dat,
  and exactly as plaintext-insecure as the 2009 original).
"""

import json
import os
import sqlite3
import struct

from . import params
from .block import CBlock
from .serialize import DataStream


class BlockFile:
    """blk0001.dat — main.cpp CBlock::WriteToDisk/ReadFromDisk."""

    def __init__(self, datadir: str):
        os.makedirs(datadir, exist_ok=True)
        self.path = os.path.join(datadir, "blk0001.dat")
        if not os.path.exists(self.path):
            open(self.path, "wb").close()

    def append(self, block_bytes: bytes) -> int:
        """Returns the file position of the record (points at the magic)."""
        with open(self.path, "ab") as f:
            pos = f.tell()
            f.write(params.MESSAGE_START)
            f.write(struct.pack("<I", len(block_bytes)))
            f.write(block_bytes)
        return pos

    def read(self, pos: int) -> CBlock:
        with open(self.path, "rb") as f:
            f.seek(pos)
            magic = f.read(4)
            if magic != params.MESSAGE_START:
                raise IOError("bad magic in block file")
            (size,) = struct.unpack("<I", f.read(4))
            data = f.read(size)
        return CBlock.deserialize(DataStream(data))


class BlockIndexDB:
    """blkindex.sqlite — block index records, tx index, best chain hash."""

    def __init__(self, datadir: str):
        os.makedirs(datadir, exist_ok=True)
        self.conn = sqlite3.connect(
            os.path.join(datadir, "blkindex.sqlite"), check_same_thread=False
        )
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS blockindex(
                hash   TEXT PRIMARY KEY,   -- big-endian hex
                prev   TEXT NOT NULL,
                height INTEGER NOT NULL,
                pos    INTEGER NOT NULL    -- offset into blk0001.dat
            );
            CREATE TABLE IF NOT EXISTS txindex(
                txhash    TEXT PRIMARY KEY,
                blockhash TEXT NOT NULL,
                txn       INTEGER NOT NULL,  -- position within the block
                spent     TEXT NOT NULL      -- JSON array, null or spender txhash
            );
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            """
        )
        self.conn.commit()

    # --- block index ---
    def write_block_index(self, hash_hex: str, prev_hex: str, height: int, pos: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO blockindex VALUES (?,?,?,?)",
            (hash_hex, prev_hex, height, pos),
        )

    def load_block_index(self):
        return self.conn.execute(
            "SELECT hash, prev, height, pos FROM blockindex ORDER BY height"
        ).fetchall()

    # --- tx index (CTxIndex) ---
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

    # --- best chain (WriteHashBestChain) ---
    def read_best_chain(self) -> str | None:
        row = self.conn.execute("SELECT v FROM kv WHERE k='hashBestChain'").fetchone()
        return row[0] if row else None

    def write_best_chain(self, hash_hex: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO kv VALUES ('hashBestChain', ?)", (hash_hex,)
        )

    def commit(self):
        if self.conn is not None:
            self.conn.commit()

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


class WalletFile:
    """wallet.json — CWalletDB. Plaintext, like wallet.dat was in 2009."""

    def __init__(self, datadir: str):
        os.makedirs(datadir, exist_ok=True)
        self.path = os.path.join(datadir, "wallet.json")

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, data: dict):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, self.path)
