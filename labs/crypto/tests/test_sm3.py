"""SM3 KAT 与 roundtrip 测试（GB/T 32905-2016）。

KAT 向量：
- SM3("abc") = 66c7f0f4…（GB/T 32905-2016 附录 A 示例）
- SM3("") = 1ab21d83…（空串）
- SM3("abcd"×16) = debe9ff9…（512 位向量）
"""

import os

from gmcrypto.sm3 import sm3_digest, sm3_mac


def test_sm3_kat_abc():
    assert sm3_digest(b"abc").hex() == (
        "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    )


def test_sm3_kat_empty():
    assert sm3_digest(b"").hex() == (
        "1ab21d8355cfa17f8e61194831e81a8f22bec8c728fefb747ed035eb5082aa2b"
    )


def test_sm3_kat_512bit():
    msg = b"abcd" * 16
    assert sm3_digest(msg).hex() == (
        "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732"
    )


def test_sm3_deterministic():
    data = os.urandom(64)
    assert sm3_digest(data) == sm3_digest(data)


def test_sm3_mac_roundtrip():
    key = os.urandom(16)
    data = os.urandom(64)
    mac = sm3_mac(key, data)
    assert len(mac) == 32
    assert sm3_mac(key, data) == mac
    assert sm3_mac(key, data + b"x") != mac
