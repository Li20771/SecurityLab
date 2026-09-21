#!/usr/bin/env python3
"""
ISO-TP 畸形分帧与协议边界实验（Phase 4-P3）

用 **raw CAN 注入**绕过内核 CAN_ISOTP 的正常封装，研究虚拟 ECU 对
异常 ISO-TP 帧序列的真实处理。

为什么要绕过 ISO-TP 栈
----------------------

``isotp_send.py`` 会把 payload 交给内核分帧，因此**永远发不出畸形帧**。
要构造非法 PCI、缺帧、错误 SN、异常 FC，必须像 ``can_inject.py``
那样直接在链路层写字节。

但本模块不重新实现 ISO-TP：它只在链路层注入 raw CAN 帧，
分帧语义完全交给真实链路::

    raw CAN 帧
      → vcan0
      → 内核 SocketCAN
      → CAN_ISOTP（被注入的 ECU socket）
      → ECU 的 UDS dispatcher（ecu/ecu.py 子进程）

分层观察（本阶段最重要的方法论）
--------------------------------

被测对象的响应可能发生在**任何一层**，所以每个 case 都记录五个观察点：

1. ``INJECT``   —— 我们注入的 raw CAN 帧（ID / DLC / 字节 / 延迟）
2. ``CAPTURE``  —— vcan0 上捕获到的帧（对端是否回帧、回了什么）
3. ``ECU``      —— ECU 子进程 alive / dead
4. ``RESULT``   —— 是否产生 ISO-TP 响应、是否产生 UDS 响应、是否超时
5. ``RECOVERY`` —— 异常输入之后，正常 UDS 请求还能否成功

关键点：**"没有 UDS 响应"不等于失败**。非法 PCI 很可能在内核
CAN_ISOTP 层就被丢弃，根本没有进入 ``handle_uds_request()``——
这本身就是本阶段要测的实验结论。

PASS 的定义（见 README）
-----------------------

PASS = "行为符合本 case 事先写明的假设，且 ECU 没有异常崩溃"，
而不是 "ECU 必须返回某个错误"。

安全边界
--------

- 只使用 vcan0（见 parsers.guard_interface）
- 只与本地虚拟 ECU 子进程通信
- 确定性输入，无随机 fuzzing、无并发、无 stress
- 不接触真实车辆 / 真实 ECU / 真实 CAN 适配器

运行：

    /home/lilike/.venvs/network/bin/python tests/test_isotp_malformed_vcan.py
    /home/lilike/.venvs/network/bin/python -m unittest tests.test_isotp_malformed_vcan -v
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time
import unittest

import can

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)
_LAB_DIR = os.path.dirname(_TOOLS_DIR)
_ECU_DIR = os.path.join(_LAB_DIR, "uds-basic", "ecu")
_ECU_SCRIPT = os.path.join(_ECU_DIR, "ecu.py")

if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from parsers import (  # noqa: E402
    ALLOWED_INTERFACE,
    CAN_DATA_MAX_LEN,
    InputError,
    guard_interface,
    parse_can_id,
    parse_hex_bytes,
)

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

INTERFACE = ALLOWED_INTERFACE

#: ECU 接收（= Tester 发送）
ECU_RX_ID = 0x7E0

#: ECU 发送（= Tester 接收）
ECU_TX_ID = 0x7E8

#: 功能寻址（本阶段只做 CAN addressing 对照，不做 UDS 语义）
FUNCTIONAL_ID = 0x7DF

ECU_STARTUP_TIMEOUT = 10.0

#: 注入帧之间的固定间隔（秒）。
#: 固定值 = 确定性；足够长以避免 vcan0 上排队乱序，又足够短以保持测试快。
FRAME_GAP = 0.10

#: 等待对端回帧 / 等待 ECU 处理的时间（秒）
RESPONSE_WAIT = 0.5

#: 多帧响应的等待时间（ECU 要发 FF + 若干 CF；子进程调度可能偏慢）
MULTIFRAME_WAIT = 1.5

#: 正常恢复用的单帧请求与期望响应前缀
RECOVERY_REQUEST = bytes([0x10, 0x01])
RECOVERY_EXPECTED = bytes([0x50, 0x01])

#: 另一个更"纯读"的恢复请求
RECOVERY_REQUEST_READ = bytes([0x22, 0xF1, 0x80])
RECOVERY_EXPECTED_READ = bytes([0x62, 0xF1, 0x80])

PCI_SINGLE = 0x00
PCI_FIRST = 0x10
PCI_CONSECUTIVE = 0x20
PCI_FLOW_CONTROL = 0x30

PCI_NAMES = {
    PCI_SINGLE: "SF",
    PCI_FIRST: "FF",
    PCI_CONSECUTIVE: "CF",
    PCI_FLOW_CONTROL: "FC",
}


def pci_type(data: bytes) -> str:
    """按 PCI 高 4 位判断帧类型。"""
    if not data:
        return "empty"

    return PCI_NAMES.get(data[0] & 0xF0, f"INVALID(0x{data[0]:02X})")


def is_invalid_pci(data: bytes) -> bool:
    """PCI 高 4 位是否属于 4 类基础 ISO-TP 帧之外（0x4–0xF）。"""
    return bool(data) and (data[0] & 0xF0) not in PCI_NAMES


def describe(data: bytes) -> str:
    """人类可读的帧描述：类型 + DLC + 字节。"""
    return f"{pci_type(data):<10} DLC={len(data)}  {data.hex(' ')}"


def extract_payload(frames: list) -> bytes:
    """
    从一段**原始 CAN 帧**中提取 ISO-TP payload（UDS 字节）。

    这是本阶段最容易犯的观察错误：CAN 帧的第一个字节是 **PCI**，
    不是 UDS 数据。例如 ECU 的 ``10 01`` 响应在总线上是::

        06 50 01 00 32 00 64
        ^^ PCI: 单帧，payload 长度 6
           ^^^^^ payload: 50 01 00 32 00 64

    因此直接拿帧字节和 ``50 01`` 比较永远不相等——必须按 ISO-TP
    规则先剥掉 PCI（多帧还要拼接 FF + CF）。

    只做**重组**，不做合法性判断：畸形输入下本函数返回截断/空结果，
    这正是我们要观察的对象。
    """
    payload = bytearray()
    expected = None

    for data in frames:
        if not data:
            continue

        pci = data[0] & 0xF0

        if pci == PCI_SINGLE:
            length = data[0] & 0x0F
            payload += data[1 : 1 + length]
            return bytes(payload)

        if pci == PCI_FIRST:
            expected = ((data[0] & 0x0F) << 8) | data[1]
            payload = bytearray(data[2:])
            continue

        if pci == PCI_CONSECUTIVE:
            if expected is None:
                # 没有 FF 的孤立 CF：不构成 payload
                continue

            payload += data[1:]

            if len(payload) >= expected:
                return bytes(payload[:expected])

            continue

        # FC / 非法 PCI 不携带 payload

    return bytes(payload)


# ----------------------------------------------------------------------
# ECU 子进程管理
# ----------------------------------------------------------------------

_ECU_LOCK = threading.Lock()
_ECU_PROCESS = None
_ECU_LINES: list = []
_ECU_LOG_LOCK = threading.Lock()

#: ECU 打印的请求/响应行，例如 "RX UDS: 10 01" / "TX UDS: 50 01 ..."
_RE_ECU_RX = re.compile(r"^RX UDS: ([0-9a-f ]+)$")
_RE_ECU_TX = re.compile(r"^TX UDS: ([0-9a-f ]+)$")


def _stop_ecu() -> None:
    global _ECU_PROCESS

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

    if _ECU_PROCESS is not None and _ECU_PROCESS.stdout is not None:
        try:
            _ECU_PROCESS.stdout.close()
        except Exception:  # noqa: BLE001 - 清理阶段忽略异常
            pass

    _ECU_PROCESS = None


def _start_ecu(env_overrides: dict = None) -> subprocess.Popen:
    """启动 ECU 子进程，并把它的输出按行收集到内存。"""
    global _ECU_PROCESS, _ECU_LINES

    with _ECU_LOCK:
        _stop_ecu()

        child_env = os.environ.copy()
        child_env["UDS_LOCKOUT_SECONDS"] = "2"

        if env_overrides:
            child_env.update(env_overrides)

        with _ECU_LOG_LOCK:
            _ECU_LINES = []

        _ECU_PROCESS = subprocess.Popen(
            [sys.executable, "-u", _ECU_SCRIPT],
            cwd=_ECU_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=child_env,
            start_new_session=True,
        )

        def _reader(stream) -> None:
            for line in iter(stream.readline, b""):
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                with _ECU_LOG_LOCK:
                    _ECU_LINES.append(text)

        threading.Thread(
            target=_reader, args=(_ECU_PROCESS.stdout,), daemon=True
        ).start()

    return _ECU_PROCESS


def ecu_alive() -> bool:
    return _ECU_PROCESS is not None and _ECU_PROCESS.poll() is None


def ecu_returncode():
    return None if _ECU_PROCESS is None else _ECU_PROCESS.poll()


def ecu_log_lines() -> list:
    with _ECU_LOG_LOCK:
        return list(_ECU_LINES)


def clear_ecu_log() -> None:
    """清空已收集的 ECU 输出（用于只看某一个 case 的日志）。"""
    with _ECU_LOG_LOCK:
        _ECU_LINES.clear()


def ecu_container_errors() -> list:
    """
    返回 ECU 记录到的 transport error containment 日志行。

    修复后这些行是"异常被接住"的直接证据（而不是进程 traceback）。
    """
    return [line for line in ecu_log_lines() if "ISO-TP" in line and "error" in line]


def ecu_uds_exchanges() -> list:
    """ECU 视角的 (请求, 响应) 列表。"""
    requests = []
    responses = []

    for line in ecu_log_lines():
        match = _RE_ECU_RX.match(line)
        if match:
            requests.append(bytes.fromhex(match.group(1).replace(" ", "")))
            continue

        match = _RE_ECU_TX.match(line)
        if match:
            responses.append(bytes.fromhex(match.group(1).replace(" ", "")))

    return list(zip(requests, responses + [None] * (len(requests) - len(responses))))


def ecu_output_tail(limit: int = 12) -> str:
    return "\n".join(ecu_log_lines()[-limit:])


# ----------------------------------------------------------------------
# raw CAN 注入 + 捕获
# ----------------------------------------------------------------------

def raw_data(frame: "RawFrame") -> bytes:
    """把实验用的帧规格转换成要发送的字节。"""
    return parse_hex_bytes([frame.to_hex()])


class RawFrame:
    """
    一帧待注入的 raw CAN 帧。

    字节来源可以是 ``bytes``（实验内部构造，推荐）或 can_inject.py 那种
    十六进制写法（``"02 10 03"``）。两种写法都复用同一套长度校验。
    """

    __slots__ = ("arbitration_id", "data", "note")

    def __init__(self, arbitration_id: int, data, note: str = ""):
        self.arbitration_id = parse_can_id(str(arbitration_id))
        self.note = note

        if isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
        else:
            # 复用 can_inject.py 的十六进制解析规则
            raw = parse_hex_bytes([str(data)])

        # 与 can_inject.py 相同的长度上限（经典 CAN 单帧 8 字节）
        if len(raw) > CAN_DATA_MAX_LEN:
            raise InputError(
                f"raw CAN 帧数据 {len(raw)} 字节超过经典 CAN 上限 "
                f"{CAN_DATA_MAX_LEN} 字节"
            )

        self.data = raw

    def __str__(self) -> str:
        return f"{self.arbitration_id:03X}  {describe(self.data)}"


def sf(arbitration_id: int, payload: bytes, note: str = "SF") -> RawFrame:
    """合法单帧：PCI = 0x0N，N = payload 长度。"""
    if len(payload) > CAN_DATA_MAX_LEN - 1:
        raise InputError("单帧 payload 最多 7 字节")

    return RawFrame(arbitration_id, bytes([len(payload)]) + payload, note=note)


def ff(arbitration_id: int, total_length: int, first_data: bytes,
       note: str = "FF") -> RawFrame:
    """
    First Frame：PCI = 0x1 + 12-bit 总长度，后随最多 6 字节数据。

    ``total_length`` 与 ``first_data`` **故意分开传**：畸形实验需要让
    声明长度与实际数据不一致，这正是 Case 3/4 的核心变量。
    """
    if len(first_data) > 6:
        raise InputError("FF 最多携带 6 字节数据")

    pci = bytes([PCI_FIRST | ((total_length >> 8) & 0x0F), total_length & 0xFF])

    return RawFrame(arbitration_id, pci + first_data, note=note)


def cf(arbitration_id: int, sequence_number: int, data: bytes,
       note: str = "CF") -> RawFrame:
    """Consecutive Frame：PCI = 0x2 + SN（SN 低 4 位）。"""
    return RawFrame(
        arbitration_id,
        bytes([PCI_CONSECUTIVE | (sequence_number & 0x0F)]) + data,
        note=note,
    )


def fc(arbitration_id: int, status: int, block_size: int = 0,
       stmin: int = 0, note: str = "FC") -> RawFrame:
    """Flow Control：PCI = 0x3 + 状态（0=CTS / 1=WAIT / 2=OVERFLOW）。"""
    return RawFrame(
        arbitration_id,
        bytes([PCI_FLOW_CONTROL | (status & 0x0F), block_size, stmin]),
        note=note,
    )


def raw(arbitration_id: int, data, note: str = "raw") -> RawFrame:
    """任意原始字节（用于非法 PCI / DLC 实验）。"""
    return RawFrame(arbitration_id, data, note=note)


class RawCanHarness:
    """
    一个 vcan0 上的 raw CAN 注入 + 捕获通道。

    刻意**不**使用 python-isotp：本阶段的全部价值就在于绕过 ISO-TP 栈。
    发送路径与 ``can_inject.py`` 等价（同一个接口，同一套字节校验），
    只是为了精确控制帧间时序而在进程内实现。
    """

    def __init__(self):
        self.interface = guard_interface(INTERFACE)
        self.bus = can.interface.Bus(interface="socketcan", channel=self.interface)
        self.bus.socket.settimeout(0.05)

        self.injected: list = []
        self.captured: list = []
        self._lock = threading.Lock()

        harness = self

        class _Listener(can.Listener):
            def on_message_received(self, msg) -> None:  # noqa: D102
                with harness._lock:
                    harness.captured.append(
                        (msg.timestamp, msg.arbitration_id, bytes(msg.data))
                    )

        self._notifier = can.Notifier(self.bus, [_Listener()])

    # ---- 发送 ----

    def send(self, frame: RawFrame) -> None:
        """注入一帧（发送前记录，便于报告 INJECT 段）。"""
        message = can.Message(
            arbitration_id=frame.arbitration_id,
            is_extended_id=False,
            data=frame.data,
        )

        with self._lock:
            self.injected.append(
                (time.time(), frame.arbitration_id, frame.data, frame.note)
            )

        self.bus.send(message)

    def send_sequence(self, frames, gap: float = FRAME_GAP) -> None:
        """按固定间隔依次注入多帧。"""
        for index, frame in enumerate(frames):
            self.send(frame)

            if gap and index < len(frames) - 1:
                time.sleep(gap)

    # ---- 捕获 ----

    def mark(self) -> int:
        """记录当前捕获位置，用于只看某一段的帧。"""
        with self._lock:
            return len(self.captured)

    def since(self, mark: int) -> list:
        with self._lock:
            return list(self.captured[mark:])

    def frames_from(self, mark: int, arbitration_id: int) -> list:
        return [
            (ts, data)
            for ts, cid, data in self.since(mark)
            if cid == arbitration_id
        ]

    def data_from(self, mark: int, arbitration_id: int) -> list:
        return [data for _, data in self.frames_from(mark, arbitration_id)]

    def payloads_from(self, mark: int, arbitration_id: int) -> list:
        """
        返回该方向上**重组后的 ISO-TP payload** 列表。

        每个 payload 从它自己的 SF/FF 起算，因此一次交换得到一个元素。
        """
        payloads = []
        buffer = []
        started = False

        for data in self.data_from(mark, arbitration_id):
            if not data:
                continue

            pci = data[0] & 0xF0

            if pci == PCI_SINGLE:
                payloads.append(extract_payload([data]))
                buffer = []
                started = False
                continue

            if pci == PCI_FIRST:
                if started and buffer:
                    payloads.append(extract_payload(buffer))

                buffer = [data]
                started = True
                continue

            if pci == PCI_CONSECUTIVE and started:
                buffer.append(data)
                continue

        if started and buffer:
            payloads.append(extract_payload(buffer))

        return [p for p in payloads if p]

    def payload_from(self, mark: int, arbitration_id: int) -> bytes:
        """该方向上的第一个重组 payload（没有则返回 b""）。"""
        payloads = self.payloads_from(mark, arbitration_id)

        return payloads[0] if payloads else b""

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    # ---- 清理 ----

    def close(self) -> None:
        try:
            self._notifier.stop()
        except Exception:  # noqa: BLE001 - 清理阶段忽略异常
            pass

        try:
            self.bus.shutdown()
        except Exception:  # noqa: BLE001 - 清理阶段忽略异常
            pass


# ----------------------------------------------------------------------
# 基线请求（用 raw 帧手工写出合法的多帧请求）
# ----------------------------------------------------------------------

#: 合法的多帧 UDS 请求：ReadDataByIdentifier 0xF190 + 0xF180
#: 完整 payload = 22 F1 90 F1 80（5 字节）
MULTI_REQUEST_PAYLOAD = bytes([0x22, 0xF1, 0x90, 0xF1, 0x80])

#: 单帧合法请求 10 01
SINGLE_REQUEST_PAYLOAD = bytes([0x10, 0x01])


def report(case: str, inject: list, capture: list, ecu_state: str, result: str,
           recovery: str = "") -> str:
    """
    生成统一格式的实验记录（教学价值所在：不写裸 PASS）。
    """
    lines = [f"CASE: {case}", "", "INJECT:"]

    for _, cid, data, note in inject:
        suffix = f"   # {note}" if note else ""
        lines.append(f"    {cid:03X}  {describe(data)}{suffix}")

    lines.append("")
    lines.append("CAPTURE:")

    if capture:
        for ts, cid, data in capture:
            lines.append(f"    {ts:.6f}  {cid:03X}  {describe(data)}")
    else:
        lines.append("    (none)")

    lines.append("")
    lines.append(f"ECU: {ecu_state}")
    lines.append("")
    lines.append("RESULT:")
    lines.append(f"    {result}")

    if recovery:
        lines.append("")
        lines.append("RECOVERY:")
        lines.append(f"    {recovery}")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 测试基类
# ----------------------------------------------------------------------


class MalformedCaseTest(unittest.TestCase):
    """
    每个测试**类**共享一个 ECU 子进程；每个 case 之后做正常恢复检查。

    隔离策略（与 P2 的 test_isotp_options_vcan.py 一致）：
    每个类在 ``setUpClass`` 启动全新 ECU 进程，因此组内各 case 的
    "上一个 case 是否留下状态"不会串到别的组。

    这一点在 P3 特别重要：实验已经证明某些异常 FC 会让内核向 ECU
    抛出 ``OSError: [Errno 70]`` 并**杀死 ECU 进程**。若所有 case 共用一个
    ECU，第一个崩溃后面的 case 就全部失去意义。
    """

    ECU_ENV = {}

    #: 子类设为 True 时，允许"ECU 被实验输入杀死"——此时把它当作
    #: **已记录的实验结果**而不是测试失败（用于隔离单个输入变量）。
    ALLOW_ECU_DEATH = False

    @classmethod
    def setUpClass(cls) -> None:
        _start_ecu(cls.ECU_ENV)
        cls._wait_for_ready()

    @classmethod
    def tearDownClass(cls) -> None:
        _stop_ecu()

    @classmethod
    def _wait_for_ready(cls) -> None:
        """
        等待 ECU 就绪。

        这里也刻意用 **raw 帧**（而不是 python-isotp）来探测，
        以确认"raw CAN 注入链路本身"是通的。
        """
        deadline = time.monotonic() + ECU_STARTUP_TIMEOUT
        harness = RawCanHarness()

        try:
            while time.monotonic() < deadline:
                mark = harness.mark()

                harness.send(sf(ECU_RX_ID, RECOVERY_REQUEST, note="readiness probe"))
                harness.wait(0.3)

                # 注意：必须比较**重组后的 payload**，不是原始 CAN 帧
                for payload in harness.payloads_from(mark, ECU_TX_ID):
                    if payload[:2] == RECOVERY_EXPECTED:
                        return

                if not ecu_alive():
                    raise AssertionError(
                        f"ECU 启动后立即退出：\n{ecu_output_tail()}"
                    )

            raise AssertionError(
                f"ECU 在 {ECU_STARTUP_TIMEOUT}s 内没有就绪。\n{ecu_output_tail()}"
            )
        finally:
            harness.close()

    def setUp(self) -> None:
        # 每个 case 之前先让上一轮的残留在途帧落地并被丢弃，
        # 避免上一个 case 的延迟响应被误算进本 case 的捕获结果。
        self.harness = RawCanHarness()
        self.harness.wait(0.4)
        self.harness.mark()

    def tearDown(self) -> None:
        self.harness.close()

    # ---- 断言助手 ----

    def ecu_crash_signature(self) -> str:
        """从 ECU 输出里提取内核抛出的错误签名（用于记录与分类）。"""
        for line in reversed(ecu_log_lines()):
            line = line.strip()

            if line.startswith(("OSError", "TimeoutError", "RuntimeError", "ValueError")):
                return line

        return "(未捕获到异常行)"

    def assert_recovered_or_documented_crash(self, case: str) -> tuple:
        """
        记录 case 结束时的 ECU 状态，并返回是否恢复正常通信。

        结果分三类：

        - **正常路径**：malformed 输入被内核丢弃，ECU 存活且能响应后续正常请求
        - **进程死亡**：ECU 已退出 —— 打印错误签名供报告使用，返回 ``False``
        - **ECU 存活但无法恢复**：直接判失败（这才是需要深挖的状态污染）

        注意：本方法**只负责记录**，不判断进程死亡算不算失败。
        P3R 修复后，"输入杀死 ECU"应由各用例的断言判为**失败**
        （P3 阶段用 ``@unittest.expectedFailure`` 记录缺陷的机制已随修复移除，
        因为那会掩盖未来的回归）。
        """
        if not ecu_alive():
            signature = self.ecu_crash_signature()

            self.emit_note(
                f"[{case}] 已记录崩溃：returncode={ecu_returncode()} | {signature}"
            )

            return False, signature

        ok, recovery = self.recovery_check()

        self.assertTrue(
            ok,
            f"[{case}] ECU 存活但无法恢复正常通信（疑似状态污染）：{recovery}\n"
            f"ECU 输出：\n{ecu_output_tail(20)}",
        )

        return True, recovery

    def wait_for_ecu_death(self, timeout: float = 3.0, poll: float = 0.1) -> bool:
        """
        等待 ECU 因内核错误而退出，返回是否观察到死亡。

        为什么必须显式等待：``OSError: [Errno 70]`` 是内核在
        **N_Bs 超时之后**才抛出的（实测约 1.2 s），比一次 ISO-TP 往返
        慢得多。若测量窗口太短，就会把"还没来得及崩"误判成"存活"。
        """
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if not ecu_alive():
                # 让读取线程把最后几行 traceback 收完
                time.sleep(0.2)

                return True

            time.sleep(poll)

        return not ecu_alive()

    def assert_ecu_alive(self, case: str) -> None:
        if not ecu_alive():
            detail = (
                f"[{case}] ECU 进程已退出（returncode={ecu_returncode()}）。\n"
                f"ECU 输出：\n{ecu_output_tail(20)}"
            )

            if self.ALLOW_ECU_DEATH:
                # 本组用例的目的就是隔离"这一个输入变量是否杀死 ECU"，
                # 因此进程死亡是要记录的结果，不是测试失败。
                self.emit_note(f"[{case}] ECU 被该输入杀死（已记录）")
                return

            self.fail(detail)

    def emit_note(self, text: str) -> None:
        print(text, flush=True)

    def recovery_check(self) -> tuple:
        """
        执行一次正常 UDS 请求，返回 (是否成功, 描述)。

        这是所有 malformed case 的**强制后置检查**：
        比"刚才那个异常包有没有响应"更能说明问题。

        若 ECU 已经死亡，直接返回失败——避免在死进程上等待，
        也让报告明确显示"进程死亡"而不是"超时"。
        """
        if not ecu_alive():
            return False, f"ECU 进程已退出（returncode={ecu_returncode()}），无法恢复"

        mark = self.harness.mark()
        self.harness.send(sf(ECU_RX_ID, RECOVERY_REQUEST, note="recovery check"))
        self.harness.wait(RESPONSE_WAIT)

        payloads = self.harness.payloads_from(mark, ECU_TX_ID)

        for payload in payloads:
            if payload[:2] == RECOVERY_EXPECTED:
                return True, f"10 01 -> {payload.hex(' ')}"

        if not ecu_alive():
            return (
                False,
                f"10 01 -> ECU 在恢复检查期间退出（returncode={ecu_returncode()}）",
            )

        if payloads:
            return False, f"10 01 -> {payloads[0].hex(' ')}（不是预期的 50 01）"

        return False, "10 01 -> (no response)"

    def assert_recovered(self, case: str) -> None:
        """malformed 输入之后，ECU 必须仍能正常通信。"""
        ok, detail = self.recovery_check()

        self.assertTrue(
            ok,
            f"[{case}] 异常输入之后 ECU 无法响应正常请求：{detail}\n"
            f"ECU 输出：\n{ecu_output_tail(20)}",
        )

    def collect(self, mark: int) -> list:
        return self.harness.since(mark)

    def wait(self, seconds: float) -> None:
        self.harness.wait(seconds)

    #: 触发 20 字节多帧响应的单帧请求（22 F1 90）
    REQUEST_20B = bytes([0x22, 0xF1, 0x90])

    def inject_during_active_send(
        self,
        abnormal_fc: RawFrame,
        note: str,
        respond_to_ff: bool = True,
        fc_block_size: int = 0,
        fc_stmin: int = 0,
    ) -> dict:
        """
        在 ECU **正在发送多帧响应**时注入异常 FC。

        时序::

            1. 合法单帧请求 22 F1 90（触发 ECU 的 20 字节多帧响应）
            2. ECU 回 FF
            3. 我们回一个正常的 CTS（否则 ECU 的传输会一直等 FC）
            4. 注入实验用的异常 FC
            5. 观察 ECU 的发送是否被打断、进程是否存活

        第 3 步是**必须的**：ISO-TP 要求接收方对 FF 回 FC，
        不回 FC 会让 ECU 在 N_Bs 超时后被内核抛错杀死
        （这本身是 P3 的一个发现，但不是本组要隔离的变量）。

        受控变量只有第 4 步注入的那一帧。
        """
        mark = self.harness.mark()

        self.harness.send(sf(ECU_RX_ID, self.REQUEST_20B, note="legal SF 22 F1 90"))

        deadline = time.monotonic() + 1.0
        saw_ff = False

        while time.monotonic() < deadline:
            if any(
                d and (d[0] & 0xF0) == PCI_FIRST
                for d in self.harness.data_from(mark, ECU_TX_ID)
            ):
                saw_ff = True
                break

            time.sleep(0.02)

        if respond_to_ff and saw_ff:
            # 正常 CTS：让 ECU 的传输得以继续（这是基线行为，不是实验变量）
            self.harness.send(
                fc(ECU_RX_ID, 0, fc_block_size, fc_stmin, note="normal CTS (keep transfer alive)")
            )

        if abnormal_fc is not None:
            self.harness.send(abnormal_fc)

        self.harness.wait(MULTIFRAME_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        ecu_frames = self.harness.data_from(mark, ECU_TX_ID)

        return {
            "mark": mark,
            "saw_ff": saw_ff,
            "captured": captured,
            "payloads": payloads,
            "ecu_frames": ecu_frames,
            "note": note,
        }

    def report_active_send(self, case: str, info: dict, alive_note: str = "") -> tuple:
        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        complete = [p for p in info["payloads"] if len(p) >= 20]

        self.emit(
            case,
            self.harness.injected[-2:],
            info["captured"],
            result=(
                f"saw ECU FF: {info['saw_ff']} | "
                f"ECU frame sequence: {[pci_type(d) for d in info['ecu_frames']]} | "
                f"complete 20B payload: {[p.hex(' ') for p in complete]} | "
                f"all payloads: {[p.hex(' ') for p in info['payloads']]} | "
                f"ECU alive: {alive}{alive_note}"
            ),
            recovery=recovery,
        )

        return ok, recovery, complete

    def emit(self, case: str, inject: list, capture: list, result: str,
             recovery: str = "") -> None:
        """把实验记录打印出来（始终输出，便于人工复核）。"""
        state = (
            f"alive (pid={_ECU_PROCESS.pid})"
            if ecu_alive()
            else f"DEAD (returncode={ecu_returncode()})"
        )

        text = report(case, inject, capture, state, result, recovery)

        print()
        print("=" * 70)
        print(text)
        print("=" * 70)
        sys.stdout.flush()


# ======================================================================
# BASELINE：合法多帧必须正常
# ======================================================================


class TestBaseline(MalformedCaseTest):
    """对照组：合法 raw 帧序列必须得到正常的 UDS 响应。

    没有这个对照，后面所有"没有响应"的结论都不可信——
    可能只是 raw 注入链路本身是坏的。
    """

    def test_baseline_single_frame(self) -> None:
        """raw 单帧 10 01 → payload 50 01 ..."""
        mark = self.harness.mark()
        self.harness.send(sf(ECU_RX_ID, SINGLE_REQUEST_PAYLOAD, note="legal SF 10 01"))
        self.harness.wait(RESPONSE_WAIT)

        payloads = self.harness.payloads_from(mark, ECU_TX_ID)

        self.emit(
            "BASELINE single frame (10 01)",
            self.harness.injected[-1:],
            self.collect(mark),
            result=f"reassembled payloads: {[p.hex(' ') for p in payloads]}",
        )

        self.assertTrue(payloads, "合法单帧必须得到响应")
        self.assertEqual(payloads[0][:2], RECOVERY_EXPECTED)
        self.assert_ecu_alive("baseline-sf")

    def test_baseline_multi_frame_request(self) -> None:
        """
        raw 多帧请求：FF + CF 手工分帧，ECU 必须正确重组。

        payload = ``22 F1 90 F1 80``（5 字节）其实可以放进单帧，
        所以这里改用更长的 payload（> 7 字节）来真正触发多帧：

            22 F1 90 F1 80 F1 87 ...

        为了保持 UDS 语义可预测，这里用 8 字节 payload：
        第一个 DID 读得到数据，第二个 DID 不存在 → 得到 NRC，
        但**关键是 ECU 必须重组出完整请求并回帧**（无论响应内容）。
        """
        payload = bytes([0x22, 0xF1, 0x90, 0xF1, 0x80, 0xF1, 0x87, 0xF1])
        self.assertGreater(len(payload), CAN_DATA_MAX_LEN - 1, "本用例必须触发多帧")

        ff_frame = ff(
            ECU_RX_ID,
            len(payload),
            payload[:6],
            note="legal FF (declares total length 8)",
        )
        cf_frame = cf(ECU_RX_ID, 1, payload[6:], note="legal CF SN=1")

        mark = self.harness.mark()
        self.harness.send_sequence([ff_frame, cf_frame])
        self.harness.wait(MULTIFRAME_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)

        # ECU 必须先发 FC（因为我们是发送方），再等我们的 CF
        self.emit(
            "BASELINE multi-frame request (FF + CF)",
            self.harness.injected[-2:],
            captured,
            result=(
                f"reassembled payloads: {[p.hex(' ') for p in payloads]} | "
                f"ECU-side UDS: "
                f"{[(r.hex(' '), (s.hex(' ') if s else None)) for r, s in ecu_uds_exchanges()]}"
            ),
        )

        self.assertTrue(payloads, "合法多帧请求必须得到响应")
        self.assert_ecu_alive("baseline-mf")
        self.assert_recovered("baseline-mf")


# ======================================================================
# Layer 1 — PCI 异常
# ======================================================================


class TestInvalidPci(MalformedCaseTest):
    """
    Case 1：非法 PCI type（0x4–0xF）。

    这些高 4 位不属于 ISO-TP 的四类基础帧。**不要假定 ECU 会返回 NRC**：
    这类帧极可能在 CAN_ISOTP 层就被丢弃，根本没进 UDS dispatcher。
    """

    def _case(self, pci_byte: int) -> None:
        frame = raw(
            ECU_RX_ID,
            bytes([pci_byte, 0x10, 0x03, 0x00]),
            note=f"invalid PCI type 0x{pci_byte >> 4:X}",
        )
        case = f"INVALID-PCI 0x{pci_byte:02X}"

        self.assertTrue(
            is_invalid_pci(bytes([pci_byte])),
            "本用例的 PCI 必须不属于 4 类基础帧",
        )

        mark = self.harness.mark()
        self.harness.send(frame)
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-1:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"UDS response: {'none' if not payloads else 'yes'} | "
                f"ECU alive: {alive}"
            ),
            recovery=recovery,
        )

        # 本 case 的实验假设：非法 PCI 被丢弃，不产生 UDS 响应，ECU 不崩溃
        self.assertFalse(
            payloads,
            f"[{case}] 非法 PCI 不应产生 UDS 响应，实际 {[p.hex(' ') for p in payloads]}",
        )
        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 非法 PCI 之后 ECU 失去正常通信能力：{recovery}")

    def test_invalid_pci_4x(self) -> None:
        """PCI 0x40 不是任何 ISO-TP 帧类型。"""
        self._case(0x40)

    def test_invalid_pci_5x(self) -> None:
        self._case(0x50)

    def test_invalid_pci_6x(self) -> None:
        self._case(0x60)

    def test_invalid_pci_7x(self) -> None:
        self._case(0x70)

    def test_invalid_pci_ff(self) -> None:
        """PCI 0xF0 属于保留区间。"""
        self._case(0xF0)


class TestMalformedSingleFrame(MalformedCaseTest):
    """
    Case 2：Single Frame 的声明长度与实际字节数不一致。

    ``0x0N`` 里的 N 是 ISO-TP 的 payload 长度。内核会按 N 截断/补齐，
    因此要观察的是**它究竟交付给 UDS 的是什么字节**。
    """

    def _case(self, frame: RawFrame, case: str) -> None:
        mark = self.harness.mark()
        self.harness.send(frame)
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        exchanges = ecu_uds_exchanges()
        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-1:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU-side UDS (last): "
                f"{[(r.hex(' '), (s.hex(' ') if s else None)) for r, s in exchanges[-1:]]} | "
                f"ECU alive: {alive}"
            ),
            recovery=recovery,
        )

        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")

    def test_sf_declares_more_than_provided(self) -> None:
        """
        ``04 10 03``：声明 4 字节 payload，实际只给 2 字节。

        观察内核是否按 DLC 补齐（补齐字节会进入 UDS）。UDS 侧预期
        ``10 03 00 00`` 是无效长度 → NRC（具体 NRC 以实测为准）。
        """
        frame = raw(ECU_RX_ID, bytes([0x04, 0x10, 0x03]), note="SF declares 4, gives 2")

        self._case(frame, "MALFORMED-SF declares 4 bytes, provides 2")

    def test_sf_declares_less_than_provided(self) -> None:
        """
        ``02 10 03 99``：声明 2 字节，实际给 3 字节。

        观察第 3 个字节（0x99）是被当作 padding 丢弃，还是被交付。
        """
        frame = raw(
            ECU_RX_ID, bytes([0x02, 0x10, 0x03, 0x99]), note="SF declares 2, gives 3"
        )

        self._case(frame, "MALFORMED-SF declares 2 bytes, provides 3")

    def test_sf_declares_zero(self) -> None:
        """``00``：声明 0 字节 payload。"""
        frame = raw(ECU_RX_ID, bytes([0x00]), note="SF declares 0 bytes")

        self._case(frame, "MALFORMED-SF declares 0 bytes")


# ======================================================================
# Layer 2 — 分帧异常
# ======================================================================


class TestSegmentation(MalformedCaseTest):
    """Case 3/4/5：FF 不完整、FF 长度异常、无 FF 的孤立 CF。"""

    def test_incomplete_ff_then_recovery(self) -> None:
        """
        Case 3：FF 声明 0x20 字节，但**不发送任何 CF**。

        观察点：ECU 是否阻塞？此后正常单帧请求还能否被处理？

        注意：不发送 FC 给 ECU 时，内核不会超时"我们"；
        受影响的是**内核对本次重组的状态**。
        """
        frame = ff(
            ECU_RX_ID,
            0x20,
            bytes([0x22, 0xF1, 0x90, 0xF1, 0x80, 0xF1]),
            note="FF declares 32 bytes, no CF follows",
        )
        case = "INCOMPLETE-FF declares 32 bytes, no CF"

        mark = self.harness.mark()
        self.harness.send(frame)

        # 明显长于正常往返时间，给内核足够机会暴露超时/阻塞行为
        self.harness.wait(1.0)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)

        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-1:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU alive: {alive} | "
                f"说明：未收到 FC 时我们不发 CF，重组停在此处"
            ),
            recovery=recovery,
        )

        self.assert_ecu_alive(case)
        self.assertTrue(
            ok,
            f"[{case}] 未完成的 FF 之后 ECU 无法响应正常请求：{recovery}",
        )

    def test_ff_declares_small_but_more_cf_sent(self) -> None:
        """
        Case 4a：FF 声明总长 3 字节，却继续发送 CF。

        内核已按声明的 3 字节完成重组，多余的 CF 预期被忽略。
        """
        ff_frame = ff(
            ECU_RX_ID, 3, bytes([0x10, 0x03, 0x00, 0x00, 0x00, 0x00]),
            note="FF declares 3 bytes total",
        )
        cf_frame = cf(ECU_RX_ID, 1, bytes([0xAA, 0xBB]), note="surplus CF after complete")

        case = "WRONG-FF-LENGTH declares 3, extra CF sent"

        mark = self.harness.mark()
        self.harness.send_sequence([ff_frame, cf_frame])
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        exchanges = ecu_uds_exchanges()

        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-2:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU-side UDS (last): "
                f"{[(r.hex(' '), (s.hex(' ') if s else None)) for r, s in exchanges[-1:]]} | "
                f"ECU alive: {alive}"
            ),
            recovery=recovery,
        )

        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")

    def test_ff_declares_larger_than_cf_provides(self) -> None:
        """
        Case 4b：FF 声明 0x10（16）字节，但 CF 只补了很少的数据。

        观察点：内核是否一直等待剩余字节（重组不完成 → 无 UDS 请求）。
        """
        ff_frame = ff(
            ECU_RX_ID, 0x10, bytes([0x22, 0xF1, 0x90, 0xF1, 0x80, 0xF1]),
            note="FF declares 16 bytes total",
        )
        cf_frame = cf(ECU_RX_ID, 1, bytes([0x87, 0xF1]), note="CF provides only 2 bytes")

        case = "WRONG-FF-LENGTH declares 16, CF supplies 8 total"

        mark = self.harness.mark()
        self.harness.send_sequence([ff_frame, cf_frame])
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        exchanges = ecu_uds_exchanges()

        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-2:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU-side UDS (last): "
                f"{[(r.hex(' '), (s.hex(' ') if s else None)) for r, s in exchanges[-1:]]} | "
                f"ECU alive: {alive}"
            ),
            recovery=recovery,
        )

        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")

    def test_cf_without_ff(self) -> None:
        """
        Case 5：没有 FF 的孤立 CF（SN=1）。

        CF 不携带总长度，无法单独构成 ISO-TP PDU，预期在传输层被丢弃，
        **不应**产生任何 UDS 响应。
        """
        frame = cf(ECU_RX_ID, 1, bytes([0x10, 0x03, 0x00, 0x00]), note="orphan CF SN=1")
        case = "CF-WITHOUT-FF orphan SN=1"

        mark = self.harness.mark()
        self.harness.send(frame)
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        exchanges = ecu_uds_exchanges()

        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-1:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU-side UDS (last): "
                f"{[(r.hex(' '), (s.hex(' ') if s else None)) for r, s in exchanges[-1:]]} | "
                f"ECU alive: {alive}"
            ),
            recovery=recovery,
        )

        self.assertFalse(
            payloads, f"[{case}] 孤立 CF 不应产生 UDS 响应"
        )
        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")

    def test_orphan_cf_sequence(self) -> None:
        """Case 5b：连续多个孤立 CF（SN=1,2,3），仍不应有响应。"""
        frames = [
            cf(ECU_RX_ID, 1, bytes([0x11, 0x22]), note="orphan CF SN=1"),
            cf(ECU_RX_ID, 2, bytes([0x33, 0x44]), note="orphan CF SN=2"),
            cf(ECU_RX_ID, 3, bytes([0x55, 0x66]), note="orphan CF SN=3"),
        ]
        case = "CF-WITHOUT-FF orphan SN=1,2,3"

        mark = self.harness.mark()
        self.harness.send_sequence(frames)
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)

        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-3:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU alive: {alive}"
            ),
            recovery=recovery,
        )

        self.assertFalse(payloads, f"[{case}] 孤立 CF 序列不应产生 UDS 响应")
        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")


# ======================================================================
# Layer 2b — Consecutive Frame 序号异常（P3 最关键的一组）
# ======================================================================


class SequentialSnBase(MalformedCaseTest):
    """
    Case 6 的公共实现：正常 FF 之后，用**一个**异常的 CF 替换正常 CF。

    受控变量只有 SN（及数据字节），其余全部合法：

        FF  (合法，声明总长 22)
        CF1 (合法，SN=1)
        CF? (实验帧：SN 正常/跳号/重复/回退)

    为了让序号实验真正可观察，payload 必须**足够长**（> 2 个 CF），
    所以这里用 22 字节的多 DID 请求：

        22 F1 90 F1 80 F1 87 F1 86 F1 85 F1 84 F1 83
        (5 个 DID，共 22 字节 → FF + 3 CF)

    ``22 F1 90`` 只有 20 字节，FF + 2 CF，SN 操作空间不够，
    因此改用 5 个 DID。
    """

    #: 22 字节请求 → FF(6) + CF1(7) + CF2(7) + CF3(2)
    PAYLOAD = bytes(
        [0x22, 0xF1, 0x90, 0xF1, 0x80, 0xF1, 0x87, 0xF1, 0x86, 0xF1, 0x85, 0xF1,
         0x84, 0xF1, 0x83, 0xF1, 0x82, 0xF1, 0x81, 0xF1, 0x80, 0xF1]
    )

    def _frames(self, sn: int, note: str) -> list:
        """
        FF + CF1（合法）+ 实验 CF。

        实验 CF 携带第 3 段数据并声明 ``sn``——受控变量只有序号。
        """
        return [
            ff(ECU_RX_ID, len(self.PAYLOAD), self.PAYLOAD[0:6],
               note="legal FF (22 bytes)"),
            cf(ECU_RX_ID, 1, self.PAYLOAD[6:13], note="legal CF SN=1"),
            cf(ECU_RX_ID, sn, self.PAYLOAD[13:20], note=note),
        ]

    def _run_sn_case(self, frames: list, case: str,
                     expect_crash: bool = False) -> tuple:
        """
        执行一个 SN 异常 case。

        P3R 之后 ``expect_crash`` 默认为 False —— 即**期望 ECU 存活并恢复**。
        P3 阶段该参数默认为 True（当时序号异常确实会杀死 ECU）；
        保留这个开关是为了让"缺陷是否复发"可以显式对照，而不是隐含在断言里。
        """
        mark = self.harness.mark()
        self.harness.send_sequence(frames, gap=FRAME_GAP)
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        exchanges = ecu_uds_exchanges()

        alive_after = ecu_alive()
        signature = self.ecu_crash_signature() if not alive_after else ""

        if expect_crash:
            # 必须**真的**观察到进程死亡，否则不允许通过——
            # 否则"上一个 case 已经把 ECU 弄死了"也会让断言蒙混过关。
            self.assertFalse(
                alive_after,
                f"[{case}] 期望该输入杀死 ECU，但进程仍然存活；"
                f"因此本用例的结论必须重新评估",
            )
            self.assertIn(
                "OSError",
                signature,
                f"[{case}] 期望内核 OSError，实际签名为 {signature!r}",
            )

        _, recovery = self.assert_recovered_or_documented_crash(case)

        self.emit(
            case,
            self.harness.injected[-len(frames):],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU-side UDS (last): "
                f"{[(r.hex(' '), (s.hex(' ') if s else None)) for r, s in exchanges[-1:]]} | "
                f"ECU alive after inject: {alive_after} | "
                f"crash signature: {signature or '(none)'}"
            ),
            recovery=recovery,
        )

        return payloads, exchanges


class TestSnBaseline(SequentialSnBase):
    """对照组：完全合法的 FF + CF1 + CF2 + CF3 必须得到响应。

    没有这个对照，"序号异常导致无响应"就无法与"这套 payload 本身
    就发不通"区分开。
    """

    def test_sequential_sn_is_accepted(self) -> None:
        """SN 顺序 1,2,3 → 必须重组出完整 22 字节请求并响应。"""
        frames = [
            ff(ECU_RX_ID, len(self.PAYLOAD), self.PAYLOAD[0:6],
               note="legal FF (22 bytes)"),
            cf(ECU_RX_ID, 1, self.PAYLOAD[6:13], note="legal CF SN=1"),
            cf(ECU_RX_ID, 2, self.PAYLOAD[13:20], note="legal CF SN=2"),
            cf(ECU_RX_ID, 3, self.PAYLOAD[20:22], note="legal CF SN=3"),
        ]

        case = "BASELINE-SN sequential 1,2,3"

        mark = self.harness.mark()
        self.harness.send_sequence(frames, gap=FRAME_GAP)
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        exchanges = ecu_uds_exchanges()

        ok, recovery = self.recovery_check()

        self.emit(
            case,
            self.harness.injected[-len(frames):],
            captured,
            result=(
                f"ISO-TP response: {[p.hex(' ') for p in payloads]} | "
                f"ECU-side UDS (last): "
                f"{[(r.hex(' '), (s.hex(' ') if s else None)) for r, s in exchanges[-1:]]} | "
                f"ECU alive: {ecu_alive()}"
            ),
            recovery=recovery,
        )

        self.assertTrue(payloads, "合法序号序列必须得到响应")
        self.assertEqual(
            exchanges[-1][0],
            self.PAYLOAD,
            "ECU 必须收到完整重组的 22 字节请求",
        )


class TestTransportErrorContainmentEilseq(SequentialSnBase):
    """
    P3R 最小回归实验 A：错误 CF 序号（EILSEQ / Errno 84）。

    修复前：内核序号不匹配 → ``OSError EILSEQ`` → 未捕获 → ECU 进程退出。
    修复后期望：错误被接住、当前传输被丢弃、ECU 存活、后续正常请求可恢复。

    两个 case **分开写**（跳号 / 重复）：它们是两条独立输入，
    合成一个用例会掩盖其中一条的回归。
    """

    def _containment_case(self, frames: list, case: str, expected_errno: int) -> None:
        clear_ecu_log()

        mark = self.harness.mark()
        self.harness.send_sequence(frames, gap=FRAME_GAP)
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)

        # EILSEQ 由内核在 recv() 上报，属于即时错误；留一点余量但不必等 N_Bs
        self.harness.wait(0.5)

        alive = ecu_alive()
        container_lines = ecu_container_errors()
        signature = self.ecu_crash_signature() if not alive else ""

        # 恢复检查：必须能继续正常服务
        ok, recovery = self.recovery_check()

        self.emit(
            case,
            self.harness.injected[-len(frames):],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU alive: {alive} | "
                f"containment log: {[l.strip() for l in container_lines]}"
            ),
            recovery=recovery,
        )

        # ---- 期望行为（修复后必须成立）----
        self.assertTrue(
            alive,
            f"[{case}] ECU 进程被 transport error 杀死（回归）：{signature}",
        )
        self.assertFalse(
            payloads,
            f"[{case}] transport 层错误不应产生 UDS 响应，实际 {[p.hex(' ') for p in payloads]}",
        )
        self.assertTrue(
            any(f"errno={expected_errno}" in line for line in container_lines),
            f"[{case}] 未看到 errno={expected_errno} 的 containment 日志：{container_lines}",
        )
        self.assertTrue(ok, f"[{case}] 之后 ECU 无法恢复正常通信：{recovery}")

    def test_wrong_cf_sn_does_not_kill_ecu(self) -> None:
        """最小回归 A-1：FF + CF1 + CF3（缺 SN=2）不应杀死 ECU（EILSEQ）。"""
        self._containment_case(
            self._frames(3, note="CF SN=3 (skips SN=2)"),
            "P3R-EILSEQ CF SN skip 1->3",
            expected_errno=84,
        )

    def test_repeated_cf_sn_does_not_kill_ecu(self) -> None:
        """最小回归 A-2：FF + CF1 + CF1（重复 SN）不应杀死 ECU（EILSEQ）。"""
        self._containment_case(
            self._frames(1, note="CF SN=1 repeated"),
            "P3R-EILSEQ CF SN repeat 1,1",
            expected_errno=84,
        )


class TestSnSkip(SequentialSnBase):
    """Case 6a 隔离：SN 从 1 跳到 3（缺失 SN=2）。

    P3 实测该输入会让内核抛出 ``OSError: [Errno 84]``（EILSEQ）；
    P3R 修复后错误被 transport 层 containment 接住，ECU 必须存活。
    """

    def test_sn_skip_1_to_3(self) -> None:
        frames = self._frames(3, note="CF SN=3 (skips SN=2)")

        self._run_sn_case(frames, "SN-SKIP FF + CF1 + CF3 (SN 2 missing)")


class TestSnRepeat(SequentialSnBase):
    """Case 6b 隔离：SN 重复（CF1 + CF1）。P3R 后 ECU 必须存活。"""

    def test_sn_repeat_1_1(self) -> None:
        frames = self._frames(1, note="CF SN=1 repeated")

        self._run_sn_case(frames, "SN-REPEAT FF + CF1 + CF1")


class TestSnBackward(SequentialSnBase):
    """Case 6c 隔离：SN 回退（CF1 + CF0）。P3R 后 ECU 必须存活。"""

    def test_sn_backward_1_0(self) -> None:
        frames = self._frames(0, note="CF SN=0 (backward)")

        self._run_sn_case(frames, "SN-BACKWARD FF + CF1 + CF0")


class TestSnLargeJump(SequentialSnBase):
    """Case 6d 隔离：SN 跳到 0xF。P3R 后 ECU 必须存活。"""

    def test_sn_jump_to_f(self) -> None:
        frames = self._frames(0xF, note="CF SN=0xF (large jump)")

        self._run_sn_case(frames, "SN-JUMP FF + CF1 + CF15")


# ======================================================================
# Layer 3 — 流控异常
# ======================================================================


class TestUnsolicitedFlowControl(MalformedCaseTest):
    """Case 7：没有对应 outbound 传输时的 FC。"""

    def _case(self, frame: RawFrame, case: str) -> None:
        mark = self.harness.mark()
        self.harness.send(frame)
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)

        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-1:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU alive: {alive}"
            ),
            recovery=recovery,
        )

        self.assertFalse(
            payloads, f"[{case}] 无对应传输的 FC 不应产生响应"
        )
        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")

    def test_unsolicited_fc_cts(self) -> None:
        """ECU 并未在发送多帧，却收到 CTS。"""
        self._case(fc(ECU_RX_ID, 0, 0, 0, note="unsolicited FC CTS"), "FC-UNSOLICITED CTS 30 00 00")

    def test_unsolicited_fc_wait(self) -> None:
        """无对应传输时的 WAIT。"""
        self._case(fc(ECU_RX_ID, 1, 0, 0, note="unsolicited FC WAIT"), "FC-UNSOLICITED WAIT 31 00 00")

    def test_unsolicited_fc_overflow(self) -> None:
        """无对应传输时的 OVERFLOW。"""
        self._case(
            fc(ECU_RX_ID, 2, 0, 0, note="unsolicited FC OVERFLOW"),
            "FC-UNSOLICITED OVERFLOW 32 00 00",
        )


class FlowControlDuringActiveSend(MalformedCaseTest):
    """
    Case 8 公共实现：ECU 正在发送多帧响应时注入异常 FC。

    时序::

        1. 合法单帧请求 22 F1 90（触发 ECU 的 20 字节多帧响应）
        2. ECU 回 FF
        3. 我们回一个正常的 CTS（否则 ECU 的传输会一直等 FC 并最终被杀）
        4. 注入实验用的异常 FC
        5. 观察 ECU 的发送是否被打断、进程是否存活

    每个注入值单独一个测试类：某个输入杀死 ECU 后，其余输入仍能拿到
    干净的进程（P3 已实测：这类注入会杀死 ECU，因此隔离是必须的）。
    """

    def _inject_case(
        self,
        abnormal_fc: RawFrame,
        case: str,
        expect_crash: bool = False,
        expect_complete: bool = True,
    ) -> None:
        info = self.inject_during_active_send(abnormal_fc, note=case)

        _, _, complete = self.report_active_send(case, info)

        alive = ecu_alive()
        signature = "" if alive else self.ecu_crash_signature()

        if expect_crash:
            # 必须真的观察到进程死亡，否则结论要重新评估
            self.assertFalse(
                alive,
                f"[{case}] 期望该输入杀死 ECU，但进程仍然存活——结论需重新评估",
            )
            self.assertIn(
                "OSError", signature,
                f"[{case}] 期望内核 OSError，实际签名 {signature!r}",
            )
            _, recovery = self.assert_recovered_or_documented_crash(case)

            self.emit_note(f"[{case}] 已记录崩溃：{signature}")

            return

        self.assert_ecu_alive(case)

        if expect_complete:
            self.assertTrue(
                complete, f"[{case}] 期望完整 20 字节响应，实际未完成"
            )

        ok, recovery = self.recovery_check()
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")


class TestFcAbnormalStatusEach(FlowControlDuringActiveSend):
    """Case 8 隔离组：异常 FC 状态位。

    实测结论（重要，与最初假设不同）：

        正常 CTS + 额外注入一帧 WAIT / OVERFLOW
        → ECU **存活**，20 字节响应依然完整，恢复成功

    原因是内核只是把多出来的 FC 当作重复的流控帧，不影响已建立的传输。
    因此"注入 FC 状态位"本身并不致命——真正致命的构造见
    :class:`TestFcParamsEach` 的 ``test_params_without_normal_cts``。
    """

    def test_ctrl_normal_cts_only(self) -> None:
        """
        对照组：只回一个**正常的** CTS（30 00 00），不注入异常。

        目的：把"回 FC 这个必要动作"与"异常 FC"分开。
        """
        info = self.inject_during_active_send(
            abnormal_fc=None,
            note="control: normal CTS only",
            respond_to_ff=True,
        )

        _, _, complete = self.report_active_send(
            "CTRL-CTS-ONLY single normal CTS 30 00 00", info
        )

        self.assert_ecu_alive("ctrl-cts-only")
        self.assertTrue(complete, "只回正常 CTS 时 20 字节响应必须完整")

        ok, recovery = self.recovery_check()
        self.assertTrue(ok, f"对照组之后 ECU 应可正常通信：{recovery}")

    def test_wait_status(self) -> None:
        """WAIT（31 00 00）作为额外 FC 注入。实测：ECU 存活、响应完整。"""
        self._inject_case(
            fc(ECU_RX_ID, 1, 0, 0, note="FC WAIT"), "FC-WAIT 31 00 00"
        )

    def test_overflow_status(self) -> None:
        """OVERFLOW（32 00 00）作为额外 FC 注入。实测：ECU 存活、响应完整。"""
        self._inject_case(
            fc(ECU_RX_ID, 2, 0, 0, note="FC OVERFLOW"), "FC-OVERFLOW 32 00 00"
        )


class FcParamCase(FlowControlDuringActiveSend):
    """
    Case 9 的公共基类：每个参数取值单独一个子类。

    为什么要拆到类级：实测某些构造会杀死 ECU 进程，若多个取值共用一个
    进程，第一个崩溃会让后面所有取值失去干净的起点（P2 已确立的隔离原则）。
    """

    #: 子类覆盖：(BS, STmin)
    BLOCK_SIZE = 0
    STMIN = 0
    LABEL = ""

    def test_extra_param_fc_while_sending(self) -> None:
        """正常 CTS + 额外参数 FC：实测 ECU 存活、响应完整。"""
        self._inject_case(
            fc(
                ECU_RX_ID,
                0,
                self.BLOCK_SIZE,
                self.STMIN,
                note=f"FC BS={self.BLOCK_SIZE} STmin=0x{self.STMIN:02X}",
            ),
            self.LABEL,
        )


class TestFcParamBs1(FcParamCase):
    """FC BS=1（合法但要求分块确认）。"""

    BLOCK_SIZE = 1
    LABEL = "FC-PARAMS BS=1 STmin=0"


class TestFcParamBs255(FcParamCase):
    """FC BS=0xFF（大块大小）。"""

    BLOCK_SIZE = 0xFF
    LABEL = "FC-PARAMS BS=255 STmin=0"


class TestFcParamStmin100us(FcParamCase):
    """FC STmin=0xF1 —— 这是**合法**的 100us 档位，不是保留值。"""

    STMIN = 0xF1
    LABEL = "FC-PARAMS STmin=0xF1 (100us, LEGAL)"


class TestFcParamStmin0x80(FcParamCase):
    """FC STmin=0x80 —— **保留值**（标准要求回退到 127 ms）。"""

    STMIN = 0x80
    LABEL = "FC-PARAMS STmin=0x80 (RESERVED)"


class TestFcParamStmin0xF0(FcParamCase):
    """FC STmin=0xF0 —— 保留值（0xF1–0xF9 才是微秒档）。"""

    STMIN = 0xF0
    LABEL = "FC-PARAMS STmin=0xF0 (RESERVED)"


class TestFcParamStmin127ms(FcParamCase):
    """FC STmin=0x7F —— 合法最大值（127 ms）。"""

    STMIN = 0x7F
    LABEL = "FC-PARAMS STmin=0x7F (127ms, LEGAL max)"


class TestFcParamOnlyCts(FcParamCase):
    """
    P3R 最小回归实验 B：发送侧 FC 超时（ECOMM / Errno 70）。

    构造（P3 已固化的最小复现）::

        1. 注入 SF 03 22 f1 90        → 触发 ECU 的多帧响应
        2. ECU 回 FF 10 14 ...
        3. 只注入一个 FC 30 01 80      → 不再回后续 FC
        4. 约 1.2s 后内核报 FC 接收超时

    修复前：``OSError ECOMM`` 未捕获 → ECU 进程退出。
    修复后期望：错误被接住、本次响应发送被丢弃、ECU 存活、后续请求可恢复。

    本用例在 P3 阶段以 ``@unittest.expectedFailure`` 记录缺陷；
    P3R 修复后转为**普通通过用例**，以免未来回归被 expectedFailure 掩盖。
    """

    BLOCK_SIZE = 1
    STMIN = 0x80
    LABEL = "P3R-ECOMM FC timeout (BS=1, STmin=0x80, single CTS only)"

    def test_fc_timeout_does_not_kill_ecu(self) -> None:
        clear_ecu_log()

        info = self.inject_during_active_send(
            abnormal_fc=None,
            note="parameter-only CTS (no normal CTS first)",
            respond_to_ff=False,
        )

        # respond_to_ff=False 时 helper 不发 FC，这里补一个带参数的 CTS
        self.harness.send(
            fc(
                ECU_RX_ID,
                0,
                self.BLOCK_SIZE,
                self.STMIN,
                note=f"single CTS BS={self.BLOCK_SIZE} STmin=0x{self.STMIN:02X} (reserved)",
            )
        )

        self.harness.wait(MULTIFRAME_WAIT)

        # ECOMM 出现在内核 N_Bs 超时之后（实测约 1.2s），必须显式等待
        self.wait_for_ecu_death(timeout=2.5)

        captured = self.harness.since(info["mark"])
        payloads = self.harness.payloads_from(info["mark"], ECU_TX_ID)
        complete = [p for p in payloads if len(p) >= 20]

        alive = ecu_alive()
        container_lines = ecu_container_errors()
        signature = self.ecu_crash_signature() if not alive else ""

        ok, recovery = self.recovery_check()

        self.emit(
            f"{self.LABEL} (MINIMAL REPRO)",
            self.harness.injected[-2:],
            captured,
            result=(
                f"complete 20B payload: {[p.hex(' ') for p in complete]} | "
                f"all payloads: {[p.hex(' ') for p in payloads]} | "
                f"ECU alive: {alive} | "
                f"containment log: {[l.strip() for l in container_lines]}"
            ),
            recovery=recovery,
        )

        # ---- 期望行为（修复后必须成立）----
        self.assertTrue(
            alive,
            f"[{self.LABEL}] ECU 进程被 transport error 杀死（回归）：{signature}",
        )
        self.assertTrue(
            any("errno=70" in line for line in container_lines),
            f"[{self.LABEL}] 未看到 errno=70 的 containment 日志：{container_lines}",
        )
        # 注意：**不能**要求"完全没有 payload"。传输被中断时对端会在总线上
        # 看到半截 FF（如 '62 f1 90 54 45 53'），那是传输失败的表现，
        # 不是伪造的 UDS 响应。要求的是"没有完整交付"。
        self.assertFalse(
            complete,
            f"[{self.LABEL}] FC 超时导致发送被丢弃，不应完整交付 20 字节响应："
            f"{[p.hex(' ') for p in complete]}",
        )
        self.assertTrue(ok, f"[{self.LABEL}] 之后 ECU 无法恢复正常通信：{recovery}")


class TestFlowControlNormalBaseline(MalformedCaseTest):
    """Case 8 对照组：不注入任何额外 FC 时，多帧响应必须完整。"""

    def test_normal_multiframe_response(self) -> None:
        info = self.inject_during_active_send(None, note="no injection (baseline)")

        ok, recovery, complete = self.report_active_send(
            "BASELINE-MULTIFRAME no FC injection", info
        )

        self.assert_ecu_alive("baseline-multiframe")
        self.assertTrue(complete, "不注入异常时 20 字节响应必须完整")
        self.assertTrue(ok, f"基线之后 ECU 应可正常通信：{recovery}")


# ======================================================================
# Layer 4 — Padding / DLC 与恢复
# ======================================================================


class TestPaddingAndDlc(MalformedCaseTest):
    """
    Case 10：DLC 与逻辑 payload 不一致 / 额外 padding。

    这一组必须和 P2 的 TX_PAD / RX_PAD 结论联系起来：
    P2 已证明内核按 SF 的长度字段（或 FF 的总长度）截断数据，
    因此"多出来的字节"预期不进入 UDS。
    """

    def _case(self, frame: RawFrame, case: str) -> tuple:
        mark = self.harness.mark()
        self.harness.send(frame)
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        exchanges = ecu_uds_exchanges()

        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-1:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU-side UDS (last): "
                f"{[(r.hex(' '), (s.hex(' ') if s else None)) for r, s in exchanges[-1:]]} | "
                f"ECU alive: {alive}"
            ),
            recovery=recovery,
        )

        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")

        return payloads, exchanges

    def test_sf_with_extra_padding_bytes(self) -> None:
        """
        声明 2 字节，DLC=8，后面 5 字节是 padding（0x00）。

        预期：按长度截断 → UDS 收到 ``10 01`` → 正常 ``50 01`` 响应。
        这与 P2 的 RX_PAD 结论一致（填充不影响重组结果）。
        """
        frame = raw(
            ECU_RX_ID,
            bytes([0x02, 0x10, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00]),
            note="SF declares 2, DLC=8 with 5 pad bytes",
        )
        case = "PADDING SF declares 2 with 5 padding bytes"

        payloads, _ = self._case(frame, case)

        self.assertTrue(payloads, "带 padding 的合法 SF 应正常响应")
        self.assertEqual(payloads[0][:2], RECOVERY_EXPECTED)

    def test_sf_with_nonzero_padding(self) -> None:
        """padding 字节为 0xAA（P2 用过同一个值），仍应按长度截断。"""
        frame = raw(
            ECU_RX_ID,
            bytes([0x02, 0x10, 0x01, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA]),
            note="SF declares 2, DLC=8, pad=0xAA",
        )
        case = "PADDING SF declares 2 with 0xAA padding"

        payloads, _ = self._case(frame, case)

        self.assertTrue(payloads, "0xAA 填充不应影响单帧语义")
        self.assertEqual(payloads[0][:2], RECOVERY_EXPECTED)

    def test_sf_dlc_smaller_than_declared(self) -> None:
        """
        声明 7 字节，但 DLC 只有 3。

        预期：内核按实际收到的字节交付（或补齐），UDS 看到长度不符 → NRC。
        具体 NRC 以实测为准，本 case 只断言 ECU 存活与可恢复。
        """
        frame = raw(
            ECU_RX_ID, bytes([0x07, 0x22, 0xF1]), note="SF declares 7, DLC=3"
        )
        case = "DLC SF declares 7 bytes but DLC=3"

        self._case(frame, case)

    def test_multi_frame_with_padded_cf(self) -> None:
        """
        多帧请求中，CF 带额外 padding（DLC=8 但只有 2 字节有效数据）。

        与 P2 的结论呼应：CF 的 payload 是 DLC-1，多余字节属于填充，
        内核按 FF 总长度截断。
        """
        payload = bytes([0x22, 0xF1, 0x90, 0xF1, 0x80, 0xF1, 0x87, 0xF1])

        ff_frame = ff(ECU_RX_ID, len(payload), payload[:6], note="legal FF")
        cf_frame = raw(
            ECU_RX_ID,
            bytes([0x21]) + payload[6:] + bytes([0xAA] * 5),
            note="CF SN=1 with 5 padding bytes",
        )

        case = "PADDING multi-frame CF padded to DLC=8"

        mark = self.harness.mark()
        self.harness.send_sequence([ff_frame, cf_frame])
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        exchanges = ecu_uds_exchanges()

        ok, recovery = self.recovery_check()

        self.emit(
            case,
            self.harness.injected[-2:],
            captured,
            result=(
                f"ISO-TP response: {'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU-side UDS (last): "
                f"{[(r.hex(' '), (s.hex(' ') if s else None)) for r, s in exchanges[-1:]]} | "
                f"ECU alive: {ecu_alive()}"
            ),
            recovery=recovery,
        )

        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")


class TestCanAddressing(MalformedCaseTest):
    """
    CAN addressing 实验（**刻意与 ISO-TP malformed 分开**）。

    本组改的是 **CAN 仲裁 ID**，不是 ISO-TP 分帧内容，因此结论不能和
    "ISO-TP 层丢弃"混为一谈：帧会在内核按 ID 过滤阶段就被决定去留。

    被测 ECU 只绑定 ``0x7E0``（见 ecu.py 的 UDS_RX_ID），因此：

    - ``0x7E1``：物理寻址的相邻地址，ECU 不应接收
    - ``0x7DF``：OBD-II 功能寻址广播地址，若实现支持应响应；
      本 ECU **未绑定**功能寻址，因此预期无响应
    - ``0x000``：与诊断无关的 ID，应当完全无影响
    """

    def _addressing_case(self, arbitration_id: int, case: str) -> None:
        mark = self.harness.mark()
        self.harness.send(
            sf(arbitration_id, RECOVERY_REQUEST, note=f"10 01 sent to 0x{arbitration_id:03X}")
        )
        self.harness.wait(RESPONSE_WAIT)

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)

        ok, recovery = self.recovery_check()
        alive = ecu_alive()

        self.emit(
            case,
            self.harness.injected[-1:],
            captured,
            result=(
                f"ISO-TP response on 0x{ECU_TX_ID:03X}: "
                f"{'none' if not payloads else [p.hex(' ') for p in payloads]} | "
                f"ECU alive: {alive}"
            ),
            recovery=recovery,
        )

        self.assertFalse(
            payloads,
            f"[{case}] 该 ID 不是 ECU 绑定地址，不应产生诊断响应",
        )
        self.assert_ecu_alive(case)
        self.assertTrue(ok, f"[{case}] 之后 ECU 失去正常通信能力：{recovery}")

    def test_physical_adjacent_id_7e1(self) -> None:
        """0x7E1：物理寻址相邻地址。实测：无响应，ECU 存活。"""
        self._addressing_case(0x7E1, "CAN-ADDR 0x7E1 (adjacent physical)")

    def test_functional_id_7df(self) -> None:
        """0x7DF：功能寻址广播。实测：本 ECU 未绑定，无响应，ECU 存活。"""
        self._addressing_case(FUNCTIONAL_ID, "CAN-ADDR 0x7DF (functional broadcast)")

    def test_unrelated_id_000(self) -> None:
        """0x000：与诊断无关的 ID。实测：无响应，ECU 存活。"""
        self._addressing_case(0x000, "CAN-ADDR 0x000 (unrelated)")

    def test_correct_id_still_works(self) -> None:
        """对照组：0x7E0 必须正常响应，证明本组不是"链路坏了"。"""
        mark = self.harness.mark()
        self.harness.send(sf(ECU_RX_ID, RECOVERY_REQUEST, note="10 01 to 0x7E0"))

        self.harness.wait(RESPONSE_WAIT)

        payloads = self.harness.payloads_from(mark, ECU_TX_ID)

        self.emit(
            "CAN-ADDR 0x7E0 (control, correct address)",
            self.harness.injected[-1:],
            self.collect(mark),
            result=f"ISO-TP response: {[p.hex(' ') for p in payloads]}",
        )

        self.assertTrue(payloads, "0x7E0 必须正常响应")
        self.assertEqual(payloads[0][:2], RECOVERY_EXPECTED)


class TestFcOverflowContainment(MalformedCaseTest):
    """
    P3R2 最小回归：发送侧 FC 溢出（EMSGSIZE / Errno 90）。

    构造::

        1. 注入 SF 03 22 f1 90            → 触发 ECU 的 20 字节多帧响应
        2. ECU 回 FF
        3. 注入 FC OVERFLOW: 32 00 00     → 内核 isotp_rcv_fc() 置 sk_err = EMSGSIZE
        4. 观察 ECU 是否存活、后续能否恢复

    修复前：``OSError [Errno 90]`` 不在 ``_is_recoverable_isotp_error()``
    白名单内 → 未捕获 → ECU 进程退出。

    实测细节（P3R2）：内核在 ``isotp_rcv_fc()`` 中设置该错误，但它是在
    **后续 ``recv()``** 上被取走的（stdout 时序显示 ``TX UDS:`` 已打印、
    ``send()`` 正常返回）。因此本用例**不断言**传输失败：在部分时序下
    ECU 的 20 字节响应会在错误浮现前已经完整送达。

    污染控制（P3K 结论：``sk_err`` 是共享状态）：先做一次完整交换把可能
    挂起的旧 ``sk_err`` 消费掉，再清空日志，确保观察到的 errno=90
    来自本次注入而不是上一个 case 的残留。
    """

    #: 触发 20 字节多帧响应
    REQUEST_20B = bytes([0x22, 0xF1, 0x90])

    #: 内核 ISOTP_FC_TIMEOUT = 1s，留足余量
    ERROR_WAIT = 2.5

    def test_fc_overflow_does_not_kill_ecu(self) -> None:
        # ---- 1. 污染控制：消费掉可能挂起的旧 sk_err ----
        for _ in range(3):
            self.recovery_check()
            self.harness.wait(0.2)

        clear_ecu_log()

        # ---- 2. 发送请求，等待 ECU 的 FF ----
        mark = self.harness.mark()
        self.harness.send(sf(ECU_RX_ID, self.REQUEST_20B, note="legal SF 22 F1 90"))

        deadline = time.monotonic() + 1.0
        saw_ff = False

        while time.monotonic() < deadline:
            if any(
                d and (d[0] & 0xF0) == PCI_FIRST
                for d in self.harness.data_from(mark, ECU_TX_ID)
            ):
                saw_ff = True
                break

            self.harness.wait(0.02)

        # ---- 3. 注入 FC OVERFLOW (0x32) ----
        self.harness.send(
            fc(ECU_RX_ID, 2, 0, 0, note="FC OVERFLOW FS=0x2")
        )

        # ---- 4. 等待 transport error ----
        container_lines = []
        error_deadline = time.monotonic() + self.ERROR_WAIT

        while time.monotonic() < error_deadline:
            self.harness.wait(0.05)
            container_lines = ecu_container_errors()

            if container_lines:
                break

        captured = self.collect(mark)
        payloads = self.harness.payloads_from(mark, ECU_TX_ID)
        alive = ecu_alive()
        signature = self.ecu_crash_signature() if not alive else ""

        # ---- 5. 恢复检查 ----
        ok, recovery = self.recovery_check()

        self.emit(
            "P3R2-EMSGSIZE FC OVERFLOW 32 00 00 (MINIMAL REPRO)",
            self.harness.injected[-2:],
            captured,
            result=(
                f"saw ECU FF: {saw_ff} | "
                f"payloads: {[p.hex(' ') for p in payloads]} | "
                f"ECU alive: {alive} | "
                f"containment log: {[l.strip() for l in container_lines]}"
            ),
            recovery=recovery,
        )

        # ---- 期望行为 ----
        self.assertTrue(saw_ff, "未观察到 ECU 的 FF，本用例前提不成立")
        self.assertTrue(
            alive,
            f"[P3R2-EMSGSIZE] ECU 进程被 transport error 杀死（回归）：{signature}",
        )
        self.assertTrue(
            any("errno=90" in line for line in container_lines),
            f"[P3R2-EMSGSIZE] 未看到 errno=90 的 containment 日志，"
            f"无法证明真的触发了 EMSGSIZE：{container_lines}",
        )
        self.assertTrue(
            ok, f"[P3R2-EMSGSIZE] 之后 ECU 无法恢复正常通信：{recovery}"
        )
        self.assertEqual(
            recovery.split("-> ")[-1].strip(),
            "50 01 00 32 00 64",
            f"[P3R2-EMSGSIZE] 恢复响应不符：{recovery}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
