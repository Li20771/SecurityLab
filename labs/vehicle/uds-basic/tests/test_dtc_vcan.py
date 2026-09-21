#!/usr/bin/env python3
"""
UDS Basic —— 0x19 ReadDTCInformation vcan0 端到端测试

在真实 SocketCAN / ISO-TP 链路上验证 0x19：

    Test
     → ISO-TP (can-isotp)
     → SocketCAN vcan0
     → Virtual ECU (ecu/ecu.py，独立子进程)

覆盖（本 feature scope）:

1. 0x19/01 reportNumberOfDTCByStatusMask 正响应（mask 过滤 + DTCCount）
2. 0x19/02 reportDTCByStatusMask 正响应（过滤 + byte-level exact）
3. 0x19/0A reportSupportedDTC 正响应（全部 DTC）
4. 非法 reportType（19 03 / 19 7F）→ 7F 19 12
5. 长度错误（缺 mask / 多余数据）→ 7F 19 13
6. hardReset 保留 DTC：11 01 前后 19 02 FF 响应一致

明确**不测**：其他 0x19 reportType、suppressPosRspMsgIndicationBit。

前置：vcan0 已存在并 UP；本测试**独占总线**，勿与其它 CAN 测试并行。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_dtc_vcan.py

或：

    bash tests/run_tests.sh all
"""

import atexit
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
_ECU_SCRIPT = os.path.join(_ECU_DIR, "ecu.py")

import isotp  # noqa: E402

INTERFACE = os.environ.get("UDS_INTERFACE", "vcan0")
TX_ID = 0x7E0
RX_ID = 0x7E8

ECU_STARTUP_TIMEOUT = 10.0
RECV_TIMEOUT = 1.5

NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13

#: 出厂默认 DTC 状态（与 ecu/dtc_database.py 保持一致，用于 byte-level 断言）。
DEFAULT_DTC_RECORDS = {
    0x100001: 0x01,
    0x100002: 0x02,
    0x100003: 0x04,
}

DTC_STATUS_AVAILABILITY_MASK = 0xFF
DTC_FORMAT_IDENTIFIER = 0x01


