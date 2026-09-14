#!/usr/bin/env python3
"""
UDS Basic —— vcan0 端到端测试

本测试在真实的 SocketCAN / ISO-TP 链路上运行：

    Test
     → ISO-TP (can-isotp / CAN_ISOTP)
     → SocketCAN vcan0
     → ISO-TP
     → Virtual ECU (ecu/ecu.py，独立子进程)

覆盖范围：

1. 原始 ISO-TP 报文测试（字节级断言）
2. 否定响应 NRC 测试
3. 未实现服务测试
4. udsoncan 客户端测试
   - change_session()
   - ecu_reset()
   - read_data_by_identifier()
5. ISO-TP 分帧验证（单帧 / 多帧）
6. candump 抓包验证

安全边界：

- 只使用 vcan0（虚拟 CAN，无物理收发器）
- 只与本机虚拟 ECU 通信
- 不连接真实车辆、真实 ECU、真实 CAN 适配器

前置条件：

- vcan0 已存在并 UP
- 使用网络实验虚拟环境：
  /home/lilike/.venvs/network/bin/python

运行：

    /home/lilike/.venvs/network/bin/python tests/test_e2e_vcan.py

或：

    bash tests/run_tests.sh
"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import can
import isotp
import udsoncan

from udsoncan.client import Client
from udsoncan.connections import IsoTPSocketConnection
from udsoncan.exceptions import (
    InvalidResponseException,
    NegativeResponseException,
)


# ----------------------------------------------------------------------
# 路径与通信参数
# ----------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAB_DIR = os.path.dirname(_HERE)
_ECU_DIR = os.path.join(_LAB_DIR, "ecu")
_ECU_SCRIPT = os.path.join(_ECU_DIR, "ecu.py")

sys.path.insert(0, _ECU_DIR)

INTERFACE = "vcan0"

# Tester -> ECU
TX_ID = 0x7E0

# ECU -> Tester
RX_ID = 0x7E8

ECU_STARTUP_TIMEOUT = 10.0

# 接收超时（秒）：ECU 是本地进程，1.5s 足够
RECV_TIMEOUT = 1.5

STALE_FRAME_DRAIN = 0.2

DID_F180 = 0xF180
DID_F190 = 0xF190
DID_UNKNOWN = 0xFFFF

DID_F180_DATA = b"BOOT-SW-1.0.0"
DID_F190_DATA = b"TESTECU0000000001"


def hexs(data: bytes) -> str:
    return data.hex(" ")


def negative_response(response) -> tuple:
    """把响应解析为 (sid, nrc)；不是否定响应则返回 None。"""
    if response is None:
        return None
    if len(response) == 3 and response[0] == 0x7F:
        return response[1], response[2]
    return None


class LenientClient(Client):
    """
    在 udsoncan.Client 基础上放宽异常处理：

    - 收到否定响应（NRC）时不抛异常，返回原始 Response
    - 收到数据长度不符合 DID 编解码配置的响应时不抛异常

    这样测试可以在不断言异常的情况下检查 NRC。
    正常路径的行为与标准 Client 完全一致。
    """

    def send_request(self, request, timeout: int = -1):
        try:
            return super().send_request(request, timeout)
        except NegativeResponseException:
            return self.last_response

    def raw_request(self, request, timeout: int = -1):
        """
        发送请求并返回未解释的 Response 对象。

        即使响应数据无法按服务定义解释，也不会抛异常。
        """
        if isinstance(request, udsoncan.Request):
            payload = request.get_payload()
        else:
            payload = bytes(request)

        self.conn.send(payload)

        try:
            frame = self.conn.wait_frame(timeout=self.config["p2_timeout"], exception=True)
        except Exception:  # noqa: BLE001 - 超时/底层错误统一按“无响应”处理
            return None

        if frame is None:
            return None

        response = udsoncan.Response.from_payload(frame)
        self.last_response = response

        return response


def drain_vcan0(duration: float = STALE_FRAME_DRAIN) -> int:
    """
    丢弃 vcan0 上的残留报文。

    避免上一次实验遗留的帧干扰本次测试。
    返回丢弃的帧数。
    """
    bus = can.interface.Bus(interface="socketcan", channel=INTERFACE)
    bus.socket.settimeout(0.05)

    drained = 0
    deadline = time.monotonic() + duration

    try:
        while time.monotonic() < deadline:
            try:
                if bus.recv(timeout=0.05) is not None:
                    drained += 1
            except Exception:
                break
    finally:
        bus.shutdown()

    return drained


def iotp_frame_type(frame: bytes) -> str:
    """判断 ISO-TP 帧类型（ISO 15765-2）。"""
    if not frame:
        return "empty"

    pci = frame[0] & 0xF0

    if pci == 0x00:
        return "single"
    if pci == 0x10:
        return "first"
    if pci == 0x20:
        return "consecutive"
    if pci == 0x30:
        return "flow_control"

    return f"unknown(0x{frame[0]:02X})"


# ======================================================================
# 基础测试类：管理虚拟 ECU 生命周期
# ======================================================================


def _open_ecu_log():
    """
    选择一个可写的 ECU 日志目标。

    返回 (log_target, log_path, log_handle)：

    - 有可写临时目录：写入临时文件，log_path 为其路径
    - 否则：使用管道，log_path 为 None
    """
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="uds-basic-ecu-",
            suffix=".log",
            delete=False,
        )
    except (OSError, FileNotFoundError):
        return subprocess.PIPE, None, None

    return handle, handle.name, handle


class VcanTestCase(unittest.TestCase):
    """启动一个虚拟 ECU 子进程，并为每个测试提供干净的 ISO-TP 连接。"""

    ecu_process = None
    ecu_log_path = None
    _ecu_log_handle = None

    @classmethod
    def setUpClass(cls) -> None:
        # ECU 日志优先写入临时文件（不在仓库内留下残留）；
        # 若环境不允许写临时目录，则退化为管道，测试仍然可以运行。
        log_target, cls.ecu_log_path, cls._ecu_log_handle = _open_ecu_log()

        cls.ecu_process = subprocess.Popen(
            [sys.executable, "-u", _ECU_SCRIPT],
            cwd=_ECU_DIR,
            stdout=log_target,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        if cls._ecu_log_handle is not None:
            cls.addClassCleanup(cls._ecu_log_handle.close)

        cls._wait_for_ecu()

    @classmethod
    def _read_ecu_log(cls) -> str:
        """读取 ECU 日志内容（文件或管道）。"""
        if cls.ecu_log_path is not None:
            cls._ecu_log_handle.flush()
            with open(cls.ecu_log_path, "r", encoding="utf-8") as handle:
                return handle.read()

        if cls.ecu_process is not None and cls.ecu_process.stdout is not None:
            import select

            chunks = []
            while select.select([cls.ecu_process.stdout], [], [], 0)[0]:
                chunk = os.read(cls.ecu_process.stdout.fileno(), 65536)
                if not chunk:
                    break

                chunks.append(chunk)

            return b"".join(chunks).decode("utf-8", errors="replace")

        return ""

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.ecu_process is not None and cls.ecu_process.poll() is None:
            try:
                os.killpg(os.getpgid(cls.ecu_process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass

            try:
                cls.ecu_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(cls.ecu_process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @classmethod
    def _wait_for_ecu(cls) -> None:
        """
        等待 ECU 真正开始响应。

        做法：反复发送 10 01，直到收到 50 01 为止。

        注意：ECU 在 bind 之前发出的帧不会被任何 socket 接收，
        因此这里必须在每次尝试时新建 socket。
        """
        deadline = time.monotonic() + ECU_STARTUP_TIMEOUT
        last_error = None

        while time.monotonic() < deadline:
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
                sock.settimeout(0.5)
                sock.send(bytes([0x10, 0x01]))

                response = sock.recv()

                if response is not None and response[:2] == bytes([0x50, 0x01]):
                    return
            except Exception as exc:  # noqa: BLE001 - 启动探测允许失败重试
                last_error = exc
            finally:
                if sock is not None:
                    sock.close()

            time.sleep(0.2)

        raise AssertionError(
            f"虚拟 ECU 在 {ECU_STARTUP_TIMEOUT}s 内没有就绪。"
            f"最后一个错误：{last_error!r}。"
            f"ECU 日志：{cls.ecu_log_path}"
        )

    # ---- 每个测试的准备与清理 ----

    def setUp(self) -> None:
        drain_vcan0()

        self._connections = []
        self._sockets = []

    def tearDown(self) -> None:
        for client in getattr(self, "_connections", []):
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 清理阶段忽略异常
                pass

        for sock in getattr(self, "_sockets", []):
            try:
                sock.close()
            except Exception:  # noqa: BLE001 - 清理阶段忽略异常
                pass

    # ---- 原始 ISO-TP 工具 ----

    def new_isotp_socket(self) -> isotp.socket:
        """创建一个连接到虚拟 ECU 的原始 ISO-TP socket。"""
        sock = isotp.socket()
        sock.bind(
            INTERFACE,
            isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                rxid=RX_ID,
                txid=TX_ID,
            ),
        )
        sock.settimeout(RECV_TIMEOUT)

        self._sockets.append(sock)

        return sock

    def uds_exchange(self, request: bytes) -> bytes:
        """通过 ISO-TP 发送 UDS 请求并返回响应（超时返回 None）。"""
        sock = self.new_isotp_socket()
        sock.send(request)

        return sock.recv()

    # ---- udsoncan 工具 ----

    def new_connection(self) -> IsoTPSocketConnection:
        """创建一个使用 Linux CAN_ISOTP 的 udsoncan 连接（已打开）。"""
        connection = IsoTPSocketConnection(
            interface=INTERFACE,
            address=isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                rxid=RX_ID,
                txid=TX_ID,
            ),
        )

        connection.open()

        self._connections.append(connection)

        return connection

    def new_client(self) -> LenientClient:
        """
        创建一个标准 udsoncan 客户端。

        使用标准 Client 的 API（change_session / ecu_reset /
        read_data_by_identifier），并在收到否定响应时返回 Response
        而不是抛异常，便于断言 NRC。
        """
        client = LenientClient(
            self.new_connection(),
            config={
                "data_identifiers": {
                    DID_F180: udsoncan.AsciiCodec(len(DID_F180_DATA)),
                    DID_F190: udsoncan.AsciiCodec(len(DID_F190_DATA)),
                }
            },
        )

        client.open()

        self._connections.append(client)

        return client

    # ---- 断言工具 ----

    def assertPositive(self, response, expected_sid: int, label: str = "") -> None:
        self.assertIsNotNone(response, f"{label} 未收到响应（超时）")
        self.assertEqual(
            response[0],
            expected_sid,
            f"{label} 期望肯定响应 0x{expected_sid:02X}，实际 {hexs(response)}",
        )

    def assertNrc(self, response, expected_sid: int, expected_nrc: int, label: str = "") -> None:
        parsed = negative_response(response)

        self.assertIsNotNone(
            parsed,
            f"{label} 期望否定响应 7F {expected_sid:02X} {expected_nrc:02X}，"
            f"实际 {None if response is None else hexs(response)}",
        )

        self.assertEqual(parsed[0], expected_sid, f"{label} 否定响应回显 SID 错误")
        self.assertEqual(parsed[1], expected_nrc, f"{label} NRC 错误")


# ======================================================================
# 原始 ISO-TP 报文测试
# ======================================================================


class TestRawIsotp(VcanTestCase):
    """直接发送 UDS 原始报文字节，做字节级断言。"""

    def test_10_01_default_session(self) -> None:
        """10 01 -> 50 01 00 32 00 64"""
        response = self.uds_exchange(bytes([0x10, 0x01]))

        self.assertEqual(response, bytes([0x50, 0x01, 0x00, 0x32, 0x00, 0x64]))

    def test_10_03_extended_session(self) -> None:
        response = self.uds_exchange(bytes([0x10, 0x03]))

        self.assertEqual(response, bytes([0x50, 0x03, 0x00, 0x32, 0x00, 0x64]))

    def test_11_01_hard_reset(self) -> None:
        """11 01 -> 51 01"""
        response = self.uds_exchange(bytes([0x11, 0x01]))

        self.assertEqual(response, bytes([0x51, 0x01]))

    def test_22_f190_read_single_did(self) -> None:
        """22 F1 90 -> 62 F1 90 <data>"""
        response = self.uds_exchange(bytes([0x22, 0xF1, 0x90]))

        self.assertPositive(response, 0x62, "22 F1 90")

        expected = bytes([0x62, 0xF1, 0x90]) + DID_F190_DATA
        self.assertEqual(response, expected)

    def test_22_f180_read_single_did(self) -> None:
        response = self.uds_exchange(bytes([0x22, 0xF1, 0x80]))

        expected = bytes([0x62, 0xF1, 0x80]) + DID_F180_DATA
        self.assertEqual(response, expected)

    def test_22_multiple_dids(self) -> None:
        """22 F1 90 F1 80 -> 62 F1 90 <data> F1 80 <data>"""
        response = self.uds_exchange(bytes([0x22, 0xF1, 0x90, 0xF1, 0x80]))

        expected = (
            bytes([0x62, 0xF1, 0x90])
            + DID_F190_DATA
            + bytes([0xF1, 0x80])
            + DID_F180_DATA
        )

        self.assertEqual(response, expected)

    def test_11_malformed_too_short(self) -> None:
        """11 -> 7F 11 13"""
        response = self.uds_exchange(bytes([0x11]))

        self.assertNrc(response, 0x11, 0x13, "11（缺少子功能）")

    def test_11_malformed_too_long(self) -> None:
        """11 01 00 -> 7F 11 13"""
        response = self.uds_exchange(bytes([0x11, 0x01, 0x00]))

        self.assertNrc(response, 0x11, 0x13, "11 01 00（多余字节）")

    def test_11_unsupported_reset_type(self) -> None:
        """11 02 -> 7F 11 12"""
        response = self.uds_exchange(bytes([0x11, 0x02]))

        self.assertNrc(response, 0x11, 0x12, "11 02（keyOffOnReset 未实现）")

    def test_11_unsupported_reset_type_ff(self) -> None:
        response = self.uds_exchange(bytes([0x11, 0xFF]))

        self.assertNrc(response, 0x11, 0x12, "11 FF（未知 resetType）")

    def test_10_unsupported_session(self) -> None:
        response = self.uds_exchange(bytes([0x10, 0x04]))

        self.assertNrc(response, 0x10, 0x12, "10 04（会话类型不支持）")

    def test_22_unknown_did(self) -> None:
        """22 FF FF -> 7F 22 31"""
        response = self.uds_exchange(bytes([0x22, 0xFF, 0xFF]))

        self.assertNrc(response, 0x22, 0x31, "22 FF FF（未知 DID）")

    def test_22_malformed_odd_length(self) -> None:
        """22 F1 -> 7F 22 13"""
        response = self.uds_exchange(bytes([0x22, 0xF1]))

        self.assertNrc(response, 0x22, 0x13, "22 F1（DID 不完整）")

    def test_unimplemented_service_0x27(self) -> None:
        """27 01 -> 7F 27 11 ServiceNotSupported"""
        response = self.uds_exchange(bytes([0x27, 0x01]))

        self.assertNrc(response, 0x27, 0x11, "27 01（SecurityAccess 未实现）")

    def test_unimplemented_service_0x2e(self) -> None:
        response = self.uds_exchange(bytes([0x2E, 0xF1, 0x90, 0x00]))

        self.assertNrc(response, 0x2E, 0x11, "2E（WriteDataByIdentifier 未实现）")

    def test_unimplemented_service_0x3e(self) -> None:
        response = self.uds_exchange(bytes([0x3E, 0x00]))

        self.assertNrc(response, 0x3E, 0x11, "3E（TesterPresent 未实现）")

    def test_unimplemented_service_0x19(self) -> None:
        response = self.uds_exchange(bytes([0x19, 0x02]))

        self.assertNrc(response, 0x19, 0x11, "19（ReadDTCInformation 未实现）")

    def test_unimplemented_service_0x99(self) -> None:
        response = self.uds_exchange(bytes([0x99]))

        self.assertNrc(response, 0x99, 0x11, "99（未知服务）")


# ======================================================================
# ISO-TP 分帧验证
# ======================================================================


class TestIsotpFraming(VcanTestCase):
    """验证 ISO-TP 分帧行为（ISO 15765-2）。"""

    def test_single_frame_pci(self) -> None:
        """
        短请求应使用单帧（SF）。

        10 01 的 PCI 为 0x02（长度 2）。
        """
        bus = can.interface.Bus(interface="socketcan", channel=INTERFACE)
        bus.socket.settimeout(0.05)

        frames = []

        class Collector(can.Listener):
            def on_message_received(self, msg) -> None:  # noqa: D102
                frames.append(bytes(msg.data))

        notifier = can.Notifier(bus, [Collector()])

        try:
            sock = self.new_isotp_socket()
            sock.send(bytes([0x10, 0x01]))
            sock.recv()

            time.sleep(0.1)
        finally:
            notifier.stop()
            bus.shutdown()

        requests = [f for f in frames if len(f) >= 2 and f[0] == 0x02 and f[1] == 0x10]

        self.assertTrue(
            requests,
            f"未捕获到 10 01 的单帧请求，捕获到的帧："
            f"{[hexs(f) for f in frames]}",
        )

    def test_multiframe_response(self) -> None:
        """
        多 DID 响应超过单帧容量，应使用首帧 + 连续帧。

        62 F1 90 <17B> F1 80 <13B> 共 36 字节 > 7 字节（经典 CAN 单帧上限）。
        """
        bus = can.interface.Bus(interface="socketcan", channel=INTERFACE)
        bus.socket.settimeout(0.05)

        frames = []

        class Collector(can.Listener):
            def on_message_received(self, msg) -> None:  # noqa: D102
                frames.append(bytes(msg.data))

        notifier = can.Notifier(bus, [Collector()])

        try:
            sock = self.new_isotp_socket()
            sock.send(bytes([0x22, 0xF1, 0x90, 0xF1, 0x80]))

            response = sock.recv()

            time.sleep(0.1)
        finally:
            notifier.stop()
            bus.shutdown()

        self.assertIsNotNone(response)

        # 62(1) + F1 90(2) + 数据(17) + F1 80(2) + 数据(13) = 35 字节
        self.assertEqual(len(response), 35)

        types = [iotp_frame_type(f) for f in frames]

        self.assertIn("first", types, f"缺少首帧（FF），实际帧类型：{types}")
        self.assertIn(
            "consecutive", types, f"缺少连续帧（CF），实际帧类型：{types}"
        )


# ======================================================================
# udsoncan 客户端测试
# ======================================================================


class TestUdsoncanClient(VcanTestCase):
    """验证 udsoncan 客户端可用性。"""

    def test_change_session(self) -> None:
        """client.change_session(extendedDiagnosticSession)"""
        client = self.new_client()

        response = client.change_session(
            udsoncan.services.DiagnosticSessionControl.Session.extendedDiagnosticSession
        )

        self.assertIsNotNone(response, "change_session 未收到响应")
        self.assertTrue(response.positive, f"change_session 返回否定响应：{response}")
        self.assertEqual(response.service_data.session_echo, 0x03)

    def test_change_session_programming(self) -> None:
        client = self.new_client()

        response = client.change_session(
            udsoncan.services.DiagnosticSessionControl.Session.programmingSession
        )

        self.assertIsNotNone(response)
        self.assertTrue(response.positive)
        self.assertEqual(response.service_data.session_echo, 0x02)

    def test_change_session_unsupported_returns_nrc(self) -> None:
        """不支持的会话类型应返回 NRC 0x12。"""
        client = self.new_client()

        response = client.raw_request(bytes([0x10, 0x7F]))

        self.assertIsNotNone(response, "未收到响应")
        self.assertFalse(response.positive, "期望否定响应")
        self.assertEqual(response.code, udsoncan.Response.Code.SubFunctionNotSupported)

    def test_ecu_reset(self) -> None:
        """client.ecu_reset(hardReset)"""
        client = self.new_client()

        response = client.ecu_reset(
            udsoncan.services.ECUReset.ResetType.hardReset
        )

        self.assertIsNotNone(response, "ecu_reset 未收到响应")
        self.assertTrue(response.positive, f"ecu_reset 返回否定响应：{response}")
        self.assertEqual(response.service_data.reset_type_echo, 0x01)

    def test_ecu_reset_unsupported_type_returns_nrc(self) -> None:
        """softReset 未实现，应返回 NRC 0x12。"""
        client = self.new_client()

        response = client.raw_request(bytes([0x11, 0x03]))

        self.assertIsNotNone(response, "未收到响应")
        self.assertFalse(response.positive, "期望否定响应")
        self.assertEqual(response.code, udsoncan.Response.Code.SubFunctionNotSupported)

    def test_read_data_by_identifier_single(self) -> None:
        """client.read_data_by_identifier(0xF190)"""
        client = self.new_client()

        response = client.read_data_by_identifier(DID_F190)

        self.assertIsNotNone(response, "read_data_by_identifier 未收到响应")
        self.assertTrue(response.positive, f"返回否定响应：{response}")
        self.assertEqual(
            response.service_data.values[DID_F190], DID_F190_DATA.decode()
        )

    def test_read_data_by_identifier_multiple(self) -> None:
        """client.read_data_by_identifier([0xF190, 0xF180])"""
        client = self.new_client()

        response = client.read_data_by_identifier([DID_F190, DID_F180])

        self.assertIsNotNone(response)
        self.assertTrue(response.positive, f"返回否定响应：{response}")
        self.assertEqual(
            response.service_data.values[DID_F190], DID_F190_DATA.decode()
        )
        self.assertEqual(
            response.service_data.values[DID_F180], DID_F180_DATA.decode()
        )

    def test_read_data_by_identifier_unknown_did_returns_nrc(self) -> None:
        """未知 DID 应返回 NRC 0x31 RequestOutOfRange。"""
        client = self.new_client()

        # 注意：DID 未在 data_identifiers 中配置时，
        # udsoncan 会在构造请求阶段抛 ConfigError，
        # 因此这里走原始报文路径，直接观察 ECU 的 NRC。
        response = client.raw_request(bytes([0x22, 0xFF, 0xFF]))

        self.assertIsNotNone(response, "未收到响应")
        self.assertFalse(response.positive, "期望否定响应")
        self.assertEqual(response.code, udsoncan.Response.Code.RequestOutOfRange)


# ======================================================================
# ECU 进程存活检查
# ======================================================================


class TestEcuProcess(VcanTestCase):
    """确认 ECU 在整个测试过程中稳定运行。"""

    def test_ecu_process_alive(self) -> None:
        self.assertIsNotNone(self.ecu_process)
        self.assertIsNone(
            self.ecu_process.poll(),
            f"ECU 进程已退出，exit code={self.ecu_process.poll()}",
        )

    def test_ecu_log_contains_handled_requests(self) -> None:
        """ECU 日志中应记录收到的请求与发出的响应。"""
        self.uds_exchange(bytes([0x10, 0x01]))

        time.sleep(0.3)

        log = self._read_ecu_log()

        self.assertIn("RX UDS: 10 01", log)
        self.assertIn("TX UDS: 50 01 00 32 00 64", log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
