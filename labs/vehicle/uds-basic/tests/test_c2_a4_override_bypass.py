#!/usr/bin/env python3
"""
UDS Basic —— C2-A4：Override / Bypass 不变量审计

设计出处：``docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md``
§9.2.11（A4 bypass 审计方式）/ §12（Override Paths）/ C2-OD-3 / OD-4 / OD-5 / OD-6 / OD-9。

## 本阶段唯一要回答的问题

> **已 DENY 的 Gateway 请求，能否通过既有 override / extension seam
> 绕过 policy 并最终进入 target ECU？**

**不是**"policy API 能不能工作"，**不是**"A1/A2 是否测过 deny"。

```
policy evaluate
      │
      ▼
allowed == False
      │
      ├──────────┬───────────┐
      ▼          ▼           ▼
    trace     process      wire
    False        0           []
                 │
                 ▼
          state unchanged
```

## 四层证据缺一不可

`results == []` **不是**充分证据 —— 以下四种情形都会得到 `[]`：

```
policy deny | unknown physical target | functional broadcast | suppressed response
```

因此每个 deny case 同时断言 **trace / process spy / state / wire** 四层。
其中 `process` 一层刻意 spy 在 ``target.process`` **本身**（不是 handler）——
证明"请求根本没有到达目标 ECU"，而非"到达了但 handler 恰好没做事"。

## 四个 seam（§12 冻结）

```
1  VirtualEcu.handler                              → 实现替换
2  DiagnosticGateway(ecus=[…], functional_address=…) → 拓扑重构
3  Transport Protocol / GatewayRuntime              → 运行时接缝
4  build_runtime() 编程构造                          → 构造路径
```

**override 可替换实现，不可替换授权**（C2-OD-6）。

## 边界声明

* **`ecu.py` 单 ECU 直连路径不是本审计的 seam**：它是**没有 Gateway 的另一种拓扑**，
  策略是 Gateway 层控制。见下方 ``TestScopeBoundary``。
* ``build_runtime()`` 的接口白名单问题属**环境安全边界**，**不是** C2 deny policy
  —— A4 严格区分二者，且**绝不绑定任何真实 / 非 vcan0 接口**。

## DENY 策略来自测试侧

A4 的 DENY 策略是**测试内**定义的最小确定性策略；
``DEFAULT_POLICY`` **保持** allow-all 不变（见 ``TestA4TestPolicySeam``）。

## 运行

    python3 labs/vehicle/uds-basic/tests/test_c2_a4_override_bypass.py
或：
    bash labs/vehicle/uds-basic/tests/run_tests.sh unit
"""

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
if _ECU_DIR not in sys.path:
    sys.path.insert(0, _ECU_DIR)

import gateway_runtime  # noqa: E402
import uds_types  # noqa: E402
from gateway import (  # noqa: E402
    ADDRESSING_FUNCTIONAL,
    ADDRESSING_PHYSICAL,
    DEFAULT_POLICY,
    FUNCTIONAL_ADDRESS,
    DiagnosticGateway,
    PolicyDecision,
    VirtualEcu,
    build_default_gateway,
)
from uds_server import EcuState  # noqa: E402

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

SID_READ = 0x22
SID_WRITE = 0x2E
SID_SESSION = 0x10

PAYLOAD_READ = bytes([SID_READ, 0xF1, 0x90])
PAYLOAD_WRITE = bytes([SID_WRITE, 0xF1, 0x02]) + b"XY"
PAYLOAD_SESSION = bytes([SID_SESSION, 0x03])

ECU_A_REQ, ECU_A_RESP = 0x7E0, 0x7E8
ECU_B_REQ, ECU_B_RESP = 0x7E1, 0x7E9
ECU_C_REQ, ECU_C_RESP = 0x7E2, 0x7EA


# ----------------------------------------------------------------------
# A4 **测试用** DENY 策略（不是生产策略内容）
# ----------------------------------------------------------------------


def deny_all(ecu, addressing_mode, sid):
    """拒绝一切。"""
    return False


def deny_target(request_id):
    """拒绝指定 target（按 ``request_id``），其余放行。"""

    def policy(ecu, addressing_mode, sid):
        return ecu.request_id != request_id

    return policy


def allow_only(request_id):
    """**只**放行指定 target，其余一律拒绝（用于"允许 A 不自动授权 B"）。"""

    def policy(ecu, addressing_mode, sid):
        return ecu.request_id == request_id

    return policy


def deny_target_sid(request_id, denied_sid):
    """只拒绝 (指定 target, 指定 SID) 组合。"""

    def policy(ecu, addressing_mode, sid):
        return not (ecu.request_id == request_id and sid == denied_sid)

    return policy


# ----------------------------------------------------------------------
# 测试装置
# ----------------------------------------------------------------------


def make_ecu(name, request_id, response_id, *, unlocked=False, handler=None):
    """构造一个 ECU；``unlocked`` 使其处于可写状态（extended + level1）。"""
    state = EcuState()
    if unlocked:
        state.session = uds_types.SESSION_EXTENDED
        state.security_level = uds_types.SECURITY_LEVEL1
    return VirtualEcu(name, request_id, response_id, state=state, handler=handler)


def default_ecus(*, unlocked=False):
    """默认三 ECU（借用生产的 :func:`build_default_gateway` 拓扑）。"""
    template = build_default_gateway()
    ecus = []
    for ecu in template.ecus:
        state = EcuState()
        state.session = uds_types.SESSION_EXTENDED if unlocked else uds_types.SESSION_DEFAULT
        state.security_level = (
            uds_types.SECURITY_LEVEL1 if unlocked else uds_types.SECURITY_LOCKED
        )
        ecus.append(VirtualEcu(ecu.name, ecu.request_id, ecu.response_id, state=state))
    return ecus


def deny_gateway(denied_request_ids, *, unlocked=False, functional_address=FUNCTIONAL_ADDRESS):
    """默认拓扑 + 测试侧 DENY 策略（拒绝指定 request_id）。"""
    denied = set(denied_request_ids)

    def policy(ecu, addressing_mode, sid):
        return ecu.request_id not in denied

    return DiagnosticGateway(
        default_ecus(unlocked=unlocked), functional_address, policy=policy
    )


