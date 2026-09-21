#!/usr/bin/env python3
"""
UDS Basic —— 显式 ISO-TP 传输参数实验（Phase 4-P2）

本测试验证 Phase 4-P2 引入的显式传输参数**真的作用在 vcan0 上**，
且**默认行为与改造前逐字节一致**。

链路：

    测试方 (python-isotp / CAN_ISOTP)
     → SocketCAN vcan0
     → 虚拟 ECU 子进程 (ecu/ecu.py，单帧/多帧响应)

用例矩阵：

    A 默认            不设置任何 ISO-TP 参数 → 单帧仍是 03 10 03，不填充
    B --tx-pad        填充字节生效（DLC 8，尾部为填充值）
    C --rx-pad        本端容忍填充后仍能正常收发多帧
    D --fc-blocksize  本端 FC 的 BS 生效（收到 FC(BS=1)）
    E --fc-stmin      本端 FC 的 STmin 生效（ECU 实测 CF 间隔 ≈ 20 ms）
    F --tx-stmin      FORCE_TXSTMIN 生效（覆盖收到的 FC STmin）
    G --timeout       短超时下 recv 干净返回 None，不挂死

实现说明（重要）：

- 每个测试类用**独立子进程**启动 ECU，通过环境变量注入参数，
  因为 ISO-TP 选项必须在 bind() 之前下发，且是进程级状态。
- 断言只看**方向、帧类型、填充字节、时间量级**，不比对精确时间戳
  （vcan0 在虚拟机里，精确值不可靠）。
- 只使用 vcan0（虚拟 CAN），不接触真实车辆 / 真实 ECU。

前置条件：

- vcan0 已存在并 UP
- 使用网络实验虚拟环境：
  /home/lilike/.venvs/network/bin/python

运行：

    /home/lilike/.venvs/network/bin/python tests/test_isotp_options_vcan.py
"""

import os
import signal
import subprocess
import sys
import threading
import time
import unittest

import can
import isotp

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAB_DIR = os.path.dirname(_HERE)
_ECU_DIR = os.path.join(_LAB_DIR, "ecu")
_ECU_SCRIPT = os.path.join(_ECU_DIR, "ecu.py")

INTERFACE = "vcan0"

#: Tester -> ECU
TX_ID = 0x7E0

#: ECU -> Tester
RX_ID = 0x7E8

ECU_STARTUP_TIMEOUT = 10.0
RECV_TIMEOUT = 1.5
#: 抓包后等待在途帧到达的时间
CAPTURE_SETTLE = 0.3

DID_F190 = 0xF190

#: 触发多帧响应的请求（35 字节响应 = FF + 4 CF + 1 CF）
MULTI_DID_REQUEST = bytes([0x22, 0xF1, 0x90, 0xF1, 0x80])

#: 单帧请求
SINGLE_REQUEST = bytes([0x22, 0xF1, 0x90])

#: 0xF190 响应长度：62 + F1 90 + 17 字节 VIN = 20
SINGLE_RESPONSE_LEN = 20

#: 0xF190 + 0xF180 响应长度：62 + F1 90 + 17 + F1 80 + 13 = 35
MULTI_RESPONSE_LEN = 35

#: 单帧请求 10 01 的肯定响应（50 01 + P2/P2* 参数），长度 6
SESSION_RESPONSE = bytes([0x50, 0x01, 0x00, 0x32, 0x00, 0x64])

#: 填充测试使用的填充字节
TX_PAD_BYTE = 0xAA

#: D/E/F 用的分离时间：取 20 ms，既明显大于调度抖动，又不拖慢测试
STMIN_BYTE_20MS = 0x14  # 20 decimal = 20 ms

#: 允许的时间下界折扣（vcan0 在虚拟机中，留足余量，只验证量级）
STMIN_LOWER_TOLERANCE = 0.5

