#!/usr/bin/env python3
"""
UDS Basic —— 0x34/0x36/0x37 download transfer vcan0 端到端测试

在真实 SocketCAN / ISO-TP 链路上验证 atomic transfer feature：

    Test
     → ISO-TP (can-isotp)
     → SocketCAN vcan0
     → Virtual ECU (ecu/ecu.py，独立子进程)

覆盖（本 feature scope）:

1. 0x34 RequestDownload：success / bad length / bad DFI / bad ALFID
2. 0x36 TransferData：单块 / 多块 / 重复块幂等 / 错序 BSC / before 0x34
3. 0x37：incomplete / complete / complete 后再 0x36 → 0x24
4. hardReset abort（0x34 → 11 01 → 0x36 → 0x24）
5. session 切换 mid-transfer（0x34 → 10 03 → 0x36 继续成功）

重要：ISO-TP 的 FF/CF/FC 分段是**传输层信封**，与 UDS 的
blockSequenceCounter（应用层块序）是完全不同层次的概念——
同一测试中既出现单帧传输又出现多帧传输（如 126 字节数据块
由 ISO-TP 自动分帧），UDS 层 BSC 仍按 1/2/3 递增，互不混淆。

前置：vcan0 已存在并 UP；本测试**独占总线**，勿与其它 CAN 测试并行。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_transfer_vcan.py

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


def download(dfi=0x00, alfid=0x23, address=0x112233, size=8) -> bytes:
    return bytes([
        0x34, dfi, alfid,
        (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF,
        (size >> 8) & 0xFF, size & 0xFF,
    ])


def block(counter: int, data: bytes) -> bytes:
    return bytes([0x36, counter]) + data


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
        prefix="uds-transfer-ecu-", suffix=".log", delete=False
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


class TransferVcanTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        _start_fresh_ecu()

    def setUp(self) -> None:
        _drain_vcan0()
        # 模块级 ECU 单例跨测试保持状态：用 hardReset 走协议清掉
        # 进行中的 download（本 feature 定义的生命周期清理点，
        # 与 routine/dtc e2e 同思路，不是测试 hack）。
        exchange(bytes([0x11, 0x01]))


class TestTransferE2E(TransferVcanTestCase):
    """0x34/0x36/0x37 真实链路验证。"""

    def test_34_success(self) -> None:
        self.assertEqual(
            exchange(download(size=8)),
            bytes([0x74, 0x20, 0x00, 0x80]),
        )

    def test_34_bad_length(self) -> None:
        self.assertEqual(
            exchange(download(size=8)[:7]),
            bytes([0x7F, 0x34, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_34_bad_dfi(self) -> None:
        self.assertEqual(
            exchange(download(dfi=0x01)),
            bytes([0x7F, 0x34, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_34_bad_alfid(self) -> None:
        self.assertEqual(
            exchange(download(alfid=0x32)),
            bytes([0x7F, 0x34, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_34_zero_size(self) -> None:
        self.assertEqual(
            exchange(download(size=0)),
            bytes([0x7F, 0x34, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_36_single_block(self) -> None:
        exchange(download(size=4))

        self.assertEqual(
            exchange(block(1, b"\xAA\xBB\xCC\xDD")),
            bytes([0x76, 0x01]),
        )

    def test_36_multiple_blocks(self) -> None:
        exchange(download(size=6))

        self.assertEqual(exchange(block(1, b"\x01\x02")), bytes([0x76, 0x01]))
        self.assertEqual(exchange(block(2, b"\x03\x04")), bytes([0x76, 0x02]))
        self.assertEqual(exchange(block(3, b"\x05\x06")), bytes([0x76, 0x03]))

    def test_36_large_block_over_isotp_multiframe(self) -> None:
        """
        UDS 126 字节数据块经 ISO-TP 自动分帧（FF/CF/FC）传输，
        但 UDS 层 BSC 仍按 1 递增——两层分段概念显式区分。
        """
        exchange(download(size=126))

        self.assertEqual(
            exchange(block(1, bytes(126))),
            bytes([0x76, 0x01]),
        )

    def test_36_duplicate_block_idempotent(self) -> None:
        exchange(download(size=4))

        self.assertEqual(exchange(block(1, b"\xAA\xBB")), bytes([0x76, 0x01]))
        # 幂等重传：仍正响应；随后新块可用（counter 未推进）
        self.assertEqual(exchange(block(1, b"\xAA\xBB")), bytes([0x76, 0x01]))
        self.assertEqual(exchange(block(2, b"\xCC\xDD")), bytes([0x76, 0x02]))

    def test_36_wrong_bsc(self) -> None:
        exchange(download(size=4))

        self.assertEqual(
            exchange(block(2, b"\x01\x02")),
            bytes([0x7F, 0x36, NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )

    def test_36_before_34(self) -> None:
        self.assertEqual(
            exchange(block(1, b"\x01")),
            bytes([0x7F, 0x36, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_37_incomplete_then_complete(self) -> None:
        exchange(download(size=4))
        exchange(block(1, b"\xAA\xBB"))

        # incomplete → 22，随后补块可完成
        self.assertEqual(
            exchange(bytes([0x37])),
            bytes([0x7F, 0x37, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(exchange(block(2, b"\xCC\xDD")), bytes([0x76, 0x02]))
        self.assertEqual(exchange(bytes([0x37])), bytes([0x77]))

    def test_37_complete_then_transfer_is_idle(self) -> None:
        exchange(download(size=2))
        exchange(block(1, b"\xAA\xBB"))
        self.assertEqual(exchange(bytes([0x37])), bytes([0x77]))

        # 成功后回 IDLE：0x36/0x37 都得到确定性 0x24
        self.assertEqual(
            exchange(block(1, b"\x01")),
            bytes([0x7F, 0x36, NRC_REQUEST_SEQUENCE_ERROR]),
        )
        self.assertEqual(
            exchange(bytes([0x37])),
            bytes([0x7F, 0x37, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_37_before_34(self) -> None:
        self.assertEqual(
            exchange(bytes([0x37])),
            bytes([0x7F, 0x37, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_hard_reset_aborts_download(self) -> None:
        exchange(download(size=4))
        exchange(block(1, b"\xAA\xBB"))

        self.assertEqual(exchange(bytes([0x11, 0x01])), bytes([0x51, 0x01]))

        self.assertEqual(
            exchange(block(1, b"\x01")),
            bytes([0x7F, 0x36, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_session_switch_mid_transfer(self) -> None:
        exchange(download(size=4))
        exchange(block(1, b"\xAA\xBB"))

        # 切换 extended session：download 保持 active
        self.assertEqual(
            exchange(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))

        self.assertEqual(exchange(block(2, b"\xCC\xDD")), bytes([0x76, 0x02]))
        self.assertEqual(exchange(bytes([0x37])), bytes([0x77]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