def state_projection(gateway):
    """逐 ECU 三字段状态投影 —— 与 A0/A1/A2 **同口径**。"""
    return {
        ecu.name: (
            ecu.state.session,
            ecu.state.security_level,
            tuple(sorted(ecu.state.did_overrides.items())),
        )
        for ecu in gateway.ecus
    }


class ProcessSpy:
    """
    把**每个** ECU 实例的 ``process()`` 换成计数 spy（保留原实现）。

    刻意 spy 在 ``target.process`` **本身**，而不是 handler —— §9.2.11 要求证明
    "被拒请求**根本没有到达**目标 ECU"，而不是"到达了但 handler 恰好没做事"。

    ``ecu.process = spy`` 是**实例属性**，遮蔽类方法；网关内部
    ``target.process(payload)`` 的属性查找必然命中它。
    """

    def __init__(self, gateway):
        self.calls: dict[str, list[bytes]] = {ecu.name: [] for ecu in gateway.ecus}
        self._install(gateway)

    def _install(self, gateway):
        for ecu in gateway.ecus:
            original = ecu.process
            recorded = self.calls[ecu.name]

            def spy(payload, _original=original, _recorded=recorded):
                _recorded.append(payload)
                return _original(payload)

            ecu.process = spy

    def count(self, name: str) -> int:
        return len(self.calls[name])

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.calls.values())


class CountingHandler:
    """
    带可观测副作用的测试 handler（**只存在于测试对象中**）。

    用于证明 ``handler override ≠ authorization override``：
    handler 被调用 ⇒ counter 增加；被 DENY ⇒ counter 保持 0。
    """

    def __init__(self, response: bytes = b"\x77"):
        self.response = response
        self.counter = 0
        self.payloads: list[bytes] = []

    def __call__(self, payload: bytes) -> bytes:
        self.counter += 1
        self.payloads.append(payload)
        return self.response + payload


class RecordingTransport:
    """
    只做 transport 的 fake。

    **不持有** ECU 引用、**不持有** policy、**不做** target 选择 ——
    它只实现 ``open / close / receive / send`` 四个动作（见
    ``test_c3_fake_transport_surface_is_transport_only``）。

    刻意不继承任何基类：``GatewayRuntime`` 只按 ``Transport`` Protocol
    结构化使用它，这条结构接缝本身就证明了"runtime 不依赖真实 isotp 对象"。
    """

    def __init__(self, requests=None):
        self._requests: list[tuple[int, bytes]] = list(requests or [])
        self.sent: list[tuple[int, bytes]] = []
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def receive(self, timeout: float):
        return self._requests.pop(0) if self._requests else None

    def send(self, can_id: int, payload: bytes) -> None:
        self.sent.append((can_id, payload))


def install_route_spy(gateway):
    """记录 ``gateway.route`` 的调用（保留原实现）。返回记录列表。"""
    recorded: list[tuple[int, bytes]] = []
    original = gateway.route

    def spy(can_id, payload):
        recorded.append((can_id, payload))
        return original(can_id, payload)

    gateway.route = spy
    return recorded


def install_core_spy(gateway):
    """记录 ``gateway._route_core`` 的调用（保留原实现）。返回 ``collect_trace`` 列表。"""
    collected: list[bool] = []
    original = gateway._route_core

    def spy(can_id, payload, *, collect_trace):
        collected.append(collect_trace)
        return original(can_id, payload, collect_trace=collect_trace)

    gateway._route_core = spy
    return collected


def path(relative: str) -> Path:
    return Path(_ECU_DIR) / relative


def make_runtime(gateway, transport):
    """构造 ``GatewayRuntime`` 并**静音日志**（默认 ``log=print`` 会污染测试输出）。"""
    return gateway_runtime.GatewayRuntime(gateway, transport, log=lambda _message: None)


# ======================================================================
# A4 测试策略 seam / 生产策略未被触碰
# ======================================================================


class TestA4TestPolicySeam(unittest.TestCase):
    """F3 —— DENY 必须来自测试侧 seam，而不是改写 ``DEFAULT_POLICY``。"""

    def test_default_policy_is_still_allow_all(self):
        ecu = make_ecu("X", 0x700, 0x708)
        for addressing in (ADDRESSING_PHYSICAL, ADDRESSING_FUNCTIONAL):
            for sid in (SID_READ, SID_WRITE, 0x99):
                with self.subTest(addressing=addressing, sid=hex(sid)):
                    self.assertTrue(DEFAULT_POLICY(ecu, addressing, sid))

    def test_default_gateway_ships_with_default_policy(self):
        self.assertIs(build_default_gateway().policy, DEFAULT_POLICY)

    def test_deny_policies_are_test_side_objects(self):
        """A4 的 DENY 策略是独立对象，不是 ``DEFAULT_POLICY`` 的别名。"""
        import gateway

        for policy in (deny_all, deny_target(ECU_B_REQ), allow_only(ECU_A_REQ)):
            self.assertIsNot(policy, DEFAULT_POLICY)
            self.assertIsNot(policy, gateway.allow_all_policy)

    def test_gateway_policy_field_accepts_the_test_seam(self):
        gw = deny_gateway([ECU_B_REQ])
        self.assertFalse(gw.policy(gw.ecu_by_name("ECU_B"), ADDRESSING_PHYSICAL, SID_READ))
        self.assertTrue(gw.policy(gw.ecu_by_name("ECU_A"), ADDRESSING_PHYSICAL, SID_READ))


# ======================================================================
# A. handler injection（seam 1）
# ======================================================================