#: ISO-TP PCI 帧类型判断（按首字节高 4 bit）
PCI_SINGLE = 0x00
PCI_FIRST = 0x10
PCI_CONSECUTIVE = 0x20
PCI_FLOW_CONTROL = 0x30


def frame_type(data: bytes) -> str:
    """按 PCI 判断 ISO-TP 帧类型。"""
    if not data:
        return "empty"

    pci = data[0] & 0xF0

    return {
        PCI_SINGLE: "single",
        PCI_FIRST: "first",
        PCI_CONSECUTIVE: "consecutive",
        PCI_FLOW_CONTROL: "flow_control",
    }.get(pci, f"unknown(0x{data[0]:02X})")


class FrameCollector:
    """用 python-can 在 vcan0 上抓帧（旁路观察，不参与 ISO-TP 状态机）。"""

    def __init__(self):
        self.bus = can.interface.Bus(interface="socketcan", channel=INTERFACE)
        self.bus.socket.settimeout(0.05)
        self.messages = []
        self._lock = threading.Lock()

        collector = self

        class _Listener(can.Listener):
            def on_message_received(self, msg) -> None:  # noqa: D102
                with collector._lock:
                    collector.messages.append(
                        (msg.timestamp, msg.arbitration_id, bytes(msg.data))
                    )

        self._notifier = can.Notifier(self.bus, [_Listener()])

    def stop(self) -> None:
        self._notifier.stop()
        self.bus.shutdown()

    def by_id(self, arbitration_id: int) -> list:
        """返回指定 ID 的帧列表 [(timestamp, data), ...]，按时间顺序。"""
        with self._lock:
            snapshot = list(self.messages)

        return [(ts, data) for ts, cid, data in snapshot if cid == arbitration_id]

    def data_by_id(self, arbitration_id: int) -> list:
        return [data for _, data in self.by_id(arbitration_id)]

    def types_by_id(self, arbitration_id: int) -> list:
        return [frame_type(data) for data in self.data_by_id(arbitration_id)]


# ----------------------------------------------------------------------
# ECU 子进程管理（每个测试类一个独立进程）
# ----------------------------------------------------------------------

_ECU_LOCK = threading.Lock()
_ECU_PROCESS = None
_ECU_OUTPUT_CHUNKS: list = []
_ECU_OUTPUT_READER = None


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

    _ECU_PROCESS = None


def _start_ecu(env_overrides: dict = None) -> None:
    """
    启动一个注入了指定 ISO-TP 参数的 ECU 子进程。

    ECU 的参数非法时会在导入期直接退出，因此调用方还需
    :func:`wait_for_ready` 判断它是否真的起来了。

    ECU 的 stdout/stderr 通过管道收集到内存（不用文件，
    以免在只读沙箱 / 受限环境下写盘失败）。
    """
    global _ECU_PROCESS, _ECU_OUTPUT_CHUNKS, _ECU_OUTPUT_READER

    with _ECU_LOCK:
        _stop_ecu()

        child_env = os.environ.copy()
        # 缩短锁定窗口，避免与安全访问测试互相干扰
        child_env["UDS_LOCKOUT_SECONDS"] = "2"

        if env_overrides:
            child_env.update(env_overrides)

        _ECU_OUTPUT_CHUNKS = []

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
                _ECU_OUTPUT_CHUNKS.append(line.decode("utf-8", errors="replace"))

        _ECU_OUTPUT_READER = threading.Thread(
            target=_reader, args=(_ECU_PROCESS.stdout,), daemon=True
        )
        _ECU_OUTPUT_READER.start()

    try:
        wait_for_ready()
    except AssertionError as exc:
        raise AssertionError(f"{exc}\nECU 输出：\n{read_ecu_output()}") from None


def read_ecu_output() -> str:
    return "".join(_ECU_OUTPUT_CHUNKS)


