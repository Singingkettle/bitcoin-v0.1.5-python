"""密钥与 ECDSA 签名。"""

from bitcoin.hashes import hash256
from bitcoin.key import CKey

# secp256k1 的阶（曲线上点的个数），私钥必须在 1..N-1 之间
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def test_sign_verify_roundtrip():
    key = CKey.generate()
    digest = hash256(b"some message")
    assert CKey.verify_with_pubkey(key.get_pubkey(), digest, key.sign(digest))


def test_wrong_digest_fails():
    key = CKey.generate()
    sig = key.sign(hash256(b"one"))
    assert not CKey.verify_with_pubkey(key.get_pubkey(), hash256(b"two"), sig)


def test_wrong_key_fails():
    k1, k2 = CKey.generate(), CKey.generate()
    digest = hash256(b"msg")
    assert not CKey.verify_with_pubkey(k2.get_pubkey(), digest, k1.sign(digest))


def test_tampered_signature_fails():
    key = CKey.generate()
    digest = hash256(b"msg")
    sig = bytearray(key.sign(digest))
    sig[-1] ^= 0x01
    assert not CKey.verify_with_pubkey(key.get_pubkey(), digest, bytes(sig))


def test_pubkey_is_65_byte_uncompressed():
    pub = CKey.generate().get_pubkey()
    assert len(pub) == 65 and pub[0] == 0x04


def test_known_private_key_gives_generator_point():
    """私钥 = 1 时，公钥就是曲线的基点 G——所有教科书上都印着它的坐标。"""
    key = CKey.from_secret((1).to_bytes(32, "big"))
    assert key.get_pubkey().hex() == (
        "04"
        "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"
        "483ada7726a3c4655da4fbfc0e1108a8fd17b448a68554199c47d08ffb10d4b8")


def test_pubkey_lies_on_curve():
    """y² = x³ + 7 (mod p)：随机生成的公钥必须满足 secp256k1 的曲线方程。"""
    p = 2**256 - 2**32 - 977
    pub = CKey.generate().get_pubkey()
    x = int.from_bytes(pub[1:33], "big")
    y = int.from_bytes(pub[33:], "big")
    assert (y * y - (x * x * x + 7)) % p == 0


def test_secret_roundtrip():
    key = CKey.generate()
    assert 0 < int.from_bytes(key.get_secret(), "big") < N
    assert CKey.from_secret(key.get_secret()).get_pubkey() == key.get_pubkey()


def test_signature_is_der_encoded():
    sig = CKey.generate().sign(hash256(b"x"))
    assert sig[0] == 0x30                   # DER SEQUENCE
    assert sig[1] == len(sig) - 2
    assert sig[2] == 0x02                   # 第一个 INTEGER (r)


def test_signing_is_deterministic():
    """RFC 6979：同一把钥匙签同一条消息，两次结果完全一样（不依赖随机数发生器）。"""
    key = CKey.generate()
    digest = hash256(b"same message")
    assert key.sign(digest) == key.sign(digest)


def test_garbage_inputs_return_false_instead_of_raising():
    digest = hash256(b"x")
    good = CKey.generate()
    for pubkey in (b"", b"junk", b"\x04" + b"\x00" * 64, b"\x04" + b"\xff" * 64):
        assert not CKey.verify_with_pubkey(pubkey, digest, good.sign(digest))
    for sig in (b"", b"junk", b"\x30\x00", b"\x30\x06\x02\x01\x00\x02\x01\x00"):
        assert not CKey.verify_with_pubkey(good.get_pubkey(), digest, sig)
