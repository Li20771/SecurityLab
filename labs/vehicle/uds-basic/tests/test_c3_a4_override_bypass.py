#!/usr/bin/env python3
"""
C3-A4 —— Override / Bypass Audit（C3 `services_provider` seam 扩展）

设计出处：``docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md``
（**C3-OQ-8 = DECIDED：必须进行新的 override audit**；C3-OD-14 的 10 条限制）

## 本阶段证明什么

> C3 新增的 ``services_provider`` 以及 C2 既有的 override / injection seam，
> 在 C3 之后**仍然不能绕过 C2 policy 与 C1 guard**。

```
policy decision（Layer 1）
      ↓
provider / handler（Layer 2a）
      ↓
process()（Layer 2b）
      ↓
ECU state（Layer 3）
      ↓
wire response（Layer 4）
```

**只测 `route() == []` 不够** —— `[]` 无法证明 ECU 内部没有被执行。
因此每个 DENY 场景都同时断言四层。

## 与 C2-A4 的关系（**历史基线 + 最小新增**）

```
C2-A4（已合并）  = 4 个 seam（handler / topology / transport / build_runtime）
C3-A4（本文件）  = 在**同一证据模型**上新增 services_provider，
                   并**重新验证** C2 的 4 个 seam
```

装置（`ProcessSpy` / `CountingHandler` / `RecordingTransport` /
`install_allow_observer` / 策略工厂）**直接复用** C2-A4 测试模块，
不重新设计一套 —— 见模块级 import。

## 与 C3-A2 的职责隔离

A4 **不是** differential audit：不使用 C3-A0 Golden / C3-A2 expected /
手写 frozen answer。A4 只证明 **policy / authorization 边界**。

## ⚠️ 本文件曾是"缺陷可见性"的来源 —— 现已转为**回归护栏**

原 C3-A4 审计（PR #55）发现了一个真实生产缺陷：

    DEFECT-1 —— 网关级 `services_provider` 会**静默覆盖** ECU 自带的
                custom handler（破坏 C2-A4 已审计的 seam-1 语义）。

该缺陷已由 **PR #56**（`fix: preserve custom handlers with gateway provider`）
修复。因此本文件在 **C3-A4 Re-Audit** 基线上的语义变为：

```
原 A4   （BLOCKED）: `TestDefectReproduction` **断言正确行为 ⇒ 预期失败**
本次 Re-Audit      : `TestDefectRegressionGuard` **断言同一行为 ⇒ 必须通过**
                     —— 缺陷一旦回归，本类立刻失败
```

**这不是"把失败用例改绿"** —— 断言的行为一字未变，变的是 production：
修复前它确实被违反，修复后确实成立。

运行：

    python3 tests/test_c3_a4_override_bypass.py
或：
    bash tests/run_tests.sh unit
"""

