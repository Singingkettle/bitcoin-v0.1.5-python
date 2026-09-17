# 第 7 章　脚本：可编程的锁

> **本章目标**：理解比特币最独特的设计之一——"这笔钱归谁"不是一个字段，而是一小段程序。
> 学完这章，你能在纸上一步步模拟一笔交易的验证过程，并且明白签名到底签的是什么。
>
> **对应源码**：[bitcoin/script.py](../bitcoin/script.py)（全项目最值得精读的文件）
> 　　**实验**：[labs/lab06_script.py](../labs/lab06_script.py)

## 7.1　为什么是"程序"而不是"收款人"

最直观的设计是在交易输出里写一个"收款人"字段。中本聪没有这么做。他在每个输出上放了一小段**程序**
（`scriptPubKey`，可以叫它"锁"），花这笔钱的人必须提供另一小段程序（`scriptSig`，"钥匙"），
两段程序合起来运行，结果为"真"，钱才能花。

最常见的锁，翻译成人话是：

> "谁能出示一个公钥，使得它的哈希等于 `62e907b1…`，并且能提供一个用对应私钥做出的有效签名，这笔钱就归谁。"

但因为锁是程序，它就**可以**表达别的条件："三个人里任意两个签名即可"（多重签名）、
"谁能说出哈希值为 X 的那个秘密就归谁"……这些后来都成了真实的应用。
中本聪在 2010 年的一个论坛帖子里说过这层意思：协议一旦发布就很难再改，
所以他把交易设计成了一种通用的、可以容纳各种未来可能用到的交易类型的形式。

## 7.2　一台只有栈的小机器

比特币脚本是一种极简的语言，运行在一台极简的虚拟机上。这台机器**只有一个栈**，没有变量，**没有循环**。

**栈**（stack）就像一摞盘子：只能从最上面放（压栈，push），只能从最上面拿（弹栈，pop）。

脚本是一串指令（**opcode**，操作码），从左到右依次执行。指令分两类：

* **压入数据**：把一段数据放到栈顶；
* **操作**：从栈顶弹出若干个数据，做点什么，把结果压回去。

算式 `2 + 3 == 5` 写成脚本是 `OP_2 OP_3 OP_ADD OP_5 OP_EQUAL`（这种"运算符写在后面"的写法叫**逆波兰式**）。
实验 6 会打印出每一步之后栈的样子：

```text
   OP_2          栈: [02]
   OP_3          栈: [02, 03]
   OP_ADD        栈: [05]              弹出 2 和 3，压入它们的和
   OP_5          栈: [05, 05]
   OP_EQUAL      栈: [01]              弹出两个 5，相等，压入 1（真）
   => 脚本结果：True                   结束时栈顶非零 -> 通过
```

**为什么没有循环？** 因为每个节点都要执行它收到的每一笔交易里的脚本。如果脚本里能写死循环，
任何人都可以用一笔交易让全世界的节点卡死。没有循环，脚本的执行时间就一定是有限的、和脚本长度成正比的。
（这也是比特币和后来的以太坊在设计哲学上最大的分野之一。）

## 7.3　指令是怎么编码的

脚本在交易里是一串字节。每条指令的第一个字节决定了它是什么：

| 第一个字节 | 含义 |
|---|---|
| `01` ~ `4b`（1~75） | **把后面的这么多个字节压栈**。比如 `41` 后面跟 65 字节 = 压入一个公钥 |
| `4c` OP_PUSHDATA1 | 后面 1 个字节是长度，再后面是数据（用于 76~255 字节） |
| `4d` OP_PUSHDATA2 | 后面 2 个字节是长度 |
| `00` OP_0 | 压入一个空值（代表 0 / 假） |
| `51` ~ `60` OP_1 ~ OP_16 | 压入数字 1~16 |
| `76` OP_DUP | 复制栈顶 |
| `87` OP_EQUAL | 弹出两个，相等压入 1 否则压入 0 |
| `a9` OP_HASH160 | 弹出一个，压入它的 Hash160 |
| `ac` OP_CHECKSIG | 弹出公钥和签名，验证签名 |
| …… | v0.1.5 一共定义了 100 多个 |

所以上一章那笔真实交易的输出脚本 `41 04ae1a62…d84c ac`，读作："压入 65 字节（一个公钥）；OP_CHECKSIG"。

`script.py` 的开头列出了全部 opcode。这些数值不是手抄的——
是用脚本从中本聪原版 `script.h` 的枚举里**解析**出来的，`tests/test_script.py` 的第一个测试会逐个核对 108 个值。

解析一条指令的函数是 `CScript.get_op()`，它返回"新位置、opcode、压栈数据"：