class TestHandlerInjectionBypass(unittest.TestCase):
    """
    C2-OD-6：``handler`` 注入可以替换 ECU **实现**，但不能替换**授权**。

    策略求值点在 ``VirtualEcu.handler`` **之外**（``route()`` 内），
    因此被拒请求不会到达 handler，也不会到达 ``process()``。
    """

    def _gateway_with_handler(self, handler, policy):
        return DiagnosticGateway(
            [make_ecu("X", 0x700, 0x708, handler=handler)], policy=policy
        )

    def test_a1_deny_blocks_custom_handler_four_layers(self):
        """A1 —— DENY + 自定义 handler：trace / process / state / wire 四层。"""
        handler = CountingHandler()
        gw = self._gateway_with_handler(handler, deny_all)
        spy = ProcessSpy(gw)

        before = state_projection(gw)
        results, trace = gw.route_with_trace(0x700, PAYLOAD_READ)
        after = state_projection(gw)

        # Layer 1 — trace
        self.assertEqual(
            trace.decisions,
            (
                PolicyDecision(
                    target=0x700,
                    addressing_mode=ADDRESSING_PHYSICAL,
                    sid=SID_READ,
                    allowed=False,
                ),
            ),
        )
        # Layer 2 — process spy（在 process 本身，不是在 handler 上）
        self.assertEqual(spy.total, 0, "DENY ⇒ target.process() 必须为 0")
        # Layer 3 — state
        self.assertEqual(before, after, "DENY ⇒ EcuState 不得变化")
        # Layer 4 — wire
        self.assertEqual(results, [], "DENY ⇒ 不得产生任何帧")

        # 附加：handler 计数器同样为 0（实现层未被触达）
        self.assertEqual(handler.counter, 0)
        self.assertEqual(handler.payloads, [])

    def test_a2_deny_leaves_custom_handler_counter_at_zero(self):
        """A2 —— 自定义 handler 的可观测副作用计数器在 DENY 下必须为 0。"""
        for payload in (PAYLOAD_READ, PAYLOAD_WRITE, PAYLOAD_SESSION, b"\x99"):
            with self.subTest(payload=payload.hex(" ")):
                handler = CountingHandler()
                gw = self._gateway_with_handler(handler, deny_all)
                gw.route(0x700, payload)
                self.assertEqual(handler.counter, 0)

    def test_a3_allow_control_calls_the_handler_exactly_once(self):
        """A3 —— ALLOW 对照：handler 恰好被调用一次，且响应如实返回。"""
        handler = CountingHandler(response=b"\x77")
        gw = self._gateway_with_handler(handler, DEFAULT_POLICY)

        results = gw.route(0x700, PAYLOAD_READ)

        self.assertEqual(handler.counter, 1)
        self.assertEqual(handler.payloads, [PAYLOAD_READ])
        self.assertEqual(results, [(0x708, b"\x77" + PAYLOAD_READ)])

    def test_a3b_allow_control_is_also_observable_in_trace(self):
        handler = CountingHandler()
        gw = self._gateway_with_handler(handler, DEFAULT_POLICY)
        _results, trace = gw.route_with_trace(0x700, PAYLOAD_READ)
        self.assertTrue(trace.decisions[0].allowed)
        self.assertEqual(handler.counter, 1)

    def test_a4_spy_is_installed_on_process_itself_not_on_the_handler(self):
        """
        A4 —— spy 落在 ``target.process`` **本身**。

        证明方式：spy 安装后实例上存在 ``process`` 实例属性（遮蔽类方法）；
        且在 ALLOW 路径下它**确实**记录了调用 —— 说明 spy 位于真实执行路径上，
        而不是一个永远为 0 的装饰。
        """
        handler = CountingHandler()
        gw = self._gateway_with_handler(handler, DEFAULT_POLICY)
        spy = ProcessSpy(gw)

        ecu = gw.ecu_by_name("X")
        self.assertIn("process", vars(ecu), "spy 必须是实例属性（遮蔽类方法）")
        self.assertIsNot(ecu.process, VirtualEcu.process)

        gw.route(0x700, PAYLOAD_READ)
        self.assertEqual(spy.total, 1, "ALLOW 路径下 spy 必须记录到调用")
        self.assertEqual(spy.count("X"), 1)

    def test_a4b_same_spy_reads_zero_under_deny(self):
        """反向：同一 spy 在 DENY 下读数为 0（同一装置、两种结论）。"""
        handler = CountingHandler()
        gw = self._gateway_with_handler(handler, deny_all)
        spy = ProcessSpy(gw)
        gw.route(0x700, PAYLOAD_READ)
        self.assertEqual(spy.total, 0)

    def test_empty_payload_does_not_enter_policy_by_design(self):
        """
        **C2-OD-8 的显式记录**（不是 bypass，是冻结语义）。

        空 payload 结构上取不到 SID ⇒ ``_allow`` 直接放行且**不产生 decision**。
        因此 **DENY 策略不会拦住空 payload** —— 这是**设计**，不是漏洞：

        * trace 中没有任何 decision（可观测地证明"策略未被求值"）；
        * 请求交给 handler 由 C1/协议层处理（本例返回 ``7F 00 13``）。

        把它写进 A4 是为了**避免误读**：A4 证明的是"已进入策略求值的请求不能被绕过"，
        而不是"一切请求都被策略覆盖"。
        """
        gw = DiagnosticGateway([make_ecu("X", 0x700, 0x708)], policy=deny_all)
        spy = ProcessSpy(gw)

        results, trace = gw.route_with_trace(0x700, b"")

        self.assertEqual(trace.decisions, (), "空 payload 不产生 decision（C2-OD-8）")
        self.assertEqual(spy.total, 1, "空 payload 按设计直达 handler，不经策略")
        self.assertEqual(results, [(0x708, bytes([0x7F, 0x00, 0x13]))])

    def test_unknown_target_does_not_enter_policy_by_design(self):
        """C2-OD-8 的另一半：未知物理 target 是**路由归属失败**，不是 deny。"""
        gw = DiagnosticGateway([make_ecu("X", 0x700, 0x708)], policy=deny_all)
        spy = ProcessSpy(gw)

        results, trace = gw.route_with_trace(0x7E5, PAYLOAD_READ)

        self.assertEqual(results, [])
        self.assertEqual(trace.decisions, (), "不得产生 decision —— 它从未进入策略")
        self.assertEqual(spy.total, 0)

    def test_deny_also_blocks_the_default_protocol_handler(self):
        """非自定义 handler（默认协议层 dispatcher）同样不可被绕过。"""
        gw = DiagnosticGateway([make_ecu("X", 0x700, 0x708)], policy=deny_all)
        spy = ProcessSpy(gw)
        before = state_projection(gw)

        results, trace = gw.route_with_trace(0x700, PAYLOAD_SESSION)

        self.assertEqual(results, [])
        self.assertEqual(spy.total, 0)
        self.assertEqual(state_projection(gw), before)
        self.assertFalse(trace.decisions[0].allowed)


# ======================================================================
# B. custom topology（seam 2）
# ======================================================================


