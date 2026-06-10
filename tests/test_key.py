from bitcoin.hashes import hash256
from bitcoin.key import CKey


def test_sign_verify_roundtrip():
    key = CKey.generate()
    digest = hash256(b"some message")
    sig = key.sign(digest)
    assert CKey.verify_with_pubkey(key.get_pubkey(), digest, sig)


def test_wrong_digest_fails():
    key = CKey.generate()
    sig = key.sign(hash256(b"one"))
    assert not CKey.verify_with_pubkey(key.get_pubkey(), hash256(b"two"), sig)


def test_wrong_key_fails():
    k1, k2 = CKey.generate(), CKey.generate()
    digest = hash256(b"msg")
    sig = k1.sign(digest)
    assert not CKey.verify_with_pubkey(k2.get_pubkey(), digest, sig)


def test_pubkey_is_uncompressed():
    key = CKey.generate()
    pub = key.get_pubkey()
    assert len(pub) == 65 and pub[0] == 0x04


def test_secret_roundtrip():
    key = CKey.generate()
    again = CKey.from_secret(key.get_secret())
    assert again.get_pubkey() == key.get_pubkey()


def test_garbage_inputs_do_not_crash():
    digest = hash256(b"x")
    assert not CKey.verify_with_pubkey(b"\x04" + b"\x00" * 64, digest, b"junk")
    assert not CKey.verify_with_pubkey(b"junk", digest, b"junk")
