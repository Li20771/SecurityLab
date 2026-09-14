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

import atexit
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
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

import keygen  # noqa: E402  (ecu/ 已加入 sys.path)

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
DID_F187 = 0xF187          # 受保护 DID：需要 extended session + 解锁
DID_UNKNOWN = 0xFFFF

DID_F180_DATA = b"BOOT-SW-1.0.0"
DID_F190_DATA = b"TESTECU0000000001"
DID_F187_DATA = b"SPARE-PART-0001"

# SecurityAccess
SECURITY_LEVEL1 = 0x01

NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_INVALID_KEY = 0x35
NRC_EXCEED_NUMBER_OF_ATTEMPTS = 0x36
NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED = 0x37


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


# ----------------------------------------------------------------------
# 模块级 ECU 单例
# ----------------------------------------------------------------------
#
# 整个测试模块只启动一个 ECU 子进程，在所有测试类之间保持存活。
# 这样 security 状态（失败计数 / 锁定）才是连续可观测的。

_ECU_LOCK = threading.Lock()
_ECU_PROCESS = None
_ECU_LOG_HANDLE = None
_ECU_LOG_PATH = None
_ECU_PIPE_CHUNKS: list = []
_ECU_PIPE_READER = None

#: 端到端测试使用的锁定窗口（秒）。
#:
#: 故意设得很短：锁定状态**无法通过标准协议清除**（这正是防暴力
#: 破解的设计意图），所以测试不能"重置"它，只能等它自然到期。
#: 短窗口让"等待锁定解除"的代价可接受，同时不改变协议语义。
#: 注意：ECU 默认值仍是 10 秒，供手动实验使用。
E2E_LOCKOUT_SECONDS = "2"


def _start_fresh_ecu(env_overrides: dict = None) -> subprocess.Popen:
    """
    启动一个**全新的** ECU 子进程。

    每个测试类开始前都会调用本函数（先杀掉上一个 ECU），
    从而保证：

    - 安全状态（失败计数 / 锁定 / 解锁状态）从一个干净的进程开始
    - 不需要任何"重置"消息，避免额外 CAN 流量干扰 ISO-TP 重组

    ECU 的失败计数与锁定是进程内状态且无法通过标准协议清除，
    重启进程是唯一干净、且不改变协议语义的隔离手段。
    """
    global _ECU_PROCESS, _ECU_LOG_HANDLE, _ECU_LOG_PATH
    global _ECU_PIPE_CHUNKS, _ECU_PIPE_READER

    with _ECU_LOCK:
        _stop_ecu_process()

        child_env = os.environ.copy()

        # 锁定窗口设短：锁定无法提前解除，测试只能等它到期。
        # ECU 默认值仍是 10 秒（供手动实验）。
        child_env["UDS_LOCKOUT_SECONDS"] = E2E_LOCKOUT_SECONDS

        if env_overrides:
            child_env.update(env_overrides)

        log_target, _ECU_LOG_PATH, _ECU_LOG_HANDLE = _open_ecu_log()

        _ECU_PIPE_CHUNKS = []

        _ECU_PROCESS = subprocess.Popen(
            [sys.executable, "-u", _ECU_SCRIPT],
            cwd=_ECU_DIR,
            stdout=log_target,
            stderr=subprocess.STDOUT,
            env=child_env,
            start_new_session=True,
        )

        # 管道模式下启动后台读取线程，避免缓冲区写满导致 ECU 阻塞
        if log_target is subprocess.PIPE and _ECU_PROCESS.stdout is not None:
            def _reader(stream) -> None:
                for line in iter(stream.readline, b""):
                    _ECU_PIPE_CHUNKS.append(line.decode("utf-8", errors="replace"))

            _ECU_PIPE_READER = threading.Thread(
                target=_reader, args=(_ECU_PROCESS.stdout,), daemon=True
            )
            _ECU_PIPE_READER.start()

        _wait_for_ecu_ready()

        return _ECU_PROCESS


def _stop_ecu_process() -> None:
    """终止模块级 ECU 子进程（若在运行）。"""
    global _ECU_PROCESS, _ECU_LOG_HANDLE

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

    if _ECU_LOG_HANDLE is not None:
        try:
            _ECU_LOG_HANDLE.close()
        except Exception:  # noqa: BLE001 - 清理阶段忽略异常
            pass

        _ECU_LOG_HANDLE = None

    _ECU_PROCESS = None


def _wait_for_ecu_ready() -> None:
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
        f"最后一个错误：{last_error!r}。ECU 日志：{_ECU_LOG_PATH}"
    )


atexit.register(_stop_ecu_process)


