#!/usr/bin/env python3
"""
UDS Basic —— 0x31 RoutineControl vcan0 端到端测试

在真实 SocketCAN / ISO-TP 链路上验证 0x31：

    Test
     → ISO-TP (can-isotp)
     → SocketCAN vcan0
     → Virtual ECU (ecu/ecu.py，独立子进程)

覆盖（本 feature scope）:

1. StartRoutine（0x2001 / 0x2002）→ 71 01
2. StopRoutine → 71 02
3. RequestRoutineResults（0x2001 count / 0x2002 魔数）
4. double-start → 7F 31 22
5. stop-without-active → 7F 31 22
6. 未知 RID → 7F 31 31；未知 subFunction → 7F 31 12；长度错 → 7F 31 13
7. hardReset 后 routine inactive：Start → 11 01 → Stop → 7F 31 22
   （真实 vCAN 请求链验证 reset 生命周期）

明确**不测**：0x33 / 0x7E / 0x7F、routineControlOptionRecord /
routineStatusRecord（本 feature 不实现）。

前置：vcan0 已存在并 UP；本测试**独占总线**，勿与其它 CAN 测试并行。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_routine_vcan.py

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
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_OUT_OF_RANGE = 0x31

ROUTINE_START_COUNTER = 0x2001
ROUTINE_MAGIC_ECHO = 0x2002
MAGIC_ECHO_RESULT = b"\x31\x41\x59\x26"


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


def routine(sub_function: int, routine_id: int) -> bytes:
    """构造标准 4 字节请求：31 <sub> <RID:2>。"""
    return bytes([0x31, sub_function, (routine_id >> 8) & 0xFF, routine_id & 0xFF])


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
        prefix="uds-routine-ecu-", suffix=".log", delete=False
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


class RoutineVcanTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        _start_fresh_ecu()

    def setUp(self) -> None:
        _drain_vcan0()
        # 模块级 ECU 单例跨测试保持状态：用 hardReset 走协议把
        # routine 状态清空（本 feature 定义的生命周期清理点，
        # 与 test_e2e_vcan 的 _reset_to_default_session 同思路，
        # 不是测试 hack）。同时把会话复位回 default。
        exchange(bytes([0x11, 0x01]))

    def assertNegative(self, response, nrc: int) -> None:
        self.assertEqual(
            response,
            bytes([0x7F, 0x31, nrc]),
            f"期望 7F 31 {nrc:02X}，实际 {None if response is None else response.hex(' ')}",
        )


class TestRoutineE2E(RoutineVcanTestCase):
    """0x31 状态机 / NRC / reset 生命周期的真实链路验证。"""

    def test_start_2001(self) -> None:
        self.assertEqual(
            exchange(routine(0x01, ROUTINE_START_COUNTER)),
            bytes([0x71, 0x01, 0x20, 0x01]),
        )

    def test_start_2002(self) -> None:
        self.assertEqual(
            exchange(routine(0x01, ROUTINE_MAGIC_ECHO)),
            bytes([0x71, 0x01, 0x20, 0x02]),
        )

    def test_stop_2001_after_start(self) -> None:
        exchange(routine(0x01, ROUTINE_START_COUNTER))

        self.assertEqual(
            exchange(routine(0x02, ROUTINE_START_COUNTER)),
            bytes([0x71, 0x02, 0x20, 0x01]),
        )

    def test_results_2001(self) -> None:
        exchange(routine(0x01, ROUTINE_START_COUNTER))

        self.assertEqual(
            exchange(routine(0x03, ROUTINE_START_COUNTER)),
            bytes([0x71, 0x03, 0x20, 0x01, 0x00, 0x00, 0x00, 0x01]),
        )

    def test_results_2002_fixed_magic(self) -> None:
        self.assertEqual(
            exchange(routine(0x03, ROUTINE_MAGIC_ECHO)),
            bytes([0x71, 0x03, 0x20, 0x02]) + MAGIC_ECHO_RESULT,
        )

    def test_double_start_rejected(self) -> None:
        exchange(routine(0x01, ROUTINE_START_COUNTER))

        self.assertNegative(
            exchange(routine(0x01, ROUTINE_START_COUNTER)),
            NRC_CONDITIONS_NOT_CORRECT,
        )

    def test_stop_without_active_rejected(self) -> None:
        self.assertNegative(
            exchange(routine(0x02, ROUTINE_START_COUNTER)),
            NRC_CONDITIONS_NOT_CORRECT,
        )

    def test_unknown_rid(self) -> None:
        self.assertNegative(
            exchange(routine(0x01, 0xAAAA)),
            NRC_REQUEST_OUT_OF_RANGE,
        )

    def test_unknown_sub_function(self) -> None:
        self.assertNegative(
            exchange(routine(0x04, ROUTINE_START_COUNTER)),
            NRC_SUBFUNCTION_NOT_SUPPORTED,
        )

    def test_bad_length(self) -> None:
        self.assertNegative(
            exchange(bytes([0x31, 0x01])),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_hard_reset_clears_routine_state(self) -> None:
        """真实链路验证 reset 生命周期：Start → 11 01 → Stop → 7F 31 22。"""
        self.assertEqual(
            exchange(routine(0x01, ROUTINE_START_COUNTER)),
            bytes([0x71, 0x01, 0x20, 0x01]),
        )

        self.assertEqual(exchange(bytes([0x11, 0x01])), bytes([0x51, 0x01]))

        self.assertNegative(
            exchange(routine(0x02, ROUTINE_START_COUNTER)),
            NRC_CONDITIONS_NOT_CORRECT,
        )

        # 复位后 Results 计数归零
        self.assertEqual(
            exchange(routine(0x03, ROUTINE_START_COUNTER)),
            bytes([0x71, 0x03, 0x20, 0x01, 0x00, 0x00, 0x00, 0x00]),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