import ast
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
for _p in (_HERE, _ECU_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: **复用 C2-A4 既有审计装置**（ProcessSpy / CountingHandler / RecordingTransport /
#: install_allow_observer / 策略工厂 / state_projection …）。
#: 这是"C2-A4 既有证据 = 历史基线，C3-A4 = 最小新增"的落地方式。
import gateway_runtime  # noqa: E402
import test_c2_a4_override_bypass as c2a4  # noqa: E402
import uds_server  # noqa: E402
import uds_types  # noqa: E402
from gateway import (  # noqa: E402
    FUNCTIONAL_ADDRESS,
    DiagnosticGateway,
    VirtualEcu,
)

SID_SECURITY_ACCESS = 0x27
SID_WRITE_DID = 0x2E
SID_READ_DID = 0x22

REQUEST_SEED = bytes([SID_SECURITY_ACCESS, 0x01])
ECU_A_REQ, ECU_A_RESP = 0x7E0, 0x7E8
ECU_B_REQ, ECU_B_RESP = 0x7E1, 0x7E9
ECU_C_REQ, ECU_C_RESP = 0x7E2, 0x7EA

TEST_SECURITY_CONFIG = {
    "max_attempts": 3,
    "lockout_seconds": 10,
    "seed_expiry_seconds": 5,
}


# ======================================================================
# C3 新增装置：可观测 provider
# ======================================================================


class InstrumentedProvider:
    """
    带**调用计数**的 ``services_provider``。

    A4 的核心新增证据依赖"provider 是否被调用"这一事实 ——
    因此 provider 自身必须可观测，且能返回一个
    **绝不会被误认为正常状态机输出**的 marker handler。
    """

    def __init__(self):
        self.calls = 0
        #: 每次调用时返回的 SID → handler 映射（默认空表）。
        self.table: dict = {}

    def __call__(self):
        self.calls += 1
        return dict(self.table)

    # ---- 构造辅助 ----

    @classmethod
    def with_marker(cls, sid: int, marker: bytes = b"\xEE\x01"):
        """返回 `{sid: marker_handler}` 的 provider（marker 明显不是 0x27 输出）。"""
        provider = cls()
        hits = []

        def marker_handler(request, state):
            hits.append(request)
            return marker

        provider.table = {sid: marker_handler}
        provider.marker_hits = hits
        return provider

    @classmethod
    def with_security_access(cls):
        """返回 C3-A1 语义的 0x27 provider（走**唯一**绑定定义）。"""
        provider = cls()
        provider.table = uds_server.security_access_services(
            now=1000.0, security_config=TEST_SECURITY_CONFIG
        )
        return provider


def wired_ecus(provider, *, policy=None, names=None) -> DiagnosticGateway:
    """默认三 ECU + 网关级 provider 的网关（测试装置）。"""
    ecus = [
        c2a4.make_ecu(name, req, resp)
        for name, req, resp in (
            names or (("ECU_A", ECU_A_REQ, ECU_A_RESP),
                      ("ECU_B", ECU_B_REQ, ECU_B_RESP),
                      ("ECU_C", ECU_C_REQ, ECU_C_RESP))
        )
    ]
    return DiagnosticGateway(ecus, policy=policy, services_provider=provider)


def security_state_of(gateway, name: str) -> dict:
    state = gateway.ecu_by_name(name).state
    return {
        "pending_seed": None if state.pending_seed is None else state.pending_seed.hex(),
        "seed_consumed": state.seed_consumed,
        "failed_attempts": state.failed_attempts,
        "lockout_until": state.lockout_until,
        "seed_counter": state.seed_counter,
    }


def read_source(module) -> str:
    with open(module.__file__, encoding="utf-8") as handle:
        return handle.read()


# ======================================================================
# §6 —— physical DENY + provider
# ======================================================================


class TestProviderSeamPhysicalDeny(unittest.TestCase):
    """A4-P1：**DENY 必须发生在 provider 生效之前。**"""

    def _setup(self):
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_target(ECU_A_REQ))
        spy = c2a4.ProcessSpy(gateway)
        return provider, gateway, spy

    def test_six_layers_of_evidence(self):
        provider, gateway, spy = self._setup()
        before = security_state_of(gateway, "ECU_A")

        results, trace = gateway.route_with_trace(ECU_A_REQ, REQUEST_SEED)
        after = security_state_of(gateway, "ECU_A")

        # Layer 1 —— policy decision
        self.assertEqual(len(trace.decisions), 1)
        decision = trace.decisions[0]
        self.assertEqual(decision.target, ECU_A_REQ)
        self.assertEqual(decision.addressing_mode, "physical")
        self.assertEqual(decision.sid, SID_SECURITY_ACCESS)
        self.assertFalse(decision.allowed)

        # Layer 2a —— provider **未被调用**（deny 在 provider 之前）
        self.assertEqual(provider.calls, 0, "DENY ⇒ provider 不得被调用")

        # Layer 2b —— process()
        self.assertEqual(spy.total, 0, "DENY ⇒ target.process() 不得被调用")

        # Layer 3 —— ECU state
        self.assertEqual(after, before)

        # Layer 4 —— wire
        self.assertEqual(results, [])

    def test_target_security_state_is_pristine(self):
        provider, gateway, _spy = self._setup()
        gateway.route(ECU_A_REQ, REQUEST_SEED)

        state = security_state_of(gateway, "ECU_A")
        self.assertIsNone(state["pending_seed"], "DENY ⇒ 不得产生 seed")
        self.assertEqual(state["seed_counter"], 0)
        self.assertEqual(state["failed_attempts"], 0)
        self.assertFalse(state["seed_consumed"])
        self.assertIsNone(state["lockout_until"])
        self.assertEqual(gateway.ecu_by_name("ECU_A").state.security_level,
                         uds_types.SECURITY_LOCKED)

    def test_untargeted_ecus_are_untouched(self):
        provider, gateway, spy = self._setup()
        gateway.route(ECU_A_REQ, REQUEST_SEED)

        for name in ("ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                self.assertEqual(spy.count(name), 0)
                self.assertEqual(security_state_of(gateway, name),
                                 security_state_of(wired_ecus(InstrumentedProvider(),
                                                              policy=c2a4.deny_target(ECU_A_REQ)),
                                                   name))


# ======================================================================
# §7 —— functional DENY + provider
# ======================================================================


class TestProviderSeamFunctionalDeny(unittest.TestCase):
    """A4-P2：广播逐 ECU 求值，但 deny 时 provider **不得**因广播而提前执行。"""

    def _setup(self):
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_all)
        spy = c2a4.ProcessSpy(gateway)
        return provider, gateway, spy

    def test_all_three_decisions_are_denied(self):
        provider, gateway, _spy = self._setup()
        _results, trace = gateway.route_with_trace(FUNCTIONAL_ADDRESS, REQUEST_SEED)

        self.assertEqual(len(trace.decisions), 3)
        self.assertEqual(
            [(d.target, d.allowed) for d in trace.decisions],
            [(ECU_A_REQ, False), (ECU_B_REQ, False), (ECU_C_REQ, False)],
        )
        self.assertTrue(all(d.addressing_mode == "functional" for d in trace.decisions))

    def test_provider_is_not_invoked_by_the_broadcast(self):
        provider, gateway, _spy = self._setup()
        gateway.route(FUNCTIONAL_ADDRESS, REQUEST_SEED)
        self.assertEqual(provider.calls, 0, "全 deny 广播 ⇒ provider 调用次数必须为 0")

    def test_mixed_policy_authorizes_per_ecu_without_partial_bypass(self):
        """
        混合策略（只拒 A）下必须**逐 ECU 正确授权**，且**被拒者的 provider 不得被执行**。

        .. important::
           ``provider.calls`` 在混合场景下**不为 0 才是正确的** ——
           B/C 被放行 ⇒ 它们各自调用 provider 一次（共 2 次）。
           因此"无部分绕过"的证据**不是**总数，而是**按 ECU 归因**
           （``ProcessSpy`` 的逐 ECU 计数）+ 被拒者的状态不变。

           全 deny 广播的 ``provider.calls == 0`` 由
           :meth:`test_provider_is_not_invoked_by_the_broadcast` 单独覆盖。
        """
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_target(ECU_A_REQ))
        spy = c2a4.ProcessSpy(gateway)
        before = {n: security_state_of(gateway, n) for n in ("ECU_A", "ECU_B", "ECU_C")}

        results, trace = gateway.route_with_trace(FUNCTIONAL_ADDRESS, REQUEST_SEED)

        # 被拒者：完全不可见
        self.assertEqual(spy.count("ECU_A"), 0, "被拒 ECU 的 process 必须为 0")
        self.assertEqual(security_state_of(gateway, "ECU_A"), before["ECU_A"],
                         "被拒 ECU 的 security state 不得变化")
        self.assertFalse(trace.decisions[0].allowed)

        # 放行者：正常执行
        self.assertEqual(spy.count("ECU_B"), 1)
        self.assertEqual(spy.count("ECU_C"), 1)
        self.assertEqual(provider.calls, 2, "只有被放行的 2 个 ECU 调用了 provider")
        self.assertNotEqual(security_state_of(gateway, "ECU_B"), before["ECU_B"])

        # wire 契约不变
        self.assertEqual(results, [])
        self.assertEqual(
            [(d.target, d.allowed) for d in trace.decisions],
            [(ECU_A_REQ, False), (ECU_B_REQ, True), (ECU_C_REQ, True)],
        )

    def test_wire_is_empty(self):
        provider, gateway, _spy = self._setup()
        self.assertEqual(gateway.route(FUNCTIONAL_ADDRESS, REQUEST_SEED), [])


