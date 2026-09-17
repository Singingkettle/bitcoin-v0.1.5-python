"""命令行参数与配置文件——对应原版 util.cpp 的 mapArgs / ParseParameters。

支持的参数（原版有的就沿用原版的写法）：
  -datadir=<目录>        数据目录（默认 ./data/node）
  -port=<端口>           监听端口（默认 18444）
  -connect=<ip[:端口]>   启动后主动连接这个节点（可以写多个）
  -addnode=<ip[:端口]>   同上（原版里两者略有区别，私网里等价）
  -nolisten              不接受别人连进来
  -gen                   启动后立刻开始挖矿

数据目录下如果有 bitcoin.conf（每行一个 key=value），其中的配置作为默认值。
原版靠 IRC 聊天频道自动发现其他节点；私网没有 IRC，节点之间靠上面的参数手动互连。
"""

import os

from . import params


class Config:
    def __init__(self, argv: list[str] | None = None):
        self.map_args: dict[str, list[str]] = {}
        for arg in argv or []:
            if not arg.startswith("-"):
                continue
            key, _, value = arg.lstrip("-").partition("=")
            self.map_args.setdefault(key, []).append(value)
        conf = os.path.join(self.datadir, "bitcoin.conf")
        if os.path.exists(conf):
            with open(conf, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if "=" in line:
                        key, value = line.split("=", 1)
                        self.map_args.setdefault(key.strip(), []).append(value.strip())

    @property
    def datadir(self) -> str:
        return self.get("datadir") or os.path.join("data", "node")

    @property
    def port(self) -> int:
        return int(self.get("port") or params.DEFAULT_PORT)

    @property
    def listen(self) -> bool:
        return "nolisten" not in self.map_args

    @property
    def generate(self) -> bool:
        return "gen" in self.map_args

    def get(self, key: str) -> str | None:
        values = self.map_args.get(key)
        return values[0] if values else None

    def get_all(self, key: str) -> list[str]:
        return self.map_args.get(key, [])

    def peers(self) -> list[tuple[str, int]]:
        out = []
        for spec in self.get_all("connect") + self.get_all("addnode"):
            host, _, port = spec.partition(":")
            out.append((host, int(port) if port else params.DEFAULT_PORT))
        return out
