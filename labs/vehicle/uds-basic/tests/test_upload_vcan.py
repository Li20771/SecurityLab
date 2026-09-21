#!/usr/bin/env python3
"""
UDS Basic —— 0x35 RequestUpload vcan0 端到端测试

在真实 SocketCAN / ISO-TP 链路上验证 upload transfer：

    Test
     → ISO-TP (can-isotp)
     → SocketCAN vcan0
     → Virtual ECU (ecu/ecu.py，独立子进程)

覆盖（按已冻结的 Design Review v3）:

1. 35 success（75 20 00 80）；bad DFI / ALFID → 31
2. 35 后立即 36 00 → 73（initial duplicate 误判防线，真实链路）
3. 36 01 first block；duplicate → 同块重发
4. multi-block（含 126 字节块经 ISO-TP 多帧传输——transport
   segmentation ≠ UDS BSC，两层显式区分）
5. wrong BSC → 73；upload 带 data 请求 → 13；before 35 → 24
6. 37 incomplete → 22 → 补块 → 77；complete 后 36/37 → 24
7. hardReset abort（35 → 11 01 → 36 → 24）
8. session mid-transfer（35 → 10 03 → 36 继续）
9. 34 ↔ 35 互斥（真实链路双向）

前置：vcan0 已存在并 UP；本测试**独占总线**，勿与其它 CAN 测试并行。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_upload_vcan.py

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

NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_WRONG_BLOCK_SEQUENCE_COUNTER = 0x73


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


def upload_request(dfi=0x00, alfid=0x23, address=0x112233, size=130) -> bytes:
    return bytes([
        0x35, dfi, alfid,
        (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF,
        (size >> 8) & 0xFF, size & 0xFF,
    ])


def download_request(size: int = 8) -> bytes:
    return bytes([0x34, 0x00, 0x23, 0x11, 0x22, 0x33,
                  (size >> 8) & 0xFF, size & 0xFF])


def block_request(counter: int, data: bytes = b"") -> bytes:
    return bytes([0x36, counter]) + data


def source_chunk(position: int, length: int) -> bytes:
    """与 ECU 侧 _upload_chunk 相同的 256 字节循环模式（测试端对照）。"""
    return bytes((position + i) % 256 for i in range(length))


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
        prefix="uds-upload-ecu-", suffix=".log", delete=False
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


class UploadVcanTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        _start_fresh_ecu()

    def setUp(self) -> None:
        _drain_vcan0()
        # 模块级 ECU 单例跨测试保持状态：用 hardReset 走协议清掉
        # 进行中的 transfer（本 feature 定义的生命周期清理点，
        # 与 download/routine/dtc e2e 同思路，不是测试 hack）。
        exchange(bytes([0x11, 0x01]))


class TestUploadE2E(UploadVcanTestCase):
    """0x35 upload 方向真实链路验证。"""

    def test_35_success(self) -> None:
        self.assertEqual(
            exchange(upload_request(size=130)),
            bytes([0x75, 0x20, 0x00, 0x80]),
        )

    def test_35_bad_dfi(self) -> None:
        self.assertEqual(
            exchange(upload_request(dfi=0x01)),
            bytes([0x7F, 0x35, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_35_bad_alfid(self) -> None:
        self.assertEqual(
            exchange(upload_request(alfid=0x32)),
            bytes([0x7F, 0x35, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_35_while_download_active(self) -> None:
        exchange(download_request(size=4))

        self.assertEqual(
            exchange(upload_request(size=130)),
            bytes([0x7F, 0x35, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_34_while_upload_active(self) -> None:
        exchange(upload_request(size=130))

        self.assertEqual(
            exchange(download_request(size=4)),
            bytes([0x7F, 0x34, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_36_00_immediately_after_35(self) -> None:
        """Blocker 1 真实链路：初始 36 00 → 73，不是 duplicate。"""
        exchange(upload_request(size=130))

        self.assertEqual(
            exchange(block_request(0x00)),
            bytes([0x7F, 0x36, NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )

    def test_36_01_first_block(self) -> None:
        exchange(upload_request(size=130))

        response = exchange(block_request(0x01))

        self.assertEqual(response, bytes([0x76, 0x01]) + source_chunk(0, 126))

    def test_36_duplicate_block(self) -> None:
        exchange(upload_request(size=130))

        first = exchange(block_request(0x01))
        duplicate = exchange(block_request(0x01))

        self.assertEqual(duplicate, first)

    def test_36_multiblock(self) -> None:
        exchange(upload_request(size=130))

        self.assertEqual(
            exchange(block_request(0x01)),
            bytes([0x76, 0x01]) + source_chunk(0, 126))
        self.assertEqual(
            exchange(block_request(0x02)),
            bytes([0x76, 0x02]) + source_chunk(126, 4))

    def test_36_wrong_bsc(self) -> None:
        exchange(upload_request(size=130))

        self.assertEqual(
            exchange(block_request(0x05)),
            bytes([0x7F, 0x36, NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )

    def test_36_data_bearing_request_rejected(self) -> None:
        exchange(upload_request(size=130))

        self.assertEqual(
            exchange(block_request(0x01, b"\xAA")),
            bytes([0x7F, 0x36, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_36_before_35(self) -> None:
        self.assertEqual(
            exchange(block_request(0x01)),
            bytes([0x7F, 0x36, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_37_incomplete_then_complete(self) -> None:
        exchange(upload_request(size=130))
        exchange(block_request(0x01))

        self.assertEqual(
            exchange(bytes([0x37])),
            bytes([0x7F, 0x37, NRC_CONDITIONS_NOT_CORRECT]),
        )

        exchange(block_request(0x02))
        self.assertEqual(exchange(bytes([0x37])), bytes([0x77]))

    def test_37_after_complete_is_idle(self) -> None:
        exchange(upload_request(size=4))
        exchange(block_request(0x01))
        self.assertEqual(exchange(bytes([0x37])), bytes([0x77]))

        self.assertEqual(
            exchange(block_request(0x01)),
            bytes([0x7F, 0x36, NRC_REQUEST_SEQUENCE_ERROR]),
        )
        self.assertEqual(
            exchange(bytes([0x37])),
            bytes([0x7F, 0x37, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_hard_reset_aborts_upload(self) -> None:
        exchange(upload_request(size=130))
        exchange(block_request(0x01))

        self.assertEqual(exchange(bytes([0x11, 0x01])), bytes([0x51, 0x01]))

        self.assertEqual(
            exchange(block_request(0x01)),
            bytes([0x7F, 0x36, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_session_switch_mid_upload(self) -> None:
        exchange(upload_request(size=130))
        exchange(block_request(0x01))

        self.assertEqual(
            exchange(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))

        self.assertEqual(
            exchange(block_request(0x02)),
            bytes([0x76, 0x02]) + source_chunk(126, 4))
        self.assertEqual(exchange(bytes([0x37])), bytes([0x77]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
