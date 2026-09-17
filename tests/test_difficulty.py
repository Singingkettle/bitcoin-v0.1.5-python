"""难度调整 GetNextWorkRequired 的数学。用人造的区块索引链，不需要真的挖矿。"""

from bitcoin import params
from bitcoin.block import CBlock, CBlockIndex
from bitcoin.blockchain import Blockchain


def make_chain(n_blocks: int, spacing: int, n_bits: int):
    indexes, prev = [], None
    for h in range(n_blocks):
        pindex = CBlockIndex(CBlock(n_time=params.GENESIS_TIME + h * spacing, n_bits=n_bits), h)
        pindex.n_height = h
        pindex.pprev = prev
        indexes.append(pindex)
        prev = pindex
    return indexes


class FakeChain:
    """只借用 Blockchain 的这一个方法，它不依赖任何实例状态。"""
    get_next_work_required = Blockchain.get_next_work_required


next_bits = FakeChain().get_next_work_required
LIMIT_BITS = params.target_to_compact(params.PROOF_OF_WORK_LIMIT)


def test_first_block_after_genesis_uses_pow_limit_when_no_previous():
    assert next_bits(None) == LIMIT_BITS


def test_no_retarget_inside_an_interval():
    chain = make_chain(2015, 600, 0x1F00FFFF)
    for h in (0, 1, 1000, 2013):
        assert next_bits(chain[h]) == 0x1F00FFFF


def test_retarget_happens_exactly_every_2016_blocks():
    chain = make_chain(2 * 2016, 1, 0x1E00FFFF)
    assert next_bits(chain[2014]) == 0x1E00FFFF         # 下一个块高度 2015：不调整
    assert next_bits(chain[2015]) != 0x1E00FFFF         # 下一个块高度 2016：调整
    assert next_bits(chain[2016]) == chain[2016].n_bits
    assert next_bits(chain[4031]) != 0x1E00FFFF


def test_on_target_chain_retargets_by_the_off_by_one_factor():
    """出块节奏完全精确（每块 600 秒）时，难度理应不变。但原版只往回走了 2015 步，
    量到的是 2015 个间隔，却除以完整的两周——所以目标值会变成原来的 2015/2016。"""
    start = params.PROOF_OF_WORK_LIMIT >> 12
    bits = params.target_to_compact(start)
    chain = make_chain(2016, 600, bits)
    expected = params.target_to_compact(
        params.compact_to_target(bits) * (2015 * 600) // params.TARGET_TIMESPAN)
    assert next_bits(chain[-1]) == expected
    assert params.compact_to_target(expected) < params.compact_to_target(bits)


def test_fast_blocks_make_it_harder_but_at_most_4x():
    bits = params.target_to_compact(params.PROOF_OF_WORK_LIMIT >> 16)
    chain = make_chain(2016, 1, bits)                   # 一秒一个块：快了 600 倍
    expected = params.target_to_compact(params.compact_to_target(bits) // 4)
    assert next_bits(chain[-1]) == expected             # 但单次最多只调 4 倍


def test_slow_blocks_make_it_easier_but_at_most_4x():
    bits = params.target_to_compact(params.PROOF_OF_WORK_LIMIT >> 16)
    chain = make_chain(2016, 600 * 100, bits)           # 慢了 100 倍
    expected = params.target_to_compact(params.compact_to_target(bits) * 4)
    assert next_bits(chain[-1]) == expected


def test_twice_as_slow_doubles_the_target():
    bits = params.target_to_compact(params.PROOF_OF_WORK_LIMIT >> 16)
    chain = make_chain(2016, 1200, bits)
    got = params.compact_to_target(next_bits(chain[-1]))
    want = params.compact_to_target(bits) * (2015 * 1200) // params.TARGET_TIMESPAN
    assert got == params.compact_to_target(params.target_to_compact(want))


def test_never_easier_than_the_pow_limit():
    chain = make_chain(2016, 600 * 100, LIMIT_BITS)
    assert next_bits(chain[-1]) == LIMIT_BITS


def test_private_net_first_retarget():
    """私网的真实情形：创世难度 0x1f00ffff，挖得飞快 -> 第 2016 块起难度提高 4 倍。"""
    chain = make_chain(2016, 1, params.GENESIS_BITS)
    new_bits = next_bits(chain[-1])
    assert params.compact_to_target(new_bits) == params.compact_to_target(params.GENESIS_BITS) // 4
