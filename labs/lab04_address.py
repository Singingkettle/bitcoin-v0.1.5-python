"""实验 4：从私钥到比特币地址，一步一步来。
对应教程第 5 章。运行：python labs/lab04_address.py
"""
import hashlib

from _common import step, title

from bitcoin import base58
from bitcoin.hashes import hash160, hash256, ripemd160
from bitcoin.key import CKey

title("实验 4：私钥 -> 公钥 -> 地址")

step("1. 私钥：一个随机的 256 位整数")
key = CKey.generate()
print(f"   {key.get_secret().hex()}")
print("   这 64 个十六进制字符就是你的全部财产权。谁知道它，谁就能花你的钱。")

step("2. 公钥 = 私钥 × G（椭圆曲线上的乘法，见实验 3）")
pubkey = key.get_pubkey()
print(f"   {pubkey.hex()}")
print(f"   共 {len(pubkey)} 字节：1 字节前缀 04（表示“未压缩”）+ 32 字节 x 坐标 + 32 字节 y 坐标")

step("3. 对公钥连做两次哈希：先 SHA-256，再 RIPEMD-160")
sha = hashlib.sha256(pubkey).digest()
h160 = ripemd160(sha)
print(f"   SHA-256    -> {sha.hex()}  （32 字节）")
print(f"   RIPEMD-160 -> {h160.hex()}  （20 字节，这就是“公钥哈希”）")
assert h160 == hash160(pubkey)

step("4. 前面加 1 字节版本号（主网地址是 00）")
payload = b"\x00" + h160
print(f"   {payload.hex()}")

step("5. 算校验和：对上一步的结果做两次 SHA-256，取前 4 字节")
checksum = hash256(payload)[:4]
print(f"   校验和 = {checksum.hex()}")

step("6. 把“版本 + 公钥哈希 + 校验和”共 25 字节用 Base58 编码")
address = base58.encode(payload + checksum)
print(f"   地址 = {address}")
assert address == base58.pubkey_to_address(pubkey)
print("   开头的 1 来自版本字节 00：Base58 里每个前导 0 字节写成一个字符 '1'")

step("7. Base58 的字母表：去掉了容易看错的 0、O、I、l")
print(f"   {base58.ALPHABET}  （共 {len(base58.ALPHABET)} 个字符）")

step("8. 校验和的作用：抄错一个字符，几乎必然被发现")
typo = address[:10] + ("2" if address[10] != "2" else "3") + address[11:]
print(f"   正确地址 {address} -> 有效吗？{base58.is_valid_address(address)}")
print(f"   抄错一位 {typo} -> 有效吗？{base58.is_valid_address(typo)}")
print("   抄错后校验和恰好还对得上的概率是 1/2^32，约四十三亿分之一。")

step("9. 单向：从地址能还原出公钥哈希，但还原不出公钥，更还原不出私钥")
print(f"   地址 -> 公钥哈希：{base58.address_to_hash160(address).hex()}")
print("   私钥 --(椭圆曲线乘法，不可逆)--> 公钥 --(哈希，不可逆)--> 公钥哈希 <--(Base58，可逆)--> 地址")

step("10. 验证一个著名的地址：比特币创世块的收款地址")
satoshi_pubkey = bytes.fromhex(
    "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61de"
    "b649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f")
print(f"   中本聪写在创世块里的公钥 -> {base58.pubkey_to_address(satoshi_pubkey)}")
print("\n实验 4 完成。")