def ecu_address() -> isotp.Address:
    """
    测试方（Tester）的 ISO-TP 地址。

    注意 ``isotp.Address`` 的**位置参数顺序是 (txid, rxid)**，
    与直觉相反，所以这里一律用关键字参数，避免再次搞反：

        txid = Tester 发送 = 0x7E0
        rxid = Tester 接收 = 0x7E8
    """
    return isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        txid=TX_ID,
        rxid=RX_ID,
    )


def wait_for_ready() -> None:
    """反复发送 10 01，直到收到 50 01。"""
    deadline = time.monotonic() + ECU_STARTUP_TIMEOUT
    last_error = None

    while time.monotonic() < deadline:
        sock = None
        try:
            sock = isotp.socket()
            sock.bind(INTERFACE, ecu_address())
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
        f"虚拟 ECU 在 {ECU_STARTUP_TIMEOUT}s 内没有就绪（最后一个错误：{last_error!r}）"
    )


def new_socket(**fc_options) -> isotp.socket:
    """创建测试方 ISO-TP socket（可在 bind 前注入 FC 选项）。"""
    sock = isotp.socket()

    if fc_options:
        sock.set_fc_opts(**fc_options)

    sock.bind(INTERFACE, ecu_address())
    sock.settimeout(RECV_TIMEOUT)

    return sock


def exchange(sock, request: bytes, timeout: float = RECV_TIMEOUT):
    """发送请求并返回响应（超时返回 None）；跳过自身回显。"""
    sock.send(request)

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        frame = sock.recv()

        if frame is None:
            return None

        if frame == request:
            # vcan0 回环：跳过自己发出的请求
            continue

        return frame

    return None


def drain(sock, quiet: float = 0.05) -> int:
    """读空 socket 缓冲区，避免残留帧污染下一次交换。"""
    drained = 0
    sock.settimeout(quiet)

    while True:
        try:
            frame = sock.recv()
        except Exception:  # noqa: BLE001 - 超时即视为读空
            break

        if frame is None:
            break

        drained += 1

    return drained


def gaps_seconds(timestamps: list) -> list:
    """相邻时间戳差值（秒）。"""
    return [b - a for a, b in zip(timestamps, timestamps[1:])]


class IsotpOptionTestCase(unittest.TestCase):
    """基类：按需用环境变量启动注入了 ISO-TP 参数的 ECU。"""

    #: 子类覆盖：注入给 ECU 的 ISO-TP 环境变量
    ECU_ENV = {}

    @classmethod
    def setUpClass(cls) -> None:
        _start_ecu(cls.ECU_ENV)

    @classmethod
    def tearDownClass(cls) -> None:
        _stop_ecu()

    def setUp(self) -> None:
        self._sockets = []
        self._collectors = []

        # 清掉启动探测/上一个用例留下的在途帧
        sock = self._new_socket()
        drain(sock, quiet=0.1)
        sock.close()

    def tearDown(self) -> None:
        for collector in self._collectors:
            try:
                collector.stop()
            except Exception:  # noqa: BLE001 - 清理阶段忽略异常
                pass

        for sock in self._sockets:
            try:
                sock.close()
            except Exception:  # noqa: BLE001 - 清理阶段忽略异常
                pass

    def _new_socket(self, **fc_options) -> isotp.socket:
        sock = new_socket(**fc_options)
        self._sockets.append(sock)
        return sock

    def _new_collector(self) -> FrameCollector:
        collector = FrameCollector()
        self._collectors.append(collector)
        return collector

    def ecu_pid(self):
        return _ECU_PROCESS.pid if _ECU_PROCESS is not None else None


# ----------------------------------------------------------------------
# A. 默认行为不变
# ----------------------------------------------------------------------