# ======================================================================
# §8 —— provider ALLOW 路径：证明顺序没有反过来
# ======================================================================


class TestProviderSeamAllowPath(unittest.TestCase):
    """
    本用例的目的**不是**重证 C3-A2 已证的状态机，
    而是证明 ``policy → provider → process`` 这个顺序没有反过来。
    """

    def test_physical_allow_counts_are_1_1_1(self):
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_target(ECU_B_REQ))
        spy = c2a4.ProcessSpy(gateway)

        results, trace = gateway.route_with_trace(ECU_A_REQ, REQUEST_SEED)

        self.assertTrue(trace.decisions[0].allowed)
        self.assertEqual(provider.calls, 1, "允许 ⇒ provider 恰好 1 次")
        self.assertEqual(spy.count("ECU_A"), 1, "允许 ⇒ process 恰好 1 次")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], ECU_A_RESP)
        self.assertEqual(results[0][1][:2], bytes([0x67, 0x01]), "0x27 确实被执行")

    def test_physical_allow_advances_the_target_ecu(self):
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_target(ECU_B_REQ))
        gateway.route(ECU_A_REQ, REQUEST_SEED)

        state = security_state_of(gateway, "ECU_A")
        self.assertIsNotNone(state["pending_seed"])
        self.assertEqual(state["seed_counter"], 1)

    def test_functional_allow_counts_are_3_3_3(self):
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider)
        spy = c2a4.ProcessSpy(gateway)

        results, trace = gateway.route_with_trace(FUNCTIONAL_ADDRESS, REQUEST_SEED)

        self.assertEqual(len(trace.decisions), 3, "policy 逐 ECU 求值 3 次")
        self.assertTrue(all(d.allowed for d in trace.decisions))
        self.assertEqual(provider.calls, 3, "3 个 ECU 各自调用 provider 一次")
        self.assertEqual(spy.total, 3, "3 个 ECU 各自 process 一次")
        self.assertEqual(results, [], "C2-OD-4：功能寻址恒不收集响应")

    def test_functional_allow_advances_every_ecu_independently(self):
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider)
        gateway.route(FUNCTIONAL_ADDRESS, REQUEST_SEED)

        for name in ("ECU_A", "ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                state = security_state_of(gateway, name)
                self.assertIsNotNone(state["pending_seed"])
                self.assertEqual(state["seed_counter"], 1)


# ======================================================================
# §9 —— provider 与 custom handler 同时存在
# ======================================================================


class TestProviderAndCustomHandler(unittest.TestCase):
    """
    ``custom handler`` 与 ``provider`` 的优先关系。

    .. important::
       必须区分两件**完全不同**的事：

       ```
       ① "custom handler 能绕过 provider"  → 既有 handler override 语义，**允许**
       ② "custom handler 能绕过 policy"    → **必须 FAIL**
       ```
    """

    def _both_seams(self, policy, provider):
        """**构造期**同时给出 custom handler 与 ECU 级 provider（C3-A1 的既有语义）。"""
        calls = []

        def custom(payload):
            calls.append(payload)
            return b"\x99" + payload

        ecu = VirtualEcu(
            "X", 0x700, 0x708, handler=custom, services_provider=provider
        )
        gateway = DiagnosticGateway([ecu], policy=policy)
        return gateway, calls

    def test_allow_keeps_the_custom_handler(self):
        provider = InstrumentedProvider.with_marker(SID_SECURITY_ACCESS)
        gateway, calls = self._both_seams(c2a4.deny_target(0x7FF), provider)

        results = gateway.route(0x700, REQUEST_SEED)

        self.assertEqual(calls, [REQUEST_SEED], "custom handler 必须被调用")
        self.assertEqual(results, [(0x708, b"\x99" + REQUEST_SEED)])
        self.assertEqual(provider.calls, 0, "custom handler 生效时 provider 不参与")
        self.assertEqual(provider.marker_hits, [], "provider 的 marker 不得执行")

    def test_deny_blocks_the_custom_handler(self):
        """② 的反证：**custom handler 不能绕过 policy**。"""
        provider = InstrumentedProvider.with_marker(SID_SECURITY_ACCESS)
        gateway, calls = self._both_seams(c2a4.deny_all, provider)
        spy = c2a4.ProcessSpy(gateway)
        before = security_state_of(gateway, "X")

        results, trace = gateway.route_with_trace(0x700, REQUEST_SEED)

        self.assertEqual(results, [], "DENY ⇒ wire 为空")
        self.assertFalse(trace.decisions[0].allowed)
        self.assertEqual(spy.total, 0, "DENY ⇒ process() 不得被调用")
        self.assertEqual(calls, [], "DENY ⇒ custom handler 不得被调用")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(security_state_of(gateway, "X"), before)

    def test_provider_does_not_preempt_the_custom_handler(self):
        """① 的正证：custom handler 存在时，默认 provider 路径不得抢占它。"""
        provider = InstrumentedProvider.with_marker(SID_SECURITY_ACCESS)
        gateway, _calls = self._both_seams(c2a4.deny_target(0x7FF), provider)

        gateway.route(0x700, REQUEST_SEED)

        self.assertEqual(provider.marker_hits, [], "provider 的 handler 不得替换 custom handler")


# ======================================================================
# §10 —— same-SID provider override
# ======================================================================


class TestSameSidProviderOverride(unittest.TestCase):
    """
    C3-OQ-12：``{**SERVICES, **provider()}`` 允许**同名 SID 覆盖**。

    必须证明：**provider 能 override service implementation ≠ 能 override policy**。
    """

    def test_allow_lets_the_marker_handler_run(self):
        provider = InstrumentedProvider.with_marker(SID_SECURITY_ACCESS, b"\xEE\x01")
        gateway = wired_ecus(provider)
        spy = c2a4.ProcessSpy(gateway)

        results, trace = gateway.route_with_trace(ECU_A_REQ, REQUEST_SEED)

        self.assertTrue(trace.decisions[0].allowed)
        self.assertEqual(results, [(ECU_A_RESP, b"\xEE\x01")], "marker handler 确实执行")
        self.assertEqual(provider.marker_hits, [REQUEST_SEED])
        self.assertEqual(spy.count("ECU_A"), 1)
        self.assertEqual(provider.calls, 1)

    def test_deny_blocks_the_same_marker_handler(self):
        provider = InstrumentedProvider.with_marker(SID_SECURITY_ACCESS, b"\xEE\x01")
        gateway = wired_ecus(provider, policy=c2a4.deny_all)
        spy = c2a4.ProcessSpy(gateway)
        before = security_state_of(gateway, "ECU_A")

        results, trace = gateway.route_with_trace(ECU_A_REQ, REQUEST_SEED)

        self.assertEqual(provider.marker_hits, [], "DENY ⇒ marker handler 必须 0 次")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(spy.total, 0)
        self.assertEqual(results, [])
        self.assertFalse(trace.decisions[0].allowed)
        self.assertEqual(security_state_of(gateway, "ECU_A"), before)

    def test_non_overridden_sids_keep_the_static_service(self):
        """不同 SID ⇒ 静态 ``SERVICES`` 保留。"""
        provider = InstrumentedProvider.with_marker(SID_SECURITY_ACCESS)
        gateway = wired_ecus(provider)

        results = gateway.route(ECU_A_REQ, bytes([SID_READ_DID, 0xF1, 0x90]))

        self.assertEqual(results[0][1][:3], bytes([0x62, 0xF1, 0x90]))


# ======================================================================
# §11 —— C1 guard 不得被 same-SID override 绕过
# ======================================================================


class TestC1GuardPreservation(unittest.TestCase):
    """
    C3-OQ-12 冻结约束：**``services=`` 覆盖的是实现，不是授权。**

    选用 ``0x2E``：它在 ``_SPEC_EXECUTION`` 中（guard **实际执行**），
    且带 6 个 guard（含 session 与 security）。
    """

    def test_guard_still_rejects_before_the_marker_handler(self):
        provider = InstrumentedProvider.with_marker(SID_WRITE_DID, b"\xEE\x02")
        gateway = wired_ecus(provider)
        # 默认会话 + LOCKED ⇒ 0x2E 的 session guard 应当拒绝
        before = security_state_of(gateway, "ECU_A")

        results, trace = gateway.route_with_trace(
            ECU_A_REQ, bytes([SID_WRITE_DID, 0xF1, 0x02]) + b"XY"
        )

        self.assertEqual(
            results[0][1], bytes([0x7F, SID_WRITE_DID, 0x7F]), "会话 guard 必须仍然生效"
        )
        self.assertEqual(provider.marker_hits, [], "guard 拒绝 ⇒ marker handler 0 次")
        self.assertEqual(gateway.ecu_by_name("ECU_A").state.did_overrides, {},
                         "被拒 ⇒ state 不变")
        self.assertEqual(security_state_of(gateway, "ECU_A"), before)
        self.assertTrue(trace.decisions[0].allowed,
                        "C1 guard 属于 **C1 层** —— policy 仍应放行（两件事）")

    def test_guard_is_read_from_the_static_spec_not_the_override(self):
        """结构性证据：``resolve_service_spec`` 对已建模 SID **保留** guards。"""
        provider_table = {SID_WRITE_DID: lambda request, state: b"\xEE\x02"}
        spec = uds_server.resolve_service_spec(SID_WRITE_DID, provider_table)

        self.assertIsNotNone(spec)
        self.assertEqual(
            spec.guards, uds_server.SERVICE_SPECS[SID_WRITE_DID].guards,
            "覆盖表只能替换 handler，guards 必须来自静态规格表",
        )

    def test_guard_passes_when_conditions_are_met(self):
        """反向对照：条件满足时 marker handler 才执行（证明前一条不是恒拒绝）。"""
        provider = InstrumentedProvider.with_marker(SID_WRITE_DID, b"\xEE\x02")
        ecus = [c2a4.make_ecu("ECU_A", ECU_A_REQ, ECU_A_RESP, unlocked=True)]
        gateway = DiagnosticGateway(ecus, services_provider=provider)

        results = gateway.route(ECU_A_REQ, bytes([SID_WRITE_DID, 0xF1, 0x02]) + b"XY")

        self.assertEqual(results, [(ECU_A_RESP, b"\xEE\x02")])
        self.assertEqual(len(provider.marker_hits), 1)


# ======================================================================
# §12 —— policy 求值次数 / 顺序
# ======================================================================


class TestPolicyEvaluationOrderAndCount(unittest.TestCase):
    """**顺序必须是** ``policy → provider → process``。"""

    def _run(self, *, can_id, payload, policy, provider):
        gateway = wired_ecus(provider, policy=policy)
        spy = c2a4.ProcessSpy(gateway)
        results, trace = gateway.route_with_trace(can_id, payload)
        return provider, gateway, spy, results, trace

    def test_physical_allow_is_1_1_1(self):
        provider = InstrumentedProvider.with_security_access()
        provider, _gw, spy, _r, trace = self._run(
            can_id=ECU_A_REQ, payload=REQUEST_SEED,
            policy=c2a4.deny_target(ECU_B_REQ), provider=provider,
        )
        self.assertEqual(len(trace.decisions), 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(spy.total, 1)

    def test_physical_deny_is_1_0_0(self):
        provider = InstrumentedProvider.with_security_access()
        provider, _gw, spy, _r, trace = self._run(
            can_id=ECU_A_REQ, payload=REQUEST_SEED,
            policy=c2a4.deny_all, provider=provider,
        )
        self.assertEqual(len(trace.decisions), 1)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(spy.total, 0)

    def test_functional_allow_is_3_3_3(self):
        provider = InstrumentedProvider.with_security_access()
        provider, _gw, spy, _r, trace = self._run(
            can_id=FUNCTIONAL_ADDRESS, payload=REQUEST_SEED,
            policy=c2a4.deny_target(0x7FF), provider=provider,
        )
        self.assertEqual(len(trace.decisions), 3)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(spy.total, 3)

    def test_functional_deny_is_3_0_0(self):
        provider = InstrumentedProvider.with_security_access()
        provider, _gw, spy, _r, trace = self._run(
            can_id=FUNCTIONAL_ADDRESS, payload=REQUEST_SEED,
            policy=c2a4.deny_all, provider=provider,
        )
        self.assertEqual(len(trace.decisions), 3)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(spy.total, 0)

    def test_decision_order_and_fields_match_ecus_traversal(self):
        """不只数计数：decision 的**顺序与字段**也要逐条核对。"""
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider)
        _results, trace = gateway.route_with_trace(FUNCTIONAL_ADDRESS, REQUEST_SEED)

        self.assertEqual(
            [
                (d.target, d.addressing_mode, d.sid, d.allowed)
                for d in trace.decisions
            ],
            [
                (ECU_A_REQ, "functional", SID_SECURITY_ACCESS, True),
                (ECU_B_REQ, "functional", SID_SECURITY_ACCESS, True),
                (ECU_C_REQ, "functional", SID_SECURITY_ACCESS, True),
            ],
        )
        self.assertEqual(
            [d.target for d in trace.decisions],
            [ecu.request_id for ecu in gateway.ecus],
        )


# ======================================================================
# §13 —— C2 四个既有 seam 复验（保持四层证据）
# ======================================================================


class TestC2SeamsReverification(unittest.TestCase):
    """C2-A4 的四个 seam 在 C3 之后**逐一复验**（四层证据不变）。"""

    def _deny_four_layers(self, gateway, can_id=ECU_A_REQ, payload=REQUEST_SEED):
        spy = c2a4.ProcessSpy(gateway)
        before = c2a4.state_projection(gateway)

        results, trace = gateway.route_with_trace(can_id, payload)

        self.assertEqual(results, [])
        self.assertEqual(spy.total, 0)
        self.assertEqual(c2a4.state_projection(gateway), before)
        self.assertTrue(trace.decisions)
        self.assertTrue(all(not d.allowed for d in trace.decisions))

    def test_handler_seam_still_denied(self):
        calls = []

        def custom(payload):
            calls.append(payload)
            return b"\x99"

        gateway = DiagnosticGateway(
            [VirtualEcu("X", 0x700, 0x708, handler=custom)], policy=c2a4.deny_all
        )
        self._deny_four_layers(gateway, can_id=0x700)
        self.assertEqual(calls, [])

    def test_topology_seam_still_denied(self):
        ecus = [c2a4.make_ecu("Y", 0x600, 0x608), c2a4.make_ecu("Z", 0x601, 0x609)]
        gateway = DiagnosticGateway(ecus, functional_address=0x6DF, policy=c2a4.deny_all)
        self._deny_four_layers(gateway, can_id=0x600)

    def test_transport_seam_still_denied(self):
        transport = c2a4.RecordingTransport([(ECU_A_REQ, REQUEST_SEED)])
        gateway = wired_ecus(InstrumentedProvider.with_security_access(),
                             policy=c2a4.deny_all)
        spy = c2a4.ProcessSpy(gateway)
        runtime = c2a4.make_runtime(gateway, transport)

        self.assertTrue(runtime.serve_once(timeout=0.0))

        self.assertEqual(transport.sent, [])
        self.assertEqual(spy.total, 0)
        self.assertEqual(c2a4.state_projection(gateway),
                         c2a4.state_projection(wired_ecus(InstrumentedProvider(),
                                                          policy=c2a4.deny_all)))

    def test_build_runtime_seam_still_denied(self):
        """C3 明确变化过的 seam —— 接线 provider 不得制造第二条 bypass path。"""
        transport = c2a4.RecordingTransport([(ECU_A_REQ, REQUEST_SEED)])
        provider = InstrumentedProvider.with_security_access()

        with mock.patch.object(
            gateway_runtime, "build_default_gateway",
            side_effect=lambda services_provider=None: wired_ecus(
                provider, policy=c2a4.deny_all
            ),
        ), mock.patch.object(gateway_runtime, "IsotpTransport", return_value=transport):
            runtime = gateway_runtime.build_runtime(
                interface="vcan0", log=lambda _m: None
            )

        spy = c2a4.ProcessSpy(runtime.gateway)
        runtime.serve_once(timeout=0.0)

        self.assertEqual(transport.sent, [])
        self.assertEqual(spy.total, 0)
        self.assertEqual(provider.calls, 0, "deny ⇒ provider 不得提前执行")

    def test_build_runtime_seam_allow_works(self):
        """反向对照：``build_runtime`` + allow 时 provider 按 A1 接线正常工作。"""
        transport = c2a4.RecordingTransport([(ECU_A_REQ, REQUEST_SEED)])
        provider = InstrumentedProvider.with_security_access()

        with mock.patch.object(
            gateway_runtime, "build_default_gateway",
            side_effect=lambda services_provider=None: wired_ecus(provider),
        ), mock.patch.object(gateway_runtime, "IsotpTransport", return_value=transport):
            runtime = gateway_runtime.build_runtime(
                interface="vcan0", log=lambda _m: None
            )

        runtime.serve_once(timeout=0.0)

        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(transport.sent[0][0], ECU_A_RESP)
        self.assertEqual(transport.sent[0][1][:2], bytes([0x67, 0x01]))


# ======================================================================
# §14 —— Source-level invariant audit（AST 级）
# ======================================================================


class TestSourceLevelInvariants(unittest.TestCase):
    """结构化检查（AST），不只靠 grep。"""

    @staticmethod
    def _calls_of(tree, attr: str) -> list:
        return sorted(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr
        )

    def test_process_call_sites_are_still_exactly_two(self):
        """C2 冻结不变量：全包 ``.process(...)`` 调用点恰好 2 处。"""
        gateway_tree = ast.parse(read_source(__import__("gateway")))
        self.assertEqual(len(self._calls_of(gateway_tree, "process")), 2)

        # 其余模块（含 gateway_runtime）当前均**不**调用 process —— 逐模块断言，
        # 使"新增第二处执行路径"立刻可见
        for module_name in ("gateway_runtime", "uds_server", "ecu", "keygen"):
            with self.subTest(module=module_name):
                tree = ast.parse(read_source(__import__(module_name)))
                self.assertEqual(self._calls_of(tree, "process"), [])

    def test_policy_is_evaluated_only_in_route_core(self):
        """policy 只在 ``_route_core``（经 ``_allow``）中求值。"""
        source = read_source(__import__("gateway"))
        tree = ast.parse(source)

        allow_lines = self._calls_of(tree, "_allow")
        self.assertTrue(allow_lines, "应当存在 _allow 调用")

        route_core = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_route_core"
        )
        for line in allow_lines:
            with self.subTest(line=line):
                self.assertTrue(route_core.lineno <= line <= route_core.end_lineno,
                                "_allow 必须只在 _route_core 内被调用")

    def test_no_second_policy_implementation_outside_gateway(self):
        """``gateway_runtime`` 仍不得出现第二套 policy / 授权实现。"""
        source = read_source(gateway_runtime)
        for token in ("policy", "_allow", "_by_request", "PolicyDecision", "allow_all"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_provider_does_not_touch_policy_or_state(self):
        """
        provider 的**契约**（C3-OD-14 限制 1）：只做 dependency binding。

        证据：production 中 provider 的调用点只有一处（默认 handler），
        且其返回值只被合并进 ``services=``，不接触 policy / EcuState。
        """
        source = read_source(__import__("gateway"))
        self.assertIn("services={**uds_server.SERVICES, **services_provider()}", source)
        for token in ("services_provider()[" , "services_provider().get"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_gateway_does_not_know_clock_or_config(self):
        source = read_source(__import__("gateway"))
        for token in ("_now", "SECURITY_CONFIG", "keygen"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_no_functional_0x27_special_case(self):
        for module_name in ("gateway", "gateway_runtime"):
            with self.subTest(module=module_name):
                source = read_source(__import__(module_name))
                for token in ("is_functional", "functional_0x27", "SID == 0x27", "0x27 =="):
                    self.assertNotIn(token, source)

    def test_no_second_security_access_state_machine(self):
        """网关层不得出现 0x27 状态机实现细节。"""
        source = read_source(__import__("gateway"))
        for token in ("seed_counter", "failed_attempts", "lockout_until", "verify_key",
                      "derive_key", "generate_seed"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


# ======================================================================
# §16 —— Negative controls
# ======================================================================


class TestNegativeControls(unittest.TestCase):
    """证明上述断言**不是**"永远 PASS"。"""

    def _deny_baseline(self):
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_all)
        spy = c2a4.ProcessSpy(gateway)
        results, trace = gateway.route_with_trace(ECU_A_REQ, REQUEST_SEED)
        return provider, gateway, spy, results, trace

    def test_baseline_is_a_clean_deny(self):
        provider, _gw, spy, results, trace = self._deny_baseline()
        self.assertEqual(results, [])
        self.assertEqual(provider.calls, 0)
        self.assertEqual(spy.total, 0)
        self.assertFalse(trace.decisions[0].allowed)

    def test_policy_decision_perturbation_is_detectable(self):
        _p, _gw, _s, _r, trace = self._deny_baseline()
        self.assertFalse(trace.decisions[0].allowed)
        self.assertTrue(trace.decisions[0].allowed is False)

    def test_provider_count_perturbation_would_be_detected(self):
        """provider 计数断言对"被调用过"敏感。"""
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_target(0x7FF))
        gateway.route(ECU_A_REQ, REQUEST_SEED)
        self.assertEqual(provider.calls, 1, "允许路径下计数为 1（⇒ 断言非恒零）")

    def test_state_projection_detects_a_change(self):
        """
        反向自证：``state_projection`` 三字段**确实能**检出变化。

        .. note::
           必须用**会改这三字段**的 payload —— ``0x27`` 只推进
           ``pending_seed`` / ``seed_counter``，**不在**三字段投影内，
           用它做对照会得到一个**恒真**的假断言。
           这里改用 ``10 03``（会话切换）。
        """
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_target(0x7FF))
        before = c2a4.state_projection(gateway)
        gateway.route(ECU_A_REQ, bytes([0x10, 0x03]))
        self.assertNotEqual(
            c2a4.state_projection(gateway), before,
            "允许路径确实改变 state ⇒ state 断言非恒真",
        )

    def test_security_state_projection_detects_a_change(self):
        """三字段投影对 0x27 **不敏感** —— 这正是 A4 需要 security_state 投影的原因。"""
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_target(0x7FF))
        state_before = c2a4.state_projection(gateway)
        sec_before = security_state_of(gateway, "ECU_A")

        gateway.route(ECU_A_REQ, REQUEST_SEED)

        self.assertEqual(c2a4.state_projection(gateway), state_before,
                         "0x27 不改变三字段投影")
        self.assertNotEqual(security_state_of(gateway, "ECU_A"), sec_before,
                            "但 security_state 投影必须检出变化")

    def test_wire_assertion_detects_a_response(self):
        provider = InstrumentedProvider.with_security_access()
        gateway = wired_ecus(provider, policy=c2a4.deny_target(0x7FF))
        self.assertNotEqual(gateway.route(ECU_A_REQ, REQUEST_SEED), [])


