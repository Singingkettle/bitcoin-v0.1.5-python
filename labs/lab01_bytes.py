"""实验 1：计算机怎么表示数据——二进制、十六进制、字节、大端与小端。
对应教程第 2 章。运行：python labs/lab01_bytes.py
"""
from _common import step, title

from bitcoin.serialize import DataStream, ser_compact_size, uint256_from_hex, uint256_to_hex

title("实验 1：字节、十六进制、大小端")

step("1. 同一个数的三种写法")
n = 2009
print(f"   十进制 {n} = 二进制 {n:b} = 十六进制 {n:x}")
print("   十六进制的一位 = 二进制的四位；两位十六进制 = 8 个比特 = 1 个字节")

step("2. 一个数占多少字节？")
for value in (255, 256, 65535, 65536, 50 * 100_000_000):
    print(f"   {value:>13} 至少需要 {(value.bit_length() + 7) // 8} 个字节")

step("3. 大端与小端：同一个数，字节的排列顺序不同")
n = 0x12345678
print(f"   数字 0x{n:x}")
print(f"   大端（高位在前，人类的书写习惯）：{n.to_bytes(4, 'big').hex(' ')}")
print(f"   小端（低位在前，x86 CPU 的内存习惯）：{n.to_bytes(4, 'little').hex(' ')}")
print("   比特币协议里几乎所有整数都是小端。")

step("4. 用本项目的 DataStream 写几个整数，再原样读回来")
s = DataStream()
s.write_int32(1)                # 版本号
s.write_int64(50 * 100_000_000)  # 50 BTC = 50 亿聪
s.write_uint32(0xFFFFFFFF)
raw = s.getvalue()
print(f"   写出的字节：{raw.hex(' ')}")
r = DataStream(raw)
print(f"   读回来：{r.read_int32()}, {r.read_int64()}, {r.read_uint32():#x}，还剩 {r.remaining()} 字节")

step("5. 变长整数：小数字少占地方")
for n in (5, 252, 253, 1000, 70000):
    print(f"   {n:>6} -> {ser_compact_size(n).hex(' ')}")
print("   规则：<253 用 1 字节；否则先写一个标记字节(fd/fe/ff)，再写 2/4/8 字节的小端整数")

step("6. 最容易踩的坑：哈希的“显示顺序”和“字节顺序”是反的")
display = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
n = uint256_from_hex(display)
s = DataStream()
s.write_uint256(n)
print(f"   区块浏览器上显示的创世块哈希：{display}")
print(f"   它在字节流里实际的样子：      {s.getvalue().hex()}")
print("   开头那串 0 在字节流里跑到了最后面——因为 256 位的大整数也是按小端存的。")
assert uint256_to_hex(n) == display
print("\n实验 1 完成。")
