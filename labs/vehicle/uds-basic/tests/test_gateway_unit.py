#!/usr/bin/env python3
"""
UDS Basic —— 多 ECU 诊断网关（gateway.py）离线单元测试。

纯协议层测试：不启动 ECU 进程、不依赖 vcan0 / SocketCAN / ISO-TP，
验证 DiagnosticGateway 的物理寻址单播、0x7DF 功能寻址广播、
以及多 ECU 之间的状态隔离。

运行：

    python3 tests/test_gateway_unit.py
或：
    bash tests/run_tests.sh
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import uds_server  # noqa: E402
import uds_types  # noqa: E402
from gateway import (  # noqa: E402
    FUNCTIONAL_ADDRESS,
    DiagnosticGateway,
    VirtualEcu,
    build_default_gateway,
)
from uds_server import EcuState  # noqa: E402


def unlock_extended(state: EcuState) -> None:
    state.session = uds_types.SESSION_EXTENDED
    state.security_level = uds_types.SECURITY_LEVEL1


class GatewayTopologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gw = build_default_gateway()

    def test_default_topology_has_three_ecus(self):
        self.assertEqual([e.name for e in self.gw.ecus], ["ECU_A", "ECU_B", "ECU_C"])
        self.assertEqual(
            [(e.request_id, e.response_id) for e in self.gw.ecus],
            [(0x7E0, 0x7E8), (0x7E1, 0x7E9), (0x7E2, 0x7EA)],
        )

    def test_physical_routing_uses_each_response_id(self):
        # 22 F1 90：F190 是公开 DID，默认会话即可读
        for req_id, resp_id in ((0x7E0, 0x7E8), (0x7E1, 0x7E9), (0x7E2, 0x7EA)):
            with self.subTest(req_id=hex(req_id)):
                results = self.gw.route(req_id, bytes([0x22, 0xF1, 0x90]))
                self.assertEqual(len(results), 1)
                out_id, payload = results[0]
                self.assertEqual(out_id, resp_id)
                self.assertEqual(payload[0], 0x62)
                self.assertEqual((payload[1], payload[2]), (0xF1, 0x90))

    def test_unknown_physical_address_has_no_route(self):
        self.assertEqual(self.gw.route(0x7E5, bytes([0x3E, 0x00])), [])

    def test_physical_tester_present_positive(self):
        self.assertEqual(
            self.gw.route(0x7E1, bytes([0x3E, 0x00])),
            [(0x7E9, bytes([0x7E, 0x00]))],
        )

    def test_physical_suppress_bit_produces_no_frame(self):
        self.assertEqual(self.gw.route(0x7E0, bytes([0x3E, 0x80])), [])

    def test_physical_negative_response_is_routed(self):
        # 默认会话写 DID -> 7F 2E 7F，且回应 ID 正确
        self.assertEqual(
            self.gw.route(0x7E0, bytes([0x2E, 0xF1, 0x02]) + b"X"),
            [(0x7E8, bytes([0x7F, 0x2E, 0x7F]))],
        )


class GatewayStateIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gw = build_default_gateway()

    def test_write_to_one_ecu_does_not_affect_others(self):
        ecu_a = self.gw.ecu_by_name("ECU_A")
        unlock_extended(ecu_a.state)

        # 只对 ECU_A 写入
        self.assertEqual(
            self.gw.route(0x7E0, bytes([0x2E, 0xF1, 0x02]) + b"A_CONF"),
            [(0x7E8, bytes([0x6E, 0xF1, 0x02]))],
        )

        # ECU_A 读回新值
        _, a_payload = self.gw.route(0x7E0, bytes([0x22, 0xF1, 0x02]))[0]
        self.assertEqual(a_payload, bytes([0x62, 0xF1, 0x02]) + b"A_CONF")

        # ECU_B / ECU_C 仍是出厂默认值（状态隔离）
        for req_id in (0x7E1, 0x7E2):
            with self.subTest(req_id=hex(req_id)):
                _, payload = self.gw.route(req_id, bytes([0x22, 0xF1, 0x02]))[0]
                self.assertEqual(
                    payload, bytes([0x62, 0xF1, 0x02]) + b"DEFAULT_CONFIG"
                )

    def test_functional_broadcast_changes_all_sessions_but_returns_nothing(self):
        # 功能寻址 10 03：所有 ECU 切到 extended，但不收集响应
        self.assertEqual(self.gw.route(FUNCTIONAL_ADDRESS, bytes([0x10, 0x03])), [])

        for ecu in self.gw.ecus:
            with self.subTest(ecu=ecu.name):
                self.assertEqual(ecu.state.session, uds_types.SESSION_EXTENDED)

    def test_functional_tester_present_suppressed_is_silent(self):
        # 广播 3E 80：每个 ECU 都返回 b""，网关对外仍静默
        self.assertEqual(self.gw.route(FUNCTIONAL_ADDRESS, bytes([0x3E, 0x80])), [])

    def test_functional_broadcast_does_not_leak_responses(self):
        # 即使广播的是会产生正响应的服务（22 读 DID），功能寻址也不回帧，
        # 以避免多个 ECU 同时驱动总线造成仲裁冲突。
        results = self.gw.route(FUNCTIONAL_ADDRESS, bytes([0x22, 0xF1, 0x90]))
        self.assertEqual(results, [])


class GatewayConstructionTests(unittest.TestCase):
    def test_empty_gateway_rejected(self):
        with self.assertRaises(ValueError):
            DiagnosticGateway([])

    def test_duplicate_request_id_rejected(self):
        ecus = [
            VirtualEcu("A", 0x7E0, 0x7E8),
            VirtualEcu("B", 0x7E0, 0x7E9),
        ]
        with self.assertRaises(ValueError):
            DiagnosticGateway(ecus)

    def test_custom_handler_is_used(self):
        # 注入自定义 handler，验证网关按 ECU 分发到正确 handler
        ecu = VirtualEcu(
            "X",
            0x700,
            0x708,
            handler=lambda payload: bytes([0x77]) + payload,
        )
        gw = DiagnosticGateway([ecu])
        self.assertEqual(gw.route(0x700, b"\x01\x02"), [(0x708, b"\x77\x01\x02")])


class GatewayProviderHandlerPrecedenceTests(unittest.TestCase):
    """
    C3-DEFECT-1 —— ``services_provider`` 不得覆盖 ECU 自带的 **custom handler**。

    ## 缺陷（修复前）

    网关级 ``services_provider`` 会把**用户显式注入**的 custom handler
    静默替换为 provider-bound default handler：

        ecu = VirtualEcu("X", 0x700, 0x708, handler=custom)
        gw  = DiagnosticGateway([ecu], services_provider=provider)
        gw.ecus[0].handler is custom   →  False   ← 缺陷

    根因：接线判据 ``handler is not None`` 无法区分
    "framework 自动绑定的 default handler" 与 "用户显式提供的 custom handler"。

    ## 正确语义（两种来源严格区分）

        framework 自动绑定的 default handler  → 网关级 provider **可以**重绑
        用户显式注入的 custom handler         → **任何情况下**不得替换

    判定用 **handler 身份**（``VirtualEcu.has_custom_handler``），
    而不是可能过期的 boolean。
    """

    #: 测试探针 payload（显式字节构造，避免转义字面量）。
    PROBE = bytes([0x01, 0x02])

    SECURITY_CONFIG = {
        "max_attempts": 3,
        "lockout_seconds": 10,
        "seed_expiry_seconds": 5,
    }

    @classmethod
    def _services(cls):
        """测试侧 provider：走 C3 的**唯一**绑定定义。"""
        return uds_server.security_access_services(
            now=1000.0, security_config=cls.SECURITY_CONFIG
        )

    @staticmethod
    def _state_of(ecu):
        return (
            ecu.state.session,
            ecu.state.security_level,
            dict(ecu.state.did_overrides),
        )

    # ---- Test 1：custom handler + 网关级 provider ⇒ 保留 ----

    def test_gateway_provider_preserves_a_custom_handler(self):
        calls = []

        def custom(payload):
            calls.append(payload)
            return bytes([0x99]) + payload

        ecu = VirtualEcu("X", 0x700, 0x708, handler=custom)
        gw = DiagnosticGateway([ecu], services_provider=self._services)

        self.assertIs(
            gw.ecus[0].handler, custom,
            "网关级 provider 不得替换用户显式注入的 custom handler（C3-DEFECT-1）",
        )
        self.assertTrue(gw.ecus[0].has_custom_handler, "应被判定为 custom handler")
        self.assertEqual(gw.route(0x700, self.PROBE), [(0x708, bytes([0x99]) + self.PROBE)])
        self.assertEqual(calls, [self.PROBE], "custom handler 必须被调用")

    def test_construct_time_provider_keeps_the_custom_handler(self):
        """构造期同时给出 handler 与 provider —— 该形式修复前后均正确（反向对照）。"""

        def custom(payload):
            return bytes([0x99]) + payload

        ecu = VirtualEcu(
            "X", 0x700, 0x708, handler=custom, services_provider=self._services
        )
        gw = DiagnosticGateway([ecu])

        self.assertIs(gw.ecus[0].handler, custom)
        self.assertEqual(gw.route(0x700, self.PROBE), [(0x708, bytes([0x99]) + self.PROBE)])

    # ---- Test 2：default handler + 网关级 provider ⇒ 正常接线 ----

    def test_gateway_provider_rebinds_the_default_handler(self):
        gw = build_default_gateway(services_provider=self._services)
        ecu = gw.ecu_by_name("ECU_A")

        self.assertFalse(
            ecu.has_custom_handler,
            "framework 自动绑定的 handler 不得被误判为 custom（否则 provider 永不生效）",
        )
        self.assertIs(ecu.handler, ecu.default_handler)

        results = gw.route(0x7E0, bytes([0x27, 0x01]))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 0x7E8)
        self.assertEqual(results[0][1][:2], bytes([0x67, 0x01]), "provider 应当已接线 0x27")

    def test_unwired_gateway_still_does_not_provide_security_access(self):
        """反向对照：不接线时 0x27 仍不可达（机制与接线分离，C3-OD-7）。"""
        self.assertEqual(
            build_default_gateway().route(0x7E0, bytes([0x27, 0x01])),
            [(0x7E8, bytes([0x7F, 0x27, 0x11]))],
        )

    def test_rebinding_is_idempotent_across_two_wirings(self):
        """重绑后 ``default_handler`` 必须同步更新，使第二次接线仍被正确识别。"""
        ecu = VirtualEcu("X", 0x7E0, 0x7E8)
        first = DiagnosticGateway([ecu], services_provider=self._services)
        self.assertFalse(ecu.has_custom_handler, "第一次接线后仍是 framework handler")

        second = DiagnosticGateway([ecu], services_provider=self._services)
        self.assertIsNotNone(second)
        self.assertFalse(ecu.has_custom_handler)
        self.assertEqual(
            first.route(0x7E0, bytes([0x27, 0x01]))[0][1][:2], bytes([0x67, 0x01])
        )

    # ---- Test 3：custom + 网关级 provider + policy deny ⇒ 仍被拦下 ----

    def test_policy_deny_still_blocks_a_custom_handler(self):
        """
        修复**不得**顺带改变 C2 policy boundary：
        custom handler 能被保留 ≠ 能绕过 policy。
        """
        calls = []

        def custom(payload):
            calls.append(payload)
            return bytes([0x99]) + payload

        def deny_all(ecu, addressing_mode, sid):
            return False

        ecu = VirtualEcu("X", 0x700, 0x708, handler=custom)
        gw = DiagnosticGateway(
            [ecu], policy=deny_all, services_provider=self._services
        )

        process_calls = []
        original_process = ecu.process

        def spy(payload):
            process_calls.append(payload)
            return original_process(payload)

        ecu.process = spy
        before = self._state_of(ecu)

        results = gw.route(0x700, bytes([0x27, 0x01]))

        self.assertEqual(results, [], "DENY ⇒ wire 为空")
        self.assertEqual(calls, [], "DENY ⇒ custom handler 不得被调用")
        self.assertEqual(process_calls, [], "DENY ⇒ target.process() 不得被调用")
        self.assertEqual(self._state_of(ecu), before, "DENY ⇒ state 不变")
        self.assertIs(gw.ecus[0].handler, custom, "DENY 下 handler 仍应保留 custom")

    def test_policy_decision_is_still_recorded_for_a_custom_handler(self):
        """trace 证据：deny 的 decision 仍然如实产生。"""
        def custom(payload):
            return bytes([0x99])

        def deny_all(ecu, addressing_mode, sid):
            return False

        gw = DiagnosticGateway(
            [VirtualEcu("X", 0x700, 0x708, handler=custom)],
            policy=deny_all,
            services_provider=self._services,
        )
        _results, trace = gw.route_with_trace(0x700, bytes([0x27, 0x01]))

        self.assertEqual(len(trace.decisions), 1)
        self.assertEqual(trace.decisions[0].target, 0x700)
        self.assertEqual(trace.decisions[0].sid, 0x27)
        self.assertFalse(trace.decisions[0].allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