# ======================================================================
# DEFECT-1 回归护栏（修复后**必须通过**；一旦回归立刻失败）
# ======================================================================


class TestDefectRegressionGuard(unittest.TestCase):
    """
    **DEFECT-1 的永久回归护栏** —— 原 A4 的 reproduction 用例，
    在 PR #56 修复后转为"正确行为必须成立"的常驻断言。

    ```
    现象
        `DiagnosticGateway(ecus, services_provider=…)`（**网关级**注入）
        会**静默覆盖** ECU 自带的 custom handler。

    触发条件（三种形式对比）
        A) VirtualEcu(handler=custom, services_provider=prov)          → handler 保留 ✓
        B) VirtualEcu(handler=custom) + gw(services_provider=prov)     → **handler 被覆盖** ✗
        C) VirtualEcu(默认) + gw(services_provider=prov)               → 预期行为（默认 handler 重绑）✓

    根因
        gateway.py::DiagnosticGateway.__init__ 中：

            if ecu.services_provider is None:
                ecu.services_provider = services_provider
                if ecu.handler is not None:
                    ecu.handler = _default_handler_for(ecu.state, services_provider)

        该 `if ecu.handler is not None` **无法区分**
        "未接线的默认 handler" 与 "用户显式注入的 custom handler"。

    影响
        破坏 C2-A4 已审计的 seam-1 语义（"handler 注入可替换**实现**"）。
        生产路径（build_runtime → build_default_gateway）走形式 C ⇒ **不受影响**；
        既有 gate 也未覆盖形式 B ⇒ **无人发现**。

    为什么不是授权 bypass
        policy **仍然生效**（deny 依旧静默丢弃）。
        受损的是"实现替换"语义，不是"授权"语义。

    本类断言**正确行为**（custom handler 应被保留）。
    该行为在 PR #56 修复后**成立** ⇒ 本类通过；若哪天回归，本类立刻失败。
    """

    def test_gateway_level_provider_must_not_replace_a_custom_handler(self):
        calls = []

        def custom(payload):
            calls.append(payload)
            return b"\x99" + payload

        provider = InstrumentedProvider.with_security_access()
        ecu = VirtualEcu("X", 0x700, 0x708, handler=custom)
        gateway = DiagnosticGateway([ecu], services_provider=provider)

        self.assertIs(
            gateway.ecus[0].handler, custom,
            "网关级 provider 不得覆盖 ECU 自带的 custom handler"
            "（破坏 C2-A4 seam-1：handler 注入可替换实现）",
        )

        results = gateway.route(0x700, REQUEST_SEED)
        self.assertEqual(calls, [REQUEST_SEED], "custom handler 必须被调用")
        self.assertEqual(results, [(0x708, b"\x99" + REQUEST_SEED)])

    def test_post_construction_handler_reassignment_is_preserved(self):
        """
        **构造后**把 ``handler`` 重赋为 custom —— 网关接线仍不得替换它。

        这是本修复采用 **handler 身份**（``handler is not default_handler``）
        而不是 boolean 的**直接理由**：

        * boolean（如构造时记录 ``handler_is_default=True``）在事后赋值后
          **过期**，会把 custom handler 误判为 framework handler 并覆盖；
        * 身份判定天然跟随真实来源。
        """
        calls = []

        def custom(payload):
            calls.append(payload)
            return bytes([0x99]) + payload

        provider = InstrumentedProvider.with_security_access()
        ecu = VirtualEcu("X", 0x700, 0x708)      # 构造期不给 handler ⇒ 绑定 default
        self.assertFalse(ecu.has_custom_handler, "构造后应是 framework default")

        ecu.handler = custom                      # **构造后**重赋
        self.assertTrue(ecu.has_custom_handler, "重赋后必须被判定为 custom")

        gateway = DiagnosticGateway([ecu], services_provider=provider)

        self.assertIs(
            gateway.ecus[0].handler, custom,
            "构造后重赋的 custom handler 同样不得被网关级 provider 替换",
        )
        self.assertEqual(
            gateway.route(0x700, REQUEST_SEED), [(0x708, bytes([0x99]) + REQUEST_SEED)]
        )
        self.assertEqual(calls, [REQUEST_SEED])

    def test_construct_time_provider_form_is_unaffected(self):
        """反向对照：形式 A（构造期同时给出两者）**当前是正确的** —— 缺陷只限于形式 B。"""
        calls = []

        def custom(payload):
            calls.append(payload)
            return b"\x99" + payload

        provider = InstrumentedProvider.with_security_access()
        ecu = VirtualEcu("X", 0x700, 0x708, handler=custom, services_provider=provider)
        gateway = DiagnosticGateway([ecu])

        self.assertIs(gateway.ecus[0].handler, custom)
        self.assertEqual(gateway.route(0x700, REQUEST_SEED), [(0x708, b"\x99" + REQUEST_SEED)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