```python
        if opcode <= OP_PUSHDATA4:
            if opcode < OP_PUSHDATA1:
                n = opcode                      # 1~75：opcode 本身就是长度
            elif opcode == OP_PUSHDATA1:
                ...
            if pc + n > len(self):
                raise ValueError("压栈数据超出脚本末尾")    # 又一次：不要相信别人给的长度
            data = bytes(self[pc:pc + n])
```

### 脚本里的数字

栈里放的都是字节串。当一条指令需要把它当数字用时（比如 OP_ADD），按这个规则解释：
**小端、最后一个字节的最高位是符号位**。这是原版 `CBigNum` 的编码（`bn_serialize` / `bn_deserialize`）：

```text
0 -> (空)      1 -> 01      -1 -> 81      127 -> 7f      128 -> 80 00      -128 -> 80 80
```

128 为什么是两个字节？因为 `80` 的最高位是 1，会被当成符号位（读作 −0），所以要再补一个 `00`。
主网创世块 coinbase 里的第一个数 `ffff001d` 就是这样编码的 486604799。

v0.1.5 的脚本数字是**任意精度**的（想多大就多大），后来的版本改成了最多 4 字节。

## 7.4　两种标准的锁

v0.1.5 的钱包只会生成、也只认识两种锁（`script.py` 里的 `solver()` 函数负责识别）：

### P2PK：付给公钥（Pay to Public Key）

```text
锁   scriptPubKey：   <公钥> OP_CHECKSIG
钥匙 scriptSig：      <签名>
```

挖矿所得用的就是这种。上一章中本聪付给 Hal Finney 的那笔交易，两个输出也都是这种。

### P2PKH：付给公钥哈希（Pay to Public Key Hash）——也就是"付给地址"

```text
锁   scriptPubKey：   OP_DUP OP_HASH160 <20 字节公钥哈希> OP_EQUALVERIFY OP_CHECKSIG
钥匙 scriptSig：      <签名> <公钥>
```

你往一个地址转账时，钱包就是把地址解码成 20 字节的公钥哈希（第 5 章），填进这个模板。

## 7.5　一步一步执行 P2PKH

验证时，把"钥匙"和"锁"拼成一段脚本，从头执行。下面是实验 6 的真实输出（栈顶在右边）：

```text
   压入 <签名>              栈: [签名]
   压入 <公钥>              栈: [签名, 公钥]                          ← 以上来自 scriptSig（钥匙）
   OP_CODESEPARATOR         栈: [签名, 公钥]                          ← 分隔符（见 7.7 节）
   OP_DUP                   栈: [签名, 公钥, 公钥]                    ← 以下来自 scriptPubKey（锁）
   OP_HASH160               栈: [签名, 公钥, 公钥的哈希]
   压入 <锁里写的哈希>      栈: [签名, 公钥, 公钥的哈希, 锁里写的哈希]
   OP_EQUALVERIFY           栈: [签名, 公钥]                          两个哈希相等，都弹掉，继续
   OP_CHECKSIG              栈: [01]                                  签名有效
   => 脚本结果：True
```

这段脚本同时检查了两件事：

1. **你出示的公钥是不是锁上指定的那个？**（`OP_DUP OP_HASH160 … OP_EQUALVERIFY`）
2. **你有没有这个公钥对应的私钥？**（`OP_CHECKSIG`）

如果小偷 Bob 用自己的私钥签名、附上自己的公钥去开 Alice 的锁，会卡在第 1 项（实验 6 第 4 步）：

```text
   OP_HASH160               栈: [签名, Bob的公钥, 05ea6ba5…]
   压入 83a41a21…           栈: [签名, Bob的公钥, 05ea6ba5…, 83a41a21…]
   OP_EQUALVERIFY           栈: [签名, Bob的公钥, 空]                 两个哈希不相等 -> 失败
   => 脚本结果：False
```

## 7.6　签名到底签的是什么

`OP_CHECKSIG` 要验证签名，就得知道被签名的"消息"是什么。直觉的答案是"这笔交易"。但这里有个鸡生蛋的问题：

> 签名是交易的一部分（它在 `scriptSig` 里）。在签名做出来之前，交易还不完整；
> 而签名一旦放进去，交易的内容就变了。**签名不可能签到它自己身上。**

中本聪的解决办法在 `signature_hash()` 函数里。要为第 `n_in` 个输入签名时，先造一份交易的**副本**：

```python
    # 1. 把所有输入的 scriptSig 清空
    txtmp = CTransaction(... vin=[CTxIn(prevout, CScript(), sequence) for i in tx_to.vin] ...)
    # 2. 把当前这个输入的 scriptSig 换成它要解的那把锁
    txtmp.vin[n_in].script_sig = script_code
    # 3.（按 hashtype 决定保留/抹掉哪些输出和其它输入，见下）
    # 4. 序列化这份副本，末尾再接 4 字节的 hashtype，做两次 SHA-256
    s = DataStream(...)
    txtmp.serialize(s)
    s.write_int32(hash_type)
    return int.from_bytes(hash256(s.getvalue()), "little")
```

