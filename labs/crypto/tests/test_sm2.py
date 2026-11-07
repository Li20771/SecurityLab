"""SM2 KAT 与 roundtrip 测试（GM/T 0003.5-2012）。"""

import os

from gmcrypto.sm2 import sm2_keygen, sm2_sign, sm2_verify


def test_sm2_keygen_range_and_length():
    d, pub = sm2_keygen()
    assert len(d) == 32
    assert len(pub) == 64
    assert 1 <= int.from_bytes(d, "big") < 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123


def test_sm2_pubkey_matches_private_key():
    # GM/T 0003.5-2012 附录 A 示例：d 与 P = d·G
    d = bytes.fromhex("3945208f7b2144b13f36e38ac6d39f95889393692860b51a42fb81ef4df7c5b8".lower())
    expected_pub = bytes.fromhex(
        "09f9df311e5421a150dd7d161e4bc5c672179fad1833fc076bb08ff356f35020"
        "ccea490ce26775a52dc6ea718cc1aa600aed05fbf35e084a6632f6072da9ad13"
    )
    from gmcrypto.sm2 import _ec_mul, _GX, _GY  # 私有函数，测试用
    pub = _ec_mul(int.from_bytes(d, "big"), (_GX, _GY))
    assert pub[0].to_bytes(32, "big") == expected_pub[:32]
    assert pub[1].to_bytes(32, "big") == expected_pub[32:]


def test_sm2_sign_verify_roundtrip():
    d, pub = sm2_keygen()
    msg = os.urandom(64)
    sig = sm2_sign(d, msg)
    assert len(sig) == 64
    assert sm2_verify(pub, msg, sig)


def test_sm2_tampered_message_fails():
    d, pub = sm2_keygen()
    msg = os.urandom(64)
    sig = sm2_sign(d, msg)
    assert not sm2_verify(pub, msg + b"x", sig)


def test_sm2_wrong_key_fails():
    d1, pub1 = sm2_keygen()
    _, pub2 = sm2_keygen()
    msg = os.urandom(64)
    sig = sm2_sign(d1, msg)
    assert not sm2_verify(pub2, msg, sig)


def test_sm2_tampered_signature_fails():
    d, pub = sm2_keygen()
    msg = os.urandom(64)
    sig = sm2_sign(d, msg)
    assert not sm2_verify(pub, msg + b"x", sig)
