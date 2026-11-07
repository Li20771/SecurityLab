"""OTA vcan0 端到端测试（WSL/Linux + vcan0；本会话 Windows 自动 skip）。

前置：
    sudo modprobe vcan && sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0
    pip install isotp udsoncan gmssl

运行（WSL）：
    cd labs/vehicle/ota && python -m pytest tests/test_ota_vcan.py -v

本文件在缺少 isotp / vcan0 时**整模块 skip**，skip 原因计入报告 —— 不伪造结果。
"""

import os
import pathlib
import subprocess
import sys
import time

import keygen
import pytest
from gmcrypto.sm2 import sm2_keygen
from ota_image import build_manifest, build_package, canonical_manifest_bytes

isotp = pytest.importorskip("isotp")

INTERFACE = os.environ.get("UDS_INTERFACE", "vcan0")
CLIENT_TX_ID = 0x7E0
CLIENT_RX_ID = 0x7E8

BOOT_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "ota_ecu_boot.py"

RECV_TIMEOUT = 1.5
ECU_STARTUP_TIMEOUT = 10.0

PROGRAMMING_SESSION_RESPONSE = bytes([0x50, 0x02, 0x00, 0xC8, 0x13, 0x88])
DOWNLOAD_OK = bytes([0x74, 0x20, 0x00, 0x80])


def exchange(payload: bytes, timeout: float = RECV_TIMEOUT):
    """以 (tx=0x7E0, rx=0x7E8) 新建 ISO-TP 连接收发；超时返回 None。"""
    sock = None
    try:
        sock = isotp.socket()
        sock.bind(
            INTERFACE,
            isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                rxid=CLIENT_RX_ID,
                txid=CLIENT_TX_ID,
            ),
        )
        sock.settimeout(timeout)
        sock.send(payload)
        return sock.recv()
    except Exception:  # noqa: BLE001 - 超时/底层错误统一按"无响应"处理
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture(scope="module")
def oem_keys():
    return sm2_keygen()


@pytest.fixture
def ecu_process(oem_keys, tmp_path):
    """每个测试启动一个独立的 OTA ECU 子进程（保证版本状态确定）。"""
    _, pub = oem_keys
    log = (tmp_path / "ota_ecu.log").open("w", encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, str(BOOT_SCRIPT), "--pub-hex", pub.hex()],
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    deadline = time.monotonic() + ECU_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        response = exchange(bytes([0x10, 0x01]), timeout=0.5)
        if response is not None and response[:2] == bytes([0x50, 0x01]):
            break
        time.sleep(0.1)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        log_path = tmp_path / "ota_ecu.log"
        content = (
            log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            if log_path.exists()
            else "<no log>"
        )
        pytest.fail(f"OTA ECU 未就绪，日志内容：\n{content}")

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log.close()


def unlock() -> None:
    assert exchange(bytes([0x10, 0x02])) == PROGRAMMING_SESSION_RESPONSE
    seed_response = exchange(bytes([0x27, 0x01]))
    assert seed_response is not None and seed_response[:2] == bytes([0x67, 0x01])
    key = keygen.derive_key(seed_response[2:6])
    assert exchange(bytes([0x27, 0x02]) + key) == bytes([0x67, 0x02])


def uds_transfer(package: bytes, block_size: int = 126) -> bytes:
    request = bytes([
        0x34, 0x00, 0x23,
        0x00, 0x11, 0x22,
        (len(package) >> 8) & 0xFF, len(package) & 0xFF,
    ])
    assert exchange(request) == DOWNLOAD_OK

    block_counter = 1
    for offset in range(0, len(package), block_size):
        chunk = package[offset:offset + block_size]
        assert exchange(bytes([0x36, block_counter]) + chunk) == bytes([0x76, block_counter])
        block_counter = (block_counter + 1) & 0xFF

    return exchange(bytes([0x37]))


