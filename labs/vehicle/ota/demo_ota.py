"""labs/vehicle/ota 可复现演示脚本 —— 完整 OTA 安全流程（协议层，无需 CAN）。

用法（在 labs/vehicle/ota 下）::

    python demo_ota.py

流程：
    1. 生成 OEM / 攻击者两对 SM2 密钥（模拟：OEM 私钥离线、攻击者密钥）
    2. 编程会话 + SecurityAccess 解锁
    3. 0x34 → 0x36×N → 0x37 传输合法签名包（v2.4.0）→ 0x77，版本更新
    4. 攻击者密钥签名 v9.9.9 → 0x72（signature_invalid，先验签）
    5. 签名合法的旧版本 v2.3.0 → 0x72（version_not_monotonic，防降级）

实测输出追加到 ``report/demo_output.txt``。
"""

import datetime
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "uds-basic" / "ecu"))
sys.path.insert(0, str(_HERE.parent.parent / "crypto"))

import keygen  # noqa: E402
import uds_server  # noqa: E402
from gmcrypto.sm2 import sm2_keygen  # noqa: E402
from ota_ecu import OtaEcu, OtaState  # noqa: E402
from ota_image import build_manifest, build_package  # noqa: E402

OUT = _HERE / "report" / "demo_output.txt"

PROGRAMMING_SESSION_RESPONSE = bytes([0x50, 0x02, 0x00, 0xC8, 0x13, 0x88])
DOWNLOAD_OK = bytes([0x74, 0x20, 0x00, 0x80])

OEM_PRIV, OEM_PUB = sm2_keygen()
ATTACKER_PRIV, _ = sm2_keygen()


def unlock(ecu) -> None:
    assert ecu.handle(bytes([0x10, 0x02])) == PROGRAMMING_SESSION_RESPONSE
    seed_response = ecu.handle(bytes([0x27, 0x01]))
    assert seed_response[:2] == bytes([0x67, 0x01])
    key = keygen.derive_key(seed_response[2:6])
    assert ecu.handle(bytes([0x27, 0x02]) + key) == bytes([0x67, 0x02])


def transfer(ecu, package: bytes, block_size: int = 126) -> list[str]:
    log = []
    request = bytes([
        0x34, 0x00, 0x23, 0x00, 0x11, 0x22,
        (len(package) >> 8) & 0xFF, len(package) & 0xFF,
    ])
    log.append(f"  34 -> {ecu.handle(request).hex(' ')}")
    block_counter = 1
    for offset in range(0, len(package), block_size):
        chunk = package[offset:offset + block_size]
        response = ecu.handle(bytes([0x36, block_counter]) + chunk)
        log.append(f"  36 {block_counter:02X} ({len(chunk)}B) -> {response.hex(' ')}")
        block_counter = (block_counter + 1) & 0xFF
    log.append(f"  37 -> {ecu.handle(bytes([0x37])).hex(' ')}")
    return log


def main() -> int:
    lines: list[str] = []
    lines.append(f"===== labs/vehicle/ota demo @ {datetime.datetime.now().isoformat(timespec='seconds')} =====")

    state = uds_server.EcuState()
    ecu = OtaEcu(OEM_PUB, state=state, ota_state=OtaState(current_version="2.3.0"))
    lines.append(f"[INIT] current_version = {ecu.ota.current_version}")

    lines.append("[1] 编程会话 + 解锁（10 02 -> 27 01/02）")
    unlock(ecu)
    lines.append(f"  session=0x{state.session:02X} security=0x{state.security_level:02X}")

    lines.append("[2] 合法签名包 v2.4.0（min_version=2.3.0）")
    manifest = build_manifest("2.4.0", "2.3.0", b"synthetic firmware image v2.4.0")
    package = build_package(manifest, OEM_PRIV)
    lines.extend(transfer(ecu, package))
    lines.append(f"  verdict={ecu.ota.last_verdict}  current_version -> {ecu.ota.current_version}")
    ok_accept = ecu.ota.current_version == "2.4.0" and ecu.ota.last_verdict == "pass"

    lines.append("[3] 攻击者密钥签名 v9.9.9 -> 必须拒绝（先验签）")
    attack_manifest = build_manifest("9.9.9", "2.3.0", b"attacker image")
    unlock(ecu)
    lines.extend(transfer(ecu, build_package(attack_manifest, ATTACKER_PRIV)))
    lines.append(f"  verdict={ecu.ota.last_verdict}")
    ok_attacker = ecu.ota.last_verdict == "signature_invalid"

    lines.append("[4] 签名合法的旧版本 v2.3.0 -> 必须拒绝（防降级）")
    old_manifest = build_manifest("2.3.0", "2.3.0", b"old image")
    unlock(ecu)
    lines.extend(transfer(ecu, build_package(old_manifest, OEM_PRIV)))
    lines.append(f"  verdict={ecu.ota.last_verdict}")
    ok_downgrade = ecu.ota.last_verdict == "version_not_monotonic"

    all_ok = ok_accept and ok_attacker and ok_downgrade
    lines.append(f"===== result: {'PASS' if all_ok else 'FAIL'} =====")

    text = "\n".join(lines)
    print(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
