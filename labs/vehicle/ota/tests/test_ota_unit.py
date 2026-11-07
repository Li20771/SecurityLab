"""OTA 安全层协议级测试（无 CAN / ISO-TP，纯 UDS 字节流）。

覆盖 P16 四类安全测试：
1. 固件签名校验（合法通过 / 篡改拒绝 / 攻击者密钥拒绝）
2. 版本回滚与降级防护（降级 / 等版本 / 低于下限）
3. 越权升级（default session / 未解锁）
4. 分块传输完整性（丢块 / 乱序 / 超长 / 越界 / 幂等 / 未完成退出）
"""

import keygen
import pytest
import uds_server
from gmcrypto.sm2 import sm2_keygen
from ota_ecu import OtaEcu, OtaState
from ota_image import build_manifest, build_package, canonical_manifest_bytes

OEM_PRIV, OEM_PUB = sm2_keygen()
ATTACKER_PRIV, _ = sm2_keygen()

PROGRAMMING_SESSION_RESPONSE = bytes([0x50, 0x02, 0x00, 0xC8, 0x13, 0x88])
DOWNLOAD_OK = bytes([0x74, 0x20, 0x00, 0x80])
EXIT_OK = bytes([0x77])
NRC = 0x7F


def make_ecu(current_version: str = "2.3.0"):
    state = uds_server.EcuState()
    return OtaEcu(OEM_PUB, state=state, ota_state=OtaState(current_version=current_version)), state


def unlock(ecu, now: float | None = None) -> None:
    """10 02 → 27 01 → 27 02 <key>：编程会话 + 安全解锁。"""
    assert ecu.handle(bytes([0x10, 0x02]), now=now) == PROGRAMMING_SESSION_RESPONSE

    seed_response = ecu.handle(bytes([0x27, 0x01]), now=now)
    assert seed_response[:2] == bytes([0x67, 0x01]), seed_response

    key = keygen.derive_key(seed_response[2:6])
    assert ecu.handle(bytes([0x27, 0x02]) + key, now=now) == bytes([0x67, 0x02])


def request_download(ecu, size: int, now: float | None = None) -> bytes:
    request = bytes([
        0x34, 0x00, 0x23,
        0x00, 0x11, 0x22,
        (size >> 8) & 0xFF, size & 0xFF,
    ])
    return ecu.handle(request, now=now)


def transfer(ecu, package: bytes, block_size: int = 126, now: float | None = None) -> bytes:
    """完整走 34 → 36×N → 37，返回 0x37 的响应。"""
    assert request_download(ecu, len(package), now=now) == DOWNLOAD_OK

    block_counter = 1
    for offset in range(0, len(package), block_size):
        chunk = package[offset:offset + block_size]
        response = ecu.handle(bytes([0x36, block_counter]) + chunk, now=now)
        assert response == bytes([0x76, block_counter]), response
        block_counter = (block_counter + 1) & 0xFF

    return ecu.handle(bytes([0x37]), now=now)


# ======================================================================
# 3. 越权升级（未授权会话拒绝）
# ======================================================================


def test_download_rejected_in_default_session():
    ecu, _ = make_ecu()
    assert request_download(ecu, 10) == bytes([NRC, 0x34, 0x7F])


def test_download_rejected_when_locked():
    ecu, _ = make_ecu()
    assert ecu.handle(bytes([0x10, 0x02])) == PROGRAMMING_SESSION_RESPONSE
    assert request_download(ecu, 10) == bytes([NRC, 0x34, 0x33])


def test_download_ok_after_unlock():
    ecu, _ = make_ecu()
    unlock(ecu)
    assert request_download(ecu, 10) == DOWNLOAD_OK


# ======================================================================
# 1. 固件签名校验
# ======================================================================


def test_valid_signed_package_accepted():
    ecu, state = make_ecu()
    unlock(ecu)

    manifest = build_manifest("2.4.0", "2.3.0", b"synthetic firmware image v2.4.0")
    response = transfer(ecu, build_package(manifest, OEM_PRIV))

    assert response == EXIT_OK
    assert ecu.ota.current_version == "2.4.0"
    assert ecu.ota.last_verdict == "pass"
    assert state.download_active is False


def test_tampered_manifest_rejected():
    """TC-03：签名后改 manifest 字段（不重签）→ signature_invalid。"""
    ecu, state = make_ecu()
    unlock(ecu)

    manifest = build_manifest("2.4.0", "2.3.0", b"image")
    package = build_package(manifest, OEM_PRIV)

    tampered = dict(manifest)
    tampered["version"] = "9.9.9"
    tampered_package = canonical_manifest_bytes(tampered) + package[-64:]

    response = transfer(ecu, tampered_package)

    assert response == bytes([NRC, 0x37, 0x72])
    assert ecu.ota.last_verdict == "signature_invalid"
    assert ecu.ota.current_version == "2.3.0"
    # fail-closed：失败后 transfer 已中止
    assert state.download_active is False
    assert state.download_size == 0


def test_attacker_signed_package_rejected():
    """TC-01：攻击者密钥签名 → 拒绝；且校验顺序是**先验签**（不是先看版本）。"""
    ecu, _ = make_ecu()
    unlock(ecu)

    manifest = build_manifest("9.9.9", "2.3.0", b"image")
    response = transfer(ecu, build_package(manifest, ATTACKER_PRIV))

    assert response == bytes([NRC, 0x37, 0x72])
    # 若实现先判版本、后验签，这里会是 version_not_monotonic —— 必须不是
    assert ecu.ota.last_verdict == "signature_invalid"


