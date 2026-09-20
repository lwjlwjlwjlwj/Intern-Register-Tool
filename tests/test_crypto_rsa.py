"""`src/crypto_rsa.py` 的行为测试 —— 密码加密是注册链路的**第一个字节**。

为什么补这个文件（2026-09-20）
------------------------------
原先模块里有个 `selftest()`（断言密文长度落在 RSA-1024 单块区间），但它
**没有任何调用者**、也没有 `__main__` 守卫 —— 等于永远不跑，属死代码。
它断言的那条不变式本身是对的，所以**删函数的同时把断言搬到这里**，
而不是连不变式一起丢掉。

为什么值得单独钉住：`encrypt_password` 目前的生产调用者只有两个探针
（`probe_quota_scope.py` / `probe_reg_interval.py`），而两者都会**真打注册接口**。
也就是说这条加密逻辑此前**零测试覆盖**，它一旦坏掉，症状是服务端报
`A0216「用户密码解密失败」` —— 从报错看不出是本地加密的问题。

🔴 本文件刻意**不写**"三个字段都参与了密文"这类断言 —— 写了也是假的
（理由见 `test_padding_is_randomized_so_ciphertext_differs_each_call`）。
能判定的只有**长度**参与，见最后一条。

跑法：
    pytest tests/test_crypto_rsa.py -v
"""

import base64

import pytest

from src import crypto_rsa

# RSA-1024 单块密文 = 128 字节
_BLOCK = 128
# PKCS#1 v1.5 填充占 11 字节 ⇒ 明文上限 117 字节
_MAX_PLAIN = _BLOCK - 11


def test_ciphertext_is_exactly_one_rsa_block():
    """原 `selftest()` 的实质断言：密文必须是 128 字节的单块。"""
    ct = crypto_rsa.encrypt_password("a@b.com", "Test123!")
    assert len(base64.b64decode(ct)) == _BLOCK


def test_selftest_length_window_still_holds():
    """原 `selftest()` 用的判据是 base64 **字符数**区间（168..176）—— 一并钉住。

    字符数比字节数松（base64 有填充），但它是原实现的判据，
    保留下来才能证明"删 selftest 没丢覆盖"。
    """
    ct = crypto_rsa.encrypt_password("a@b.com", "Test123!")
    assert 168 <= len(ct) <= 176


def test_padding_is_randomized_so_ciphertext_differs_each_call():
    """🔴 同一组输入连调两次，密文**不同** —— PKCS#1 v1.5 的**加密**填充是随机的。

    （注意别和 PKCS#1 v1.5 **签名**搞混：签名是确定性的，加密不是。
    本文件初稿就在这里写错过一条断言，是测试自己把它抓出来的。）

    这条不是凑数，它有两个真实后果：

    1. **任何"比对两次密文是否相同"的用法都是错的** —— 它比的是随机噪声。
       本项目两个探针都比**服务端响应**（`msgCode` / 状态码），不是比密文，
       这条断言把"为什么必须这样"固定下来。
    2. 它意味着**不能用密文相等来证明明文相等**。因此本文件不写
       "identity / password / timestamp 都参与了加密"的断言 ——
       密文每次都不同，那种断言恒真、**零判别力**，只会制造假信心。
       真要证明内容参与，只能拿私钥解密，而私钥在服务端。
    """
    a = crypto_rsa.encrypt_password("a@b.com", "pw", timestamp=1_700_000_000)
    b = crypto_rsa.encrypt_password("a@b.com", "pw", timestamp=1_700_000_000)
    assert a != b


def test_plaintext_over_pkcs1_limit_raises():
    """明文超过 117 字节会抛 —— 这是超长邮箱/密码的**真实上限**，不是理论边界。

    这也是**唯一**能离线判定"三个字段的长度确实都进了明文"的办法：
    明文长度 = `len(identity) + 2 + len(password) + len(str(ts))`，
    所以固定 `ts` 和空 `password` 时，`identity` 的可用长度可精确算出。

    ⚠ 别写成 `identity = "a" * 117` —— 那实际是 120 字节，一上来就抛，
    测到的是"超限会抛"而不是边界。中间那句 `assert` 就是防这个的。
    """
    ts = 1
    ok_len = _MAX_PLAIN - 2 - len(str(ts))          # password 取空串
    ok_identity = "a" * ok_len
    assert len(f"{ok_identity}||{ts}") == _MAX_PLAIN, "边界算错了，先修这条"

    crypto_rsa.encrypt_password(ok_identity, "", timestamp=ts)      # 恰好用满，应通过
    with pytest.raises(ValueError):
        crypto_rsa.encrypt_password("a" * (ok_len + 1), "", timestamp=ts)
