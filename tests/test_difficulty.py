"""GetNextWorkRequired math, on synthetic block indexes (no PoW needed)."""

from bitcoin import params
from bitcoin.block import CBlock, CBlockIndex


def _make_chain(n_blocks: int, spacing: int, n_bits: int):
    """Linked CBlockIndex list with the given inter-block spacing."""
    indexes = []
    prev = None
    for h in range(n_blocks):
        block = CBlock(n_time=params.GENESIS_TIME + h * spacing, n_bits=n_bits)
        pindex = CBlockIndex(block, h)
        pindex.n_height = h
        pindex.pprev = prev
        indexes.append(pindex)
        prev = pindex
    return indexes


class FakeChain:
    """Just enough of Blockchain for get_next_work_required."""

    from bitcoin.blockchain import Blockchain as _B

    get_next_work_required = _B.get_next_work_required


def test_no_retarget_mid_interval():
    chain = _make_chain(100, 600, 0x1F00FFFF)
    assert FakeChain().get_next_work_required(chain[-1]) == 0x1F00FFFF


def test_retarget_keeps_difficulty_at_target_rate():
    # block 2015 is the last of the first interval; spacing exactly 600 s
    chain = _make_chain(params.INTERVAL, params.TARGET_SPACING, 0x1F00FFFF)
    new_bits = FakeChain().get_next_work_required(chain[-1])
    # the original measures nInterval-1 spacings against a full timespan,
    # so a perfectly-on-target chain still retargets slightly easier-to-harder
    # by the off-by-one factor 2015/2016
    expected = params.target_to_compact(
        params.compact_to_target(0x1F00FFFF)
        * ((params.INTERVAL - 1) * params.TARGET_SPACING)
        // params.TARGET_TIMESPAN
    )
    assert new_bits == expected


def test_retarget_clamps_fast_chain():
    # blocks arriving instantly: timespan clamps to TARGET_TIMESPAN/4
    start_bits = params.target_to_compact(params.PROOF_OF_WORK_LIMIT >> 16)
    chain = _make_chain(params.INTERVAL, 1, start_bits)
    new_bits = FakeChain().get_next_work_required(chain[-1])
    expected = params.target_to_compact(
        params.compact_to_target(start_bits) // 4
    )
    assert new_bits == expected


def test_retarget_capped_at_pow_limit():
    # an absurdly slow chain can't get easier than the proof-of-work limit
    start_bits = params.target_to_compact(params.PROOF_OF_WORK_LIMIT)
    chain = _make_chain(params.INTERVAL, params.TARGET_SPACING * 100, start_bits)
    new_bits = FakeChain().get_next_work_required(chain[-1])
    assert new_bits == params.target_to_compact(params.PROOF_OF_WORK_LIMIT)


def test_genesis_gets_pow_limit():
    assert (FakeChain().get_next_work_required(None)
            == params.target_to_compact(params.PROOF_OF_WORK_LIMIT))
