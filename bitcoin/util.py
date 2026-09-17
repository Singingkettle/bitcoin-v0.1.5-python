"""杂项工具函数——原版 util.h / util.cpp 里我们用得到的那一小部分。"""

import logging
import os
import sys
import time

from . import params


def get_time() -> int:
    """GetTime()：当前 Unix 时间戳（秒）。"""
    return int(time.time())


def get_adjusted_time() -> int:
    """GetAdjustedTime()：原版会用各个对端报告的时间取中位数来校正本机时钟；
    私网里所有节点都在同一台机器上，时钟天然一致，所以直接返回本机时间。"""
    return get_time()


def format_money(n: int, f_plus: bool = False) -> str:
    """FormatMoney()：把"聪"格式化成界面上显示的字符串。

    忠实于原版：2009 年的界面只显示到"分"（两位小数，多余的精度直接截掉），
    整数部分每三位加一个逗号，例如 1,234.50。
    """
    negative = n < 0
    cents = abs(n) // params.CENT          # C++ 的整数除法向零取整，所以先取绝对值
    s = f"{cents // 100:,}.{cents % 100:02d}"
    if negative and cents > 0:
        return "-" + s
    if f_plus and cents > 0:
        return "+" + s
    return s


def parse_money(text: str) -> int:
    """ParseMoney()：把用户输入的金额字符串解析成"聪"。解析失败抛 ValueError。

    忠实于原版的规则：整数部分可以带千位逗号，小数最多两位，前后可以有空白，
    其他任何字符都算错误（所以 "1.234" 是非法的）。
    """
    s = text.strip()
    if not s:
        raise ValueError("金额为空")
    whole, dot, frac = s.partition(".")
    whole = whole.replace(",", "")
    if whole == "" and frac == "":
        raise ValueError("金额格式错误")
    if whole and not whole.isdigit():
        raise ValueError("金额格式错误")
    if len(whole) > 14:
        raise ValueError("金额过大")
    if frac and (not frac.isdigit() or len(frac) > 2):
        raise ValueError("金额最多两位小数")
    cents = int((frac + "00")[:2]) if dot else 0
    return (int(whole or "0") * 100 + cents) * params.CENT


def setup_logging(datadir: str | None = None, name: str = "bitcoin") -> logging.Logger:
    """日志同时输出到屏幕和数据目录下的 debug.log（对应原版的 OutputDebugStringF）。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    try:
        # 日志里有中文；万一控制台的编码显示不了，就用 ? 代替，而不是抛异常
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
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