class TestCustomTopologyBypass(unittest.TestCase):
    """C2-OD-6 第 2 行：策略按**实际 target** 判定，与拓扑无关。"""

    def test_b1_custom_ids_physical_deny_four_layers(self):
        """B1 —— 自定义 request/response ID + physical DENY。"""
        ecus = [make_ecu("Y", 0x600, 0x608)]
        gw = DiagnosticGateway(ecus, policy=deny_all)
        spy = ProcessSpy(gw)
        before = state_projection(gw)

        results, trace = gw.route_with_trace(0x600, PAYLOAD_READ)

        self.assertEqual(
            trace.decisions,
            (
                PolicyDecision(
                    target=0x600,
                    addressing_mode=ADDRESSING_PHYSICAL,
                    sid=SID_READ,
                    allowed=False,
                ),
            ),
        )
        self.assertEqual(spy.total, 0)
        self.assertEqual(state_projection(gw), before)
        self.assertEqual(results, [])

    def test_b1b_custom_ids_allow_control_uses_custom_response_id(self):
        gw = DiagnosticGateway([make_ecu("Y", 0x600, 0x608)], policy=DEFAULT_POLICY)
        results = gw.route(0x600, PAYLOAD_READ)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 0x608)

    def test_b2_custom_functional_address_deny_four_layers(self):
        """B2 —— 自定义 functional address + functional DENY。"""
        custom = 0x600
        gw = DiagnosticGateway(default_ecus(), custom, policy=deny_all)
        spy = ProcessSpy(gw)
        before = state_projection(gw)

        results, trace = gw.route_with_trace(custom, PAYLOAD_SESSION)

        self.assertEqual(results, [])
        self.assertEqual(spy.total, 0)
        self.assertEqual(state_projection(gw), before)
        self.assertEqual(len(trace.decisions), 3)
        self.assertEqual(
            [d.addressing_mode for d in trace.decisions],
            [ADDRESSING_FUNCTIONAL] * 3,
        )
        self.assertTrue(all(not d.allowed for d in trace.decisions))

    def test_b2b_default_broadcast_address_is_now_an_unknown_physical_target(self):
        """自定义 functional address 后，0x7DF 变成"未知物理 target" —— 不进入策略。"""
        custom = 0x600
        gw = DiagnosticGateway(default_ecus(), custom, policy=deny_all)
        results, trace = gw.route_with_trace(FUNCTIONAL_ADDRESS, PAYLOAD_SESSION)
        self.assertEqual(results, [])
        self.assertEqual(
            trace.decisions, (), "未知物理 target 是路由归属失败，不是 deny（C2-OD-8）"
        )

    def test_b3_functional_mixed_allow_deny_allow(self):
        """B3 —— functional mixed：A allow / B deny / C allow（C2-OD-4 / OD-5）。"""
        gw = deny_gateway([ECU_B_REQ])
        spy = ProcessSpy(gw)
        before = state_projection(gw)

        results, trace = gw.route_with_trace(FUNCTIONAL_ADDRESS, PAYLOAD_SESSION)
        after = state_projection(gw)

        # trace：逐 ECU 的真实结果
        self.assertEqual(
            [(d.target, d.allowed) for d in trace.decisions],
            [(ECU_A_REQ, True), (ECU_B_REQ, False), (ECU_C_REQ, True)],
        )
        # process：A=1 B=0 C=1
        self.assertEqual(
            [spy.count(n) for n in ("ECU_A", "ECU_B", "ECU_C")], [1, 0, 1]
        )
        # state：A/C 变化，B 不变
        self.assertNotEqual(after["ECU_A"], before["ECU_A"])
        self.assertNotEqual(after["ECU_C"], before["ECU_C"])
        self.assertEqual(after["ECU_B"], before["ECU_B"])
        self.assertEqual(after["ECU_A"][0], uds_types.SESSION_EXTENDED)
        self.assertEqual(after["ECU_B"][0], uds_types.SESSION_DEFAULT)
        # wire：功能寻址恒不收集响应
        self.assertEqual(results, [])

    def test_b3b_functional_mixed_wire_stays_silent(self):
        """functional 无论放行几个 ECU，wire 恒为 ``[]``（总线约束，不是策略）。"""
        gw = deny_gateway([ECU_B_REQ])
        self.assertEqual(gw.route(FUNCTIONAL_ADDRESS, PAYLOAD_READ), [])
        gw_all_allow = deny_gateway([])
        self.assertEqual(gw_all_allow.route(FUNCTIONAL_ADDRESS, PAYLOAD_READ), [])

    def test_b4_allowing_a_does_not_authorize_b_or_c(self):
        """
        B4 —— "允许 A" 不能自动授权 B —— 这是 §10.2 明确要排除的失败模式。

        策略**只**放行 0x7E0；广播时 B / C 必须不被 process。
        """
        gw = DiagnosticGateway(default_ecus(), FUNCTIONAL_ADDRESS, policy=allow_only(ECU_A_REQ))
        spy = ProcessSpy(gw)

        results, trace = gw.route_with_trace(FUNCTIONAL_ADDRESS, PAYLOAD_SESSION)

        self.assertEqual([spy.count(n) for n in ("ECU_A", "ECU_B", "ECU_C")], [1, 0, 0])
        self.assertEqual(
            [(d.target, d.allowed) for d in trace.decisions],
            [(ECU_A_REQ, True), (ECU_B_REQ, False), (ECU_C_REQ, False)],
        )
        self.assertEqual(results, [])

    def test_b5_functional_address_colliding_with_a_request_id_still_per_ecu(self):
        """
        B5 —— ``functional_address`` 与某 ECU 的 ``request_id`` 重合的既有边界。

        ``_route_core`` 的 functional 分支**先于** physical 判定，因此该 request_id
        上的请求会被当作广播处理。A4 只**记录并验证**这一既有语义
        （不为它"修复"生产代码），并确认策略仍**逐 ECU** 生效。
        """
        ecus = [make_ecu("A", ECU_A_REQ, ECU_A_RESP), make_ecu("X", ECU_B_REQ, ECU_B_RESP)]
        gw = DiagnosticGateway(ecus, functional_address=ECU_A_REQ, policy=deny_target(ECU_B_REQ))
        spy = ProcessSpy(gw)

        results, trace = gw.route_with_trace(ECU_A_REQ, PAYLOAD_SESSION)

        # 被当作广播：不收集响应
        self.assertEqual(results, [], "重合拓扑下该地址走 functional 分支")
        # 策略仍逐 ECU 生效
        self.assertEqual([spy.count(n) for n in ("A", "X")], [1, 0])
        self.assertEqual(
            [(d.target, d.addressing_mode, d.allowed) for d in trace.decisions],
            [
                (ECU_A_REQ, ADDRESSING_FUNCTIONAL, True),
                (ECU_B_REQ, ADDRESSING_FUNCTIONAL, False),
            ],
        )

    def test_b5b_collision_topology_keeps_the_other_physical_path_working(self):
        """重合拓扑下，**不**重合的那个 request_id 仍走物理寻址。"""
        ecus = [make_ecu("A", ECU_A_REQ, ECU_A_RESP), make_ecu("X", ECU_B_REQ, ECU_B_RESP)]
        gw = DiagnosticGateway(ecus, functional_address=ECU_A_REQ, policy=DEFAULT_POLICY)
        results = gw.route(ECU_B_REQ, PAYLOAD_READ)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], ECU_B_RESP)

    def test_b6_denied_ecu_state_unchanged_while_allowed_ecu_changes(self):
        """B6 —— 同一次广播中：deny 的 ECU 不变，allow 的 ECU 正常改变。"""
        gw = deny_gateway([ECU_B_REQ])
        before = state_projection(gw)

        gw.route(FUNCTIONAL_ADDRESS, PAYLOAD_SESSION)
        after = state_projection(gw)

        self.assertEqual(after["ECU_B"], before["ECU_B"])
        self.assertNotEqual(after["ECU_A"], before["ECU_A"])
        self.assertNotEqual(after["ECU_C"], before["ECU_C"])
        self.assertEqual(after["ECU_A"][0], uds_types.SESSION_EXTENDED)
        self.assertEqual(after["ECU_C"][0], uds_types.SESSION_EXTENDED)
        self.assertEqual(after["ECU_B"][0], uds_types.SESSION_DEFAULT)

    def test_b6b_denied_ecu_did_overrides_unchanged_on_broadcast_write(self):
        gw = deny_gateway([ECU_B_REQ], unlocked=True)
        gw.route(FUNCTIONAL_ADDRESS, PAYLOAD_WRITE)
        self.assertEqual(gw.ecu_by_name("ECU_B").state.did_overrides, {})
        self.assertNotEqual(gw.ecu_by_name("ECU_A").state.did_overrides, {})