class TestDefaultTransportUnchanged(IsotpOptionTestCase):
    """A. 不设置任何 ISO-TP 参数：行为必须与改造前逐字节一致。"""

    ECU_ENV = {}

    def test_short_request_is_unpadded_three_byte_single_frame(self) -> None:
        """
        10 01 请求 → 单帧，DLC=3，内容 02 10 01。

        关键回归点：**绝不能**填充到 8 字节（例如 02 10 01 00 00 00 00 00）。
        """
        collector = self._new_collector()
        sock = self._new_socket()

        try:
            frame = exchange(sock, bytes([0x10, 0x01]))
            time.sleep(CAPTURE_SETTLE)
        finally:
            collector.stop()

        self.assertIsNotNone(frame)
        # 10 01 的肯定响应是 6 字节：50 01 + P2/P2* 参数
        self.assertEqual(frame, SESSION_RESPONSE)

        requests = [d for d in collector.data_by_id(TX_ID) if d[:2] == bytes([0x02, 0x10])]

        self.assertTrue(requests, f"未捕获到 10 01 请求帧：{collector.data_by_id(TX_ID)}")

        for data in requests:
            with self.subTest(data=data.hex(" ")):
                self.assertEqual(len(data), 3, "默认不应填充：单帧必须是 3 字节")
                self.assertEqual(data, bytes([0x02, 0x10, 0x01]))

    def test_default_flow_control_is_30_00_00(self) -> None:
        """多帧响应时，本端默认 FC 必须是 BS=0 / STmin=0。"""
        collector = self._new_collector()
        sock = self._new_socket()

        try:
            response = exchange(sock, MULTI_DID_REQUEST)
            time.sleep(CAPTURE_SETTLE)
        finally:
            collector.stop()

        self.assertIsNotNone(response)

        flow_controls = [
            d
            for d in collector.data_by_id(TX_ID)
            if frame_type(d) == "flow_control"
        ]

        self.assertTrue(flow_controls, "未捕获到本端发出的 FC")

        for data in flow_controls:
            with self.subTest(data=data.hex(" ")):
                self.assertEqual(data[:3], bytes([0x30, 0x00, 0x00]))

    def test_multi_frame_response_still_reassembles(self) -> None:
        """默认参数下多帧响应必须完整重组。"""
        sock = self._new_socket()

        response = exchange(sock, MULTI_DID_REQUEST)

        self.assertIsNotNone(response)
        self.assertEqual(len(response), MULTI_RESPONSE_LEN)
        self.assertEqual(response[:3], bytes([0x62, 0xF1, 0x90]))


# ----------------------------------------------------------------------
# B / C. 填充
# ----------------------------------------------------------------------


class TestTxPadding(IsotpOptionTestCase):
    """B. 本端发送填充：CAN 帧应被填充到 8 字节，尾部为填充值。

    观察层次很重要：填充只改变 **CAN 帧的 DLC**。
    实测（UDS_ISOTP_TX_PAD=0xAA）::

        单帧应答   无填充: DLC=7  06 50 01 00 32 00 64
                   有填充: DLC=8  06 50 01 00 32 00 64 AA

    即只有 **1 个** 填充字节（原帧 DLC=7，补到 8），
    且 ``recv()`` 重组后的 payload 长度不变。
    """

    ECU_ENV = {"UDS_ISOTP_TX_PAD": hex(TX_PAD_BYTE)}

    def test_single_frame_is_padded_to_dlc_8(self) -> None:
        collector = self._new_collector()
        sock = self._new_socket()

        try:
            frame = exchange(sock, bytes([0x10, 0x01]))
            time.sleep(CAPTURE_SETTLE)
        finally:
            collector.stop()

        self.assertIsNotNone(frame)

        responses = [
            d
            for d in collector.data_by_id(RX_ID)
            if d and (d[0] & 0xF0) == PCI_SINGLE and d[1:3] == bytes([0x50, 0x01])
        ]

        self.assertTrue(
            responses, f"未捕获到 50 01 应答帧：{collector.data_by_id(RX_ID)}"
        )

        data = responses[0]

        self.assertEqual(len(data), 8, "启用 TX 填充后 CAN 帧应为 8 字节")
        # 实测帧：06 50 01 00 32 00 64 AA
        # PCI 长度字段是 06（= SF 的 payload 长度，不含 PCI 本身，
        # 也不含填充），因此 DLC 7 → 8 只需补 1 个字节。
        self.assertEqual(data[:7], bytes([0x06, 0x50, 0x01, 0x00, 0x32, 0x00, 0x64]))
        self.assertEqual(
            data[7:],
            bytes([TX_PAD_BYTE]),
            f"填充字节应为 0x{TX_PAD_BYTE:02X}",
        )

    def test_padding_does_not_change_isotp_payload(self) -> None:
        """
        填充只影响链路层 DLC，ISO-TP 重组结果必须完全不变。

        实测对照：0xF190 应答在填充前后都是 20 字节
        （``62 F1 90`` + 17 字节 VIN），内容逐字节相同。
        """
        sock = self._new_socket()

        response = exchange(sock, SINGLE_REQUEST)

        self.assertIsNotNone(response)
        self.assertEqual(response[:3], bytes([0x62, 0xF1, 0x90]))
        self.assertEqual(
            len(response), SINGLE_RESPONSE_LEN, "填充不应改变 ISO-TP payload 长度"
        )
        self.assertEqual(response[3:], b"TESTECU0000000001")


