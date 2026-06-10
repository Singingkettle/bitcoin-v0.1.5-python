"""Rebuild the real mainnet genesis block field by field, exactly the way
main.cpp does it, and check the merkle root and block hash. This one test
exercises tx/block serialization, CScript push encoding and the merkle tree."""

from bitcoin import params
from bitcoin.block import CBlock
from bitcoin.script import OP_CHECKSIG, CScript, bn_serialize
from bitcoin.serialize import uint256_from_hex, uint256_to_hex
from bitcoin.tx import COutPoint, CTransaction, CTxIn, CTxOut

SATOSHI_PUBKEY = bytes.fromhex(
    "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
    "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f"
)
TIMESTAMP = b"The Times 03/Jan/2009 Chancellor on brink of second bailout for banks"


def build_mainnet_genesis() -> CBlock:
    # txNew.vin[0].scriptSig = CScript() << 486604799 << CBigNum(4) << timestamp
    # NB: CBigNum(4) is a DATA push (01 04), unlike operator<<(int) which
    # would have encoded OP_4 — that distinction is why the genesis
    # scriptSig famously starts 04ffff001d0104.
    script_sig = (CScript().push_int(486604799)
                  .push_data(bn_serialize(4)).push_data(TIMESTAMP))
    # txNew.vout[0].scriptPubKey = CScript() << pubkey << OP_CHECKSIG
    script_pubkey = CScript().push_data(SATOSHI_PUBKEY).push_opcode(OP_CHECKSIG)
    txnew = CTransaction(
        vin=[CTxIn(COutPoint(), script_sig)],
        vout=[CTxOut(50 * params.COIN, script_pubkey)],
    )
    block = CBlock(
        n_version=1,
        hash_prev_block=0,
        n_time=1231006505,
        n_bits=0x1D00FFFF,
        n_nonce=2083236893,
        vtx=[txnew],
    )
    block.hash_merkle_root = block.get_merkle_root()
    return block


def test_genesis_coinbase_scriptsig_encoding():
    script_sig = (CScript().push_int(486604799)
                  .push_data(bn_serialize(4)).push_data(TIMESTAMP))
    assert bytes(script_sig).hex().startswith("04ffff001d0104455468652054696d6573")


def test_genesis_merkle_root():
    block = build_mainnet_genesis()
    assert uint256_to_hex(block.hash_merkle_root) == (
        "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"
    )


def test_genesis_block_hash():
    block = build_mainnet_genesis()
    assert uint256_to_hex(block.get_hash()) == (
        "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
    )


def test_genesis_block_serialization_roundtrip():
    from bitcoin.serialize import DataStream

    block = build_mainnet_genesis()
    data = block.serialized()
    again = CBlock.deserialize(DataStream(data))
    assert again.get_hash() == block.get_hash()
    assert again.get_merkle_root() == block.hash_merkle_root
    assert len(again.vtx) == 1
    assert again.vtx[0].get_hash() == block.vtx[0].get_hash()


def test_genesis_satisfies_its_own_pow():
    block = build_mainnet_genesis()
    target = params.compact_to_target(block.n_bits)
    assert block.get_hash() <= target


def test_compact_target_roundtrip():
    assert params.compact_to_target(0x1D00FFFF) == 0xFFFF << (8 * (0x1D - 3))
    assert params.target_to_compact(params.compact_to_target(0x1D00FFFF)) == 0x1D00FFFF
    assert params.target_to_compact(params.compact_to_target(0x1F00FFFF)) == 0x1F00FFFF
