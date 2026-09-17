"""用纯 Python 复刻的 Bitcoin v0.1.5（中本聪 2009 年的 alpha 版）。

模块划分与原版 C++ 源文件一一对应：

    params.py     <- main.h / net.h / serialize.h 里的常量
    serialize.py  <- serialize.h      序列化
    hashes.py     <- util.h / sha.cpp 哈希函数
    base58.py     <- base58.h         地址编码
    key.py        <- key.h            密钥与签名
    tx.py         <- main.h           交易
    block.py      <- main.h           区块
    script.py     <- script.h/.cpp    脚本虚拟机
    db.py         <- db.cpp           存储（Berkeley DB 换成了 sqlite）
    blockchain.py <- main.cpp         共识核心
    mempool.py    <- main.cpp         内存池
    wallet.py     <- main.cpp         钱包
    miner.py      <- main.cpp         矿工
    net.py        <- net.h/.cpp       网络底层
    node.py       <- main.cpp         消息处理与整机装配

建议的阅读顺序就是上面从上到下的顺序，详见 docs/ 目录下的教程。
"""

__version__ = "0.1.5"