class TestRxPaddingAccepted(IsotpOptionTestCase):
    """C. 本端接收填充：对端带填充时仍能正常收发。"""

    ECU_ENV = {"UDS_ISOTP_RX_PAD": "0xCC"}

    def test_multi_frame_exchange_with_padded_peer(self) -> None:
        """测试方开启 TX 填充发请求，ECU 开启 RX 填充接收 → 交换仍成功。"""
        sock = isotp.socket()
        sock.set_opts(txpad=0xCC)
        sock.bind(INTERFACE, ecu_address())
        sock.settimeout(RECV_TIMEOUT)
        self._sockets.append(sock)

        response = exchange(sock, MULTI_DID_REQUEST)

        self.assertIsNotNone(response, "开启 RX 填充后交换不应失败")
        self.assertEqual(len(response), MULTI_RESPONSE_LEN)
        self.assertEqual(response[:3], bytes([0x62, 0xF1, 0x90]))


# ----------------------------------------------------------------------
# D. 流控块大小
# ----------------------------------------------------------------------


class TestFcBlocksize(IsotpOptionTestCase):
    """D. 本端 FC 的块大小 BS 生效。

    方向说明（实测确认）：本 ECU 只作为**响应发送方**，
    因此多帧交换中 FC 只可能由**测试方**发出；
    ECU 永远不发 FC。所以 BS 只能在测试方方向观察。
    """

    ECU_ENV = {"UDS_ISOTP_FC_BLOCKSIZE": "1"}

    def test_ecu_never_emits_flow_control(self) -> None:
        """本 ECU 作为发送方不发 FC —— 这是下面用例方向判断的依据。"""
        collector = self._new_collector()
        sock = self._new_socket()

        try:
            response = exchange(sock, MULTI_DID_REQUEST)
            time.sleep(CAPTURE_SETTLE)
        finally:
            collector.stop()

        self.assertIsNotNone(response)

        ecu_fc = [
            d for d in collector.data_by_id(RX_ID) if frame_type(d) == "flow_control"
        ]

        self.assertEqual(
            ecu_fc,
            [],
            f"本 ECU 不应发出 FC，实际捕获：{[d.hex(' ') for d in ecu_fc]}",
        )

        ecu_types = collector.types_by_id(RX_ID)

        self.assertIn("first", ecu_types)
        self.assertIn("consecutive", ecu_types)

    def test_tester_flow_control_reports_bs_1(self) -> None:
        """
        测试方 FC 设 BS=1：每收 1 个 CF 就要再发一次 FC。

        35 字节响应 = FF + 5 个 CF（实测帧序列 21,22,23,24,25），
        因此在 BS=1 下测试方应发出 **多于一个** FC
        （BS=0 时只会发一个）。
        """
        collector = self._new_collector()
        sock = self._new_socket(bs=1)

        try:
            response = exchange(sock, MULTI_DID_REQUEST)
            time.sleep(CAPTURE_SETTLE)
        finally:
            collector.stop()

        self.assertIsNotNone(response)
        self.assertEqual(len(response), MULTI_RESPONSE_LEN, "BS=1 不应破坏重组")

        tester_fc = [
            d for d in collector.data_by_id(TX_ID) if frame_type(d) == "flow_control"
        ]

        self.assertGreaterEqual(
            len(tester_fc),
            2,
            f"BS=1 时应发出多个 FC，实际 {[d.hex(' ') for d in tester_fc]}",
        )

        for data in tester_fc:
            with self.subTest(data=data.hex(" ")):
                self.assertEqual(data[0], 0x30)
                self.assertEqual(data[1], 1, "FC 中的 BS 应为 1")

    def test_default_blocksize_gives_single_flow_control(self) -> None:
        """对照组：BS=0（默认）时只发一个 FC。"""
        collector = self._new_collector()
        sock = self._new_socket()

        try:
            response = exchange(sock, MULTI_DID_REQUEST)
            time.sleep(CAPTURE_SETTLE)
        finally:
            collector.stop()

        self.assertIsNotNone(response)

        tester_fc = [
            d for d in collector.data_by_id(TX_ID) if frame_type(d) == "flow_control"
        ]

        self.assertEqual(
            len(tester_fc),
            1,
            f"BS=0 时应只发一个 FC，实际 {[d.hex(' ') for d in tester_fc]}",
        )