def test_tampered_signature_rejected():
    ecu, _ = make_ecu()
    unlock(ecu)

    manifest = build_manifest("2.4.0", "2.3.0", b"image")
    package = bytearray(build_package(manifest, OEM_PRIV))
    package[-1] ^= 0x01  # 翻转签名 1 bit

    response = transfer(ecu, bytes(package))
    assert response == bytes([NRC, 0x37, 0x72])
    assert ecu.ota.last_verdict == "signature_invalid"


# ======================================================================
# 2. 版本回滚与降级防护
# ======================================================================


def test_downgrade_to_older_signed_package_rejected():
    """TC-06：签名完全合法的旧版本包 → 拒绝降级。"""
    ecu, _ = make_ecu(current_version="2.4.0")
    unlock(ecu)

    manifest = build_manifest("2.3.0", "2.3.0", b"image")
    response = transfer(ecu, build_package(manifest, OEM_PRIV))

    assert response == bytes([NRC, 0x37, 0x72])
    assert ecu.ota.last_verdict == "version_not_monotonic"
    assert ecu.ota.current_version == "2.4.0"


def test_equal_version_rejected():
    ecu, _ = make_ecu(current_version="2.4.0")
    unlock(ecu)

    manifest = build_manifest("2.4.0", "2.3.0", b"image")
    response = transfer(ecu, build_package(manifest, OEM_PRIV))

    assert response == bytes([NRC, 0x37, 0x72])
    assert ecu.ota.last_verdict == "version_not_monotonic"


def test_below_min_version_rejected():
    """版本单调（2.3.5 > 2.3.0）但低于 manifest 回滚下限（2.4.0）→ 拒绝。"""
    ecu, _ = make_ecu(current_version="2.3.0")
    unlock(ecu)

    manifest = build_manifest("2.3.5", "2.4.0", b"image")
    response = transfer(ecu, build_package(manifest, OEM_PRIV))

    assert response == bytes([NRC, 0x37, 0x72])
    assert ecu.ota.last_verdict == "below_min_version"


# ======================================================================
# 4. 分块传输完整性（丢块 / 乱序 / 超长 / 越界 / 幂等 / 未完成退出）
# ======================================================================


def test_missing_first_block_rejected():
    ecu, _ = make_ecu()
    unlock(ecu)
    assert request_download(ecu, 300) == DOWNLOAD_OK

    response = ecu.handle(bytes([0x36, 0x02]) + b"\xaa" * 5)
    assert response == bytes([NRC, 0x36, 0x73])


def test_out_of_order_block_rejected():
    ecu, _ = make_ecu()
    unlock(ecu)
    assert request_download(ecu, 300) == DOWNLOAD_OK

    assert ecu.handle(bytes([0x36, 0x01]) + b"\xaa" * 126) == bytes([0x76, 0x01])
    response = ecu.handle(bytes([0x36, 0x03]) + b"\xaa" * 126)
    assert response == bytes([NRC, 0x36, 0x73])


def test_oversized_block_rejected():
    ecu, _ = make_ecu()
    unlock(ecu)
    assert request_download(ecu, 300) == DOWNLOAD_OK

    response = ecu.handle(bytes([0x36, 0x01]) + b"\xaa" * 127)  # data > 126
    assert response == bytes([NRC, 0x36, 0x13])


def test_overflow_beyond_requested_size_rejected():
    ecu, _ = make_ecu()
    unlock(ecu)
    assert request_download(ecu, 10) == DOWNLOAD_OK

    assert ecu.handle(bytes([0x36, 0x01]) + b"\xaa" * 8) == bytes([0x76, 0x01])
    response = ecu.handle(bytes([0x36, 0x02]) + b"\xbb" * 8)  # 8+8 > 10
    assert response == bytes([NRC, 0x36, 0x13])


def test_duplicate_block_is_idempotent():
    ecu, state = make_ecu()
    unlock(ecu)
    assert request_download(ecu, 300) == DOWNLOAD_OK

    request = bytes([0x36, 0x01]) + b"\xaa" * 126
    assert ecu.handle(request) == bytes([0x76, 0x01])
    assert ecu.handle(request) == bytes([0x76, 0x01])  # 完全重复：幂等
    assert len(state.download_buffer) == 126


def test_incomplete_transfer_exit_rejected():
    ecu, state = make_ecu()
    unlock(ecu)
    assert request_download(ecu, 300) == DOWNLOAD_OK
    assert ecu.handle(bytes([0x36, 0x01]) + b"\xaa" * 126) == bytes([0x76, 0x01])

    response = ecu.handle(bytes([0x37]))
    assert response == bytes([NRC, 0x37, 0x22])  # conditionsNotCorrect，保持 active
    assert state.download_active is True


def test_transfer_data_without_download_rejected():
    ecu, _ = make_ecu()
    unlock(ecu)

    response = ecu.handle(bytes([0x36, 0x01]) + b"\xaa")
    assert response == bytes([NRC, 0x36, 0x24])


def test_malformed_package_rejected():
    ecu, _ = make_ecu()
    unlock(ecu)
    assert request_download(ecu, 5) == DOWNLOAD_OK
    assert ecu.handle(bytes([0x36, 0x01]) + b"short") == bytes([0x76, 0x01])

    response = ecu.handle(bytes([0x37]))
    assert response == bytes([NRC, 0x37, 0x72])
    assert ecu.ota.last_verdict == "malformed_package"


def test_reset_aborts_transfer():
    ecu, state = make_ecu()
    unlock(ecu)
    assert request_download(ecu, 300) == DOWNLOAD_OK

    assert ecu.handle(bytes([0x11, 0x01])) == bytes([0x51, 0x01])
    assert state.download_active is False

    assert ecu.handle(bytes([0x36, 0x01]) + b"\xaa") == bytes([NRC, 0x36, 0x24])
