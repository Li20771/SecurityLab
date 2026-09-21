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


if __name__ == "__main__":
    unittest.main(verbosity=2)
