"""Pure-Python re-creation of Bitcoin v0.1.5 (Satoshi's 2009 alpha).

Module layout mirrors the original C++ source files:

    serialize.py  <- serialize.h
    base58.py     <- base58.h
    key.py        <- key.h
    script.py     <- script.h / script.cpp
    tx.py         <- main.h (CTransaction half)
    block.py      <- main.h (CBlock half)
    blockchain.py <- main.cpp (consensus half)
    mempool.py    <- main.cpp (mapTransactions)
    wallet.py     <- main.cpp / db.cpp (wallet half)
    db.py         <- db.cpp (Berkeley DB replaced with sqlite/json)
    miner.py      <- main.cpp (BitcoinMiner)
    net.py        <- net.h / net.cpp
    node.py       <- main.cpp (ProcessMessage / SendMessages)
"""

__version__ = "0.1.5"