# ======================================================================
# C. fake Transport / GatewayRuntime（seam 3）
# ======================================================================


class TestFakeTransportAndRuntime(unittest.TestCase):
    """
    Runtime 层**不含任何寻址判断**：它只把 transport 上收到的请求交给
    ``gateway.route()``，并把 route 返回的项发回总线。
    """

    def test_c1_deny_produces_no_send(self):
        """C1 —— receive → handle_request → gateway.route → DENY ⇒ 不发帧。"""
        transport = RecordingTransport([(ECU_B_REQ, PAYLOAD_WRITE)])
        runtime = make_runtime(deny_gateway([ECU_B_REQ]), transport)

        handled = runtime.serve_once(timeout=0.0)

        self.assertTrue(handled, "请求被处理（不是空闲轮）")
        self.assertEqual(transport.sent, [], "DENY ⇒ transport 上不得出现任何帧")

    def test_c2_deny_four_layers_through_the_runtime(self):
        """C2 —— 经 runtime 的 DENY 同样具备四层证据。"""
        transport = RecordingTransport([(ECU_B_REQ, PAYLOAD_WRITE)])
        gw = deny_gateway([ECU_B_REQ])
        spy = ProcessSpy(gw)
        before = state_projection(gw)

        runtime = make_runtime(gw, transport)
        runtime.serve_once(timeout=0.0)

        self.assertEqual(transport.sent, [])
        self.assertEqual(spy.total, 0)
        self.assertEqual(state_projection(gw), before)

        # trace：runtime 后单独复算一次同一请求（新网关，避免状态污染）
        gw2 = deny_gateway([ECU_B_REQ])
        _results, trace = gw2.route_with_trace(ECU_B_REQ, PAYLOAD_WRITE)
        self.assertEqual(
            trace.decisions,
            (
                PolicyDecision(
                    target=ECU_B_REQ,
                    addressing_mode=ADDRESSING_PHYSICAL,
                    sid=SID_WRITE,
                    allowed=False,
                ),
            ),
        )

    def test_c3_fake_transport_surface_is_transport_only(self):
        """C3 —— fake transport 的公开可调用面**只有** transport 的四个动作。"""
        transport = RecordingTransport()
        callables = {
            name
            for name in dir(transport)
            if not name.startswith("_") and callable(getattr(transport, name))
        }
        self.assertEqual(callables, {"open", "close", "receive", "send"})

    def test_c3b_fake_transport_holds_no_ecu_policy_or_target_selector(self):
        transport = RecordingTransport()
        for name, value in vars(transport).items():
            with self.subTest(attr=name):
                self.assertNotIsInstance(value, VirtualEcu)
                self.assertNotIsInstance(value, DiagnosticGateway)
        for forbidden in ("policy", "ecus", "gateway", "targets"):
            self.assertFalse(hasattr(transport, forbidden))

    def test_c4_runtime_delegates_to_gateway_route(self):
        """C4 —— runtime 的授权决定来自 ``gateway.route()``，不是它自己。"""
        gw = deny_gateway([ECU_B_REQ])
        recorded = install_route_spy(gw)
        transport = RecordingTransport()
        runtime = make_runtime(gw, transport)

        runtime.handle_request(ECU_B_REQ, PAYLOAD_WRITE)

        self.assertEqual(recorded, [(ECU_B_REQ, PAYLOAD_WRITE)], "必须调用 gateway.route")

    def test_c4b_runtime_result_equals_direct_route(self):
        """C4 —— 同一输入下 ``runtime.handle_request()`` 与直接 ``route()`` 结果一致。"""
        for request_id, payload in (
            (ECU_A_REQ, PAYLOAD_READ),
            (ECU_B_REQ, PAYLOAD_WRITE),
            (FUNCTIONAL_ADDRESS, PAYLOAD_READ),
        ):
            with self.subTest(request_id=hex(request_id), payload=payload.hex(" ")):
                direct = deny_gateway([ECU_B_REQ]).route(request_id, payload)
                runtime = make_runtime(deny_gateway([ECU_B_REQ]), RecordingTransport())
                via_runtime = runtime.handle_request(request_id, payload)
                self.assertEqual(direct, via_runtime)

    def test_c5_allow_control_sends_the_response(self):
        """C5 —— ALLOW 对照：runtime 正常把响应交给 transport。"""
        transport = RecordingTransport([(ECU_A_REQ, PAYLOAD_READ)])
        gw = deny_gateway([ECU_B_REQ])
        spy = ProcessSpy(gw)

        runtime = make_runtime(gw, transport)
        runtime.serve_once(timeout=0.0)

        self.assertEqual(spy.count("ECU_A"), 1)
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(transport.sent[0][0], ECU_A_RESP)

    def test_c5b_allow_control_follows_the_denied_target(self):
        """同一 runtime 上：先被拒的 target，换成放行的 target 后正常回帧。"""
        transport = RecordingTransport(
            [(ECU_B_REQ, PAYLOAD_WRITE), (ECU_A_REQ, PAYLOAD_READ)]
        )
        runtime = make_runtime(deny_gateway([ECU_B_REQ]), transport)

        runtime.serve_once(timeout=0.0)
        self.assertEqual(transport.sent, [])

        runtime.serve_once(timeout=0.0)
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(transport.sent[0][0], ECU_A_RESP)


