#!/usr/bin/env python3
"""
UDS Basic —— Gateway Runtime v1 的 vcan0 端到端测试。

在真实 SocketCAN / ISO-TP 链路上验证 `gateway_runtime.py` 守护进程：

    Tester ──► vcan0 ──► Gateway Runtime（独立子进程）
                              │
                              ▼
                        DiagnosticGateway
                          ├── ECU_A  0x7E0 / 0x7E8
                          ├── ECU_B  0x7E1 / 0x7E9
                          └── ECU_C  0x7E2 / 0x7EA

覆盖（对应 OQ7 的 E2E Case 1–8）：

1. 物理寻址：0x7E0/0x7E1/0x7E2 → 各自的应答 ID（并在**总线层**断言没有串号）
2. 功能寻址：0x7DF 广播到三个 ECU，且总线上**完全静默**
3. ECU 状态隔离（会话状态按 ECU 独立，判据是写 DID 的 NRC 差异）
4. 未知物理 ID：无响应（总线层静默）
5. 畸形 / 非法请求：守护进程不崩溃
6. 真实 ISO-TP 分帧：多帧请求与多帧响应都经 FF/CF
7. 生命周期：SIGTERM / SIGINT → 优雅退出（退出码 0）+ socket 释放

为什么要用原始 CAN 抓包（BusSniffer）而不只用 ISO-TP 客户端：
ISO-TP 客户端只能听到**自己绑定的那一个** CAN ID。"功能寻址不该回帧"、
"响应没有串到别的应答 ID 上"这两件事只有在总线层看全部帧才能证明。

**本层的服务集 = gateway.VirtualEcu 的静态表**（0x10/0x11/0x14/0x19/0x22/
0x2E/0x31/0x34–0x38/0x3E/0x85），**不含 0x27 SecurityAccess** —— 这是 gateway.py
既有的分层边界（0x27 需要运行时注入，归属 ecu.py 入口层），不是 OQ7 的遗漏。
详见 `test_security_access_is_not_reachable_through_the_gateway_layer`。

只使用 vcan0，不接触真实 CAN / 真实 ECU / 真实车辆。

前置条件：

- vcan0 已存在且 UP，且**独占**（勿与其它 CAN 测试并行）
- 使用网络实验虚拟环境：/home/lilike/.venvs/network/bin/python

运行：

    /home/lilike/.venvs/network/bin/python tests/test_gateway_runtime_vcan.py
或：
    bash tests/run_tests.sh e2e
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import can
import isotp

# ----------------------------------------------------------------------
# 路径与通信参数
# ----------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
_RUNTIME_SCRIPT = os.path.join(_ECU_DIR, "gateway_runtime.py")

INTERFACE = os.environ.get("UDS_INTERFACE", "vcan0")

# 物理寻址对（tester 发送 ID = ECU 请求 ID，tester 接收 ID = ECU 应答 ID）
ECU_A = (0x7E0, 0x7E8)
ECU_B = (0x7E1, 0x7E9)
ECU_C = (0x7E2, 0x7EA)
PHYSICAL = (ECU_A, ECU_B, ECU_C)

#: 功能寻址（广播）请求标识符。
FUNCTIONAL_ID = 0x7DF

RESPONSE_IDS = tuple(response_id for _, response_id in PHYSICAL)

#: 不存在的物理地址（用于"无响应"断言）。
UNKNOWN_ID = 0x7E5
UNKNOWN_RESPONSE_ID = 0x7ED

READY_TIMEOUT = 12.0
RECV_TIMEOUT = 1.5

#: 功能寻址 / 无响应场景的等待窗口（秒）。
SILENCE_WINDOW = 0.8

#: 丢弃 vcan0 上残留报文的窗口（秒）。
STALE_FRAME_DRAIN = 0.2

DID_F102 = 0xF102
DID_F102_REQUEST = bytes([0x22, 0xF1, 0x02])
DID_F102_WRITE = bytes([0x2E, 0xF1, 0x02])

DEFAULT_CONFIG = b"DEFAULT_CONFIG"

READ_VIN = bytes([0x22, 0xF1, 0x90])

#: 22 F1 90 的确定性正响应（VIN 出厂值 17 字节 → 20 字节，走 ISO-TP 多帧）。
VIN_RESPONSE = bytes([0x62, 0xF1, 0x90]) + b"TESTECU0000000001"

TESTER_PRESENT = bytes([0x3E, 0x00])
TESTER_PRESENT_RESPONSE = bytes([0x7E, 0x00])

NRC_SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_INCORRECT_MESSAGE_LENGTH = 0x13

#: 可在真实 ISO-TP 上发送的畸形 / 边界请求（写入本文件前实测取得）。
#:
#: 刻意**不含空 payload**：ISO-TP 无法承载零长度 PDU（客户端 `send(b"")`
#: 直接抛 OSError(EINVAL)），因此"空请求"只在协议层/单元测试覆盖
#: （test_gateway_runtime_unit.py），这里不假装覆盖它。
MALFORMED_REQUESTS = [
    (bytes([0x22]), bytes([0x7F, 0x22, NRC_INCORRECT_MESSAGE_LENGTH])),
    (bytes([0x2E, 0xF1]), bytes([0x7F, 0x2E, NRC_INCORRECT_MESSAGE_LENGTH])),
    (bytes([0x3E]), bytes([0x7F, 0x3E, NRC_INCORRECT_MESSAGE_LENGTH])),
    (bytes([0x10]), bytes([0x7F, 0x10, NRC_INCORRECT_MESSAGE_LENGTH])),
    (bytes([0xFF]), bytes([0x7F, 0xFF, NRC_SERVICE_NOT_SUPPORTED])),
]


def hexs(data: bytes) -> str:
    return data.hex(" ")


# ----------------------------------------------------------------------
# Tester 侧：ISO-TP 收发
# ----------------------------------------------------------------------


def send_request(
    request_id: int,
    response_id: int,
    payload: bytes,
    timeout: float = RECV_TIMEOUT,
):
    """
    以 (tx=request_id, rx=response_id) 新建 ISO-TP 连接收发一条请求。

    :returns: 响应字节；超时 / 传输失败返回 ``None``。
    """
    sock = None

    try:
        sock = isotp.socket()
        sock.bind(
            INTERFACE,
            isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                rxid=response_id,
                txid=request_id,
            ),
        )
        sock.settimeout(timeout)
        sock.send(payload)

        return sock.recv()
    except Exception:  # noqa: BLE001 - 超时/传输错误统一按"无响应"处理
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


class BusSniffer:
    """
    原始 CAN 抓包器（python-can，socketcan 原始 socket）。

    抓到的是 vcan0 上的**全部**帧（含 tester 自己发出的），因此可以证明
    "某个 CAN ID 上到底有没有出现过帧"，而不仅是"我的 socket 有没有收到"。
    """

    def __init__(self, interface: str = INTERFACE) -> None:
        self.bus = can.interface.Bus(interface="socketcan", channel=interface)
        self.frames: list[tuple[int, bytes]] = []
        self._stopped = False

        collected = self

        class Collector(can.Listener):
            def on_message_received(self, msg) -> None:  # noqa: D102
                collected.frames.append((msg.arbitration_id, bytes(msg.data)))

        self._notifier = can.Notifier(self.bus, [Collector()])

    def drain(self, duration: float = STALE_FRAME_DRAIN) -> None:
        """丢弃开抓之前的残留帧（上一次实验遗留），避免误判。"""
        time.sleep(duration)
        self.frames.clear()

    def ids(self) -> set:
        return {can_id for can_id, _ in self.frames}

    def frames_on(self, can_id: int) -> list:
        return [data for frame_id, data in self.frames if frame_id == can_id]

    def stop(self) -> list[tuple[int, bytes]]:
        if not self._stopped:
            self._stopped = True
            self._notifier.stop()
            self.bus.shutdown()

        return self.frames

    def __enter__(self) -> "BusSniffer":
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()


# ----------------------------------------------------------------------
# 被测进程：gateway_runtime.py
# ----------------------------------------------------------------------


class RuntimeProcess:
    """
    gateway_runtime.py 守护进程。

    以 ``start_new_session=True`` 启动：既便于按进程组收干净，也使守护进程
    与测试进程隔离（测试自己不会被自己发的信号影响）。
    """

    def __init__(self, interface: str = INTERFACE, wait_timeout: str = "0.5") -> None:
        self.interface = interface
        self._log = tempfile.NamedTemporaryFile(
            prefix="gateway-runtime-", suffix=".log", delete=False
        )

        env = os.environ.copy()
        env["UDS_INTERFACE"] = interface

        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                _RUNTIME_SCRIPT,
                "--interface",
                interface,
                "--wait-timeout",
                wait_timeout,
            ],
            cwd=_ECU_DIR,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )

    # -- 观察 ----------------------------------------------------------

    def output(self) -> str:
        """读取守护进程日志（用于失败诊断）。"""
        try:
            with open(self._log.name, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    # -- 信号 / 清理 ---------------------------------------------------

    def send_signal(self, sig: int) -> None:
        """只给守护进程本身发信号（等价于 ``kill <pid>``）。"""
        self.proc.send_signal(sig)

    def stop(self) -> None:
        """清理：SIGTERM → 等待 → SIGKILL 兜底，确保不留常驻进程。"""
        if self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:  # noqa: BLE001
                pass

        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    # -- 就绪 ----------------------------------------------------------

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> None:
        """以真实请求探测就绪（而不是只 sleep）。"""
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if not self.is_alive():
                raise RuntimeError(
                    f"gateway runtime 启动后立即退出：\n{self.output()}"
                )

            if send_request(*ECU_A, READ_VIN, timeout=1.0) == VIN_RESPONSE:
                return

            time.sleep(0.2)

        raise RuntimeError(
            f"gateway runtime 在 {timeout}s 内未就绪：\n{self.output()}"
        )


def write_f102(physical: tuple, data: bytes) -> bytes:
    """对指定 ECU 发一条 0x2E 写 DID F102 请求。"""
    return send_request(*physical, DID_F102_WRITE + data)


def set_session(physical: tuple, subfunction: int) -> bytes:
    """对指定 ECU 发 0x10 会话切换，并断言它确实响应了。"""
    response = send_request(*physical, bytes([0x10, subfunction]))

    if response is None:
        raise AssertionError(
            f"0x{physical[0]:03X} 未响应 10 {subfunction:02X}（会话切换失败）"
        )

    return response


# ----------------------------------------------------------------------
# Case 1–7：共享一台守护进程
# ----------------------------------------------------------------------


class TestGatewayRuntimeOnVcan(unittest.TestCase):
    """在一条 vcan0 上共享一个守护进程，逐条验证 OQ7 的 E2E Case 1–7。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = RuntimeProcess()
        cls.runtime.wait_ready()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.runtime.stop()

    def setUp(self) -> None:
        """把三个 ECU 复位到已知状态，使用例之间互不依赖（顺序无关）。"""
        for request_id, response_id in PHYSICAL:
            response = send_request(request_id, response_id, bytes([0x11, 0x01]))
            self.assertEqual(
                response,
                bytes([0x51, 0x01]),
                f"0x{request_id:03X} 的 hardReset 未成功：{response!r}",
            )

    # -- Case 1/2/3：物理寻址 -----------------------------------------

    def test_physical_routing_uses_each_response_id(self):
        """Case 1–3：0x7E0→0x7E8、0x7E1→0x7E9、0x7E2→0x7EA。"""
        for request_id, response_id in PHYSICAL:
            with self.subTest(request_id=hex(request_id), response_id=hex(response_id)):
                response = send_request(request_id, response_id, READ_VIN)
                self.assertEqual(response, VIN_RESPONSE)

    def test_response_appears_only_on_the_target_can_id(self):
        """
        总线层证明：应答只出现在目标 ECU 的应答 ID 上，不串到另外两个。

        这是"按地址路由"的强证明 —— ISO-TP 客户端只能听到自己绑定的 ID。
        """
        with BusSniffer() as sniffer:
            sniffer.drain()
            response = send_request(*ECU_A, TESTER_PRESENT)
            frames = sniffer.stop()

        self.assertEqual(response, TESTER_PRESENT_RESPONSE)
        self.assertIn(ECU_A[0], sniffer.ids(), "总线上应出现请求帧 0x7E0")
        self.assertIn(ECU_A[1], sniffer.ids(), "总线上应出现应答帧 0x7E8")
        self.assertNotIn(ECU_B[1], sniffer.ids(), "应答不得出现在 0x7E9")
        self.assertNotIn(ECU_C[1], sniffer.ids(), "应答不得出现在 0x7EA")
        self.assertNotIn(ECU_B[0], sniffer.ids(), "请求不得出现在 0x7E1")
        self.assertNotIn(ECU_C[0], sniffer.ids(), "请求不得出现在 0x7E2")
        self.assertTrue(frames, "抓包不应为空（若为空说明抓包器没工作）")

    def test_each_ecu_answers_on_its_own_response_id_over_the_bus(self):
        """逐个 ECU 在总线层确认：请求问谁，就由谁在自己的应答 ID 上回答。"""
        for request_id, response_id in PHYSICAL:
            with self.subTest(request_id=hex(request_id)):
                with BusSniffer() as sniffer:
                    sniffer.drain()
                    send_request(request_id, response_id, TESTER_PRESENT)
                    sniffer.stop()

                self.assertIn(response_id, sniffer.ids())
                for other in RESPONSE_IDS:
                    if other != response_id:
                        self.assertNotIn(
                            other,
                            sniffer.ids(),
                            f"0x{request_id:03X} 的应答不应出现在 0x{other:03X}",
                        )

    # -- Case 6：多帧 ISO-TP ------------------------------------------

    def test_multiframe_response_is_segmented_by_iso_tp(self):
        """VIN 响应 20 字节 > 7：必须经首帧 + 连续帧发出。"""
        with BusSniffer() as sniffer:
            sniffer.drain()
            response = send_request(*ECU_A, READ_VIN)
            sniffer.stop()

        self.assertEqual(response, VIN_RESPONSE)

        payloads = sniffer.frames_on(ECU_A[1])
        self.assertTrue(payloads, "0x7E8 上未捕获到任何帧")

        pci_types = [data[0] & 0xF0 for data in payloads if data]
        self.assertIn(0x10, pci_types, "多帧响应应包含首帧（PCI 0x1x）")
        self.assertIn(0x20, pci_types, "多帧响应应包含连续帧（PCI 0x2x）")

    def test_multiframe_request_is_reassembled_by_the_gateway(self):
        """13 字节请求（>7）必须经首帧 + 连续帧送达网关，并被完整处理。"""
        payload = DID_F102_WRITE + b"0123456789"   # 3 + 10 = 13 字节

        with BusSniffer() as sniffer:
            sniffer.drain()
            response = send_request(*ECU_A, payload)
            sniffer.stop()

        # 默认会话 → serviceNotSupportedInActiveSession。
        # 这同时证明 13 字节被**完整重组**（长度若被截断会得到 NRC 0x13）。
        self.assertEqual(response, bytes([0x7F, 0x2E, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION]))

        request_pcis = [data[0] & 0xF0 for data in sniffer.frames_on(ECU_A[0]) if data]
        self.assertIn(0x10, request_pcis, "多帧请求应包含首帧")
        self.assertIn(0x20, request_pcis, "多帧请求应包含连续帧")

    # -- Case 4：功能寻址 ---------------------------------------------

    def test_functional_broadcast_is_silent_on_the_bus(self):
        """
        Case 4：0x7DF → 广播到 A/B/C，但**总线上不得出现任何响应帧**。

        同时用"写 DID 的 NRC 变化"证明广播确实被三个 ECU 执行了：
        默认会话 → 0x7F；广播 10 03 之后（extended，仍未解锁）→ 0x33。
        """
        # 前置：三个 ECU 均在默认会话
        for request_id, response_id in PHYSICAL:
            with self.subTest(phase="before", request_id=hex(request_id)):
                self.assertEqual(
                    send_request(request_id, response_id, DID_F102_WRITE + b"X"),
                    bytes([0x7F, 0x2E, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION]),
                )

        with BusSniffer() as sniffer:
            sniffer.drain()
            # 功能寻址：tester 绑在 0x7E8 上等，若真有 ECU 回帧就会被收到
            broadcast = send_request(
                FUNCTIONAL_ID, ECU_A[1], bytes([0x10, 0x03]), timeout=SILENCE_WINDOW
            )
            sniffer.stop()

        self.assertIsNone(broadcast, "功能寻址不得有任何 ECU 回帧")

        # 总线上除广播请求本身外不得出现任何帧
        others = [
            (can_id, data)
            for can_id, data in sniffer.frames
            if can_id != FUNCTIONAL_ID
        ]
        self.assertEqual(
            others,
            [],
            f"功能寻址后总线上出现了额外帧：{[(hex(i), hexs(d)) for i, d in others]}",
        )

        # 广播确实被执行到三个 ECU（会话已切换），且仍未解锁
        for request_id, response_id in PHYSICAL:
            with self.subTest(phase="after", request_id=hex(request_id)):
                self.assertEqual(
                    send_request(request_id, response_id, DID_F102_WRITE + b"X"),
                    bytes([0x7F, 0x2E, NRC_SECURITY_ACCESS_DENIED]),
                )

    def test_functional_broadcast_does_not_leak_positive_responses(self):
        """即使广播的是会产生正响应的服务（读 DID），也不得回帧。"""
        with BusSniffer() as sniffer:
            sniffer.drain()
            leaked = send_request(
                FUNCTIONAL_ID, ECU_A[1], READ_VIN, timeout=SILENCE_WINDOW
            )
            sniffer.stop()

        self.assertIsNone(leaked, "功能寻址读 DID 不得泄漏正响应")
        self.assertEqual(
            [can_id for can_id, _ in sniffer.frames if can_id != FUNCTIONAL_ID],
            [],
        )

    # -- Case 6：未知物理 ID ------------------------------------------

    def test_unknown_physical_id_gets_no_response(self):
        """
        Case 6：不存在的物理地址必须完全静默。

        注意：总线上**必然**能看到 tester 自己发出的那条请求帧（0x7E5），
        所以判据不是"总线为空"，而是"除了这条请求之外什么都没有" ——
        即没有任何设备在 0x7E5 所属的应答方向上回帧。
        """
        with BusSniffer() as sniffer:
            sniffer.drain()
            response = send_request(
                UNKNOWN_ID, UNKNOWN_RESPONSE_ID, TESTER_PRESENT, timeout=SILENCE_WINDOW
            )
            sniffer.stop()

        self.assertIsNone(response, "未知物理地址不应有响应")
        self.assertEqual(
            sniffer.ids(),
            {UNKNOWN_ID},
            f"未知物理地址只应出现 tester 自己的请求帧；实际抓到的 CAN ID："
            f"{[hex(i) for i in sorted(sniffer.ids())]}",
        )
        self.assertEqual(sniffer.frames_on(UNKNOWN_RESPONSE_ID), [])

    def test_unknown_id_does_not_disturb_known_ecus(self):
        """未知 ID 之后，三个 ECU 仍必须正常应答（状态未被扰动）。"""
        send_request(UNKNOWN_ID, UNKNOWN_RESPONSE_ID, bytes([0x10, 0x03]), timeout=0.3)

        for request_id, response_id in PHYSICAL:
            with self.subTest(request_id=hex(request_id)):
                response = send_request(request_id, response_id, DID_F102_REQUEST)
                self.assertEqual(
                    response,
                    bytes([0x62, 0xF1, 0x02]) + DEFAULT_CONFIG,
                    "未知 ID 不应改变任何 ECU 的会话/状态",
                )

    # -- Case 7：畸形请求 ---------------------------------------------

    def test_malformed_requests_do_not_kill_the_daemon(self):
        """Case 7：畸形请求必须被回答以 NRC，且守护进程继续运行。"""
        for payload, expected in MALFORMED_REQUESTS:
            with self.subTest(payload=hexs(payload)):
                self.assertEqual(send_request(*ECU_A, payload), expected)
                self.assertTrue(self.runtime.is_alive(), "守护进程在处理畸形请求后退出")

        # 关键：异常请求之后仍能正常服务
        self.assertEqual(send_request(*ECU_A, READ_VIN), VIN_RESPONSE)

    def test_malformed_traffic_does_not_break_the_other_ecus(self):
        """对 ECU_A 打畸形请求，不得影响 ECU_B / ECU_C。"""
        for payload, _ in MALFORMED_REQUESTS:
            send_request(*ECU_A, payload)

        for physical in (ECU_B, ECU_C):
            with self.subTest(request_id=hex(physical[0])):
                self.assertEqual(send_request(*physical, READ_VIN), VIN_RESPONSE)

    def test_daemon_survives_high_volume_garbage(self):
        """
        连续大量非法 SID：守护进程不得崩溃、不得漏掉后续正常请求。

        这些请求都在协议层被回答以 NRC 0x11（未注册 SID），因此既不修改
        ECU 状态，也不依赖任何会话前提。
        """
        for sid in range(0x00, 0x100, 7):
            send_request(*ECU_A, bytes([sid]), timeout=0.4)
            send_request(*ECU_A, bytes([sid, 0x00]), timeout=0.4)

        self.assertTrue(self.runtime.is_alive(), "守护进程在高频畸形流量下退出")
        self.assertEqual(send_request(*ECU_A, READ_VIN), VIN_RESPONSE)

    # -- Case 5：状态隔离 ---------------------------------------------

    def test_session_state_is_isolated_per_ecu(self):
        """
        Case 5：会话状态按 ECU 独立。

        只在 ECU_A 上切 extended，然后对三个 ECU 发**同一条**写 DID 请求：

            ECU_A（extended，未解锁）→ 0x33 securityAccessDenied
            ECU_B / ECU_C（default） → 0x7F serviceNotSupportedInActiveSession

        NRC 的差异就是状态隔离的判据（与既有 test_multi_ecu_vcan.py 用的是
        同一判据）。若三者共享同一个 EcuState，ECU_B/C 会跟着变成 0x33。
        """
        response = set_session(ECU_A, 0x03)
        self.assertEqual(response[:2], bytes([0x50, 0x03]))

        expected = {
            ECU_A: NRC_SECURITY_ACCESS_DENIED,
            ECU_B: NRC_SERVICE_NOT_SUPPORTED_IN_SESSION,
            ECU_C: NRC_SERVICE_NOT_SUPPORTED_IN_SESSION,
        }

        for physical, nrc in expected.items():
            with self.subTest(request_id=hex(physical[0])):
                self.assertEqual(
                    write_f102(physical, b"X"),
                    bytes([0x7F, 0x2E, nrc]),
                )

    def test_session_change_is_reversible_per_ecu_only(self):
        """把 ECU_A 切回默认会话，不得连带改变 ECU_B / ECU_C。"""
        # A/B/C → extended
        for physical in PHYSICAL:
            with self.subTest(phase="to-extended", request_id=hex(physical[0])):
                self.assertEqual(
                    set_session(physical, 0x03)[:2], bytes([0x50, 0x03])
                )

        # 只把 ECU_A 切回默认
        self.assertEqual(set_session(ECU_A, 0x01)[:2], bytes([0x50, 0x01]))

        self.assertEqual(
            write_f102(ECU_A, b"X"),
            bytes([0x7F, 0x2E, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION]),
        )

        for physical in (ECU_B, ECU_C):
            with self.subTest(request_id=hex(physical[0])):
                self.assertEqual(
                    write_f102(physical, b"X"),
                    bytes([0x7F, 0x2E, NRC_SECURITY_ACCESS_DENIED]),
                )

    def test_did_values_are_isolated_per_ecu(self):
        """只读 DID：三个 ECU 各自独立返回出厂值（互不串值）。"""
        for physical in PHYSICAL:
            with self.subTest(request_id=hex(physical[0])):
                self.assertEqual(
                    send_request(*physical, DID_F102_REQUEST),
                    bytes([0x62, 0xF1, 0x02]) + DEFAULT_CONFIG,
                )

    def test_security_access_is_not_reachable_through_the_gateway_layer(self):
        """
        边界记录：**0x27 SecurityAccess 当前不经 gateway 层提供**（已知边界）。

        `gateway.VirtualEcu` 的默认 handler 使用 `uds_server` 的**静态** SERVICES
        表，该表不含 0x27 —— 见 `gateway.py::_default_handler_for` 的说明：
        0x27 需要运行时时间/配置注入，归属 `ecu.py` 入口层，网关层聚焦路由。

        因此本 runtime 也不提供 0x27。这不是 OQ7 引入的缺陷，而是既有分层边界
        的直接结果；本用例把它**显式固化**，避免被误读成漏接。若后续 OQ 决定让
        网关透传 0x27（需同时解决"每 ECU 独立注入"与"不重复定义注入语义"），
        本用例应随之更新。
        """
        self.assertEqual(
            send_request(*ECU_A, bytes([0x27, 0x01])),
            bytes([0x7F, 0x27, NRC_SERVICE_NOT_SUPPORTED]),
        )