def _is_ecu_locked() -> bool:
    """请求 seed 并判断 ECU 是否处于锁定状态。"""
    response = _raw_exchange(bytes([0x27, 0x01]))

    return response is not None and response[:3] == bytes([0x7F, 0x27, 0x37])


# ----------------------------------------------------------------------
# 失败计数簿记
# ----------------------------------------------------------------------
#
# ECU 的 FAILED_ATTEMPTS 无法通过标准协议读取或清除。
# 测试端只能自己记账：每发送一次错误 key 就 +1，
# 接近上限时等待锁定窗口过去（e2e 设为 2 秒）再继续。

MAX_ATTEMPTS = 3
LOCKOUT_SETTLE_SECONDS = float(E2E_LOCKOUT_SECONDS) + 0.5
_FAILED_ATTEMPTS_THIS_RUN = 0


def _note_wrong_key() -> None:
    """
    记录一次错误 key 尝试。

    只计数，不在这里等待——等待锁定解除统一由测试 setUp 处理，
    这样单个测试内部（例如锁定触发测试的连续三次错误）不会被中途打断。
    """
    global _FAILED_ATTEMPTS_THIS_RUN

    _FAILED_ATTEMPTS_THIS_RUN += 1


def _reset_failed_attempt_bookkeeping() -> None:
    """清零测试端的失败计数簿记。"""
    global _FAILED_ATTEMPTS_THIS_RUN

    _FAILED_ATTEMPTS_THIS_RUN = 0


def _trigger_ecu_lockout() -> bool:
    """
    确保 ECU 处于锁定状态，返回是否确认已锁定。

    幂等：若 ECU 已经锁定，直接返回 True。

    若 ECU 正处于上一次锁定中（锁定无法提前解除），
    会先等待其自然到期，再重新构造失败计数触发新的锁定。

    注意：只应在测试套件末尾使用。
    """
    if _is_ecu_locked():
        return True

    # 可能处于上次锁定的尾巴上：等它过去
    time.sleep(LOCKOUT_SETTLE_SECONDS)

    for _ in range(MAX_ATTEMPTS):
        seed_response = _raw_exchange(bytes([0x27, 0x01]))

        if seed_response is None or seed_response[0] != 0x67:
            # 仍在锁定中，再等一轮
            time.sleep(LOCKOUT_SETTLE_SECONDS)
            continue

        seed = seed_response[2:]
        _note_wrong_key()
        wrong_key = bytes(byte ^ 0xFF for byte in keygen.derive_key(seed))

        _raw_exchange(bytes([0x27, 0x02]) + wrong_key)

    return _is_ecu_locked()


def _raw_exchange(request: bytes, timeout: float = RECV_TIMEOUT):
    """发送一条 UDS 请求并返回响应（超时返回 None）。"""
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
        sock.send(request)

        return sock.recv()
    except Exception:  # noqa: BLE001
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


