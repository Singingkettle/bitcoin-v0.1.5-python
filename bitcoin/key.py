"""密钥与签名——对应原版 key.h 的 CKey，底层用纯 Python 的 `ecdsa` 库。

- 曲线：secp256k1（和原版调用 OpenSSL 的 NID_secp256k1 一致）。
- 公钥：v0.1.5 只使用**非压缩**格式，65 字节 = 0x04 + X(32) + Y(32)。
- 签名：DER 编码（和 OpenSSL 的 ECDSA_sign 输出格式一致）。

一处有意的改进：签名用的随机数 k 改成了 RFC 6979 的确定性算法
（原版用 OpenSSL 的随机 k）。输出格式完全相同，但杜绝了
"随机数发生器有问题 -> 两次签名用了同一个 k -> 私钥被算出来"这一类事故。
"""

import hashlib

from ecdsa import SECP256k1, SigningKey, VerifyingKey
from ecdsa.keys import BadSignatureError
from ecdsa.util import sigdecode_der, sigencode_der


class CKey:
    def __init__(self, signing_key: SigningKey | None = None):
        self._sk = signing_key

    @classmethod
    def generate(cls) -> "CKey":
        """MakeNewKey()：随机生成一把新私钥。"""
        return cls(SigningKey.generate(curve=SECP256k1))

    @classmethod
    def from_secret(cls, secret: bytes) -> "CKey":
        """从 32 字节的原始私钥恢复。"""
        return cls(SigningKey.from_string(secret, curve=SECP256k1))

    def get_secret(self) -> bytes:
        """32 字节的原始私钥（原版存的是完整的 DER 私钥结构，信息量相同）。"""
        return self._sk.to_string()

    def get_pubkey(self) -> bytes:
        """GetPubKey()：65 字节非压缩公钥。"""
        return b"\x04" + self._sk.get_verifying_key().to_string()

    def sign(self, digest: bytes) -> bytes:
        """Sign()：对一个 32 字节的摘要签名，返回 DER 编码的签名。"""
        return self._sk.sign_digest_deterministic(
            digest, hashfunc=hashlib.sha256, sigencode=sigencode_der
        )

    @staticmethod
    def verify_with_pubkey(pubkey: bytes, digest: bytes, sig_der: bytes) -> bool:
        """Verify()：用公钥验证签名。任何格式错误都只返回 False，绝不抛异常——
        因为公钥和签名都来自网络上的陌生人，垃圾数据不能让节点崩溃。"""
        try:
            vk = VerifyingKey.from_string(pubkey, curve=SECP256k1)
            return vk.verify_digest(sig_der, digest, sigdecode=sigdecode_der)
        except BadSignatureError:
            return False
        except Exception:       # 公钥不在曲线上、DER 结构损坏等
            return False