# ======================================================================
# D. build_runtime()（seam 4）
# ======================================================================


class TestBuildRuntimePath(unittest.TestCase):
    """
    ``build_runtime()`` 是**构造路径**，不是授权路径：
    它返回的 runtime 仍然只能经 ``gateway.route()`` 决定放行与否。

    .. important::
       ``build_runtime()`` 的接口白名单问题属**环境安全边界**，
       **不是** C2 deny policy（见 :class:`TestScopeBoundary`）。
       本类**绝不**绑定任何真实 / 非 vcan0 接口 —— 全程用 fake transport + patch。
    """

    def _patched_build_runtime(self, gateway, transport, interface="vcan0"):
        """
        在纯单元环境中构造 runtime：patch 掉网关工厂与 IsotpTransport。

        同时传入静音 ``log``，避免默认 ``print`` 污染测试输出。
        """
        with mock.patch.object(
            gateway_runtime, "build_default_gateway", return_value=gateway
        ), mock.patch.object(
            gateway_runtime, "IsotpTransport", return_value=transport
        ):
            return gateway_runtime.build_runtime(
                interface=interface, log=lambda _message: None
            )

    def test_d1_build_runtime_path_routes_through_gateway_route(self):
        """D1 —— ``build_runtime()`` 返回的 runtime 仍经 ``gateway.route()``。"""
        gw = deny_gateway([ECU_B_REQ])
        recorded = install_route_spy(gw)
        runtime = self._patched_build_runtime(gw, RecordingTransport())

        runtime.handle_request(ECU_A_REQ, PAYLOAD_READ)

        self.assertEqual(recorded, [(ECU_A_REQ, PAYLOAD_READ)])
        self.assertEqual(len(recorded), 1, "route spy call_count == 1")

    def test_d2_build_runtime_deny_blocks_everything(self):
        """D2 —— 经 ``build_runtime()`` 的 DENY：allowed=False / process=0 / [] / 不发帧。"""
        gw = deny_gateway([ECU_B_REQ])
        spy = ProcessSpy(gw)
        transport = RecordingTransport([(ECU_B_REQ, PAYLOAD_WRITE)])
        before = state_projection(gw)

        runtime = self._patched_build_runtime(gw, transport)
        runtime.serve_once(timeout=0.0)

        self.assertEqual(transport.sent, [])
        self.assertEqual(spy.total, 0)
        self.assertEqual(state_projection(gw), before)

        _results, trace = deny_gateway([ECU_B_REQ]).route_with_trace(ECU_B_REQ, PAYLOAD_WRITE)
        self.assertFalse(trace.decisions[0].allowed)

    def test_d2b_build_runtime_bypasses_open_entirely(self):
        """本测试**不**调用 ``open()``：因此不绑定任何接口（fake transport 无 socket）。"""
        gateway = deny_gateway([ECU_B_REQ])
        transport = RecordingTransport()
        runtime = self._patched_build_runtime(gateway, transport)

        self.assertIs(runtime.gateway, gateway, "runtime 使用的正是被注入的网关")
        self.assertFalse(transport.opened, "A4 不打开任何 transport")

    def test_d3_interface_value_is_orthogonal_to_policy(self):
        """
        D3 —— 接口取值与 Gateway policy **正交**。

        用 fake transport 在纯单元环境中证明：无论 ``interface`` 传什么，
        ``build_runtime()`` 返回的 runtime 仍然执行 gateway 的策略。
        **不涉及任何真实接口绑定，也不触碰 can0/can1。**
        """
        for interface in ("vcan0", "can0", "vcan1", ""):
            with self.subTest(interface=interface):
                gw = deny_gateway([ECU_B_REQ])
                spy = ProcessSpy(gw)
                transport = RecordingTransport([(ECU_B_REQ, PAYLOAD_WRITE)])
                runtime = self._patched_build_runtime(gw, transport, interface=interface)

                runtime.serve_once(timeout=0.0)

                self.assertEqual(spy.total, 0, "策略与 interface 取值无关")
                self.assertEqual(transport.sent, [])
                self.assertFalse(transport.opened)

    def test_d3b_build_runtime_has_no_policy_parameter(self):
        """``build_runtime()`` 结构上不接收 policy ⇒ 策略只能来自被注入的网关对象。"""
        import inspect

        params = inspect.signature(gateway_runtime.build_runtime).parameters
        self.assertNotIn("policy", params)
        self.assertIn("interface", params)


# ======================================================================
# E. route / route_with_trace 在 DENY 下的一致性（§9）
# ======================================================================