# ----------------------------------------------------------------------
# Case 8：生命周期（每个用例起自己的守护进程）
# ----------------------------------------------------------------------


class TestGatewayRuntimeLifecycle(unittest.TestCase):
    """
    生命周期：启动 → 服务请求 → 信号 → 干净退出。

    独立起停，不与共享守护进程共存（同一条 vcan0 上两个守护进程会同时绑定
    相同的请求 ID，产生重复应答而互相干扰）。
    """

    def _assert_graceful_shutdown(self, sig: int, name: str) -> None:
        runtime = RuntimeProcess()

        try:
            runtime.wait_ready()
            self.assertTrue(runtime.is_alive(), "守护进程未保持运行")

            # 先确认它确实在服务
            self.assertEqual(send_request(*ECU_A, READ_VIN), VIN_RESPONSE)

            started = time.monotonic()
            runtime.send_signal(sig)
            exit_code = runtime.proc.wait(timeout=5.0)
            elapsed = time.monotonic() - started
        finally:
            runtime.stop()

        self.assertEqual(
            exit_code,
            0,
            f"{name} 后应**优雅退出**（退出码 0），实际 {exit_code}。"
            f"（负值表示被信号直接杀死，说明没有走清理路径）\n{runtime.output()}",
        )
        self.assertLess(elapsed, 5.0, f"{name} 后退出耗时过长：{elapsed:.2f}s")

        output = runtime.output()
        self.assertIn("已停止", output, "退出时未打印清理完成信息")
        self.assertIn("socket 已清理", output)

    def test_sigterm_exits_cleanly(self):
        """kill <pid>（SIGTERM）→ 退出码 0，并完成 socket 清理。"""
        self._assert_graceful_shutdown(signal.SIGTERM, "SIGTERM")

    def test_sigint_exits_cleanly(self):
        """Ctrl-C（SIGINT）→ 退出码 0，并完成 socket 清理。"""
        self._assert_graceful_shutdown(signal.SIGINT, "SIGINT")

    def test_no_leftover_process_or_dangling_socket(self):
        """
        退出后不得留下守护进程，且 socket 必须已释放。

        证明方式：SIGTERM 之后**立刻**在同一接口上重新启动一台守护进程，
        并确认它能成功绑定并就绪 —— 若上一个实例仍占着 ID / 未释放 socket，
        这里会失败。
        """
        first = RuntimeProcess()
        first.wait_ready()
        first.send_signal(signal.SIGTERM)
        self.assertEqual(first.proc.wait(timeout=5.0), 0)
        self.assertIsNotNone(first.proc.poll(), "守护进程未退出")

        second = RuntimeProcess()
        try:
            second.wait_ready()
            self.assertEqual(send_request(*ECU_A, READ_VIN), VIN_RESPONSE)
        finally:
            second.stop()

    def test_help_and_bad_arguments(self):
        """--help 必须可用；非法 --wait-timeout 必须以非 0 退出且不常驻。"""
        help_run = subprocess.run(
            [sys.executable, _RUNTIME_SCRIPT, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=_ECU_DIR,
            timeout=30,
        )
        self.assertEqual(help_run.returncode, 0)
        for expected in ("--interface", "vcan0", "0x7DF"):
            self.assertIn(expected, help_run.stdout)

        bad_run = subprocess.run(
            [sys.executable, _RUNTIME_SCRIPT, "--interface", INTERFACE, "--wait-timeout", "0"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=_ECU_DIR,
            timeout=30,
        )
        self.assertNotEqual(bad_run.returncode, 0, "非法参数必须以非 0 退出")
        self.assertIn("--wait-timeout", bad_run.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