得到的这个 256 比特的摘要，才是 ECDSA 真正签名的对象。

所以签名覆盖了：版本号、**所有输入指向哪里**、**所有输出的金额和锁**、锁定时间。
这意味着交易一旦签好，任何人——转发它的节点、打包它的矿工——都无法修改收款人或金额，
否则签名立刻失效。上一章的实验你已经看到：把付给 Hal 的金额加 1 聪，验证就失败了。

`tests/test_mainnet_vectors.py::test_first_payment_signature_hash_value` 按上面的定义**手工**拼出了
中本聪那笔交易的待签名数据，验证它的哈希与 `signature_hash()` 的结果一致。

### hashtype：签名可以只管一部分

签名的最后一个字节不属于 DER 编码，它是 **hashtype**，说明"这个签名覆盖交易的哪些部分"：

| hashtype | 值 | 含义 |
|---|---|---|
| SIGHASH_ALL | 1 | 覆盖所有输入和所有输出。**几乎所有交易都用这个** |
| SIGHASH_NONE | 2 | 不管输出："这钱我花了，给谁无所谓" |
| SIGHASH_SINGLE | 3 | 只管和本输入同序号的那一个输出 |
| SIGHASH_ANYONECANPAY | 0x80（可叠加） | 只管本输入，别人可以再添加输入——"众筹"式的交易 |

`tests/test_sighash.py` 对每一种都做了测试：签名后改哪些地方签名仍然有效、改哪些地方会失效。

## 7.7　v0.1.5 的"拼接执行"和它惹的祸

前面说"把钥匙和锁拼成一段脚本执行"。这不是比喻，原版代码就是这么写的（`script.cpp` 的 `VerifySignature`）：

```python
def verify_signature(tx_from, tx_to, n_in, hash_type=0) -> bool:
    ...
    combined = (CScript(bytes(txin.script_sig))
                + CScript(bytes([OP_CODESEPARATOR]))
                + CScript(bytes(txout.script_pubkey)))
    return eval_script(combined, tx_to, n_in, hash_type)
```

中间插入的 `OP_CODESEPARATOR` 是一个标记：`OP_CHECKSIG` 计算待签名数据时，
只取"最近一个分隔符之后"的那部分脚本（也就是锁），这样钥匙的内容就不会被卷进签名里。

"拼接后执行一次"看上去很自然，却埋下了一个大坑。在 v0.1.5 里，`OP_RETURN` 的实现是：

```python
            elif opcode == OP_RETURN:
                pc = pend                   # 不是失败！只是提前结束（见文件头说明）
```

——"跳到脚本末尾"，然后照常检查栈顶。于是，小偷根本不需要任何私钥，把 `scriptSig` 写成

```text
OP_1 OP_RETURN
```

拼接后的脚本执行到 `OP_RETURN` 就结束了，**根本没走到对方的锁**；而这时栈顶是 1（真）。验证通过。
实验 6 第 5 步演示了这一点：

```text
   OP_1                     栈: [01]
   OP_RETURN                栈: [01]
   => 脚本结果：True
   verify_signature -> True
```

**这个漏洞意味着任何人可以花任何人的钱。** 它在比特币上线后存在了一年半，
2010 年 7 月才被一位开发者发现并私下报告，中本聪悄悄地修复了它：

* `scriptSig` 和 `scriptPubKey` **分两次执行**，前者执行完把栈交给后者，钥匙里的指令再也无法影响锁的执行流程；
* `OP_RETURN` 改为立即失败。

万幸的是，漏洞公开之前没有人利用它。

本项目忠实复刻 v0.1.5，所以**这个漏洞原样保留**（`tests/test_sighash.py::test_op_return_exploit_spends_anyones_coins`
专门验证它"确实存在"）。类似地被保留的还有：

* `SignatureHash` 在输入序号越界时返回常数 `1` 而不是报错（"return 1" bug）；
* `OP_CHECKMULTISIG` 多弹出一个栈元素（这个 bug 因为修复会导致分叉，**至今仍在**，
  所有多签交易的 `scriptSig` 都必须以一个多余的 `OP_0` 开头）；
* 脚本结束时不检查 `IF`/`ENDIF` 是否配对；
* 所有 opcode 都可用，包括后来因安全问题被禁用的 `OP_CAT`、`OP_MUL`、`OP_LSHIFT` 等。

第 16 章会把这些放进历史的时间线里。

## 7.8　源码导读：eval_script

