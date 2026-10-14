#!/usr/bin/env python3
"""
UDS Basic —— Gateway Runtime（gateway_runtime.py）离线单元测试。

**离线**：不启动守护进程、不打开任何 CAN socket —— 用一个记录型 fake
transport 替换真实 ISO-TP 层，因此不需要 vcan0、不需要 root。

真实 socket 的绑定、ISO-TP 分帧与信号退出由 vcan 端到端测试
（test_gateway_runtime_vcan.py）覆盖。本文件只覆盖 **runtime 自身的职责**：

- 构造与接口配置（含安全默认值 vcan0）
- 物理寻址派发与应答路由
- 功能寻址派发（执行但不回帧）
- 未知 CAN ID / 畸形请求
- ECU 状态隔离
- 生命周期（open / close / stop / serve_forever）
- 传输错误遏制

刻意**不**重复 test_gateway_unit.py 已充分覆盖的协议层路由语义 —— 那里测的是
"``DiagnosticGateway.route()`` 决定什么"，这里测的是"runtime 如何搬运这个决定"。

运行：

    python3 tests/test_gateway_runtime_unit.py
或：
    bash tests/run_tests.sh unit
"""

import contextlib
import errno
import io
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import ecu as ecu_module  # noqa: E402
import gateway_runtime  # noqa: E402
import uds_types  # noqa: E402
from gateway import (  # noqa: E402
    FUNCTIONAL_ADDRESS,
    VirtualEcu,
    build_default_gateway,
)
from gateway_runtime import (  # noqa: E402
    DEFAULT_INTERFACE,
    DEFAULT_WAIT_TIMEOUT,
    GatewayRuntime,
    IsotpTransport,
    build_runtime,
    parse_args,
)
from uds_server import EcuState  # noqa: E402

#: 网段拓扑（与 gateway.DEFAULT_ECUS 一致，测试内显式写出以便断言）。
REQUEST_A, RESPONSE_A = 0x7E0, 0x7E8
REQUEST_B, RESPONSE_B = 0x7E1, 0x7E9
REQUEST_C, RESPONSE_C = 0x7E2, 0x7EA

DID_F102 = 0xF102
DEFAULT_CONFIG = b"DEFAULT_CONFIG"
A_CONF = b"A_CONF"

#: 22 F1 90 的确定性正响应（VIN 出厂值 17 字节）。
VIN_RESPONSE = bytes([0x62, 0xF1, 0x90]) + b"TESTECU0000000001"

#: 畸形 / 边界请求的确定性响应（本文件写入前用协议层实测取得，非推断）。
MALFORMED_CASES = [
    (b"", bytes([0x7F, 0x00, 0x13])),           # 空请求：无 SID 可回显 → 00 + 13
    (bytes([0x22]), bytes([0x7F, 0x22, 0x13])),  # 长度不足
    (bytes([0x2E, 0xF1]), bytes([0x7F, 0x2E, 0x13])),
    (bytes([0x3E]), bytes([0x7F, 0x3E, 0x13])),
    (bytes([0x10]), bytes([0x7F, 0x10, 0x13])),
    (bytes([0xFF]), bytes([0x7F, 0xFF, 0x11])),  # 未注册 SID → serviceNotSupported
]


def unlock_extended(state: EcuState) -> None:
    """把 ECU 置为 extended 会话 + 已解锁（0x2E 写入的前置条件）。"""
    state.session = uds_types.SESSION_EXTENDED
    state.security_level = uds_types.SECURITY_LEVEL1


def isotp_oserror(errnum: int) -> OSError:
    """构造一个带指定 errno 的 OSError（模拟内核 ISO-TP 错误）。"""
    return OSError(errnum, os.strerror(errnum))


class _FakeSock:
    """最小 ISO-TP socket 替身：只实现 IsotpTransport 实际用到的四个方法。"""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.closed = False
        self.bound_to = None
        self.sent: list[bytes] = []

    def bind(self, interface, address) -> None:
        if self.fail:
            raise isotp_oserror(errno.ENODEV)
        self.bound_to = (interface, address)

    def settimeout(self, value) -> None:
        pass

    def send(self, data, flags: int = 0) -> int:
        self.sent.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.closed = True


