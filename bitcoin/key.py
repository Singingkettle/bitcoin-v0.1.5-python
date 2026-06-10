"""key.h — CKey on top of the pure-Python `ecdsa` package.

v0.1.5 only knew uncompressed public keys (65 bytes, 0x04 prefix) and DER
signatures via OpenSSL; RFC 6979 deterministic nonces are used here instead
of OpenSSL's random k (same wire format, no behavioural difference).
"""

import hashlib

from ecdsa import SECP256k1, SigningKey, VerifyingKey
from ecdsa.util import sigdecode_der, sigencode_der
from ecdsa.keys import BadSignatureError


class CKey:
    def __init__(self, signing_key: SigningKey | None = None):
        self._sk = signing_key

    @classmethod
    def generate(cls) -> "CKey":
        """MakeNewKey()."""
        return cls(SigningKey.generate(curve=SECP256k1))

    @classmethod
    def from_secret(cls, secret: bytes) -> "CKey":
        """SetPrivKey() — 32-byte raw secret."""
        return cls(SigningKey.from_string(secret, curve=SECP256k1))

    def get_secret(self) -> bytes:
        """GetPrivKey() — 32-byte raw secret (the original stored full DER)."""
        return self._sk.to_string()

    def get_pubkey(self) -> bytes:
        """GetPubKey() — 65-byte uncompressed SEC point."""
        return b"\x04" + self._sk.get_verifying_key().to_string()

    def sign(self, digest: bytes) -> bytes:
        """Sign() — DER signature over a 32-byte digest."""
        return self._sk.sign_digest_deterministic(
            digest, hashfunc=hashlib.sha256, sigencode=sigencode_der
        )

    @staticmethod
    def verify_with_pubkey(pubkey: bytes, digest: bytes, sig_der: bytes) -> bool:
        """CKey::Verify() with a bare pubkey."""
        try:
            vk = VerifyingKey.from_string(pubkey, curve=SECP256k1)
            return vk.verify_digest(sig_der, digest, sigdecode=sigdecode_der)
        except BadSignatureError:
            return False
        except Exception:  # malformed pubkey / signature encoding
            return False
