"""util.py（金额格式化/解析）、params.py（区块奖励、难度编码）、config.py（参数解析）。"""

import pytest

from bitcoin import params
from bitcoin.config import Config
from bitcoin.util import format_money, parse_money

COIN, CENT = params.COIN, params.CENT


# ------------------------------------------------------------------ 金额
@pytest.mark.parametrize("n,text", [
    (0, "0.00"),
    (CENT, "0.01"),
    (50 * COIN, "50.00"),
    (COIN + 23 * CENT, "1.23"),
    (1234 * COIN + 50 * CENT, "1,234.50"),
    (1_234_567 * COIN, "1,234,567.00"),
    (21_000_000 * COIN, "21,000,000.00"),
    (CENT - 1, "0.00"),                     # 不足一分的部分直接截掉（原版界面只显示到分）
    (-10 * COIN, "-10.00"),
    (-(CENT - 1), "0.00"),                  # 截断后是 0，就不带负号
])
def test_format_money(n, text):
    assert format_money(n) == text


def test_format_money_with_plus_sign():
    assert format_money(50 * COIN, f_plus=True) == "+50.00"
    assert format_money(-50 * COIN, f_plus=True) == "-50.00"
    assert format_money(0, f_plus=True) == "0.00"


@pytest.mark.parametrize("text,n", [
    ("0", 0),
    ("1", COIN),
    ("1.5", COIN + 50 * CENT),
    ("1.50", COIN + 50 * CENT),
    ("0.01", CENT),
    (".5", 50 * CENT),
    ("  12.34  ", 12 * COIN + 34 * CENT),
    ("1,234.56", 1234 * COIN + 56 * CENT),
    ("21000000", 21_000_000 * COIN),
])
def test_parse_money(text, n):
    assert parse_money(text) == n


@pytest.mark.parametrize("bad", ["", "   ", "abc", "1.234", "1.2.3", "-1", "1e5", ".", "1 2", "0x10"])
def test_parse_money_rejects_junk(bad):
    with pytest.raises(ValueError):
        parse_money(bad)


def test_format_parse_roundtrip():
    for n in (0, CENT, 99 * CENT, COIN, 12345 * COIN + 67 * CENT):
        assert parse_money(format_money(n)) == n


# -------------------------------------------------------------- 区块奖励
def test_block_value_halving_schedule():
    assert params.block_value(0, 0) == 50 * COIN
    assert params.block_value(209_999, 0) == 50 * COIN
    assert params.block_value(210_000, 0) == 25 * COIN
    assert params.block_value(420_000, 0) == 1_250_000_000
    assert params.block_value(210_000 * 32, 0) == 1              # 最后一聪
    assert params.block_value(210_000 * 33, 0) == 0              # 之后再也没有新币
    assert params.block_value(0, 12345) == 50 * COIN + 12345     # 手续费归矿工


def test_total_supply_is_just_under_21_million():
    """2100 万上限不是写死的常数，而是这个等比数列求和的结果。"""
    total = sum(params.block_value(era * 210_000, 0) * 210_000 for era in range(34))
    assert total == 2_099_999_997_690_000
    assert total < 21_000_000 * COIN


# -------------------------------------------------------------- 难度编码
@pytest.mark.parametrize("bits,target_hex", [
    # 主网创世难度
    (0x1D00FFFF, "00000000ffff0000000000000000000000000000000000000000000000000000"),
    # 主网历史上出现过的一个难度值（比特币 wiki 上的经典例子）
    (0x1B0404CB, "00000000000404cb000000000000000000000000000000000000000000000000"),
    # 本私网的创世难度
    (0x1F00FFFF, "0000ffff00000000000000000000000000000000000000000000000000000000"),
])
def test_compact_to_target_known_values(bits, target_hex):
    assert f"{params.compact_to_target(bits):064x}" == target_hex
    assert params.target_to_compact(int(target_hex, 16)) == bits


def test_compact_small_sizes():
    assert params.compact_to_target(0x01120000) == 0x12
    assert params.compact_to_target(0x02123400) == 0x1234
    assert params.compact_to_target(0x03123456) == 0x123456
    assert params.target_to_compact(0x12) == 0x01120000
    assert params.target_to_compact(0x1234) == 0x02123400
    assert params.target_to_compact(0x123456) == 0x03123456


def test_compact_avoids_mantissa_sign_bit():
    """尾数最高位是符号位：0x80 不能写成 0x01800000，而要写成 0x02008000。"""
    assert params.target_to_compact(0x80) == 0x02008000
    assert params.compact_to_target(0x02008000) == 0x80


def test_compact_is_lossy_but_stable():
    """压缩会丢精度，但"压缩->展开->再压缩"必须稳定（区块头里比较的是 nBits）。"""
    target = (1 << 240) - 12345
    bits = params.target_to_compact(target)
    assert params.compact_to_target(bits) <= target
    assert params.target_to_compact(params.compact_to_target(bits)) == bits


def test_private_net_constants_are_consistent():
    assert params.INTERVAL == 2016
    assert params.MESSAGE_HEADER_SIZE == 20
    assert params.compact_to_target(params.GENESIS_BITS) <= params.PROOF_OF_WORK_LIMIT
    assert len(params.GENESIS_PUBKEY) == 65


# ---------------------------------------------------------------- 配置
def test_config_defaults():
    c = Config([])
    assert c.port == params.DEFAULT_PORT
    assert c.listen and not c.generate
    assert c.peers() == []


def test_config_parses_arguments():
    c = Config(["-datadir=/tmp/x", "-port=1234", "-gen", "-nolisten",
                "-connect=1.2.3.4:5", "-addnode=6.7.8.9", "ignored"])
    assert c.datadir == "/tmp/x"
    assert c.port == 1234
    assert c.generate and not c.listen
    assert c.peers() == [("1.2.3.4", 5), ("6.7.8.9", params.DEFAULT_PORT)]


def test_config_file_provides_defaults(tmp_path):
    (tmp_path / "bitcoin.conf").write_text("port=4321  # 注释\n\ngen=1\n", encoding="utf-8")
    c = Config([f"-datadir={tmp_path}"])
    assert c.port == 4321 and c.generate
    # 命令行参数优先于配置文件
    assert Config([f"-datadir={tmp_path}", "-port=99"]).port == 99