def fake_socket_factory(created: list, fail_at: int | None = None):
    """
    返回一个可 patch 到 ``gateway_runtime.isotp.socket`` 的工厂。

    :param created: 收集被创建的 fake socket（供断言"是否全部关闭"）。
    :param fail_at: 让第 N 个被创建的 socket 绑定失败（None = 全部成功）。
    """

    def factory():
        sock = _FakeSock(fail=len(created) + 1 == fail_at)
        created.append(sock)
        return sock

    return factory


class FakeTransport:
    """
    记录型 fake transport：把"总线"换成两个可检查的列表。

    刻意不继承任何基类 —— runtime 只按 ``Transport`` Protocol 的结构使用它，
    "runtime 不依赖真实 isotp 对象"这一点正是本测试接缝要证明的。
    """

    def __init__(self, requests=None):
        self.requests: list[tuple[int, bytes]] = list(requests or [])
        self.sent: list[tuple[int, bytes]] = []
        self.opened = False
        self.closed = False

        #: 下一次 receive() 要抛出的异常（抛出后自动清空，便于测"错一次就恢复"）。
        self.recv_error: Exception | None = None

        #: 每一次 send() 要抛出的异常（持续生效，便于测发送侧遏制）。
        self.send_error: Exception | None = None

        #: 队列取空时的回调（测试用来在循环内部触发 stop()）。
        self.on_empty = None

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def receive(self, timeout: float):
        if self.recv_error is not None:
            error, self.recv_error = self.recv_error, None
            raise error

        if self.requests:
            return self.requests.pop(0)

        if self.on_empty is not None:
            self.on_empty()

        return None

    def send(self, can_id: int, payload: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error

        self.sent.append((can_id, payload))


class RuntimeTestCase(unittest.TestCase):
    """公共装置：默认三 ECU 网段 + fake transport + 可断言的日志。"""

    def setUp(self) -> None:
        self.logs: list[str] = []
        self.transport = FakeTransport()
        self.runtime = GatewayRuntime(
            build_default_gateway(),
            self.transport,
            log=self.logs.append,
        )

    def feed(self, can_id: int, payload: bytes) -> None:
        """把一条请求塞进 fake 总线并让 runtime 处理一次。"""
        self.transport.requests.append((can_id, payload))
        self.assertTrue(self.runtime.serve_once(timeout=0.0))

    def ecu(self, name: str) -> VirtualEcu:
        return self.runtime.gateway.ecu_by_name(name)


# ----------------------------------------------------------------------
# 构造 / 接口配置
# ----------------------------------------------------------------------


class TestConstructionAndInterface(unittest.TestCase):
    def test_default_interface_is_virtual(self):
        """安全默认值：默认接口必须是 vcan0，绝不能默认指向真实总线。"""
        self.assertEqual(DEFAULT_INTERFACE, "vcan0")
        self.assertEqual(parse_args([]).interface, "vcan0")

    def test_interface_is_configurable(self):
        self.assertEqual(parse_args(["--interface", "vcan1"]).interface, "vcan1")

    def test_default_wait_timeout_is_used(self):
        self.assertEqual(parse_args([]).wait_timeout, DEFAULT_WAIT_TIMEOUT)

    def test_transport_stores_interface(self):
        transport = IsotpTransport("vcan2", [(REQUEST_A, RESPONSE_A)])
        self.assertEqual(transport.interface, "vcan2")
        self.assertEqual(transport.functional_address, FUNCTIONAL_ADDRESS)

    def test_build_runtime_topology(self):
        runtime = build_runtime()
        self.assertEqual(
            [(e.name, e.request_id, e.response_id) for e in runtime.gateway.ecus],
            [
                ("ECU_A", REQUEST_A, RESPONSE_A),
                ("ECU_B", REQUEST_B, RESPONSE_B),
                ("ECU_C", REQUEST_C, RESPONSE_C),
            ],
        )

    def test_build_runtime_ecus_have_independent_state(self):
        """构造出的三个 ECU 必须是三个不同的 EcuState 对象。"""
        ecus = build_runtime().gateway.ecus
        states = [id(e.state) for e in ecus]
        self.assertEqual(len(set(states)), 3)


class TestInterfaceSafetyBoundary(unittest.TestCase):
    """
    入口安全边界：**只允许 vcan0**（fail-closed）。

    与 ``TestConstructionAndInterface`` 的区别：那里测的是 parser 能否**解析**
    ``--interface``（解析能力保留，参数值可为任意字符串）；这里测的是**入口**
    是否**拒绝**非 vcan0，且拒绝必须发生在任何构造 / socket 绑定**之前**。

    仅仅验证"返回码非 0"是不够的 —— 下面同时证明：
    ``build_runtime()`` 未被调用、且**没有任何 CAN socket 被创建 / 绑定**。
    """

    def _fake_runtime(self) -> mock.MagicMock:
        """
        可安全跑完 main() 的 runtime 替身（不碰真实 socket、不阻塞）。

        ``gateway.ecus`` 用空列表、``functional_address`` 用真实值，
        以便 main() 的启动横幅能正常格式化。
        """
        fake = mock.MagicMock()
        fake.gateway.ecus = []
        fake.gateway.functional_address = FUNCTIONAL_ADDRESS
        return fake

    def _run_main(self, interface: str, wait_timeout: str = "0.1"):
        """
        以 mock 替换 runtime 与 socket，运行真实 ``main()``。

        同时吞掉 main() 的启动横幅，避免污染测试输出。

        :returns: ``(rc, build_mock, created_sockets, stderr_text)``
        """
        created: list = []

        def socket_factory(*_args, **_kwargs):
            created.append(True)
            return mock.MagicMock()

        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.object(gateway_runtime, "build_runtime") as build,
            mock.patch.object(gateway_runtime.isotp, "socket", socket_factory),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            build.return_value = self._fake_runtime()
            rc = gateway_runtime.main(
                ["--interface", interface, "--wait-timeout", wait_timeout]
            )

        return rc, build, created, stderr.getvalue()

    def test_vcan0_is_accepted(self):
        """允许的接口：正常走到 build_runtime / open / serve_forever。"""
        rc, build, _, stderr = self._run_main("vcan0")

        self.assertEqual(rc, 0)
        build.assert_called_once()
        self.assertEqual(build.call_args.args[0], "vcan0")
        self.assertEqual(stderr, "", "合法接口不应产生任何安全告警")

    def test_vcan0_is_the_only_allowed_interface(self):
        self.assertEqual(gateway_runtime.ALLOWED_INTERFACES, ("vcan0",))

    def test_non_vcan0_interfaces_are_rejected_without_binding(self):
        """can0 / can1 / vcan1 等一律拒绝：非 0 退出、不构造、不建 socket。"""
        for interface in ("can0", "can1", "vcan1", "vcan", "vcan0x", "", "VCAN0", "vcan0 "):
            with self.subTest(interface=interface):
                rc, build, created, _ = self._run_main(interface)

                self.assertNotEqual(rc, 0, f"{interface!r} 必须被拒绝")
                build.assert_not_called()
                self.assertEqual(
                    created, [], f"{interface!r} 被拒绝时不得创建任何 CAN socket"
                )

    def test_rejection_happens_before_runtime_construction(self):
        """
        拒绝必须发生在 ``build_runtime()`` **之前**。

        ``build_runtime`` 被换成会抛异常的哨兵：若入口在拒绝前先构造 runtime，
        这里会看到该异常而不是正常的非 0 返回码。
        """
        class _SentinelError(Exception):
            pass

        with mock.patch.object(
            gateway_runtime, "build_runtime", side_effect=_SentinelError
        ) as build:
            rc = gateway_runtime.main(["--interface", "can0"])

        self.assertEqual(rc, 2)
        build.assert_not_called()

    def test_rejection_message_is_explicit_on_stderr(self):
        """错误信息必须明确指出"仅允许 vcan0"，且说明拒绝原因。"""
        rc, build, created, message = self._run_main("can0")

        self.assertEqual(rc, 2)
        build.assert_not_called()
        self.assertEqual(created, [])

        self.assertIn("仅允许使用 vcan0", message)
        self.assertIn("拒绝启动非虚拟/非允许接口", message)
        self.assertIn("can0", message)


class TestTransportConstructionGuards(unittest.TestCase):
    """构造期护栏 —— 全部在创建任何 socket **之前**完成，因此可离线测试。"""

    def test_empty_address_table_rejected(self):
        with self.assertRaises(ValueError):
            IsotpTransport("vcan0", [])

    def test_duplicate_response_id_rejected(self):
        with self.assertRaises(ValueError):
            IsotpTransport("vcan0", [(REQUEST_A, RESPONSE_A), (REQUEST_B, RESPONSE_A)])

    def test_response_id_colliding_with_functional_placeholder_rejected(self):
        """
        功能寻址 socket 的 txid 占位值不得与任何应答地址重合。

        否则发送映射会指向功能寻址 socket，把一个"永不发送"的 socket
        变成真实发送路径。
        """
        placeholder = gateway_runtime.FUNCTIONAL_PLACEHOLDER_TX_ID
        with self.assertRaises(ValueError):
            IsotpTransport("vcan0", [(REQUEST_A, placeholder)])

    def test_receive_and_send_require_open(self):
        transport = IsotpTransport("vcan0", [(REQUEST_A, RESPONSE_A)])
        with self.assertRaises(RuntimeError):
            transport.receive(0.0)
        with self.assertRaises(RuntimeError):
            transport.send(RESPONSE_A, b"\x7E\x00")


class TestPartialBindFailureCleanup(unittest.TestCase):
    """
    绑定中途失败不得泄漏已经建立的 socket。

    用假的 ``isotp.socket`` 工厂驱动这条路径 —— 本文件刻意**不**打开任何
    真实 CAN socket，因此在没有 CAN 内核支持的机器上也能运行。
    """

    def _run_failing_open(self, fail_at: int) -> tuple[list, IsotpTransport]:
        """让第 ``fail_at`` 个被创建的 socket 绑定失败，返回全部 fake socket。"""
        created: list = []

        transport = IsotpTransport(
            "vcan0",
            [(REQUEST_A, RESPONSE_A), (REQUEST_B, RESPONSE_B), (REQUEST_C, RESPONSE_C)],
        )

        with mock.patch.object(
            gateway_runtime.isotp, "socket", fake_socket_factory(created, fail_at)
        ):
            with self.assertRaises(OSError):
                transport.open()

        return created, transport

    def test_midway_bind_failure_closes_all_created_sockets(self):
        # 第 4 个 socket（功能寻址那个）绑定失败
        created, _ = self._run_failing_open(fail_at=4)

        self.assertEqual(len(created), 4)
        self.assertTrue(
            all(sock.closed for sock in created),
            "绑定失败后必须关闭所有已创建的 socket（否则泄漏 fd）",
        )

    def test_first_bind_failure_is_propagated(self):
        created, _ = self._run_failing_open(fail_at=1)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    def test_close_after_failed_open_is_safe(self):
        """open() 失败后 close() 必须是安全的空操作（异常路径共用清理路径）。"""
        _, transport = self._run_failing_open(fail_at=2)
        transport.close()  # 不应抛异常


# ----------------------------------------------------------------------
# 派发与应答路由
# ----------------------------------------------------------------------


class TestPhysicalDispatch(RuntimeTestCase):
    def test_each_request_id_is_answered_on_its_own_response_id(self):
        for request_id, response_id in (
            (REQUEST_A, RESPONSE_A),
            (REQUEST_B, RESPONSE_B),
            (REQUEST_C, RESPONSE_C),
        ):
            with self.subTest(request_id=hex(request_id)):
                self.transport.sent.clear()
                self.feed(request_id, bytes([0x22, 0xF1, 0x90]))
                self.assertEqual(self.transport.sent, [(response_id, VIN_RESPONSE)])

    def test_response_never_appears_on_another_ecus_response_id(self):
        self.feed(REQUEST_A, bytes([0x3E, 0x00]))
        self.assertEqual(self.transport.sent, [(RESPONSE_A, bytes([0x7E, 0x00]))])

    def test_suppress_bit_produces_no_frame(self):
        """3E 80：协议层返回 b""，runtime 不得发出任何帧。"""
        self.feed(REQUEST_A, bytes([0x3E, 0x80]))
        self.assertEqual(self.transport.sent, [])

    def test_negative_response_is_routed(self):
        self.feed(REQUEST_A, bytes([0x2E, DID_F102 >> 8, DID_F102 & 0xFF]) + b"X")
        self.assertEqual(self.transport.sent, [(RESPONSE_A, bytes([0x7F, 0x2E, 0x7F]))])

    def test_handle_request_reports_route_decision(self):
        results = self.runtime.handle_request(REQUEST_A, bytes([0x3E, 0x00]))
        self.assertEqual(results, [(RESPONSE_A, bytes([0x7E, 0x00]))])


class TestFunctionalDispatch(RuntimeTestCase):
    def test_broadcast_executes_on_all_ecus_but_sends_nothing(self):
        self.feed(FUNCTIONAL_ADDRESS, bytes([0x10, 0x03]))

        self.assertEqual(self.transport.sent, [], "功能寻址不得回帧")

        for ecu in self.runtime.gateway.ecus:
            with self.subTest(ecu=ecu.name):
                self.assertEqual(ecu.state.session, uds_types.SESSION_EXTENDED)

    def test_broadcast_does_not_leak_responses(self):
        """即使广播的是会产生正响应的服务（22 读 DID），也不得回帧。"""
        self.feed(FUNCTIONAL_ADDRESS, bytes([0x22, 0xF1, 0x90]))
        self.assertEqual(self.transport.sent, [])

    def test_broadcast_suppressed_tester_present_is_silent(self):
        self.feed(FUNCTIONAL_ADDRESS, bytes([0x3E, 0x80]))
        self.assertEqual(self.transport.sent, [])

    def test_functional_request_counts_as_handled(self):
        """广播被"处理"了（返回 True），只是没有产生响应。"""
        self.transport.requests.append((FUNCTIONAL_ADDRESS, bytes([0x10, 0x03])))
        self.assertTrue(self.runtime.serve_once(timeout=0.0))


class TestUnknownAddressAndMalformedInput(RuntimeTestCase):
    def test_unknown_physical_id_gets_no_response(self):
        for can_id in (0x7E5, 0x7E3, 0x123, 0x7FF):
            with self.subTest(can_id=hex(can_id)):
                self.transport.sent.clear()
                self.feed(can_id, bytes([0x3E, 0x00]))
                self.assertEqual(self.transport.sent, [])

    def test_unknown_id_does_not_touch_any_ecu_state(self):
        self.feed(0x7E5, bytes([0x10, 0x03]))
        for ecu in self.runtime.gateway.ecus:
            with self.subTest(ecu=ecu.name):
                self.assertEqual(ecu.state.session, uds_types.SESSION_DEFAULT)

    def test_malformed_requests_are_answered_with_nrc(self):
        for payload, expected in MALFORMED_CASES:
            with self.subTest(payload=payload.hex(" ")):
                self.transport.sent.clear()
                self.feed(REQUEST_A, payload)
                self.assertEqual(self.transport.sent, [(RESPONSE_A, expected)])

    def test_malformed_requests_do_not_break_the_runtime(self):
        """连续畸形输入之后，正常请求仍必须被正确处理。"""
        for payload, _ in MALFORMED_CASES:
            self.feed(REQUEST_A, payload)

        self.transport.sent.clear()
        self.feed(REQUEST_A, bytes([0x22, 0xF1, 0x90]))
        self.assertEqual(self.transport.sent, [(RESPONSE_A, VIN_RESPONSE)])

    def test_binary_garbage_does_not_raise(self):
        """任意字节序列都不得让 runtime 抛异常（协议层已 fuzz 覆盖）。"""
        garbage = [bytes([b]) for b in range(256)] + [bytes(range(256))]

        for payload in garbage:
            with self.subTest(payload=payload[:4].hex(" ")):
                self.transport.sent.clear()
                self.feed(REQUEST_A, payload)


class TestStateIsolation(RuntimeTestCase):
    def _read_f102(self, request_id: int, response_id: int) -> bytes:
        self.transport.sent.clear()
        self.feed(request_id, bytes([0x22, 0xF1, 0x02]))
        self.assertEqual(len(self.transport.sent), 1)
        out_id, payload = self.transport.sent[0]
        self.assertEqual(out_id, response_id)
        return payload

    def test_write_to_one_ecu_does_not_affect_others(self):
        unlock_extended(self.ecu("ECU_A").state)

        self.feed(REQUEST_A, bytes([0x2E, 0xF1, 0x02]) + A_CONF)
        self.assertEqual(self.transport.sent, [(RESPONSE_A, bytes([0x6E, 0xF1, 0x02]))])

        # ECU_A 读回新值
        self.assertEqual(
            self._read_f102(REQUEST_A, RESPONSE_A),
            bytes([0x62, 0xF1, 0x02]) + A_CONF,
        )

        # ECU_B / ECU_C 仍是出厂默认值
        for request_id, response_id in ((REQUEST_B, RESPONSE_B), (REQUEST_C, RESPONSE_C)):
            with self.subTest(request_id=hex(request_id)):
                self.assertEqual(
                    self._read_f102(request_id, response_id),
                    bytes([0x62, 0xF1, 0x02]) + DEFAULT_CONFIG,
                )

    def test_unlock_and_write_stay_isolated_after_functional_broadcast(self):
        """
        功能寻址把三个 ECU 一起切到 extended，但**安全等级与写入仍彼此独立**。

        注意：`10 03` 会把 security_level 清零（本项目按 UDS 惯例在会话切换时
        重新锁定），因此解锁必须在广播**之后**单独对 ECU_A 进行 —— 这也正是
        "广播只共享会话、不共享安全状态"的证明。
        """
        self.feed(FUNCTIONAL_ADDRESS, bytes([0x10, 0x03]))

        for ecu in self.runtime.gateway.ecus:
            with self.subTest(ecu=ecu.name):
                self.assertEqual(ecu.state.session, uds_types.SESSION_EXTENDED)
                self.assertEqual(ecu.state.security_level, uds_types.SECURITY_LOCKED)

        # 只解锁并写入 ECU_A
        unlock_extended(self.ecu("ECU_A").state)
        self.feed(REQUEST_A, bytes([0x2E, 0xF1, 0x02]) + A_CONF)
        self.assertEqual(self.transport.sent, [(RESPONSE_A, bytes([0x6E, 0xF1, 0x02]))])

        self.assertEqual(
            self._read_f102(REQUEST_A, RESPONSE_A),
            bytes([0x62, 0xF1, 0x02]) + A_CONF,
        )

        # ECU_B 同样处于 extended，但**未解锁** → 0x33（而非写入成功）
        self.transport.sent.clear()
        self.feed(REQUEST_B, bytes([0x2E, 0xF1, 0x02]) + A_CONF)
        self.assertEqual(self.transport.sent, [(RESPONSE_B, bytes([0x7F, 0x2E, 0x33]))])

        # ECU_C 的 DID 未被污染
        self.assertEqual(
            self._read_f102(REQUEST_C, RESPONSE_C),
            bytes([0x62, 0xF1, 0x02]) + DEFAULT_CONFIG,
        )


# ----------------------------------------------------------------------
# 生命周期
# ----------------------------------------------------------------------


class TestLifecycle(RuntimeTestCase):
    def test_open_then_close_delegates_to_transport(self):
        self.runtime.open()
        self.assertTrue(self.transport.opened)

        self.runtime.close()
        self.assertTrue(self.transport.closed)

    def test_double_open_is_rejected(self):
        self.runtime.open()
        with self.assertRaises(RuntimeError):
            self.runtime.open()

    def test_close_is_idempotent(self):
        self.runtime.open()
        self.runtime.close()
        self.runtime.close()  # 不应抛异常

    def test_serve_once_returns_false_on_idle(self):
        self.assertFalse(self.runtime.serve_once(timeout=0.0))

    def test_serve_once_returns_true_after_handling(self):
        self.transport.requests.append((REQUEST_A, bytes([0x3E, 0x00])))
        self.assertTrue(self.runtime.serve_once(timeout=0.0))

    def test_stop_before_serve_returns_immediately(self):
        self.runtime.stop()
        self.runtime.serve_forever()  # 必须立即返回，不能阻塞

    def test_serve_forever_exits_on_stop_from_inside_loop(self):
        """在接收路径内部触发 stop()：循环必须退出（优雅退出语义）。"""
        self.transport.on_empty = self.runtime.stop
        self.runtime.serve_forever()

    def test_serve_forever_keeps_serving_until_stopped(self):
        for _ in range(3):
            self.transport.requests.append((REQUEST_A, bytes([0x3E, 0x00])))

        self.transport.on_empty = self.runtime.stop
        self.runtime.serve_forever()

        self.assertEqual(
            self.transport.sent,
            [(RESPONSE_A, bytes([0x7E, 0x00]))] * 3,
        )


# ----------------------------------------------------------------------
# 传输错误遏制
# ----------------------------------------------------------------------


class TestReceiveErrorContainment(RuntimeTestCase):
    def test_recoverable_errors_are_contained_and_daemon_continues(self):
        """EILSEQ / ECOMM / EMSGSIZE：丢弃当前传输，随后仍能正常服务。"""
        for errnum in sorted(ecu_module.RECOVERABLE_ISOTP_ERRNOS):
            with self.subTest(errnum=errnum):
                self.transport.recv_error = isotp_oserror(errnum)
                self.assertFalse(self.runtime.serve_once(timeout=0.0))

                # 错误之后仍必须能处理下一条请求
                self.transport.sent.clear()
                self.feed(REQUEST_A, bytes([0x3E, 0x00]))
                self.assertEqual(
                    self.transport.sent, [(RESPONSE_A, bytes([0x7E, 0x00]))]
                )

    def test_recoverable_error_is_reported_not_swallowed(self):
        self.transport.recv_error = isotp_oserror(errno.EILSEQ)
        self.runtime.serve_once(timeout=0.0)

        joined = "\n".join(self.logs)
        self.assertIn("EILSEQ", joined, "传输错误必须被报告，不能静默吞掉")
        self.assertIn("RX", joined)

    def test_timeout_is_not_an_error(self):
        """超时表示空闲，不是传输错误，也不得产生日志噪音。"""
        self.assertFalse(self.runtime.serve_once(timeout=0.0))
        self.assertEqual([line for line in self.logs if "error" in line.lower()], [])

    def test_non_recoverable_error_propagates(self):
        """接口被删 / socket 真损坏必须暴露，不得静默吞掉。"""
        for errnum in (errno.ENODEV, errno.EBADF, errno.EPERM):
            with self.subTest(errnum=errnum):
                self.transport.recv_error = isotp_oserror(errnum)
                with self.assertRaises(OSError):
                    self.runtime.serve_once(timeout=0.0)

    def test_non_recoverable_error_escapes_serve_forever(self):
        """致命错误必须终止服务循环，而不是变成忙等。"""
        self.transport.recv_error = isotp_oserror(errno.ENODEV)
        with self.assertRaises(OSError):
            self.runtime.serve_forever()


class TestSendErrorContainment(RuntimeTestCase):
    def test_recoverable_send_error_drops_response_but_keeps_running(self):
        for errnum in sorted(ecu_module.RECOVERABLE_ISOTP_ERRNOS):
            with self.subTest(errnum=errnum):
                self.transport.sent.clear()
                self.transport.send_error = isotp_oserror(errnum)

                # 不应抛异常，也不得留下半条响应
                self.feed(REQUEST_A, bytes([0x3E, 0x00]))
                self.assertEqual(self.transport.sent, [])

                # 恢复后仍能正常应答
                self.transport.send_error = None
                self.feed(REQUEST_A, bytes([0x3E, 0x00]))
                self.assertEqual(
                    self.transport.sent, [(RESPONSE_A, bytes([0x7E, 0x00]))]
                )

    def test_send_timeout_is_contained(self):
        """
        发送侧超时的含义是"本次响应没发出去"，不是"socket 坏了"。

        runtime 给 socket 设了超时（ecu.py 的默认配置没有），因此这条路径
        必须存在：单次响应失败不应终止整个守护进程。
        """
        self.transport.send_error = TimeoutError("timed out")

        self.feed(REQUEST_A, bytes([0x3E, 0x00]))

        self.assertEqual(self.transport.sent, [])
        self.assertIn("TX timeout", "\n".join(self.logs))

    def test_non_recoverable_send_error_propagates(self):
        self.transport.send_error = isotp_oserror(errno.ENODEV)

        with self.assertRaises(OSError):
            self.runtime.handle_request(REQUEST_A, bytes([0x3E, 0x00]))

    def test_unroutable_response_id_is_a_wiring_error(self):
        """
        应答地址不在网段内属于**装配错误**，必须暴露而不是静默丢弃。

        用 fake socket 让 transport 处于"已打开"状态，从而在不碰真实 CAN 的
        前提下验证 IsotpTransport.send() 的契约：查不到发送 socket → KeyError。
        """
        created: list = []
        transport = IsotpTransport("vcan0", [(REQUEST_A, RESPONSE_A)])

        with mock.patch.object(
            gateway_runtime.isotp, "socket", fake_socket_factory(created)
        ):
            transport.open()
            self.assertEqual(len(created), 2)  # 物理寻址 + 功能寻址

            with self.assertRaises(KeyError):
                transport.send(0x123, b"\x7E\x00")

            # 合法的应答地址必须能正常发出
            transport.send(RESPONSE_A, b"\x7E\x00")

            transport.close()

        self.assertEqual(created[0].sent, [b"\x7E\x00"])


class TestRecoverableErrnoPortability(unittest.TestCase):
    """
    ``_is_recoverable_isotp_error()`` 的跨平台契约。

    背景：``errno.ECOMM`` 并非所有平台都定义（Windows 的 ``errno`` 模块
    没有 ECOMM）。原实现直接引用 ``errno.ECOMM``，导致在 Windows 上
    调用该函数即抛 ``AttributeError``，把"一次传输坏了"误判成进程级崩溃。

    本组测试锁定三条不变量：

    1. **平台未定义的错误码不得被硬引用** —— 在任何平台上调用都不抛异常。
    2. **``None`` / 占位值不得误匹配** —— ``OSError.errno`` 合法地可以是
       ``None``，它必须被判为"不可恢复"，绝不能与任何有效错误码相等。
    3. **既有可恢复语义不得改变** —— 平台定义了的 EILSEQ / EMSGSIZE
       （以及 Linux 上的 ECOMM）仍是可恢复；ENODEV / EBADF / EPERM
       仍不可恢复。
    """

    def test_recoverable_set_is_a_set_of_int_codes(self):
        """可恢复错误码集合必须是 int 集合，且不含 None / 占位值。"""
        codes = ecu_module.RECOVERABLE_ISOTP_ERRNOS

        self.assertNotIn(None, codes, "None 绝不能作为占位值进入错误码集合")
        for code in codes:
            self.assertIsInstance(code, int)

    def test_platform_defined_codes_are_included(self):
        """平台定义了的传输错误码必须全部纳入（EILSEQ / EMSGSIZE 必定存在）。"""
        self.assertIn(errno.EILSEQ, ecu_module.RECOVERABLE_ISOTP_ERRNOS)
        self.assertIn(errno.EMSGSIZE, ecu_module.RECOVERABLE_ISOTP_ERRNOS)

    def test_ecomm_included_only_when_platform_defines_it(self):
        """ECOMM 存在时必须纳入；不存在时必须被安全跳过（而非崩溃）。"""
        has_ecomm = hasattr(errno, "ECOMM")
        in_set = hasattr(errno, "ECOMM") and errno.ECOMM in ecu_module.RECOVERABLE_ISOTP_ERRNOS

        if has_ecomm:
            self.assertTrue(in_set, "平台定义了 ECOMM，它必须仍被判定为可恢复")
        else:
            self.assertFalse(
                in_set, "平台未定义 ECOMM 时，不得有占位值顶替它进入集合"
            )

    def test_none_errno_is_not_recoverable(self):
        """``OSError.errno is None`` 合法存在，必须判为不可恢复。"""
        exc = OSError(None, "no errno information")

        self.assertIsNone(exc.errno)
        self.assertFalse(ecu_module._is_recoverable_isotp_error(exc))

    def test_call_does_not_raise_on_any_platform(self):
        """核心回归：平台缺少 ECOMM 时，调用不得抛 AttributeError。"""
        exc = OSError(errno.EILSEQ, "sequence mismatch")

        # 若实现硬引用 errno.ECOMM，Windows 上这里会抛 AttributeError
        self.assertTrue(ecu_module._is_recoverable_isotp_error(exc))

    def test_non_recoverable_errnos_remain_non_recoverable(self):
        """接口删除 / socket 损坏等致命错误绝不能被误判为可恢复。"""
        for errnum in (errno.ENODEV, errno.EBADF, errno.EPERM):
            with self.subTest(errnum=errnum):
                exc = OSError(errnum, os.strerror(errnum))
                self.assertFalse(ecu_module._is_recoverable_isotp_error(exc))

    def test_every_platform_defined_recoverable_code_round_trips(self):
        """集合中的每个错误码都必须真的被判为可恢复（无死条目）。"""
        for errnum in sorted(ecu_module.RECOVERABLE_ISOTP_ERRNOS):
            with self.subTest(errnum=errnum):
                exc = OSError(errnum, os.strerror(errnum))
                self.assertTrue(ecu_module._is_recoverable_isotp_error(exc))


class TestRuntimeLogging(RuntimeTestCase):
    def test_request_and_response_are_logged(self):
        self.feed(REQUEST_A, bytes([0x22, 0xF1, 0x90]))

        joined = "\n".join(self.logs)
        self.assertIn("RX 0x7E0", joined)
        self.assertIn("TX 0x7E8", joined)

    def test_silent_paths_are_not_logged_as_traffic(self):
        """未知 ID / 功能寻址不应产生 TX 日志（没有帧被发出）。"""
        self.feed(0x7E5, bytes([0x3E, 0x00]))
        self.feed(FUNCTIONAL_ADDRESS, bytes([0x10, 0x03]))

        self.assertEqual([line for line in self.logs if line.startswith("TX")], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
