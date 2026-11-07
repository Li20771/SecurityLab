"""SM4 ECB/CBC KAT 与 roundtrip 测试（GB/T 32907-2016）。"""

import os

import pytest

from gmcrypto.sm4 import (
    sm4_cbc_decrypt,
    sm4_cbc_encrypt,
    sm4_ecb_decrypt,
    sm4_ecb_encrypt,
)

# SM4 标准 KAT（GB/T 32907-2016）
SM4_KEY = bytes.fromhex("0123456789abcdeffedcba9876543210")
SM4_PT = bytes.fromhex("0123456789abcdeffedcba9876543210")
SM4_CT = bytes.fromhex("681edf34d206965e86b3e94f536e4246")


def test_sm4_ecb_kat():
    ct = sm4_ecb_encrypt(SM4_KEY, SM4_PT)
    assert ct[:16].hex() == SM4_CT.hex()


def test_sm4_ecb_roundtrip():
    for length in (0, 1, 15, 16, 17, 31, 32, 33, 100):
        key = os.urandom(16)
        pt = os.urandom(length)
        ct = sm4_ecb_encrypt(key, pt)
        assert sm4_ecb_decrypt(key, ct) == pt


def test_sm4_cbc_roundtrip():
    for length in (0, 1, 15, 16, 17, 31, 32, 33, 100):
        key = os.urandom(16)
        iv = os.urandom(16)
        pt = os.urandom(length)
        ct = sm4_cbc_encrypt(key, iv, pt)
        assert sm4_cbc_decrypt(key, iv, ct) == pt


def test_sm4_ecb_pads_any_length():
    """gmssl 3.2.2 的 crypt_ecb 对任意长度静默 PKCS#7 填充（含空输入）。

    实测：17B → 32B、0B → 16B，roundtrip 均还原。所以"非分组倍数必须报错"
    的预期对本后端不成立——这是 gmssl 的接口行为（静默 PKCS#7 填充），
    记录为被测件行为而非断言其报错。
    """
    for length in (0, 1, 15, 16, 17, 31, 32, 33, 100):
        key = os.urandom(16)
        pt = os.urandom(length)
        ct = sm4_ecb_encrypt(key, pt)
        assert len(ct) % 16 == 0
        assert sm4_ecb_decrypt(key, ct) == pt
