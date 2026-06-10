"""util.cpp's mapArgs / ReadConfigFile.

Recognised switches (same spelling as the original where it had them):
  -datadir=<dir>   data directory (default: ./data/node)
  -port=<n>        listen port (default 18444)
  -connect=<ip[:port]>   connect ONLY to this node (repeatable)
  -addnode=<ip[:port]>   also connect to this node (repeatable)
  -nolisten        don't accept inbound connections
  -gen             start generating coins immediately
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
        # bitcoin.conf in the datadir provides defaults
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
