#!/usr/bin/env python3
"""
UDS Basic —— 0x2E / 0x3E 在 vcan0 上的端到端集成测试。

与离线测试的区别：这里启动**真实 ECU 子进程**（ecu/ecu.py），通过
SocketCAN + 内核 ISO-TP 收发，完整走一遍：

    10 03 进 extended → 27 01/02 SecurityAccess 解锁
        → 2E 写 DID → 22 读回校验 → 11 01 复位恢复出厂
    3E 00 正响应 / 3E 80 总线上确实无响应（抑制位）

只允许 vcan0，不接触真实 CAN / ECU / 车辆。

运行：

    python3 tests/test_write_did_vcan.py
或：
    bash tests/run_tests.sh all
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAB_DIR = os.path.dirname(_HERE)
_ECU_DIR = os.path.join(_LAB_DIR, "ecu")
sys.path.insert(0, _ECU_DIR)

import isotp  # noqa: E402
import keygen  # noqa: E402

INTERFACE = os.environ.get("UDS_INTERFACE", "vcan0")
TX_ID = 0x7E0   # Tester -> ECU
RX_ID = 0x7E8   # ECU -> Tester
ECU_STARTUP_TIMEOUT = 10.0

DID_WRITABLE = 0xF102
DID_READONLY = 0xF190

_ECU_PROCESS = None
_ECU_LOG = None


def exchange(request: bytes, timeout: float = 1.5):
    """新建 ISO-TP 连接发送一条请求并收响应；超时/异常返回 None。"""
    sock = None
    try:
        sock = isotp.socket()
        sock.bind(
            INTERFACE,
            isotp.Address(
                isotp.AddressingMode.Normal_11bits, rxid=RX_ID, txid=TX_ID
            ),
        )
        sock.settimeout(timeout)
        sock.send(request)
        return sock.recv()
    except Exception:  # noqa: BLE001 —— 超时/无响应统一返回 None
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


def _wait_ecu_ready() -> None:
    deadline = time.monotonic() + ECU_STARTUP_TIMEOUT
    last = None
    while time.monotonic() < deadline:
        resp = exchange(bytes([0x10, 0x01]), timeout=1.0)
        if resp is not None and resp and resp[0] == 0x50:
            return
        last = resp
        time.sleep(0.3)
    raise RuntimeError(f"ECU 在 {ECU_STARTUP_TIMEOUT}s 内未就绪（last={last!r}）")


def _start_ecu() -> None:
    global _ECU_PROCESS, _ECU_LOG
    if _ECU_PROCESS is not None and _ECU_PROCESS.poll() is None:
        return
    _ECU_LOG = tempfile.NamedTemporaryFile(
        prefix="ecu-write-did-", suffix=".log", delete=False
    )
    _ECU_PROCESS = subprocess.Popen(
        [sys.executable, "-u", os.path.join(_ECU_DIR, "ecu.py")],
        cwd=_ECU_DIR,
        stdout=_ECU_LOG,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=dict(os.environ),
    )
    _wait_ecu_ready()


def _stop_ecu() -> None:
    global _ECU_PROCESS
    if _ECU_PROCESS is not None and _ECU_PROCESS.poll() is None:
        try:
            os.killpg(os.getpgid(_ECU_PROCESS.pid), signal.SIGTERM)
            _ECU_PROCESS.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                os.killpg(os.getpgid(_ECU_PROCESS.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass
    _ECU_PROCESS = None


def _unlock_extended() -> None:
    """10 03 进 extended，再走完整 27 种子/密钥流程解锁。"""
    r = exchange(bytes([0x10, 0x03]))
    assert r is not None and r[0] == 0x50, f"进入 extended 失败: {r!r}"

    seed_resp = exchange(bytes([0x27, 0x01]))
    assert seed_resp is not None and seed_resp[:2] == bytes([0x67, 0x01]), (
        f"RequestSeed 失败: {seed_resp!r}"
    )
    seed = seed_resp[2:]
    key = keygen.derive_key(seed)
    key_resp = exchange(bytes([0x27, 0x02]) + bytes(key))
    assert key_resp == bytes([0x67, 0x02]), f"SendKey 解锁失败: {key_resp!r}"


class VcanE2ETestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _start_ecu()

    def setUp(self) -> None:
        # 每个用例前回到 default 会话（同时锁定安全状态），保证起点确定
        exchange(bytes([0x10, 0x01]))

    # ---- 0x3E TesterPresent ----

    def test_tester_present_positive(self):
        resp = exchange(bytes([0x3E, 0x00]))
        self.assertEqual(resp, bytes([0x7E, 0x00]))

    def test_tester_present_suppressed_no_response(self):
        # 3E 80：ECU 必须在总线上保持沉默，客户端超时得到 None
        resp = exchange(bytes([0x3E, 0x80]), timeout=1.0)
        self.assertIsNone(resp, f"抑制位下不应有响应，实际收到: {resp!r}")

    def test_tester_present_bad_subfunction(self):
        resp = exchange(bytes([0x3E, 0x01]))
        self.assertEqual(resp, bytes([0x7F, 0x3E, 0x12]))

    # ---- 0x2E WriteDataByIdentifier ----

    def test_write_default_session_denied(self):
        resp = exchange(bytes([0x2E, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF])
                        + b"NEW")
        self.assertEqual(resp, bytes([0x7F, 0x2E, 0x7F]))

    def test_write_extended_locked_denied(self):
        r = exchange(bytes([0x10, 0x03]))
        self.assertEqual(r[0], 0x50)
        resp = exchange(bytes([0x2E, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF])
                        + b"NEW")
        self.assertEqual(resp, bytes([0x7F, 0x2E, 0x33]))

    def test_write_readback_after_unlock(self):
        _unlock_extended()
        payload = b"E2E_CONF"
        wr = exchange(
            bytes([0x2E, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF]) + payload
        )
        self.assertEqual(wr, bytes([0x6E, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF]))

        rd = exchange(bytes([0x22, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF]))
        self.assertEqual(
            rd,
            bytes([0x62, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF]) + payload,
        )

    def test_write_readonly_did_denied(self):
        _unlock_extended()
        resp = exchange(
            bytes([0x2E, (DID_READONLY >> 8) & 0xFF, DID_READONLY & 0xFF]) + b"X"
        )
        self.assertEqual(resp, bytes([0x7F, 0x2E, 0x22]))

    def test_reset_restores_factory_value(self):
        _unlock_extended()
        exchange(
            bytes([0x2E, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF]) + b"CHG"
        )
        # hardReset
        self.assertEqual(exchange(bytes([0x11, 0x01])), bytes([0x51, 0x01]))
        # 复位后默认会话即可读，值恢复出厂默认
        rd = exchange(bytes([0x22, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF]))
        self.assertEqual(rd, bytes([0x62, 0xF1, 0x02]) + b"DEFAULT_CONFIG")

    @classmethod
    def tearDownClass(cls) -> None:
        _stop_ecu()


if __name__ == "__main__":
    unittest.main(verbosity=2)
