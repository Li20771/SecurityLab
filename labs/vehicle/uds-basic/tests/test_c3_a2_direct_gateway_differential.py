#!/usr/bin/env python3
"""
C3-A2 —— **Direct ECU ↔ Gateway 差分**

设计出处：``docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md``
§13（Direct ECU ↔ Gateway 等价性策略）/ §11（多 ECU 不变量）/ §10.3（functional）。

## oracle 是**未被修改的直连 ECU 路径**

```
oracle  = Direct ECU   `ecu.handle_uds_request(request)`
subject = Gateway      `DiagnosticGateway.route()`，经 C3-A1 接线的 0x27

同一输入 ／ 同一初始 security state ／ 同一时间与 SECURITY_CONFIG
        ↓
    response 等价  ∧  state 等价  ∧  ECU 隔离正确
```

**oracle 不是**：手写 expected ／ C3-A0 的 Gateway golden（那只是"C3 接线之前"的
历史事实与迁移参照）／ 当前 Gateway 自己的 expected ／ 任何重新生成的"正确答案"。

## 核心判据

```
response mismatch = 0
state    mismatch = 0
```

## 两类期望语义（**刻意区分**）

```
equivalent   两侧必须等价（默认）—— 覆盖绝大多数 case
policy_deny  Gateway 因 **C2 policy deny** 静默丢弃 ⇒ 两侧**必然不同**
             （直连路径没有 policy 层），故断言"丢弃由策略引起"：
                 · Gateway 不产生任何帧
                 · Gateway 目标 ECU 状态保持初始值
                 · **oracle 侧确实会产生正响应**（证明不是 0x27 路径坏了）
                 · policy decision 记录 allowed=False 且 target 正确
```

## functional 0x27（设计 §10.3 冻结：ALLOW）

**不能因为"没有 response"就把它从差分中删掉。** 本文件的处理是：

```
Gateway 侧：response 恒为 []（C2-OD-4）
比较映射  ：Direct 单 ECU  vs  Gateway 的**每一个** ECU
            —— 每个 ECU 都应各自表现得像"直连 ECU 收到同一请求"
```

运行：

    python3 tests/test_c3_a2_direct_gateway_differential.py
或：
    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest
from dataclasses import replace

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import c3_differential_lib as lib  # noqa: E402

#: 冻结的 case 规模（精确相等 ⇒ "静默删减"立刻失败）。
EXPECTED_CASE_COUNT = 23

#: 覆盖矩阵（用户指定的 20 项）→ 代表性 case_id。
REQUIRED_COVERAGE = {
    "physical 27 01": ("D01-physical-request-seed",),
    "physical 27 02": ("D02-physical-send-key",),
    "wrong key": ("D03-wrong-key",),
    "third failure / lockout": ("D04-third-failure-lockout",),
    "lockout expiry": ("D05-lockout-expiry",),
    "seed expiry": ("D06-seed-expiry",),
    "consumed seed replay": ("D07-consumed-seed-replay",),
    "unlocked request seed": ("D08-seed-while-unlocked",),
    "session switch": ("D09-session-switch-relock",),
    "ECU reset": ("D10-ecu-reset-relock",),
    "protected DID locked/unlocked": (
        "D11-protected-did-locked",
        "D12-protected-did-unlocked",
    ),
    "response_id mapping": (
        "D13a-target-ecu-a",
        "D13b-target-ecu-b",
        "D13c-target-ecu-c",
    ),
    "target ECU isolation": ("D14-isolation-unlock-a", "D15-isolation-lockout-a"),
    "per-ECU seed_counter / pending_seed / failed_attempts": (
        "D16-a-request-seed-b-request-seed",
        "D17-per-ecu-seed-counter",
    ),
    "physical policy allow / deny": ("D18-policy-allow", "D19-policy-deny"),
    "functional 27 known limitation": (
        "D20-functional-request-seed",
        "D21-functional-send-key",
    ),
}


def _suite():
    """整套差分（进程内只跑一次）。"""
    global _SUITE_CACHE
    if _SUITE_CACHE is None:
        _SUITE_CACHE = [lib.run_case(case) for case in lib.build_cases()]
    return _SUITE_CACHE


_SUITE_CACHE = None


# ======================================================================
# 核心 gate
# ======================================================================


class TestDifferentialGate(unittest.TestCase):
    """
    **A2 的核心证明**：同一输入下 Direct 与 Gateway 等价。

    判定完全由两侧**真实执行**的差分决定。
    """

    @classmethod
    def setUpClass(cls):
        cls.results = _suite()

    def test_case_set_is_frozen(self):
        self.assertEqual(len(self.results), EXPECTED_CASE_COUNT)

    def test_every_case_executes_both_paths_exactly_once(self):
        for result in self.results:
            with self.subTest(case_id=result.case.case_id):
                self.assertEqual(result.direct.implementation, lib.IMPL_DIRECT)
                self.assertEqual(result.gateway.implementation, lib.IMPL_GATEWAY)
                self.assertEqual(result.direct.response_count, len(result.case.steps))
                self.assertEqual(result.gateway.response_count, len(result.case.steps))

    def test_no_case_is_mismatched(self):
        """gate 的最终判据：``mismatches == 0``。"""
        for result in self.results:
            with self.subTest(case_id=result.case.case_id):
                self.assertTrue(result.matched, result.failure_report())

    def test_response_matches_for_every_equivalent_case(self):
        for result in self.results:
            if result.case.expectation != "equivalent":
                continue
            with self.subTest(case_id=result.case.case_id):
                self.assertTrue(result.response_matches, result.failure_report())

    def test_state_matches_for_every_equivalent_case(self):
        for result in self.results:
            if result.case.expectation != "equivalent":
                continue
            with self.subTest(case_id=result.case.case_id):
                self.assertTrue(result.state_matches, result.failure_report())

    def test_aggregate_counts_are_locked(self):
        """gate 的**汇总数字**（A2 报告直接引用的口径）。"""
        equivalent = [r for r in self.results if r.case.expectation == "equivalent"]
        response_matches = sum(1 for r in equivalent if r.response_matches)
        state_matches = sum(1 for r in equivalent if r.state_matches)
        mismatches = sum(1 for r in self.results if not r.matched)

        self.assertEqual(len(self.results), EXPECTED_CASE_COUNT)
        self.assertEqual(response_matches, len(equivalent))
        self.assertEqual(state_matches, len(equivalent))
        self.assertEqual(mismatches, 0)

    def test_coverage_matrix_is_complete(self):
        declared = {cid for ids in REQUIRED_COVERAGE.values() for cid in ids}
        actual = {r.case.case_id for r in self.results}
        self.assertEqual(actual, declared)


# ======================================================================
# 独立性（设计 §13 / 用户硬约束）
# ======================================================================


class TestPathIndependence(unittest.TestCase):
    """
    两侧必须**各自新建实例、各自执行**。

    禁止：Direct 执行后拿**同一批** ECU 再跑 Gateway。
    """

    def test_gateway_uses_fresh_ecus_each_run(self):
        case = lib.build_cases()[0]
        first = lib.run_case(case)
        second = lib.run_case(case)

        self.assertIsNot(first.gateway, second.gateway)
        self.assertTrue(
            all(
                a is not b
                for a, b in zip(
                    first.gateway.state.values(), second.gateway.state.values(), strict=True
                )
            )
        )

    def test_direct_state_is_restored_after_execution(self):
        """Direct 路径执行后必须恢复 ``ecu.STATE``，不留副作用。"""
        import ecu

        before = ecu.STATE
        lib.run_case(lib.build_cases()[0])
        self.assertIs(ecu.STATE, before)

    def test_direct_and_gateway_do_not_share_state_objects(self):
        """两条路径的状态投影来自**不同**的实例 —— 由构造顺序保证。"""
        import c3_differential_lib as d

        case = d.build_cases()[0]
        direct = d.direct_execution(case, d.Clock())
        gateway = d.gateway_execution(case, d.Clock())

        # 两侧的执行对象及其状态投影必须是**不同对象**
        # （两侧引用都持有 ⇒ 用 `is not` 而非 id() 比较，避免 id 复用造成的假通过）
        self.assertIsNot(direct, gateway)
        self.assertIsNot(direct.state, gateway.state)
        self.assertIsNot(direct.security_state, gateway.security_state)

        # 结构差异（**不是** "长度不同" —— 两者长度恰好都是 3，容易误判）：
        #   Direct  ：**单个** ECU 的三字段投影（flat）
        #   Gateway ：**按 ECU 名索引**的多 ECU 投影
        self.assertEqual(set(direct.state), set(d.STATE_FIELDS), "Direct 是单 ECU 的 flat 投影")
        self.assertEqual(set(gateway.state), set(d.TOPOLOGY_NAMES), "Gateway 按 ECU 名索引")
        for name in gateway.targets:
            with self.subTest(ecu=name):
                self.assertEqual(set(gateway.state[name]), set(d.STATE_FIELDS))
        self.assertEqual(set(direct.security_state), set(d.SECURITY_STATE_FIELDS))
        for name in gateway.targets:
            with self.subTest(ecu=name):
                self.assertEqual(
                    set(gateway.security_state[name]), set(d.SECURITY_STATE_FIELDS)
                )

    def test_both_paths_use_the_same_time_and_config(self):
        """时间与安全配置输入一致（两侧各自 Clock 实例，时间线相同）。"""
        case = lib.build_cases()[0]
        result = lib.run_case(case)

        # 两侧的 seed 完全一致 ⇒ 证明 seed_counter 与时钟输入相同
        self.assertEqual(result.direct.responses, result.gateway.responses)
        self.assertEqual(
            result.direct.security_state["seed_counter"],
            result.gateway.security_state["ECU_A"]["seed_counter"],
        )


# ======================================================================
# 逐项覆盖证据
# ======================================================================


class TestPerCaseEvidence(unittest.TestCase):
    """把关键 case 的**实际值**钉住，使漂移立刻可见。"""

    @classmethod
    def setUpClass(cls):
        cls.by_id = {r.case.case_id: r for r in _suite()}

    def _responses(self, case_id):
        return [step[0] for step in self.by_id[case_id].direct.responses]

    def test_request_seed_yields_seed_on_both_paths(self):
        responses = self._responses("D01-physical-request-seed")
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0][:2], bytes([0x67, 0x01]))
        self.assertEqual(len(responses[0]), 2 + lib.keygen.SEED_LENGTH)

    def test_send_key_unlocks_on_both_paths(self):
        result = self.by_id["D02-physical-send-key"]
        self.assertEqual(self._responses("D02-physical-send-key")[-1], bytes([0x67, 0x02]))
        self.assertEqual(
            result.direct.state["security_level"], lib.uds_types.SECURITY_LEVEL1
        )
        self.assertEqual(
            result.gateway.state["ECU_A"]["security_level"], lib.uds_types.SECURITY_LEVEL1
        )

    def test_wrong_key_and_lockout_sequence(self):
        responses = self._responses("D04-third-failure-lockout")
        self.assertEqual(responses[1][:3], bytes([0x7F, 0x27, 0x35]))
        self.assertEqual(responses[3][:3], bytes([0x7F, 0x27, 0x35]))
        self.assertEqual(responses[5][:3], bytes([0x7F, 0x27, 0x36]))
        self.assertEqual(responses[6][:3], bytes([0x7F, 0x27, 0x37]))

    def test_lockout_expiry_restores_service(self):
        responses = self._responses("D05-lockout-expiry")
        self.assertEqual(responses[-1][:2], bytes([0x67, 0x01]))

    def test_seed_expiry_and_replay_and_unlocked(self):
        self.assertEqual(
            self._responses("D06-seed-expiry")[-1][:3], bytes([0x7F, 0x27, 0x24])
        )
        self.assertEqual(
            self._responses("D07-consumed-seed-replay")[-1][:3], bytes([0x7F, 0x27, 0x24])
        )
        self.assertEqual(
            self._responses("D08-seed-while-unlocked")[-1][:3], bytes([0x7F, 0x27, 0x22])
        )

    def test_session_and_reset_relock(self):
        for case_id in ("D09-session-switch-relock", "D10-ecu-reset-relock"):
            with self.subTest(case_id=case_id):
                result = self.by_id[case_id]
                self.assertEqual(
                    result.direct.state["security_level"], lib.uds_types.SECURITY_LOCKED
                )
                self.assertEqual(
                    result.gateway.state["ECU_A"]["security_level"],
                    lib.uds_types.SECURITY_LOCKED,
                )

    def test_protected_did_locked_and_unlocked(self):
        self.assertEqual(
            self._responses("D11-protected-did-locked")[0][:3], bytes([0x7F, 0x22, 0x33])
        )
        unlocked = self._responses("D12-protected-did-unlocked")[-1]
        self.assertEqual(unlocked[:3], bytes([0x62, 0xF1, 0x87]))

    def test_response_id_mapping_per_target(self):
        """覆盖项 12：0x7E0→0x7E8 / 0x7E1→0x7E9 / 0x7E2→0x7EA。"""
        expected = {
            "D13a-target-ecu-a": (0x7E8, "ECU_A"),
            "D13b-target-ecu-b": (0x7E9, "ECU_B"),
            "D13c-target-ecu-c": (0x7EA, "ECU_C"),
        }
        for case_id, (response_id, ecu_name) in expected.items():
            with self.subTest(case_id=case_id):
                result = self.by_id[case_id]
                self.assertEqual(result.gateway.response_ids_of(0), (response_id,))
                self.assertEqual(result.compared_targets, (ecu_name,))
                self.assertTrue(result.matched, result.failure_report())


# ======================================================================
# per-ECU 隔离（覆盖项 13–18）
# ======================================================================


class TestPerEcuIsolation(unittest.TestCase):
    """A 的解锁 / 锁定 / seed 计数**不得**波及 B、C。"""

    @classmethod
    def setUpClass(cls):
        cls.by_id = {r.case.case_id: r for r in _suite()}

    def _pristine(self, name: str) -> dict:
        return lib.security_state_projection(lib.fresh_state({}))

    def test_unlocking_a_leaves_b_and_c_locked(self):
        result = self.by_id["D14-isolation-unlock-a"]
        states = result.gateway.security_state

        self.assertEqual(states["ECU_A"]["seed_counter"], 1)
        for name in ("ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                self.assertEqual(states[name], self._pristine(name), "未被触及的 ECU 必须保持初始态")
                self.assertEqual(
                    result.gateway.state[name]["security_level"],
                    lib.uds_types.SECURITY_LOCKED,
                )

    def test_lockout_of_a_does_not_lock_b_or_c(self):
        result = self.by_id["D15-isolation-lockout-a"]
        states = result.gateway.security_state

        self.assertIsNotNone(states["ECU_A"]["lockout_until"])
        self.assertEqual(states["ECU_A"]["failed_attempts"], 3)
        for name in ("ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                self.assertIsNone(states[name]["lockout_until"])
                self.assertEqual(states[name]["failed_attempts"], 0)
                self.assertEqual(states[name]["seed_counter"], 0)

    def test_seed_counter_pending_seed_failed_attempts_are_per_ecu(self):
        result = self.by_id["D17-per-ecu-seed-counter"]
        states = result.gateway.security_state

        self.assertEqual(states["ECU_A"]["seed_counter"], 2)
        for name in ("ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                self.assertEqual(states[name]["seed_counter"], 0)
                self.assertIsNone(states[name]["pending_seed"])
                self.assertEqual(states[name]["failed_attempts"], 0)

    def test_each_ecu_security_state_equals_the_direct_oracle(self):
        """隔离的**正面**证据：目标 ECU 与 oracle 一致（而不仅是"其它未变"）。"""
        for case_id in ("D14-isolation-unlock-a", "D15-isolation-lockout-a"):
            with self.subTest(case_id=case_id):
                result = self.by_id[case_id]
                self.assertEqual(
                    result.gateway.security_state["ECU_A"], result.direct.security_state
                )


# ======================================================================
# policy 交互（覆盖项 19）
# ======================================================================


class TestPolicyInteraction(unittest.TestCase):
    """allow 时等价；deny 时按**策略语义**判定（两侧必然不同）。"""

    @classmethod
    def setUpClass(cls):
        cls.by_id = {r.case.case_id: r for r in _suite()}

    def test_allow_keeps_the_paths_equivalent(self):
        result = self.by_id["D18-policy-allow"]
        self.assertTrue(result.matched, result.failure_report())
        self.assertEqual(result.direct.responses, result.gateway.responses)

    def test_deny_is_evaluated_by_the_dedicated_model(self):
        result = self.by_id["D19-policy-deny"]
        self.assertEqual(result.case.expectation, "policy_deny")
        self.assertTrue(result.matched, result.failure_report())

    def test_deny_drops_silently_and_leaves_state_pristine(self):
        result = self.by_id["D19-policy-deny"]
        self.assertEqual(result.gateway.responses, ((),))
        self.assertEqual(
            result.gateway.security_state["ECU_A"], self._pristine()
        )

    def test_deny_case_proves_the_oracle_would_have_succeeded(self):
        """反向对照：oracle 侧确实产生正响应 ⇒ 丢弃是**策略**造成的。"""
        result = self.by_id["D19-policy-deny"]
        self.assertEqual(result.direct.responses[0][0][:2], bytes([0x67, 0x01]))

    def test_deny_records_a_disallowing_decision(self):
        result = self.by_id["D19-policy-deny"]
        decisions = result.gateway.policy_decisions[0]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["target"], 0x7E0)
        self.assertEqual(decisions[0]["sid"], 0x27)
        self.assertFalse(decisions[0]["allowed"])

    def _pristine(self) -> dict:
        return lib.security_state_projection(lib.fresh_state({}))


# ======================================================================
# functional 27（覆盖项 20 —— 冻结的 known limitation）
# ======================================================================


class TestFunctionalSecurityAccess(unittest.TestCase):
    """
    functional 0x27 = **ALLOW**（设计 §10.3）。

    **不能因为"没有 response"就把它从差分中删掉** —— 本类证明：
    Gateway 恒返回 ``[]``，但**每一个** ECU 都各自与 Direct oracle 一致。

    **不得**为绕过该现象而增加 functional 专门分支。
    """

    @classmethod
    def setUpClass(cls):
        cls.results = [r for r in _suite() if r.case.addressing == lib.ADDRESSING_FUNCTIONAL]

    def test_functional_cases_exist(self):
        self.assertEqual(len(self.results), 2)

    def test_gateway_returns_no_response_for_functional(self):
        """C2-OD-4：功能寻址恒不收集响应。"""
        for result in self.results:
            with self.subTest(case_id=result.case.case_id):
                self.assertTrue(result.gateway.responses, "case 必须有步骤")
                self.assertTrue(
                    all(not step for step in result.gateway.responses),
                    "功能寻址下**每一步**都必须不产生帧",
                )
                self.assertTrue(result.response_matches)

    def test_every_ecu_matches_the_direct_oracle(self):
        """逐 ECU 比较 —— 每个 ECU 都表现得像"直连 ECU 收到同一请求"。"""
        for result in self.results:
            with self.subTest(case_id=result.case.case_id):
                self.assertEqual(len(result.compared_targets), 3)
                for name in result.compared_targets:
                    with self.subTest(ecu=name):
                        self.assertEqual(result.gateway.state[name], result.direct.state)
                        self.assertEqual(
                            result.gateway.security_state[name], result.direct.security_state
                        )

    def test_functional_seed_advances_every_ecu(self):
        """known limitation 的**正面**记录：三个 ECU 都被推进。"""
        result = next(r for r in self.results if r.case.case_id == "D20-functional-request-seed")
        for name in ("ECU_A", "ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                state = result.gateway.security_state[name]
                self.assertIsNotNone(state["pending_seed"])
                self.assertEqual(state["seed_counter"], 1)

    def test_functional_does_not_change_security_level_or_penalty(self):
        result = next(r for r in self.results if r.case.case_id == "D20-functional-request-seed")
        for name in ("ECU_A", "ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                self.assertEqual(
                    result.gateway.state[name]["security_level"], lib.uds_types.SECURITY_LOCKED
                )
                self.assertEqual(result.gateway.security_state[name]["failed_attempts"], 0)

    def test_no_functional_special_case_was_introduced(self):
        """反向自证：production 中不存在 functional 0x27 的专门分支。"""
        import gateway as gateway_module
        import gateway_runtime

        for module in (gateway_module, gateway_runtime):
            with self.subTest(module=module.__name__):
                with open(module.__file__, encoding="utf-8") as handle:
                    source = handle.read()
                for token in ("is_functional", "functional_0x27", "FUNCTIONAL_ADDRESS == 0x27"):
                    self.assertNotIn(token, source)


# ======================================================================
# Comparator 反伪造（negative control）
# ======================================================================


class TestComparatorNegativeControl(unittest.TestCase):
    """
    证明 ``mismatch == 0`` **不是**"比较器永远通过"。

    用真实 case 的**单侧扰动**验证比较器能检出差异。
    """

    def _base(self):
        return lib.run_case(lib.build_cases()[0])  # D01

    def test_unperturbed_case_matches(self):
        self.assertTrue(self._base().matched)

    def test_response_perturbation_is_detected(self):
        base = self._base()
        perturbed = replace(base.gateway, responses=((b"\x67\x01\xde\xad\xbe\xef",),))
        result = lib.DifferentialResult(case=base.case, direct=base.direct, gateway=perturbed)

        self.assertFalse(result.matched)
        self.assertFalse(result.response_matches)
        self.assertTrue(result.state_matches, "响应扰动不得污染状态判定")

    def test_state_perturbation_is_detected(self):
        base = self._base()
        state = {name: dict(value) for name, value in base.gateway.state.items()}
        state["ECU_A"]["security_level"] = 0xFF
        perturbed = replace(base.gateway, state=state)
        result = lib.DifferentialResult(case=base.case, direct=base.direct, gateway=perturbed)

        self.assertFalse(result.matched)
        self.assertFalse(result.state_matches)
        self.assertTrue(result.response_matches)

    def test_security_state_perturbation_is_detected(self):
        base = self._base()
        security = {
            name: dict(value) for name, value in base.gateway.security_state.items()
        }
        security["ECU_A"]["seed_counter"] = 99
        perturbed = replace(base.gateway, security_state=security)
        result = lib.DifferentialResult(case=base.case, direct=base.direct, gateway=perturbed)

        self.assertFalse(result.matched)
        self.assertFalse(result.state_matches)

    def test_failure_report_locates_the_case_and_field(self):
        base = self._base()
        security = {
            name: dict(value) for name, value in base.gateway.security_state.items()
        }
        security["ECU_A"]["failed_attempts"] = 7
        perturbed = replace(base.gateway, security_state=security)
        result = lib.DifferentialResult(case=base.case, direct=base.direct, gateway=perturbed)
        report = result.failure_report()

        self.assertIn("case=D01-physical-request-seed", report)
        self.assertIn("ECU_A.failed_attempts", report)
        self.assertIn("direct=0", report)
        self.assertIn("gateway=7", report)


# ======================================================================
# A0 Golden 与 A2 的关系（反向自证）
# ======================================================================


class TestA2DoesNotUseTheA0GoldenAsOracle(unittest.TestCase):
    """
    C3-A0 Golden 是"C3 接线之前"的**历史基线**，只能作历史事实 / 迁移参照。

    A2 的 oracle 是 **Direct ECU 路径**。本类做反向自证。
    """

    def test_differential_lib_does_not_read_the_a0_corpus(self):
        with open(lib.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for token in ("c3_security_access_baseline", "c3_baseline_lib", "load_corpus"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_this_test_file_does_not_read_the_a0_corpus(self):
        with open(__file__, encoding="utf-8") as handle:
            source = handle.read()
        # 拼接字面量，避免断言本身把该字符串写进文件（自我指涉）
        self.assertNotIn("c3_security_access_" + "baseline.json", source)

    def test_oracle_is_the_live_direct_ecu_path(self):
        """oracle 必须是**活着的**直连入口，而不是任何冻结数据。"""
        source = open(lib.__file__, encoding="utf-8").read()
        self.assertIn("ecu.handle_uds_request", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