def test_vcan_download_rejected_in_default_session(ecu_process):
    request = bytes([0x34, 0x00, 0x23, 0x00, 0x11, 0x22, 0x00, 0x0A])
    assert exchange(request) == bytes([0x7F, 0x34, 0x7F])


def test_vcan_download_rejected_when_locked(ecu_process):
    assert exchange(bytes([0x10, 0x02])) == PROGRAMMING_SESSION_RESPONSE
    request = bytes([0x34, 0x00, 0x23, 0x00, 0x11, 0x22, 0x00, 0x0A])
    assert exchange(request) == bytes([0x7F, 0x34, 0x33])


def test_vcan_valid_package_accepted(ecu_process, oem_keys):
    oem_priv, _ = oem_keys
    unlock()

    manifest = build_manifest("2.4.0", "2.3.0", b"synthetic firmware image v2.4.0")
    assert uds_transfer(build_package(manifest, oem_priv)) == bytes([0x77])


def test_vcan_tampered_manifest_rejected(ecu_process, oem_keys):
    oem_priv, _ = oem_keys
    unlock()

    manifest = build_manifest("2.4.0", "2.3.0", b"image")
    package = build_package(manifest, oem_priv)
    tampered = dict(manifest)
    tampered["version"] = "9.9.9"
    tampered_package = canonical_manifest_bytes(tampered) + package[-64:]

    assert uds_transfer(tampered_package) == bytes([0x7F, 0x37, 0x72])


def test_vcan_downgrade_rejected(ecu_process, oem_keys):
    oem_priv, _ = oem_keys
    unlock()

    # 先接受 2.4.0，再提交签名合法的 2.3.0 → 必须拒绝降级
    new_manifest = build_manifest("2.4.0", "2.3.0", b"image")
    assert uds_transfer(build_package(new_manifest, oem_priv)) == bytes([0x77])

    old_manifest = build_manifest("2.3.0", "2.3.0", b"image")
    assert uds_transfer(build_package(old_manifest, oem_priv)) == bytes([0x7F, 0x37, 0x72])


def test_vcan_missing_block_rejected(ecu_process):
    unlock()
    assert exchange(bytes([0x34, 0x00, 0x23, 0x00, 0x11, 0x22, 0x01, 0x2C])) == DOWNLOAD_OK
    assert exchange(bytes([0x36, 0x02]) + b"\xaa" * 5) == bytes([0x7F, 0x36, 0x73])


def test_vcan_udsoncan_download_flow(ecu_process, oem_keys):
    """udsoncan 客户端路径：change_session → unlock → request_download → transfer_data → exit。"""
    from udsoncan import MemoryLocation
    from udsoncan.client import Client
    from udsoncan.connections import IsoTPSocketConnection

    oem_priv, _ = oem_keys
    manifest = build_manifest("2.4.0", "2.3.0", b"udsoncan synthetic image")
    package = build_package(manifest, oem_priv)

    connection = IsoTPSocketConnection(
        interface=INTERFACE,
        address=isotp.Address(
            isotp.AddressingMode.Normal_11bits,
            rxid=CLIENT_RX_ID,
            txid=CLIENT_TX_ID,
        ),
    )
    client = Client(
        connection,
        config={"security_algo": lambda seed, level: keygen.derive_key(seed)},
    )
    client.open()

    try:
        assert client.change_session(2).positive
        assert client.unlock_security_access(1).positive

        download = client.request_download(
            MemoryLocation(
                0x112233,
                len(package),
                address_format=24,       # 本仓 0x34 契约：地址 3 字节
                memorysize_format=16,    # 本仓 0x34 契约：size 2 字节（ALFID=0x23）
            )
        )
        assert download.positive

        block_counter = 1
        for offset in range(0, len(package), 126):
            chunk = package[offset:offset + 126]
            assert client.transfer_data(block_counter, chunk).positive
            block_counter = (block_counter + 1) & 0xFF

        assert client.request_transfer_exit().positive
    finally:
        client.close()
