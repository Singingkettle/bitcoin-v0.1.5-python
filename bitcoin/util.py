"""Misc helpers — the small surviving subset of util.h/util.cpp."""

import logging
import os
import sys
import time

from . import params


def get_time() -> int:
    """GetTime() — unix time as int64."""
    return int(time.time())


def get_adjusted_time() -> int:
    """GetAdjustedTime() — the original applied a median network offset;
    on a localhost private net the clocks agree, so this is just GetTime()."""
    return get_time()


def format_money(n: int) -> str:
    """FormatMoney() — satoshis to 'd.dd' string, trailing zeros trimmed
    to cents like the original."""
    sign = "-" if n < 0 else ""
    n = abs(n)
    whole, frac = divmod(n, params.COIN)
    s = f"{whole}.{frac:08d}"
    # original trims trailing zeros but always keeps two decimals
    s = s.rstrip("0")
    if s.endswith("."):
        s += "00"
    elif len(s.split(".")[1]) == 1:
        s += "0"
    return sign + s


def parse_money(s: str) -> int:
    """ParseMoney() — 'd.dd' string to satoshis. Raises ValueError on junk."""
    s = s.strip()
    if not s:
        raise ValueError("empty amount")
    sign = 1
    if s.startswith("-"):
        sign, s = -1, s[1:]
    if "." in s:
        whole, _, frac = s.partition(".")
        frac = (frac + "00000000")[:8]
        if not (whole or frac):
            raise ValueError("bad amount")
        value = int(whole or "0") * params.COIN + int(frac or "0")
    else:
        value = int(s) * params.COIN
    return sign * value


def setup_logging(datadir: str | None = None, name: str = "bitcoin") -> logging.Logger:
    """debug.log in the datadir plus stdout, like OutputDebugStringF."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(threadName)s %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if datadir:
        os.makedirs(datadir, exist_ok=True)
        fh = logging.FileHandler(os.path.join(datadir, "debug.log"), encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