class TestRouteCoreParityUnderDeny(unittest.TestCase):
    """C2-OD-9：两者**共用同一个** ``_route_core``，deny 的 enforcement point 相同。"""

    CASES = (
        (ECU_A_REQ, PAYLOAD_READ, False),
        (ECU_B_REQ, PAYLOAD_WRITE, True),
        (ECU_C_REQ, PAYLOAD_SESSION, False),
        (FUNCTIONAL_ADDRESS, PAYLOAD_SESSION, True),
    )

    def test_e1_deny_route_and_route_with_trace_agree(self):
        """E1 —— DENY 下两者 wire 结果逐字节相同；trace 只由后者提供。"""
        for request_id, payload, _denied in self.CASES:
            with self.subTest(request_id=hex(request_id), payload=payload.hex(" ")):
                gw_plain = deny_gateway([ECU_B_REQ])
                gw_trace = deny_gateway([ECU_B_REQ])

                plain = gw_plain.route(request_id, payload)
                traced, trace = gw_trace.route_with_trace(request_id, payload)

                self.assertEqual(plain, traced)
                self.assertEqual(state_projection(gw_plain), state_projection(gw_trace))
                self.assertIsInstance(trace.decisions, tuple)

    def test_e2_both_paths_call_the_same_route_core_exactly_once(self):
        """E2 —— spy 证明两者各调用 ``_route_core`` 恰一次，仅 ``collect_trace`` 不同。"""
        gw = deny_gateway([ECU_B_REQ])
        collected = install_core_spy(gw)

        gw.route(ECU_B_REQ, PAYLOAD_WRITE)
        self.assertEqual(collected, [False])

        gw.route_with_trace(ECU_B_REQ, PAYLOAD_WRITE)
        self.assertEqual(collected, [False, True])

    def test_e3_plain_route_also_enforces_deny(self):
        """E3 —— 反向验证：**不是**只有 trace 路径才跑策略。"""
        gw = deny_gateway([ECU_B_REQ])
        spy = ProcessSpy(gw)

        results = gw.route(ECU_B_REQ, PAYLOAD_WRITE)

        self.assertEqual(results, [])
        self.assertEqual(spy.total, 0, "普通 route() 路径同样不得触达 target")

    def test_e3b_plain_route_deny_leaves_state_untouched(self):
        gw = deny_gateway([ECU_B_REQ], unlocked=True)
        before = state_projection(gw)
        gw.route(ECU_B_REQ, PAYLOAD_WRITE)
        self.assertEqual(state_projection(gw), before)

    def test_e3c_route_returns_a_plain_list(self):
        """C2-OD-9：``route()`` 的返回类型不因 A4 而改变。"""
        self.assertIsInstance(deny_gateway([ECU_B_REQ]).route(ECU_B_REQ, PAYLOAD_WRITE), list)


# ======================================================================
# F. 结构性不变量（源码级，防"第二套执行路径"）
# ======================================================================


class TestStructuralInvariants(unittest.TestCase):
    """
    运行期断言只能覆盖"测到的那几条路径"。本类补**源码级**不变量，
    使"存在第二条通往 ECU 的执行路径"这件事在结构上不可能被漏掉。
    """

    def _package_sources(self):
        return {
            p.name: p
            for p in sorted(Path(_ECU_DIR).glob("*.py"))
        }

    @staticmethod
    def _attr_call_lines(tree, attr_name):
        lines = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr_name
            ):
                lines.append(node.lineno)
        return sorted(lines)

    @staticmethod
    def _function_span(tree, name):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node.lineno, node.end_lineno
        return None

    def test_f1_process_is_called_only_from_route_core(self):
        """
        F1 —— ``ecu/`` 包内 ``.process(...)`` 调用点**恰好两处**，且都在
        ``gateway.py::_route_core`` 函数体内。

        这是"不存在第二套执行路径"的直接结构证据（AST 级，不受注释/文档字符串干扰）。
        """
        hits = {}
        for name, source_path in self._package_sources().items():
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            lines = self._attr_call_lines(tree, "process")
            if lines:
                hits[name] = (source_path, tree, lines)

        self.assertEqual(
            sorted(hits), ["gateway.py"], f"process() 只应出现在 gateway.py，实际：{sorted(hits)}"
        )

        source_path, tree, lines = hits["gateway.py"]
        self.assertEqual(len(lines), 2, f"预期恰好 2 处，实际行号 {lines}")

        span = self._function_span(tree, "_route_core")
        self.assertIsNotNone(span)
        start, end = span
        for line in lines:
            with self.subTest(line=line):
                self.assertTrue(
                    start <= line <= end,
                    f"process() 调用（行 {line}）必须落在 _route_core（行 {start}-{end}）内",
                )

    @staticmethod
    def _is_allow_call(node):
        return isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == "_allow")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "_allow")
        )

    def test_f1b_process_call_sites_are_policy_guarded(self):
        """
        两处 ``process()`` 都必须受 ``_allow(...)`` 守卫。

        两种既有守卫形态都要被结构性地承认（**不硬编码行号**）：

        * **正分支守卫** —— ``if self._allow(...): ecu.process(...)``
          （functional：process 落在 If 体**内**）
        * **早返回守卫** —— ``if not self._allow(...): return []`` 之后再调用
          （physical：process 落在 If 体**之后**）

        本用例从 gateway.py 的 **AST** 推导出两类守卫的位置，再断言每个
        ``process()`` 调用都落在其中之一 —— 因此 gateway.py 行号变动不会
        造成假失败，而**移除守卫**必然使其失败。
        """
        tree = ast.parse(path("gateway.py").read_text(encoding="utf-8"))
        route_core = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_route_core"
        )

        guarded_spans = []
        early_return_ends = []
        process_lines = []

        for node in ast.walk(route_core):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "process":
                    process_lines.append(node.lineno)

            if isinstance(node, ast.If) and any(
                self._is_allow_call(n) for n in ast.walk(node.test)
            ):
                if any(isinstance(n, ast.Return) for n in ast.walk(node)):
                    early_return_ends.append(node.end_lineno)
                else:
                    guarded_spans.append((node.body[0].lineno, node.end_lineno))

        self.assertEqual(len(process_lines), 2, "本用例假定 _route_core 内有 2 处 process()")
        self.assertTrue(
            guarded_spans or early_return_ends,
            "_route_core 内必须存在 _allow(...) 守卫 —— 找不到任何守卫即为缺陷",
        )

        for line in process_lines:
            with self.subTest(line=line):
                in_branch = any(start <= line <= end for start, end in guarded_spans)
                after_early_return = any(line > end for end in early_return_ends)
                self.assertTrue(
                    in_branch or after_early_return,
                    f"process() 调用（行 {line}）未被任何 _allow() 守卫保护",
                )

    def test_f2_gateway_runtime_has_no_second_authorization_path(self):
        """F2 —— ``gateway_runtime.py`` 不含 target 解析 / allow-deny / policy。"""
        source = path("gateway_runtime.py").read_text(encoding="utf-8")

        for forbidden in ("policy", "_by_request", "_allow", "allow_all", "PolicyDecision"):
            with self.subTest(token=forbidden):
                self.assertNotIn(
                    forbidden, source, f"gateway_runtime.py 不得出现 {forbidden!r}"
                )

    def test_f2b_gateway_runtime_calls_gateway_route_exactly_once(self):
        """F2 —— runtime 内 ``.route(...)`` 调用点恰好一处，且形如 ``self.gateway.route``。"""
        source_path = path("gateway_runtime.py")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        lines = self._attr_call_lines(tree, "route")
        self.assertEqual(len(lines), 1, f"预期恰好 1 处 route() 调用，实际行号 {lines}")

        source = source_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("self.gateway.route(", source[lines[0] - 1])

    def test_f2c_gateway_runtime_never_calls_process(self):
        """F2 —— runtime 不得直接调用 ``process()``（那将是第二条执行路径）。"""
        tree = ast.parse(path("gateway_runtime.py").read_text(encoding="utf-8"))
        self.assertEqual(self._attr_call_lines(tree, "process"), [])

    def test_f3_default_policy_object_is_not_rebound(self):
        """F3 —— A4 运行前后 ``DEFAULT_POLICY`` 仍是 allow-all 生产默认策略。"""
        import gateway

        self.assertIs(gateway.DEFAULT_POLICY, gateway.allow_all_policy)
        self.assertIs(DEFAULT_POLICY, gateway.allow_all_policy)
        self.assertIs(build_default_gateway().policy, gateway.allow_all_policy)


