#!/usr/bin/env python3
"""
UDS Basic —— C2-A1：空策略 + per-call PolicyTrace 机制

设计出处：``docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md``
（C2-OD-1 … C2-OD-9 均已 DESIGN FROZEN）。

## 本阶段的唯一主张

> 引入"**空策略 + per-call PolicyTrace**"机制，并在空策略 / default-allow 下
> 证明 Gateway 的 **wire behavior 与 state behavior 不发生变化**。

**不做** C2-A2 的完整 Golden 对拍 —— 那属下一阶段。

## 覆盖点

   1  empty policy 下 physical positive 仍正常
   2  physical negative 仍转发
   3  suppress 仍 []
   4  unknown target 仍 [] 且 trace 无 decision
   5  empty payload 仍交给 handler 且 trace 无 decision
   6  physical 非空 payload ⇒ 1 条 decision
   7  functional 非空 payload ⇒ 每个 ECU 1 条 decision
   8  functional trace 顺序稳定（== self.ecus 遍历顺序）
   9  default-allow ⇒ 所有 decision.allowed == True
  10  route() 与 route_with_trace() 的 response / state 一致
  11  deny 时 target.process() 不被调用
  12  custom handler 不能绕过 policy enforcement point

运行：

    python3 tests/test_c2_a1_empty_policy_trace.py
或：
    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import uds_types  # noqa: E402
from gateway import (  # noqa: E402
    ADDRESSING_FUNCTIONAL,
    ADDRESSING_PHYSICAL,
    DEFAULT_POLICY,
    FUNCTIONAL_ADDRESS,
    DiagnosticGateway,
    PolicyDecision,
    PolicyTrace,
    VirtualEcu,
    build_default_gateway,
)

SID_READ = 0x22
SID_WRITE = 0x2E
SID_TESTER_PRESENT = 0x3E

UNKNOWN_ADDRESS = 0x7E5

ECU_A_REQ, ECU_A_RESP = 0x7E0, 0x7E8


# ----------------------------------------------------------------------
# 测试用策略（**仅测试注入**；A1 的生产默认是 DEFAULT_POLICY）
# ----------------------------------------------------------------------


def deny_all(ecu, addressing_mode, sid):
    """拒绝一切的策略 —— 用于证明 deny 语义（C2-OD-3）。"""
    return False


def deny_sid(denied_sid):
    """只拒绝指定 SID 的策略。"""

    def policy(ecu, addressing_mode, sid):
        return sid != denied_sid

    return policy


def deny_target(denied_request_id):
    """只拒绝指定目标 ECU 的策略（按 request_id 判定）。"""

    def policy(ecu, addressing_mode, sid):
        return ecu.request_id != denied_request_id

    return policy


def gateway_snapshot(gw):
    """逐 ECU 三字段状态投影（与 A0 Golden 同口径）。"""
    return {
        ecu.name: (
            ecu.state.session,
            ecu.state.security_level,
            tuple(sorted(ecu.state.did_overrides.items())),
        )
        for ecu in gw.ecus
    }


class TestEmptyPolicyIsANoOp(unittest.TestCase):
    """覆盖点 1 / 2 / 3 / 9 —— 空策略与"机制引入前"逐字节相同。"""

    def setUp(self):
        self.gw = build_default_gateway()

    def test_default_policy_is_the_empty_policy(self):
        self.assertIs(self.gw.policy, DEFAULT_POLICY)

    # ---- 1 physical positive ----
    def test_physical_positive_still_works(self):
        self.assertEqual(
            self.gw.route(ECU_A_REQ, bytes([SID_READ, 0xF1, 0x90])),
            [(ECU_A_RESP, bytes([0x62, 0xF1, 0x90]) + b"TESTECU0000000001")],
        )

    def test_physical_positive_on_every_ecu(self):
        expected = {0x7E0: 0x7E8, 0x7E1: 0x7E9, 0x7E2: 0x7EA}
        for request_id, response_id in expected.items():
            with self.subTest(request_id=hex(request_id)):
                results = self.gw.route(request_id, bytes([SID_READ, 0xF1, 0x90]))
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0][0], response_id)

    # ---- 2 physical negative 仍转发 ----
    def test_physical_negative_response_still_forwarded(self):
        """``22 FF FF`` → ``7F 22 31``：ECU 拒绝被**转发**，不是 Gateway deny。"""
        self.assertEqual(
            self.gw.route(ECU_A_REQ, bytes([SID_READ, 0xFF, 0xFF])),
            [(ECU_A_RESP, bytes([0x7F, SID_READ, 0x31]))],
        )

    def test_protected_did_negative_response_still_forwarded(self):
        self.assertEqual(
            self.gw.route(ECU_A_REQ, bytes([SID_READ, 0xF1, 0x87])),
            [(ECU_A_RESP, bytes([0x7F, SID_READ, 0x33]))],
        )

    # ---- 3 suppress ----
    def test_suppress_still_produces_no_frame(self):
        self.assertEqual(
            self.gw.route(ECU_A_REQ, bytes([SID_TESTER_PRESENT, 0x80])), []
        )

    # ---- 9 default-allow ----
    def test_all_decisions_are_allowed_under_empty_policy(self):
        cases = [
            (ECU_A_REQ, bytes([SID_READ, 0xF1, 0x90])),
            (ECU_A_REQ, bytes([SID_READ, 0xFF, 0xFF])),
            (ECU_A_REQ, bytes([SID_TESTER_PRESENT, 0x80])),
            (FUNCTIONAL_ADDRESS, bytes([0x10, 0x03])),
        ]
        for can_id, payload in cases:
            with self.subTest(can_id=hex(can_id), payload=payload.hex(" ")):
                gw = build_default_gateway()
                _results, trace = gw.route_with_trace(can_id, payload)
                self.assertTrue(trace.decisions, "这些请求都应进入策略求值")
                for decision in trace.decisions:
                    self.assertTrue(decision.allowed)


class TestPolicySkipRules(unittest.TestCase):
    """覆盖点 4 / 5 —— C2-OD-8：只有两种情形**不进入**策略。"""

    def setUp(self):
        self.gw = build_default_gateway()

    # ---- 4 unknown physical target ----
    def test_unknown_target_returns_empty_and_records_no_decision(self):
        results, trace = self.gw.route_with_trace(
            UNKNOWN_ADDRESS, bytes([SID_TESTER_PRESENT, 0x00])
        )
        self.assertEqual(results, [])
        self.assertEqual(
            trace.decisions,
            (),
            "未知物理 target 是**路由归属失败**，不是 deny —— 不得产生 decision",
        )

    def test_unknown_target_is_not_recorded_even_with_deny_policy(self):
        """
        即使注入"拒绝一切"的策略，未知 target 仍**不产生 decision** ——
        因为它在策略**之前**就被路由归属判定筛掉了。
        """
        gw = DiagnosticGateway(
            [VirtualEcu("ECU_A", ECU_A_REQ, ECU_A_RESP)], policy=deny_all
        )
        results, trace = gw.route_with_trace(UNKNOWN_ADDRESS, bytes([SID_READ]))
        self.assertEqual(results, [])
        self.assertEqual(trace.decisions, ())

    # ---- 5 empty payload ----
    def test_empty_payload_is_handed_to_handler_without_decision(self):
        results, trace = self.gw.route_with_trace(ECU_A_REQ, b"")
        self.assertEqual(results, [(ECU_A_RESP, bytes([0x7F, 0x00, 0x13]))])
        self.assertEqual(
            trace.decisions, (), "空 payload 结构上取不到 SID ⇒ 不进入策略"
        )

    def test_empty_payload_functional_still_fans_out_without_decision(self):
        """空 payload 的功能寻址仍扇出（行为不变），但**不产生** decision。"""
        gw = build_default_gateway()
        results, trace = gw.route_with_trace(FUNCTIONAL_ADDRESS, b"")
        self.assertEqual(results, [])
        self.assertEqual(trace.decisions, ())


class TestTraceShape(unittest.TestCase):
    """覆盖点 6 / 7 / 8 —— trace 的形状与顺序（C2-OD-4 / C2-OD-5 / §9.2.5）。"""

    def setUp(self):
        self.gw = build_default_gateway()

    # ---- 6 physical ⇒ 1 条 ----
    def test_physical_non_empty_payload_records_exactly_one_decision(self):
        _results, trace = self.gw.route_with_trace(
            ECU_A_REQ, bytes([SID_READ, 0xF1, 0x90])
        )
        self.assertEqual(len(trace.decisions), 1)

    def test_physical_decision_fields(self):
        _results, trace = self.gw.route_with_trace(
            ECU_A_REQ, bytes([SID_READ, 0xF1, 0x90])
        )
        self.assertEqual(
            trace.decisions[0],
            PolicyDecision(
                target=ECU_A_REQ,
                addressing_mode=ADDRESSING_PHYSICAL,
                sid=SID_READ,
                allowed=True,
            ),
        )

    def test_decision_target_is_the_request_id(self):
        """``target`` 必须是稳定的 ``request_id``，不是对象身份。"""
        for request_id in (0x7E0, 0x7E1, 0x7E2):
            with self.subTest(request_id=hex(request_id)):
                gw = build_default_gateway()
                _r, trace = gw.route_with_trace(request_id, bytes([SID_READ]))
                self.assertEqual(trace.decisions[0].target, request_id)

    def test_addressing_mode_is_a_semantic_string(self):
        _r, physical = self.gw.route_with_trace(ECU_A_REQ, bytes([SID_READ]))
        _r, functional = self.gw.route_with_trace(
            FUNCTIONAL_ADDRESS, bytes([SID_READ])
        )
        self.assertEqual(physical.decisions[0].addressing_mode, ADDRESSING_PHYSICAL)
        self.assertEqual(functional.decisions[0].addressing_mode, ADDRESSING_FUNCTIONAL)

    # ---- 7 functional ⇒ 每个 ECU 1 条 ----
    def test_functional_records_one_decision_per_ecu(self):
        gw = build_default_gateway()
        _results, trace = gw.route_with_trace(FUNCTIONAL_ADDRESS, bytes([SID_READ]))
        self.assertEqual(len(trace.decisions), len(gw.ecus))
        self.assertEqual(len(trace.decisions), 3)

    # ---- 8 顺序稳定 ----
    def test_functional_decisions_follow_ecus_traversal_order(self):
        gw = build_default_gateway()
        _results, trace = gw.route_with_trace(FUNCTIONAL_ADDRESS, bytes([SID_READ]))
        self.assertEqual(
            [d.target for d in trace.decisions],
            [ecu.request_id for ecu in gw.ecus],
            "functional decision 顺序必须等于 self.ecus 的确定性遍历顺序",
        )

    def test_functional_order_is_stable_across_calls(self):
        orders = []
        for _ in range(3):
            gw = build_default_gateway()
            _r, trace = gw.route_with_trace(FUNCTIONAL_ADDRESS, bytes([SID_READ]))
            orders.append([d.target for d in trace.decisions])
        self.assertEqual(len({tuple(o) for o in orders}), 1, "顺序必须可重复")

    def test_trace_is_immutable(self):
        _r, trace = self.gw.route_with_trace(ECU_A_REQ, bytes([SID_READ]))
        self.assertIsInstance(trace, PolicyTrace)
        self.assertIsInstance(trace.decisions, tuple)

    def test_trace_is_per_call_and_not_shared(self):
        """per-call：两次调用的 trace 互不污染（无 global 状态）。"""
        _r, first = self.gw.route_with_trace(ECU_A_REQ, bytes([SID_READ]))
        _r, second = self.gw.route_with_trace(FUNCTIONAL_ADDRESS, bytes([SID_READ]))
        self.assertEqual(len(first.decisions), 1)
        self.assertEqual(len(second.decisions), 3)
        self.assertIsNot(first, second)

    def test_trace_does_not_accumulate_across_calls(self):
        sizes = []
        for _ in range(4):
            _r, trace = self.gw.route_with_trace(ECU_A_REQ, bytes([SID_READ]))
            sizes.append(len(trace.decisions))
        self.assertEqual(sizes, [1, 1, 1, 1], "trace 不得跨调用累积")


class TestRouteAndTraceAgree(unittest.TestCase):
    """覆盖点 10 —— ``route()`` 与 ``route_with_trace()`` 共用同一条 core。"""

    CASES = [
        (ECU_A_REQ, bytes([SID_READ, 0xF1, 0x90])),
        (ECU_A_REQ, bytes([SID_READ, 0xFF, 0xFF])),
        (ECU_A_REQ, bytes([SID_READ, 0xF1, 0x87])),
        (ECU_A_REQ, bytes([SID_TESTER_PRESENT, 0x80])),
        (ECU_A_REQ, bytes([SID_TESTER_PRESENT, 0x00])),
        (ECU_A_REQ, bytes([SID_WRITE, 0xF1, 0x02]) + b"X"),
        (ECU_A_REQ, b""),
        (UNKNOWN_ADDRESS, bytes([SID_TESTER_PRESENT, 0x00])),
        (FUNCTIONAL_ADDRESS, bytes([0x10, 0x03])),
        (FUNCTIONAL_ADDRESS, bytes([SID_READ, 0xF1, 0x90])),
        (FUNCTIONAL_ADDRESS, bytes([SID_TESTER_PRESENT, 0x80])),
        (0x7E1, bytes([SID_READ, 0xF1, 0x90])),
        (0x7E2, bytes([SID_READ, 0xFF, 0xFF])),
    ]

    def test_response_and_state_are_identical(self):
        for can_id, payload in self.CASES:
            with self.subTest(can_id=hex(can_id), payload=payload.hex(" ")):
                gw_plain = build_default_gateway()
                plain_results = gw_plain.route(can_id, payload)
                plain_state = gateway_snapshot(gw_plain)

                gw_trace = build_default_gateway()
                trace_results, _trace = gw_trace.route_with_trace(can_id, payload)
                trace_state = gateway_snapshot(gw_trace)

                self.assertEqual(plain_results, trace_results)
                self.assertEqual(plain_state, trace_state)

    def test_route_still_returns_plain_list(self):
        """C2-OD-9：``route()`` 的返回类型**不变**（仍为 ``list``）。"""
        results = build_default_gateway().route(ECU_A_REQ, bytes([SID_READ]))
        self.assertIsInstance(results, list)

    def test_route_also_evaluates_policy(self):
        """
        ``route()`` **同样**求值策略（否则就成了两套语义）。

        用一个拒绝策略 + 自定义 handler 证明：``route()`` 路径下 handler
        同样不被调用。
        """
        calls = []

        def recording_handler(payload: bytes) -> bytes:
            calls.append(payload)
            return b"\x77"

        gw = DiagnosticGateway(
            [VirtualEcu("ECU_A", ECU_A_REQ, ECU_A_RESP, handler=recording_handler)],
            policy=deny_all,
        )
        self.assertEqual(gw.route(ECU_A_REQ, bytes([SID_READ])), [])
        self.assertEqual(calls, [], "route() 路径下被拒请求同样不得到达 handler")


class TestDenySemantics(unittest.TestCase):
    """覆盖点 11 / 12 —— C2-OD-3 的 deny 语义与 override 不变量（C2-OD-6）。"""

    def _recording_gateway(self, policy):
        calls = []

        def recording_handler(payload: bytes) -> bytes:
            calls.append(payload)
            return b"\x77"

        gw = DiagnosticGateway(
            [VirtualEcu("ECU_A", ECU_A_REQ, ECU_A_RESP, handler=recording_handler)],
            policy=policy,
        )
        return gw, calls

    # ---- 11 deny 时 process() 不被调用 ----
    def test_deny_does_not_reach_the_target_ecu(self):
        gw, calls = self._recording_gateway(deny_all)
        results, trace = gw.route_with_trace(ECU_A_REQ, bytes([SID_READ]))

        self.assertEqual(results, [], "deny ⇒ 不产生任何响应")
        self.assertEqual(calls, [], "deny ⇒ target.process() **不得被调用**")
        self.assertEqual(len(trace.decisions), 1)
        self.assertFalse(trace.decisions[0].allowed)

    def test_deny_is_silent_on_the_wire(self):
        """C2-OD-3：不产生帧、不合成 7F、不生成 NRC。"""
        gw, _calls = self._recording_gateway(deny_all)
        self.assertEqual(gw.route(ECU_A_REQ, bytes([SID_READ])), [])

    def test_deny_leaves_ecu_state_untouched(self):
        gw, _calls = self._recording_gateway(deny_all)
        gw.route(ECU_A_REQ, bytes([SID_WRITE, 0xF1, 0x02]) + b"X")
        self.assertEqual(gw.ecus[0].state.did_overrides, {})

    def test_deny_decision_records_the_real_reason_fields(self):
        gw, _calls = self._recording_gateway(deny_all)
        _r, trace = gw.route_with_trace(ECU_A_REQ, bytes([SID_WRITE, 0xF1, 0x02]))
        self.assertEqual(
            trace.decisions[0],
            PolicyDecision(
                target=ECU_A_REQ,
                addressing_mode=ADDRESSING_PHYSICAL,
                sid=SID_WRITE,
                allowed=False,
            ),
        )

    # ---- 12 custom handler 不能绕过 enforcement point ----
    def test_custom_handler_cannot_bypass_policy(self):
        gw, calls = self._recording_gateway(deny_all)
        _r, trace = gw.route_with_trace(ECU_A_REQ, b"\x01\x02")
        self.assertEqual(calls, [], "策略求值点在 handler **之外** ⇒ 不可被绕过")
        self.assertEqual(len(trace.decisions), 1)
        self.assertFalse(trace.decisions[0].allowed)

    def test_custom_handler_still_works_when_policy_allows(self):
        """反向对照：策略放行时自定义 handler 照常执行。"""
        gw, calls = self._recording_gateway(DEFAULT_POLICY)
        self.assertEqual(gw.route(ECU_A_REQ, b"\x01\x02"), [(ECU_A_RESP, b"\x77")])
        self.assertEqual(calls, [b"\x01\x02"])

    # ---- functional 下的逐 ECU deny（C2-OD-4 / OD-5）----

    def test_functional_deny_is_evaluated_per_ecu(self):
        """
        只拒绝 ECU_B ⇒ 广播下 A / C 仍执行、B 不执行；三条 decision 各按实际结果。
        """
        calls = []

        def make_handler(name):
            def handler(payload: bytes) -> bytes:
                calls.append(name)
                return b"\x77"

            return handler

        gw = DiagnosticGateway(
            [
                VirtualEcu("ECU_A", 0x7E0, 0x7E8, handler=make_handler("A")),
                VirtualEcu("ECU_B", 0x7E1, 0x7E9, handler=make_handler("B")),
                VirtualEcu("ECU_C", 0x7E2, 0x7EA, handler=make_handler("C")),
            ],
            policy=deny_target(0x7E1),
        )
        results, trace = gw.route_with_trace(FUNCTIONAL_ADDRESS, bytes([SID_READ]))

        self.assertEqual(results, [], "功能寻址恒不收集响应")
        self.assertEqual(calls, ["A", "C"], "被拒的 ECU_B 不得执行")
        self.assertEqual(
            [(d.target, d.allowed) for d in trace.decisions],
            [(0x7E0, True), (0x7E1, False), (0x7E2, True)],
            "decision 必须逐 ECU 记录真实结果",
        )

    def test_sid_scoped_policy_only_affects_that_sid(self):
        gw, calls = self._recording_gateway(deny_sid(SID_WRITE))
        self.assertEqual(gw.route(ECU_A_REQ, bytes([SID_WRITE, 0xF1, 0x02])), [])
        self.assertEqual(calls, [])
        self.assertEqual(gw.route(ECU_A_REQ, bytes([SID_READ])), [(ECU_A_RESP, b"\x77")])
        self.assertEqual(calls, [bytes([SID_READ])])

    def test_unknown_sid_is_still_a_policy_input(self):
        """
        C2-OD-8：**未知 SID 仍进入策略** —— C2 不判断该 SID 是否由 ECU 实现。

        用一个"拒绝一切"的策略 + 一个未实现的 SID 证明：策略照样会被求值。
        """
        gw, _calls = self._recording_gateway(deny_all)
        _r, trace = gw.route_with_trace(ECU_A_REQ, bytes([0x99]))
        self.assertEqual(len(trace.decisions), 1)
        self.assertEqual(trace.decisions[0].sid, 0x99)

    def test_policy_is_never_asked_about_ecu_internal_state(self):
        """
        C2-OD-8：策略只能看到 ``(target, addressing_mode, sid)`` ——
        不得感知 ``EcuState``（session / security_level / did_overrides）。
        """
        seen = []

        def inspecting_policy(ecu, addressing_mode, sid):
            seen.append((ecu.request_id, addressing_mode, sid))
            return True

        gw = DiagnosticGateway(
            [VirtualEcu("ECU_A", ECU_A_REQ, ECU_A_RESP)], policy=inspecting_policy
        )
        gw.route(ECU_A_REQ, bytes([SID_READ]))
        self.assertEqual(seen, [(ECU_A_REQ, ADDRESSING_PHYSICAL, SID_READ)])

        # 反向：让 EcuState 处于"未解锁"状态，策略输入**不应**因此改变
        seen.clear()
        gw.ecus[0].state.session = uds_types.SESSION_DEFAULT
        gw.ecus[0].state.security_level = uds_types.SECURITY_LOCKED
        gw.route(ECU_A_REQ, bytes([SID_READ]))
        self.assertEqual(seen, [(ECU_A_REQ, ADDRESSING_PHYSICAL, SID_READ)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
