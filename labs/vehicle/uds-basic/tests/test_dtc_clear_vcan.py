#!/usr/bin/env python3
"""
UDS Basic —— 0x14 ClearDiagnosticInformation vcan0 端到端测试

在真实 SocketCAN / ISO-TP 链路上验证 0x14：

    Test
     → ISO-TP (can-isotp)
     → SocketCAN vcan0
     → Virtual ECU (ecu/ecu.py，独立子进程)

覆盖（按已冻结的 Design Review）：

1. **核心顺序链路**（单条用例内严格按序执行，不依赖 unittest 的字母序）：

       19 0A        → 清除前有 3 个 DTC（status 01/02/04）
       14 FF FF FF  → 54
       14 FF FF FF  → 54（all-clear 幂等）
       19 01 FF     → DTCCount = 0
       19 02 FF     → 无匹配项（仅 availability mask）
       19 0A        → **仍保留 3 个成员，但 status 全 0**
       14 10 00 01  → 54（对已为 0 的 DTC 再 clear，幂等）
       11 01        → 51 01
       19 0A        → status **不被复位**（hardReset 保留 DTC 记录内容）

2. NRC 用例（4 字节长度 / 未支持 groupOfDTC），**不修改任何 DTC status**

**为何必须独占 ECU 进程**：本 ECU **没有任何协议可见的手段恢复 DTC
status** —— ``0x11 hardReset`` 刻意保留 ``dtc_records`` 内容，``0x19``
只读。因此一旦清除即在本进程内不可逆。故本文件使用自己的 ECU 子进程，
不与 ``test_dtc_vcan.py``（断言出厂 status 01/02/04）共用。

**为何分两个测试类**：链路类必须看到**出厂** status（01/02/04），
而它自己会把 status 清成 0。若把 NRC 用例与链路类放在同一个 ECU 上，
任何会清除状态的用例都会按字母序抢在链路类之前跑、把链路类的基线断言
改坏（这正是本文件初版踩过的坑）。两类各自 ``setUpClass`` 启动独立
ECU，从而与执行顺序无关。

**已知限制（如实记录）**：本 ECU **没有 DTC 产生 / 故障注入机制**
（``0x85 ControlDTCSetting`` 未实现，也没有任何服务会置位 DTC status）。
因此本测试只能验证"清除 → 观察 → 复位保留"三段语义，
**不能**验证"故障发生 → DTC 置位 → 清除"的真实链路。
本 PR **不**为此新建 DTC 产生框架。

前置：vcan0 已存在并 UP；本测试**独占总线**，勿与其它 CAN 测试并行。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_dtc_clear_vcan.py

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

SID_CLEAR_DTC = 0x14
SID_READ_DTC = 0x19
SID_ECU_RESET = 0x11

POS_CLEAR_DTC = 0x54
POS_ECU_RESET = 0x51

NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_REQUEST_OUT_OF_RANGE = 0x31

#: 出厂 DTC 状态（与 ecu/dtc_database.py 一致，用于 byte-level 断言）。
FACTORY = {0x100001: 0x01, 0x100002: 0x02, 0x100003: 0x04}

DTC_STATUS_AVAILABILITY_MASK = 0xFF


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


def clear_request(group_of_dtc: int) -> bytes:
    return bytes([
        SID_CLEAR_DTC,
        (group_of_dtc >> 16) & 0xFF,
        (group_of_dtc >> 8) & 0xFF,
        group_of_dtc & 0xFF,
    ])


def expected_supported_dtc(statuses: list[int]) -> bytes:
    """``19 0A`` 的期望响应：availability mask + 按 DTC 升序的记录。"""
    payload = bytes([0x59, DTC_STATUS_AVAILABILITY_MASK])

    for dtc, status in zip(sorted(FACTORY), statuses, strict=True):
        payload += bytes([(dtc >> 16) & 0xFF, (dtc >> 8) & 0xFF, dtc & 0xFF, status])

    return payload


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
        prefix="uds-dtc-clear-ecu-", suffix=".log", delete=False
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


class ClearDtcVcanTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        _start_fresh_ecu()

    def setUp(self) -> None:
        # 刻意**不**在 setUp 里用 hardReset 当"复位"：hardReset 保留 DTC
        # 记录内容，对 DTC status 毫无复位作用。清除在本进程内不可逆，
        # 因此状态相关的断言全部集中在下面那条顺序链路用例里。
        _drain_vcan0()


class TestClearDtcChainE2E(ClearDtcVcanTestCase):
    """核心顺序链路：整个场景写在一**条**用例里，严格按序执行。

    刻意不用多个方法 + 字母序隐式排序：清除在本进程内不可逆，
    拆成多个方法会让"谁先跑"变成隐性依赖。
    """

    def test_clear_chain_end_to_end(self) -> None:
        """19 0A → 14（含重复）→ 19 → 14 → 11 → 19（单条用例内严格按序）。"""

        # --- 清除前：3 个 DTC，status 01/02/04 ---
        self.assertEqual(
            exchange(bytes([SID_READ_DTC, 0x0A])),
            expected_supported_dtc([0x01, 0x02, 0x04]),
        )
        self.assertEqual(
            exchange(bytes([SID_READ_DTC, 0x01, 0xFF])),
            bytes([0x59, 0xFF, 0x01, 0x00, 0x03]),
        )

        # --- all-clear ---
        self.assertEqual(exchange(clear_request(0xFFFFFF)), bytes([POS_CLEAR_DTC]))

        # --- all-clear 幂等：重复执行仍 54 ---
        self.assertEqual(exchange(clear_request(0xFFFFFF)), bytes([POS_CLEAR_DTC]))

        # --- DTCCount 归零 ---
        self.assertEqual(
            exchange(bytes([SID_READ_DTC, 0x01, 0xFF])),
            bytes([0x59, 0xFF, 0x01, 0x00, 0x00]),
        )

        # --- 无匹配项：只回 availability mask ---
        self.assertEqual(
            exchange(bytes([SID_READ_DTC, 0x02, 0xFF])),
            bytes([0x59, 0xFF]),
        )

        # --- 成员保留、status 全 0（本 feature 的核心可观察语义）---
        self.assertEqual(
            exchange(bytes([SID_READ_DTC, 0x0A])),
            expected_supported_dtc([0x00, 0x00, 0x00]),
        )

        # --- 对已为 0 的 DTC 再 clear：幂等 ---
        self.assertEqual(exchange(clear_request(0x100001)), bytes([POS_CLEAR_DTC]))

        # --- hardReset 不恢复 status ---
        self.assertEqual(
            exchange(bytes([SID_ECU_RESET, 0x01])),
            bytes([POS_ECU_RESET, 0x01]),
        )
        self.assertEqual(
            exchange(bytes([SID_READ_DTC, 0x0A])),
            expected_supported_dtc([0x00, 0x00, 0x00]),
        )


class TestClearDtcNrcE2E(ClearDtcVcanTestCase):
    """与 DTC 状态无关的 NRC 用例（不修改任何 DTC status）。

    本类拥有**自己的 ECU 进程**：虽然清除在本进程内不可逆，但两类各自
    持有独立 ECU，因此执行顺序无关、互不影响。
    """

    def test_unknown_dtc_is_out_of_range(self) -> None:
        self.assertEqual(
            exchange(clear_request(0x100009)),
            bytes([0x7F, SID_CLEAR_DTC, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_reversed_byte_order_is_out_of_range(self) -> None:
        """``00 01 10`` 若按小端解析会命中 0x100001 —— 必须 0x31。"""
        self.assertEqual(
            exchange(bytes([SID_CLEAR_DTC, 0x00, 0x01, 0x10])),
            bytes([0x7F, SID_CLEAR_DTC, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_arbitrary_unknown_is_out_of_range(self) -> None:
        self.assertEqual(
            exchange(clear_request(0x123456)),
            bytes([0x7F, SID_CLEAR_DTC, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_functional_group_ffff00_is_out_of_range(self) -> None:
        """功能组 OUT OF SCOPE：真实链路上同样是 0x31（本项目 profile）。"""
        self.assertEqual(
            exchange(clear_request(0xFFFF00)),
            bytes([0x7F, SID_CLEAR_DTC, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_functional_group_fffffe_is_out_of_range(self) -> None:
        self.assertEqual(
            exchange(clear_request(0xFFFFFE)),
            bytes([0x7F, SID_CLEAR_DTC, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_zero_group_is_out_of_range(self) -> None:
        self.assertEqual(
            exchange(clear_request(0x000000)),
            bytes([0x7F, SID_CLEAR_DTC, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_bare_14_is_length_error(self) -> None:
        self.assertEqual(
            exchange(bytes([SID_CLEAR_DTC])),
            bytes([0x7F, SID_CLEAR_DTC, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_short_request_is_length_error(self) -> None:
        self.assertEqual(
            exchange(bytes([SID_CLEAR_DTC, 0xFF, 0xFF])),
            bytes([0x7F, SID_CLEAR_DTC, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_extra_byte_is_length_error(self) -> None:
        self.assertEqual(
            exchange(bytes([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF, 0x00])),
            bytes([0x7F, SID_CLEAR_DTC, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_extra_two_bytes_is_length_error(self) -> None:
        self.assertEqual(
            exchange(bytes([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])),
            bytes([0x7F, SID_CLEAR_DTC, NRC_INCORRECT_MESSAGE_LENGTH]),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