def exchange(payload: bytes, timeout: float = RECV_TIMEOUT):
    """以 (tx=0x7E0, rx=0x7E8) 新建 ISO-TP 连接收发；超时返回 None。"""
    sock = None
    try:
        sock = isotp.socket()
        sock.bind(
            INTERFACE,
            isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                rxid=RX_ID,
                txid=TX_ID,
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


# ----------------------------------------------------------------------
# 模块级 ECU 单例（整文件只启动一个 ECU 子进程）
# ----------------------------------------------------------------------

_ECU_PROCESS = None
_ECU_LOG = None


def _wait_for_ecu_ready() -> None:
    """反复 10 01 探测，直到 ECU 应答 50 01（就绪探测，非测试断言）。"""
    deadline = time.monotonic() + ECU_STARTUP_TIMEOUT
    last_error = None

    while time.monotonic() < deadline:
        try:
            response = exchange(bytes([0x10, 0x01]), timeout=0.5)

            if response is not None and response[:2] == bytes([0x50, 0x01]):
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc

        time.sleep(0.2)

    raise AssertionError(
        f"虚拟 ECU 在 {ECU_STARTUP_TIMEOUT}s 内没有就绪：{last_error!r}"
    )


def _start_fresh_ecu() -> None:
    """启动一个全新 ECU 子进程（每测试类一次，保证状态干净）。"""
    global _ECU_PROCESS, _ECU_LOG

    _stop_ecu_process()

    env = os.environ.copy()
    env["UDS_LOCKOUT_SECONDS"] = "2"

    _ECU_LOG = tempfile.NamedTemporaryFile(
        prefix="uds-dtc-ecu-", suffix=".log", delete=False
    )
    _ECU_PROCESS = subprocess.Popen(
        [sys.executable, "-u", _ECU_SCRIPT],
        cwd=_ECU_DIR,
        stdout=_ECU_LOG,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )

    _wait_for_ecu_ready()


def _stop_ecu_process() -> None:
    global _ECU_PROCESS, _ECU_LOG

    if _ECU_PROCESS is not None and _ECU_PROCESS.poll() is None:
        try:
            os.killpg(os.getpgid(_ECU_PROCESS.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            _ECU_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(_ECU_PROCESS.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass

    if _ECU_LOG is not None:
        try:
            _ECU_LOG.close()
        except Exception:  # noqa: BLE001
            pass

        _ECU_LOG = None

    _ECU_PROCESS = None


atexit.register(_stop_ecu_process)


def _drain_vcan0(duration: float = 0.2) -> None:
    """丢弃 vcan0 上残留报文，避免上次实验遗留帧干扰。"""
    import can

    bus = can.interface.Bus(interface="socketcan", channel=INTERFACE)
    bus.socket.settimeout(0.05)

    deadline = time.monotonic() + duration

    try:
        while time.monotonic() < deadline:
            try:
                if bus.recv(timeout=0.05) is None:
                    continue
            except Exception:  # noqa: BLE001
                break
    finally:
        bus.shutdown()


# ----------------------------------------------------------------------
# 测试
# ----------------------------------------------------------------------


class DtcVcanTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        _start_fresh_ecu()

    def setUp(self) -> None:
        _drain_vcan0()

    def assertNegative(self, response, nrc: int) -> None:
        self.assertEqual(
            response,
            bytes([0x7F, 0x19, nrc]),
            f"期望 7F 19 {nrc:02X}，实际 {None if response is None else response.hex(' ')}",
        )


class TestDtcE2E(DtcVcanTestCase):
    """0x19 正响应 / NRC / reset 语义的真实链路验证。"""

    def test_01_positive_full_mask(self) -> None:
        response = exchange(bytes([0x19, 0x01, 0xFF]))

        self.assertEqual(
            response,
            bytes([0x59, DTC_STATUS_AVAILABILITY_MASK,
                   DTC_FORMAT_IDENTIFIER, 0x00, len(DEFAULT_DTC_RECORDS)]),
        )

    def test_01_positive_zero_mask(self) -> None:
        response = exchange(bytes([0x19, 0x01, 0x00]))

        self.assertEqual(response, bytes([0x59, 0xFF, 0x01, 0x00, 0x00]))

    def test_02_positive_byte_exact(self) -> None:
        response = exchange(bytes([0x19, 0x02, 0xFF]))

        expected = bytes([0x59, DTC_STATUS_AVAILABILITY_MASK])

        for dtc in sorted(DEFAULT_DTC_RECORDS):
            status = DEFAULT_DTC_RECORDS[dtc]
            expected += bytes([
                (dtc >> 16) & 0xFF, (dtc >> 8) & 0xFF, dtc & 0xFF, status,
            ])

        self.assertEqual(response, expected)

    def test_02_filters_by_mask(self) -> None:
        response = exchange(bytes([0x19, 0x02, 0x01]))

        self.assertEqual(
            response,
            bytes([0x59, 0xFF, 0x10, 0x00, 0x01, 0x01]),
        )

    def test_0a_positive_reports_all(self) -> None:
        response = exchange(bytes([0x19, 0x0A]))

        expected = bytes([0x59, DTC_STATUS_AVAILABILITY_MASK])

        for dtc in sorted(DEFAULT_DTC_RECORDS):
            status = DEFAULT_DTC_RECORDS[dtc]
            expected += bytes([
                (dtc >> 16) & 0xFF, (dtc >> 8) & 0xFF, dtc & 0xFF, status,
            ])

        self.assertEqual(response, expected)

    def test_invalid_report_type(self) -> None:
        for report_type in (0x03, 0x7F):
            with self.subTest(report_type=report_type):
                self.assertNegative(
                    exchange(bytes([0x19, report_type])),
                    NRC_SUBFUNCTION_NOT_SUPPORTED,
                )

    def test_bad_lengths(self) -> None:
        cases = (
            (bytes([0x19, 0x01]), "01 缺 mask"),
            (bytes([0x19, 0x01, 0xFF, 0x00]), "01 多余数据"),
            (bytes([0x19, 0x02]), "02 缺 mask"),
            (bytes([0x19, 0x02, 0xFF, 0x00]), "02 多余数据"),
            (bytes([0x19, 0x0A, 0x00]), "0A 多余数据"),
        )

        for request, label in cases:
            with self.subTest(label=label):
                self.assertNegative(
                    exchange(request), NRC_INCORRECT_MESSAGE_LENGTH
                )

    def test_hard_reset_preserves_dtc(self) -> None:
        """11 01 前后 19 02 FF 响应必须一致：DTC 是故障记忆，不随复位清除。"""
        before = exchange(bytes([0x19, 0x02, 0xFF]))

        reset_response = exchange(bytes([0x11, 0x01]))
        self.assertEqual(reset_response, bytes([0x51, 0x01]))

        after = exchange(bytes([0x19, 0x02, 0xFF]))

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