`eval_script()` 是一个大循环 + 一长串 `if/elif`，每个分支处理一种 opcode。读的时候抓住几个要点：

```python
def eval_script(script, tx_to, n_in, hash_type=0, trace=None) -> bool:
    stack: list[bytes] = []
    altstack: list[bytes] = []
    vf_exec: list[bool] = []        # IF/ELSE 嵌套的执行状态栈
    ...
    try:
        while pc < pend:
            f_exec = all(vf_exec)                       # 所有外层 IF 都为真才执行
            pc, opcode, data = script.get_op(pc)
            ...
    except (IndexError, ValueError):
        return False                        # 栈元素不足，或指令被截断
    return bool(stack) and cast_to_bool(stack[-1])
```

* **`vf_exec`**：实现 `OP_IF … OP_ELSE … OP_ENDIF`。每遇到一个 `IF` 就压入一个真/假；
  只有当这个列表里全是真时，当前指令才会被执行。处在"假"分支里的指令会被跳过（但仍然要被解析）。
* **`need(n)` / `popn(n)`**：栈里元素不够时抛 `IndexError`，被外层 `except` 捕获后返回 `False`。
  脚本来自陌生人，任何畸形的脚本都只应该导致"验证失败"，而不是程序崩溃。
* **`trace`**：教学用的钩子，实验 6 就是靠它打印出每一步的栈。
* `OP_CHECKSIG` 分支里的 `find_and_delete`：计算待签名数据之前，先把签名自己从脚本里删掉——
  呼应 7.6 节说的"签名不能签到自己"。

签名的生成在 `sign_signature()`：用 `solver()` 判断锁是哪种模板 → 从钱包里找到对应的私钥 →
`signature_hash()` 算摘要 → `key.sign()` → 在末尾接上 hashtype 字节 → 按模板拼出 `scriptSig` →
最后自己验一遍（和原版一样）。

## 7.9　动手实验

```powershell
python labs\lab06_script.py
```

**改一改**：

1. 用 `CScript()` 的 `push_int` / `push_opcode` 拼一段脚本，计算 `(3 + 4) × 5 == 35`，用实验里的 `run()` 跟踪它。
   （乘法是 `OP_MUL`，记得从 `bitcoin.script` 导入。）
2. 设计一把"谜题锁"：`OP_SHA256 <某个哈希值> OP_EQUAL`——谁能提供一段数据使其 SHA-256 等于锁里写的值，谁就能花这笔钱。
   写出对应的 `scriptSig` 并验证。想一想：这把锁在真实网络里安全吗？
   （提示：你的交易在被打包之前，会先被广播给所有人……）

## 本章小结

* 每个输出带一把**锁**（`scriptPubKey`），每个输入带一把**钥匙**（`scriptSig`），都是小程序。
* 脚本语言只有一个栈，**没有循环**，执行时间必然有限。
* 两种标准锁：**P2PK**（付给公钥）和 **P2PKH**（付给公钥哈希，即"付给地址"）。
* 签名签的不是原交易，而是一份"清空了所有 scriptSig、并在当前输入处填入了锁"的副本的哈希；
  hashtype 决定副本里保留哪些部分。
* v0.1.5 把钥匙和锁**拼接后执行一次**，加上 `OP_RETURN` 的实现方式，造成了"任何人可花任何人的钱"的漏洞——
  2010 年修复为分两阶段执行。

## 思考题

1. 在纸上模拟执行 P2PK：`<签名> OP_CODESEPARATOR <公钥> OP_CHECKSIG`，写出每一步之后的栈。
2. 如果签名时不清空其它输入的 `scriptSig`，一笔有两个输入、分属两个人的交易能签得出来吗？
3. 7.9 节的"谜题锁"为什么在真实网络里不安全？怎样改造才安全？

<details><summary>参考答案</summary>

1. `[签名]` → `[签名]`（分隔符不动栈）→ `[签名, 公钥]` → `[01]`。
2. 签不出来。甲签名时需要知道乙的签名（因为它在交易里），而乙签名时又需要甲的——互相等待。
   清空所有 `scriptSig` 后，两人签的是同一份"空白"副本，谁先签都行。
3. 第一个看到你交易的矿工（或任何节点）可以直接抄走你 `scriptSig` 里的答案，
   自己构造一笔把钱付给他自己的交易并抢先打包。因为答案本身不和"付给谁"绑定。
   改造办法：在锁里同时要求一个签名（`OP_SHA256 <h> OP_EQUALVERIFY <公钥> OP_CHECKSIG`），
   这样答案虽然公开了，别人没有私钥也改不了收款人——这正是后来闪电网络里"哈希时间锁合约"的基本结构。
</details>

[← 上一章](ch06-transaction.md)　|　[下一章：区块与默克尔树 →](ch08-block.md)