# ----------------------------------------------------------------------
# E / F. 分离时间（两个方向）
# ----------------------------------------------------------------------


class TestFcStmin(IsotpOptionTestCase):
    """E. 本端 FC 的 STmin 生效：ECU 的 CF 间隔应被拉长。"""

    ECU_ENV = {"UDS_ISOTP_FC_STMIN": hex(STMIN_BYTE_20MS)}

    def test_ecu_consecutive_frames_spaced_by_fc_stmin(self) -> None:
        """
        测试方 FC 请求 STmin=20 ms → ECU 的 CF 间隔必须被拉长。

        对照：默认（STmin=0）时 CF 间隔是微秒级。
        """
        collector = self._new_collector()
        sock = self._new_socket(stmin=STMIN_BYTE_20MS)

        try:
            response = exchange(sock, MULTI_DID_REQUEST)
            time.sleep(CAPTURE_SETTLE)
        finally:
            collector.stop()

        self.assertIsNotNone(response)
        self.assertEqual(len(response), MULTI_RESPONSE_LEN, "拉长 STmin 不应破坏重组")

        ecu_frames = collector.by_id(RX_ID)
        cf_timestamps = [
            ts for ts, data in ecu_frames if frame_type(data) == "consecutive"
        ]

        self.assertGreaterEqual(
            len(cf_timestamps), 2, f"连续帧数量不足：{len(cf_timestamps)}"
        )

        measured = gaps_seconds(cf_timestamps)
        expected = STMIN_BYTE_20MS / 1000.0
        lower = expected * STMIN_LOWER_TOLERANCE

        for gap in measured:
            with self.subTest(gap_ms=gap * 1000):
                self.assertGreaterEqual(
                    gap,
                    lower,
                    f"CF 间隔 {gap * 1000:.1f} ms 小于 FC 请求的 "
                    f"{expected * 1000:.0f} ms（下界 {lower * 1000:.0f} ms）",
                )