# ======================================================================
# Negative controls —— 证明不是"永远不调用 process"
# ======================================================================


class TestNegativeControls(unittest.TestCase):
    """DENY ⇒ 0；ALLOW ⇒ 1。确保上述所有 0 有意义。"""

    def test_deny_control_process_count_is_zero(self):
        gw = deny_gateway([ECU_B_REQ])
        spy = ProcessSpy(gw)
        gw.route(ECU_B_REQ, PAYLOAD_READ)
        self.assertEqual(spy.total, 0)

    def test_allow_control_process_count_is_one(self):
        gw = deny_gateway([ECU_B_REQ])
        spy = ProcessSpy(gw)
        gw.route(ECU_A_REQ, PAYLOAD_READ)
        self.assertEqual(spy.total, 1)

    def test_allow_control_for_functional_counts_every_allowed_ecu(self):
        gw = deny_gateway([ECU_B_REQ])
        spy = ProcessSpy(gw)
        gw.route(FUNCTIONAL_ADDRESS, PAYLOAD_SESSION)
        self.assertEqual(spy.total, 2, "A 与 C 各一次，B 被拒")

    def test_sid_scoped_deny_only_blocks_that_sid(self):
        """策略粒度到 SID：同一 target 的其他 SID 仍放行（证明策略真的按输入求值）。"""
        gw = DiagnosticGateway(default_ecus(), FUNCTIONAL_ADDRESS, policy=deny_target_sid(ECU_A_REQ, SID_WRITE))
        spy = ProcessSpy(gw)

        gw.route(ECU_A_REQ, PAYLOAD_READ)
        self.assertEqual(spy.count("ECU_A"), 1)

        gw.route(ECU_A_REQ, PAYLOAD_WRITE)
        self.assertEqual(spy.count("ECU_A"), 1, "该 SID 被拒 ⇒ 不再执行")


# ======================================================================
# 范围边界（显式记录，不作为 seam 审计）
# ======================================================================


class TestScopeBoundary(unittest.TestCase):
    """
    **不是** bypass 审计，而是把两条边界**显式记录**下来，
    防止后续阶段把不同性质的问题混为一谈。
    """

    def test_ecu_py_is_a_separate_topology_without_a_gateway(self):
        """
        ``ecu.py`` 是单 ECU 独立守护进程：它**没有** gateway 对象，
        因而没有 Gateway policy 概念。

        ⇒ **直接运行 ecu.py 在 Gateway policy 范围之外，不是 Gateway bypass。**
           A4 不为它增加 bypass 测试面（设计 §12 冻结的是 4 类 seam）。
        """
        source = path("ecu.py").read_text(encoding="utf-8")
        self.assertNotIn("DiagnosticGateway", source)
        self.assertNotIn("_route_core", source)
        tree = ast.parse(source)
        self.assertEqual(self._gateway_attr_calls(tree), [])

    @staticmethod
    def _gateway_attr_calls(tree):
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("route", "process"):
                    out.append(node.lineno)
        return out

    @staticmethod
    def _code_string_literals(tree):
        """模块内**代码**中的字符串字面量（排除文档字符串）。"""
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                text = ast.get_docstring(node, clean=False)
                if text:
                    docstrings.add(text)
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        return literals - docstrings

    def test_interface_whitelist_is_environment_policy_not_gateway_policy(self):
        """
        接口白名单（vcan0）是**环境安全边界**，与 Gateway 授权策略正交。

        静态确认二者位于**不同模块、不同机制**：

        * 接口边界 —— ``gateway_runtime.py`` 的 ``DEFAULT_INTERFACE`` 与 ``main()`` 校验；
        * 授权策略 —— ``gateway.py`` 的 ``GatewayPolicy`` / ``policy`` 求值。

        协议层**代码**中不得出现任何接口名字面量（文档字符串提及不算 ——
        用 AST 区分，避免把注释误判成实现）。
        """
        self.assertEqual(gateway_runtime.DEFAULT_INTERFACE, "vcan0")

        gateway_tree = ast.parse(path("gateway.py").read_text(encoding="utf-8"))
        code_strings = self._code_string_literals(gateway_tree)
        offenders = sorted(s for s in code_strings if "vcan" in s or "can0" in s)
        self.assertEqual(
            offenders, [], f"协议层代码不应包含接口名字面量，实际：{offenders}"
        )

        runtime_source = path("gateway_runtime.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_INTERFACE", runtime_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