class VcanTestCase(unittest.TestCase):
    """
    为每个测试提供干净的 ISO-TP 连接。

    虚拟 ECU 子进程是**模块级单例**：整个测试模块只启动一次，
    在所有测试类之间保持存活（进程退出时统一回收）。

    这一点很重要：ECU 的 security 状态（失败计数、锁定）是进程内状态，
    如果每个测试类都重启 ECU，锁定状态会被意外清除，
    导致"锁定在复位后仍然存在"这类测试失去意义。
    """

    @classmethod
    def setUpClass(cls) -> None:
        # 每个测试类使用全新 ECU 进程：安全状态（失败计数 / 锁定）
        # 是进程内状态且无法通过标准协议清除，重启进程是唯一干净的隔离方式。
        _start_fresh_ecu()

        # 新进程的失败计数为 0，测试端簿记必须同步归零
        _reset_failed_attempt_bookkeeping()

    @classmethod
    def _read_ecu_log(cls) -> str:
        """读取 ECU 日志内容（文件或管道）。"""
        if _ECU_LOG_PATH is not None and _ECU_LOG_HANDLE is not None:
            _ECU_LOG_HANDLE.flush()
            with open(_ECU_LOG_PATH, "r", encoding="utf-8") as handle:
                return handle.read()

        # 管道模式：ECU 日志由独立读取线程累积
        return "".join(_ECU_PIPE_CHUNKS)

    # ---- 每个测试的准备与清理 ----

    def setUp(self) -> None:
        drain_vcan0()

        self._connections = []
        self._sockets = []

        self._await_lockout_clear()
        self._reset_to_default_session()

    #: 是否在测试前等待锁定解除。
    #:
    #: 锁定测试类必须设为 False：等待解除会让它们要断言的
    #: "锁定期间行为"消失（这些类各自管理自己的锁定状态）。
    WAIT_FOR_LOCKOUT_CLEAR = True

    def _await_lockout_clear(self) -> None:
        """
        若本轮测试累计的错误 key 已让 ECU 进入锁定，则等它自然到期。

        失败计数与锁定是 ECU 进程内状态，且**无法通过标准协议清除**
        （这正是防暴力破解的设计意图）。测试端通过 _note_wrong_key()
        自行记账；一旦触及上限，ECU 会锁定 UDS_LOCKOUT_SECONDS（e2e 设为 2 秒），
        必须等它过去，否则后续测试会收到 0x37 而非预期的 NRC。
        """
        if not self.WAIT_FOR_LOCKOUT_CLEAR:
            return

        if _FAILED_ATTEMPTS_THIS_RUN >= MAX_ATTEMPTS:
            time.sleep(LOCKOUT_SETTLE_SECONDS)
            _reset_failed_attempt_bookkeeping()

    def _reset_to_default_session(self) -> None:
        """
        把 ECU 切回 defaultSession，使每个测试都从"未解锁"开始。

        为什么需要：同一测试类内多个测试共用一个 ECU 进程，
        而"已解锁"状态会残留。切换到任意会话都会按协议清除安全等级
        与 seed 生命周期（这是 ECU 的实现语义，不是测试 hack）。

        注意：这**不会**清除失败计数与锁定——那两个状态
        只能通过重启 ECU 进程（每个测试类的 setUpClass）来隔离。
        """
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
            sock.settimeout(RECV_TIMEOUT)
            sock.send(bytes([0x10, 0x01]))
            sock.recv()
        except Exception:  # noqa: BLE001 - 基线重置失败不应中断测试
            pass
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:  # noqa: BLE001
                    pass

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
        """
        通过 ISO-TP 发送 UDS 请求并返回响应（超时返回 None）。

        处理两个 vcan0 回环带来的干扰：

        1. **回显**：CAN_ISOTP socket 也会收到自己发出的帧，
           若第一次 recv 拿到的是回显，断言就会整体错位→跳过它。
        2. **残留帧**：上一个交换多帧响应可能还剩若干帧在途。
           这些帧若与本次请求的帧混在一起，内核 ISO-TP 重组会失败
           （表现为 recv 抛 OSError / Errno 84）。
           因此本方法在返回前把缓冲区读空。

        注意：**不能**用"发送前定时读空"的方式清理——
        多帧响应的首帧可能在清理窗口内被丢弃，同样导致重组失败。
        """
        sock = self.new_isotp_socket()

        try:
            sock.send(request)

            deadline = time.monotonic() + RECV_TIMEOUT
            response = None

            while time.monotonic() < deadline:
                frame = sock.recv()

                if frame is None:
                    break

                # 回显：跳过自己发出的请求帧
                if frame == request:
                    continue

                response = frame
                break

            self._drain_socket(sock)

            return response
        finally:
            pass

    @staticmethod
    def _drain_socket(sock, quiet: float = 0.03) -> int:
        """
        读空 socket 缓冲区中已排队的帧，返回丢弃的帧数。

        用于在一次交换结束后清理残留帧，
        避免它们污染下一次交换的内核 ISO-TP 重组。
        """
        drained = 0

        try:
            sock.settimeout(quiet)

            while True:
                try:
                    frame = sock.recv()
                except Exception:  # noqa: BLE001 - 超时/异常即视为已读空
                    break

                if frame is None:
                    break

                drained += 1
        finally:
            try:
                sock.settimeout(RECV_TIMEOUT)
            except Exception:  # noqa: BLE001
                pass

        return drained

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
        read_data_by_identifier / unlock_security_access），并在收到
        否定响应时返回 Response 而不是抛异常，便于断言 NRC。

        security_algo 使用与虚拟 ECU 相同的教学算法（ecu/keygen.py），
        使 udsoncan 可以自动完成 seed -> key 派生。
        """
        client = LenientClient(
            self.new_connection(),
            config={
                "data_identifiers": {
                    DID_F180: udsoncan.AsciiCodec(len(DID_F180_DATA)),
                    DID_F190: udsoncan.AsciiCodec(len(DID_F190_DATA)),
                    DID_F187: udsoncan.AsciiCodec(len(DID_F187_DATA)),
                },
                # udsoncan 在 unlock_security_access() 中调用本函数派生 key
                "security_algo": lambda seed, level: keygen.derive_key(seed),
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

        # extendedDiagnosticSession 现在返回自己的 P2 / P2*（100 ms / 1000 ms）
        self.assertEqual(response, bytes([0x50, 0x03, 0x00, 0x64, 0x03, 0xE8]))

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

    def test_security_access_request_seed(self) -> None:
        """
        27 01 -> 67 01 <4 字节 seed>

        （本用例原为 test_unimplemented_service_0x27，断言 7F 27 11；
        SecurityAccess 实现后改写为断言新行为。）
        """
        response = self.uds_exchange(bytes([0x27, 0x01]))

        self.assertPositive(response, 0x67, "27 01（RequestSeed）")
        self.assertEqual(response[1], 0x01)
        self.assertEqual(len(response), 6, "seed 应为 4 字节")

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
        """
        不支持的会话类型应返回 NRC 0x12。

        这里使用 0x04（safetySystemDiagnosticSession），
        本实验只实现 default / programming / extended 三个会话。
        """
        client = self.new_client()

        response = client.raw_request(bytes([0x10, 0x04]))

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
        self.assertIsNotNone(_ECU_PROCESS)
        self.assertIsNone(
            _ECU_PROCESS.poll(),
            f"ECU 进程已退出，exit code={_ECU_PROCESS.poll()}",
        )

    def test_ecu_log_contains_handled_requests(self) -> None:
        """
        ECU 日志中应记录收到的请求与发出的响应。

        本类在测试顺序上位于锁定测试之后，ECU 可能正处于锁定状态，
        因此这里使用**任何时候都允许**的 10 01 请求，
        只验证"ECU 在持续处理请求并记录日志"，不假设安全状态。
        """
        request = bytes([0x10, 0x01])
        response = self.uds_exchange(request)

        self.assertIsNotNone(response, "ECU 未响应 10 01")

        time.sleep(0.3)

        log = self._read_ecu_log()

        self.assertIn("RX UDS: 10 01", log, "ECU 日志未记录收到的请求")
        self.assertTrue(
            "TX UDS: 50 01" in log or "TX UDS: 7f" in log,
            "ECU 日志未记录发出的响应",
        )


# ======================================================================
# 0x27 SecurityAccess —— 正常路径
# ======================================================================


class TestSecurityAccessHappyPath(VcanTestCase):
    """seed / key 正常流程。"""

    def _request_seed(self) -> bytes:
        """请求 seed 并返回 seed 字节。"""
        response = self.uds_exchange(bytes([0x27, 0x01]))

        self.assertPositive(response, 0x67, "27 01（RequestSeed）")
        self.assertEqual(response[1], 0x01)
        self.assertEqual(len(response), 6, "seed 应为 4 字节")

        return response[2:]

    def test_request_seed_returns_four_byte_seed(self) -> None:
        seed = self._request_seed()

        self.assertEqual(len(seed), 4)

    def test_request_seed_is_not_all_zero(self) -> None:
        """本实验不使用全 0 seed。"""
        seed = self._request_seed()

        self.assertNotEqual(seed, b"\x00" * 4)

    def test_seed_changes_between_requests(self) -> None:
        first = self._request_seed()
        second = self._request_seed()

        self.assertNotEqual(first, second, "seed 在两次请求间没有变化")

    def test_full_unlock_flow(self) -> None:
        """完整流程：27 01 -> 27 02 <正确 key> -> 67 02"""
        seed = self._request_seed()

        response = self.uds_exchange(bytes([0x27, 0x02]) + keygen.derive_key(seed))

        self.assertEqual(response, bytes([0x67, 0x02]))

    def test_unlock_enables_protected_did(self) -> None:
        """解锁后可读取受保护 DID。"""
        self.uds_exchange(bytes([0x10, 0x03]))

        seed = self._request_seed()
        self.uds_exchange(bytes([0x27, 0x02]) + keygen.derive_key(seed))

        response = self.uds_exchange(bytes([0x22, 0xF1, 0x87]))

        self.assertPositive(response, 0x62, "22 F1 87（已解锁）")
        self.assertEqual(response[3:], DID_F187_DATA)

    def test_udsoncan_unlock_security_access(self) -> None:
        """udsoncan 的 unlock_security_access() 正常路径。"""
        client = self.new_client()

        response = client.unlock_security_access(SECURITY_LEVEL1)

        self.assertIsNotNone(response, "unlock_security_access 未收到响应")
        self.assertTrue(response.positive, f"返回否定响应：{response}")

    def test_udsoncan_unlock_then_read_protected_did(self) -> None:
        """udsoncan 解锁后读取受保护 DID。"""
        client = self.new_client()

        client.change_session(
            udsoncan.services.DiagnosticSessionControl.Session.extendedDiagnosticSession
        )
        client.unlock_security_access(SECURITY_LEVEL1)

        response = client.read_data_by_identifier(DID_F187)

        self.assertIsNotNone(response, "未收到响应")
        self.assertTrue(response.positive, f"返回否定响应：{response}")
        self.assertEqual(
            response.service_data.values[DID_F187], DID_F187_DATA.decode()
        )

    def test_udsoncan_request_seed_only(self) -> None:
        """udsoncan 直接调用 request_seed()。"""
        client = self.new_client()

        response = client.request_seed(SECURITY_LEVEL1)

        self.assertIsNotNone(response, "request_seed 未收到响应")
        self.assertTrue(response.positive, f"返回否定响应：{response}")
        self.assertEqual(len(response.service_data.seed), 4)


# ======================================================================
# 0x27 SecurityAccess —— 错误 key 与锁定
# ======================================================================


class TestSecurityAccessFailures(VcanTestCase):
    """错误 key、尝试上限与锁定。"""

    def _request_seed(self) -> bytes:
        response = self.uds_exchange(bytes([0x27, 0x01]))

        self.assertPositive(response, 0x67, "27 01（RequestSeed）")

        return response[2:]

    def _wrong_key(self, seed: bytes) -> bytes:
        """生成错误 key，并在测试端记账（失败计数无法从 ECU 读回）。"""
        _note_wrong_key()

        return bytes(byte ^ 0xFF for byte in keygen.derive_key(seed))

    def test_wrong_key_returns_invalid_key(self) -> None:
        """错误 key -> 7F 27 35"""
        seed = self._request_seed()

        response = self.uds_exchange(bytes([0x27, 0x02]) + self._wrong_key(seed))

        self.assertNrc(response, 0x27, NRC_INVALID_KEY, "错误 key")

    def test_attempt_limit_sequence_35_35_36(self) -> None:
        """
        达到上限前，错误 key 均返回 0x35。

        注意：这里**只做 2 次**错误尝试（上限为 3），
        以免在共享 ECU 上触发锁定而影响后续测试类。
        完整的 35 → 35 → 36 序列由
        TestSecurityAccessLockoutTrigger 在套件末尾验证。
        """
        actual = []

        for _ in range(2):
            seed = self._request_seed()
            response = self.uds_exchange(bytes([0x27, 0x02]) + self._wrong_key(seed))
            actual.append(response[2])

        self.assertEqual(
            actual,
            [NRC_INVALID_KEY, NRC_INVALID_KEY],
            "未达上限的错误 key 应返回 0x35",
        )

    def test_wrong_key_does_not_unlock(self) -> None:
        """错误 key 之后受保护 DID 仍不可读（未解锁）。"""
        seed = self._request_seed()
        self.uds_exchange(bytes([0x27, 0x02]) + self._wrong_key(seed))

        self.uds_exchange(bytes([0x10, 0x03]))

        response = self.uds_exchange(bytes([0x22, 0xF1, 0x87]))

        self.assertNrc(
            response, 0x22, NRC_SECURITY_ACCESS_DENIED, "错误 key 后受保护 DID"
        )

    def test_zz_udsoncan_wrong_key_via_raw_path(self) -> None:
        """
        错误 NRC 不使用 udsoncan 解释，改走 raw_request。

        （udsoncan 在错误 NRC 上会抛异常，按约定不强行让它解释。）

        命名以 zz_ 开头：本测试会跨过失败计数上限，
        必须在本类最后执行，避免影响其他错误 key 测试。
        """
        client = self.new_client()

        seed_response = client.raw_request(bytes([0x27, 0x01]))

        self.assertIsNotNone(seed_response, "未收到 RequestSeed 响应")
        self.assertTrue(seed_response.positive, "RequestSeed 应为肯定响应")

        # raw_request 不做服务级解释，因此直接从原始 payload 取 seed
        payload = seed_response.get_payload()
        self.assertEqual(payload[0], 0x67)
        self.assertEqual(payload[1], 0x01)

        seed = bytes(payload[2:])
        self.assertEqual(len(seed), 4, "seed 应为 4 字节")

        _note_wrong_key()
        wrong_key = bytes(byte ^ 0xFF for byte in keygen.derive_key(seed))
        response = client.raw_request(bytes([0x27, 0x02]) + wrong_key)

        self.assertIsNotNone(response, "未收到响应")
        self.assertFalse(response.positive, "期望否定响应")
        self.assertEqual(response.code, udsoncan.Response.Code.InvalidKey)


# ======================================================================
# 0x27 SecurityAccess —— 顺序 / 校验错误
# ======================================================================


class TestSecurityAccessErrors(VcanTestCase):
    """顺序错误、子功能不支持、malformed。"""

    def test_send_key_without_seed(self) -> None:
        """没有 seed 直接发送 key -> 7F 27 24"""
        response = self.uds_exchange(bytes([0x27, 0x02]) + bytes(4))

        self.assertNrc(response, 0x27, NRC_REQUEST_SEQUENCE_ERROR, "无 seed 直接 SendKey")

    def test_replay_of_consumed_seed(self) -> None:
        """已消费的 seed 重放 -> 7F 27 24（防重放）"""
        seed_response = self.uds_exchange(bytes([0x27, 0x01]))
        seed = seed_response[2:]
        key = keygen.derive_key(seed)

        first = self.uds_exchange(bytes([0x27, 0x02]) + key)
        self.assertEqual(first, bytes([0x67, 0x02]))

        replay = self.uds_exchange(bytes([0x27, 0x02]) + key)

        self.assertNrc(replay, 0x27, NRC_REQUEST_SEQUENCE_ERROR, "重放已消费的 seed")

    def test_request_seed_while_unlocked(self) -> None:
        """已解锁再次 RequestSeed -> 7F 27 22"""
        seed_response = self.uds_exchange(bytes([0x27, 0x01]))
        self.assertPositive(seed_response, 0x67, "解锁前的 RequestSeed")
        seed = seed_response[2:]

        unlock = self.uds_exchange(bytes([0x27, 0x02]) + keygen.derive_key(seed))
        self.assertEqual(unlock, bytes([0x67, 0x02]), "解锁失败")

        response = self.uds_exchange(bytes([0x27, 0x01]))

        self.assertNrc(response, 0x27, NRC_CONDITIONS_NOT_CORRECT, "已解锁再请求 seed")

    def test_unsupported_subfunction(self) -> None:
        """不支持的 security sub-function -> 7F 27 12"""
        for subfunction in (0x03, 0x04, 0x05, 0xFF):
            with self.subTest(subfunction=hex(subfunction)):
                response = self.uds_exchange(bytes([0x27, subfunction]))

                self.assertNrc(
                    response,
                    0x27,
                    NRC_SUBFUNCTION_NOT_SUPPORTED,
                    f"27 {subfunction:02X}",
                )

    def test_malformed_no_subfunction(self) -> None:
        """27 -> 7F 27 13"""
        response = self.uds_exchange(bytes([0x27]))

        self.assertNrc(response, 0x27, NRC_INCORRECT_MESSAGE_LENGTH, "27（无子功能）")

    def test_malformed_send_key_without_key(self) -> None:
        """27 02 -> 7F 27 13"""
        response = self.uds_exchange(bytes([0x27, 0x02]))

        self.assertNrc(response, 0x27, NRC_INCORRECT_MESSAGE_LENGTH, "27 02（缺 key）")

    def test_malformed_send_key_wrong_length(self) -> None:
        """27 02 <3 或 5 字节> -> 7F 27 13"""
        for key in (bytes(3), bytes(5)):
            with self.subTest(key_length=len(key)):
                response = self.uds_exchange(bytes([0x27, 0x02]) + key)

                self.assertNrc(
                    response,
                    0x27,
                    NRC_INCORRECT_MESSAGE_LENGTH,
                    f"27 02 + {len(key)} 字节 key",
                )


# ======================================================================
# SecurityState 与会话 / 复位交互
# ======================================================================


class TestSecurityStateInteraction(VcanTestCase):
    """会话切换与 ECUReset 对安全状态的影响。"""

    def _unlock(self) -> bytes:
        """在当前会话下解锁，返回使用的 seed。"""
        seed = self.uds_exchange(bytes([0x27, 0x01]))[2:]
        response = self.uds_exchange(bytes([0x27, 0x02]) + keygen.derive_key(seed))

        self.assertEqual(response, bytes([0x67, 0x02]), "解锁失败")

        return seed

    def test_session_switch_relocks(self) -> None:
        """解锁 -> 10 01 -> 受保护 DID 重新被拒绝。"""
        self.uds_exchange(bytes([0x10, 0x03]))
        self._unlock()

        # 解锁状态下可读
        self.assertPositive(
            self.uds_exchange(bytes([0x22, 0xF1, 0x87])), 0x62, "解锁后可读"
        )

        # 切回 default session 后必须重新锁定
        self.uds_exchange(bytes([0x10, 0x01]))

        response = self.uds_exchange(bytes([0x22, 0xF1, 0x87]))

        self.assertNrc(
            response, 0x22, NRC_SECURITY_ACCESS_DENIED, "切会话后受保护 DID"
        )

    def test_ecu_reset_relocks(self) -> None:
        """解锁 -> 11 01 -> 受保护 DID 重新被拒绝。"""
        self.uds_exchange(bytes([0x10, 0x03]))
        self._unlock()

        self.uds_exchange(bytes([0x11, 0x01]))

        response = self.uds_exchange(bytes([0x22, 0xF1, 0x87]))

        self.assertNrc(
            response, 0x22, NRC_SECURITY_ACCESS_DENIED, "复位后受保护 DID"
        )

    def test_replay_after_session_switch_rejected(self) -> None:
        """解锁后切会话，重放原 key -> 7F 27 24"""
        self.uds_exchange(bytes([0x10, 0x03]))
        seed = self._unlock()
        key = keygen.derive_key(seed)

        self.uds_exchange(bytes([0x10, 0x01]))

        response = self.uds_exchange(bytes([0x27, 0x02]) + key)

        self.assertNrc(response, 0x27, NRC_REQUEST_SEQUENCE_ERROR, "切会话后重放")

    def test_udsoncan_change_session_before_unlock(self) -> None:
        """udsoncan：先切 extended session 再解锁。"""
        client = self.new_client()

        session_response = client.change_session(
            udsoncan.services.DiagnosticSessionControl.Session.extendedDiagnosticSession
        )
        self.assertTrue(session_response.positive)

        unlock_response = client.unlock_security_access(SECURITY_LEVEL1)
        self.assertTrue(unlock_response.positive)


# ======================================================================
# 受保护 DID 的权限模型
# ======================================================================


class TestProtectedDidPermission(VcanTestCase):
    """Session → Security → Permission 三层权限。"""

    def test_public_did_readable_when_locked(self) -> None:
        """公开 DID 在 default + locked 下可读。"""
        response = self.uds_exchange(bytes([0x22, 0xF1, 0x90]))

        self.assertPositive(response, 0x62, "公开 DID")
        self.assertEqual(response[3:], DID_F190_DATA)

    def test_protected_did_denied_in_default_session(self) -> None:
        """Default + Locked -> 7F 22 33"""
        response = self.uds_exchange(bytes([0x22, 0xF1, 0x87]))

        self.assertNrc(
            response, 0x22, NRC_SECURITY_ACCESS_DENIED, "default+locked 受保护 DID"
        )

    def test_protected_did_denied_in_extended_session(self) -> None:
        """Extended + Locked -> 7F 22 33"""
        self.uds_exchange(bytes([0x10, 0x03]))

        response = self.uds_exchange(bytes([0x22, 0xF1, 0x87]))

        self.assertNrc(
            response, 0x22, NRC_SECURITY_ACCESS_DENIED, "extended+locked 受保护 DID"
        )

    def test_protected_did_readable_in_extended_unlocked(self) -> None:
        """Extended + Level 1 -> 正常读取。"""
        self.uds_exchange(bytes([0x10, 0x03]))

        seed = self.uds_exchange(bytes([0x27, 0x01]))[2:]
        self.uds_exchange(bytes([0x27, 0x02]) + keygen.derive_key(seed))

        response = self.uds_exchange(bytes([0x22, 0xF1, 0x87]))

        self.assertPositive(response, 0x62, "extended+unlocked 受保护 DID")
        self.assertEqual(response[3:], DID_F187_DATA)

    def test_mixed_request_denied_as_a_whole(self) -> None:
        """
        混合公开 + 受保护 DID 时整体拒绝，不返回部分数据。
        """
        response = self.uds_exchange(bytes([0x22, 0xF1, 0x90, 0xF1, 0x87]))

        self.assertNrc(
            response, 0x22, NRC_SECURITY_ACCESS_DENIED, "混合 DID 请求"
        )
        self.assertNotIn(
            DID_F190_DATA, response, "被拒绝的响应中泄露了公开 DID 数据"
        )

    def test_udsoncan_protected_did_denied_as_a_whole(self) -> None:
        """udsoncan 混合请求在未解锁时整体失败。"""
        client = self.new_client()

        response = client.raw_request(bytes([0x22, 0xF1, 0x90, 0xF1, 0x87]))

        self.assertIsNotNone(response, "未收到响应")
        self.assertFalse(response.positive, "期望否定响应")
        self.assertEqual(
            response.code, udsoncan.Response.Code.SecurityAccessDenied
        )


# ======================================================================
# 0x27 SecurityAccess —— 锁定（必须放在最后）
# ======================================================================
#
# 锁定状态一旦触发就**无法通过协议清除**（这正是设计意图）。
# 因此拆成两个类，且必须保持"先触发、后断言"的顺序：
#
#   1. TestSecurityAccessLockoutTrigger —— 负责触发锁定并断言触发过程
#   2. TestSecurityAccessLockout        —— 断言锁定期间的行为
#
# 两个类的 setUp 都会把 ECU 切回 default session（清除安全等级），
# 但**不会**清除失败计数与锁定状态，因此第二个类开始时 ECU 仍处于锁定。


class TestSecurityAccessLockoutTrigger(VcanTestCase):
    """
    触发锁定，并验证触发过程本身。

    必须位于文件末尾附近：锁定一旦触发就无法通过标准协议清除，
    后续测试只能断言"锁定期间的行为"。

    本测试内部连续发送 3 次错误 key，因此 setUp 中的
    等待锁定逻辑不会中途打断它（计数在测试结束时才越限）。
    """

    def test_lockout_triggers_after_three_failures(self) -> None:
        """
        连续三次错误 key：前两次 0x35，第三次 0x36 并进入锁定。

        0x36 只出现在"触发锁定的那一次"。
        """
        codes = []

        for _ in range(3):
            seed_response = self.uds_exchange(bytes([0x27, 0x01]))
            self.assertPositive(seed_response, 0x67, "触发锁定前的 RequestSeed")
            seed = seed_response[2:]

            wrong_key = bytes(byte ^ 0xFF for byte in keygen.derive_key(seed))
            response = self.uds_exchange(bytes([0x27, 0x02]) + wrong_key)

            codes.append(response[2])

        self.assertEqual(
            codes,
            [NRC_INVALID_KEY, NRC_INVALID_KEY, NRC_EXCEED_NUMBER_OF_ATTEMPTS],
            "错误 key 序列不符合 35/35/36 预期",
        )

        # 触发后立即应处于锁定
        locked = self.uds_exchange(bytes([0x27, 0x01]))

        self.assertNrc(
            locked,
            0x27,
            NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED,
            "锁定后 RequestSeed",
        )


class TestSecurityAccessLockout(VcanTestCase):
    """
    锁定期间的行为。

    setUpClass 先启动全新 ECU，再**主动触发锁定**，
    因此本类既不依赖其他测试类的状态，也不受 unittest 排序影响。
    本类内部不发送错误 key，所以整个类期间锁定状态保持有效。
    """

    WAIT_FOR_LOCKOUT_CLEAR = False

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        if not _trigger_ecu_lockout():
            raise AssertionError(
                "无法触发 ECU 锁定，锁定行为测试无法进行"
            )

    def test_lockout_blocks_request_seed(self) -> None:
        """锁定期间 RequestSeed 也被拒绝 -> 7F 27 37"""
        response = self.uds_exchange(bytes([0x27, 0x01]))

        self.assertNrc(
            response,
            0x27,
            NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED,
            "锁定中 RequestSeed",
        )

    def test_lockout_blocks_send_key(self) -> None:
        """锁定期间 SendKey -> 7F 27 37"""
        response = self.uds_exchange(bytes([0x27, 0x02]) + bytes(4))

        self.assertNrc(
            response,
            0x27,
            NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED,
            "锁定中 SendKey",
        )

    def test_lockout_blocks_protected_did(self) -> None:
        """锁定状态下受保护 DID 仍然不可读。"""
        self.uds_exchange(bytes([0x10, 0x03]))

        response = self.uds_exchange(bytes([0x22, 0xF1, 0x87]))

        self.assertNrc(
            response, 0x22, NRC_SECURITY_ACCESS_DENIED, "锁定中受保护 DID"
        )

    def test_lockout_survives_ecu_reset(self) -> None:
        """
        锁定状态在 11 01 后仍然存在。

        这是本教学 ECU 的明确设计选择：防止通过复位 ECU 刷掉
        暴力破解的惩罚状态。**不是所有真实 ECU 都必然如此**，
        部分真实 ECU 会在复位时清空失败计数与锁定。
        """
        reset = self.uds_exchange(bytes([0x11, 0x01]))
        self.assertEqual(reset, bytes([0x51, 0x01]))

        response = self.uds_exchange(bytes([0x27, 0x01]))

        self.assertNrc(
            response,
            0x27,
            NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED,
            "复位后仍应处于锁定",
        )

    def test_lockout_takes_priority_over_any_key(self) -> None:
        """
        锁定期间任意 key 都被 0x37 拦截（优先于 0x24 / 0x35）。

        锁定后 PENDING_SEED 已被清除，因此这里验证的是
        "锁定检查优先于顺序检查与 key 校验"。
        """
        response = self.uds_exchange(bytes([0x27, 0x02]) + bytes([0xAA] * 4))

        self.assertNrc(
            response,
            0x27,
            NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED,
            "锁定中任意 key",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