class TestTxStmin(IsotpOptionTestCase):
    """F. 本端 FORCE_TXSTMIN 生效，且覆盖收到的 FC STmin。"""

    ECU_ENV = {"UDS_ISOTP_TX_STMIN": "2ms"}

    def test_forced_tx_stmin_overrides_received_fc_stmin(self) -> None:
        """
        ECU 强制 2 ms 发送间隔；测试方 FC 要求 20 ms。

        预期：ECU 的 CF 间隔 ≈ 2 ms（**覆盖**生效），
        明显小于 FC 请求的 20 ms。
        """
        collector = self._new_collector()
        sock = self._new_socket(stmin=STMIN_BYTE_20MS)

        try:
            response = exchange(sock, MULTI_DID_REQUEST)
            time.sleep(CAPTURE_SETTLE)
        finally:
            collector.stop()

        self.assertIsNotNone(response)
        self.assertEqual(len(response), MULTI_RESPONSE_LEN)

        ecu_frames = collector.by_id(RX_ID)
        cf_timestamps = [
            ts for ts, data in ecu_frames if frame_type(data) == "consecutive"
        ]

        self.assertGreaterEqual(
            len(cf_timestamps), 2, f"连续帧数量不足：{len(cf_timestamps)}"
        )

        measured = gaps_seconds(cf_timestamps)
        requested_ms = STMIN_BYTE_20MS

        for gap in measured:
            with self.subTest(gap_ms=gap * 1000):
                # 必须明显小于 FC 请求的 20 ms（证明覆盖生效）
                self.assertLess(
                    gap * 1000,
                    requested_ms * 0.5,
                    f"CF 间隔 {gap * 1000:.1f} ms 接近 FC 请求的 "
                    f"{requested_ms} ms，说明 FORCE_TXSTMIN 没有生效",
                )


# ----------------------------------------------------------------------
# G. 超时
# ----------------------------------------------------------------------


class TestIsotpTimeoutOption(IsotpOptionTestCase):
    """G. UDS_ISOTP_TIMEOUT 只改变 recv 阻塞行为，不改变协议语义。"""

    ECU_ENV = {"UDS_ISOTP_TIMEOUT": "0.3"}

    def test_ecu_responds_with_timeout_configured(self) -> None:
        """
        设置 recv 超时后 ECU 仍必须正常应答。

        这条保证 ``if data is None: continue`` 分支被真正走通：
        recv 会周期性返回 None，而不是永久阻塞。
        """
        sock = self._new_socket()

        for request, expected in (
            (bytes([0x10, 0x01]), SESSION_RESPONSE),
            (SINGLE_REQUEST, bytes([0x62, 0xF1, 0x90])),
        ):
            with self.subTest(request=request.hex(" ")):
                drain(sock)
                response = exchange(sock, request)

                self.assertIsNotNone(response)
                self.assertEqual(response[: len(expected[:2])], expected[:2])

    def test_idle_period_produces_no_frames(self) -> None:
        """ECU 空闲（recv 超时返回 None）时不得发送任何帧。"""
        collector = self._new_collector()

        # 先做一次正常交换，再观察空闲窗口
        sock = self._new_socket()
        self.assertIsNotNone(exchange(sock, bytes([0x10, 0x01])))

        time.sleep(CAPTURE_SETTLE)

        before = len(collector.by_id(RX_ID))
        time.sleep(0.8)
        after = len(collector.by_id(RX_ID))

        collector.stop()

        self.assertEqual(
            before,
            after,
            "ECU 在空闲窗口不应主动发送帧（recv 超时后必须 continue）",
        )


@unittest.skipUnless(
    os.path.exists(f"/sys/class/net/{INTERFACE}"),
    f"{INTERFACE} 不存在",
)
class TestInterfaceAvailable(unittest.TestCase):
    """前置条件检查：vcan0 必须存在且 UP（不需要启动 ECU）。"""

    def test_interface_is_up(self) -> None:
        with open(f"/sys/class/net/{INTERFACE}/operstate") as fh:
            state = fh.read().strip()

        self.assertIn(state, ("up", "unknown"), f"{INTERFACE} 状态异常：{state}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
