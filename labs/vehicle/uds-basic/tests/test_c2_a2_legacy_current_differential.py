#!/usr/bin/env python3
"""
UDS Basic —— C2-A2：**Legacy ↔ Current 差分对拍**

设计出处：``docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md`` §13
（C2-A2 = "legacy ↔ policy 逐字节对拍（空策略）"）。

## 本阶段的核心主张

> **同一个 case，Legacy 冻结实现与 Current 生产实现各自独立执行一次，
> response 与 state 逐字节一致。**

```
same case ──┬── Legacy （gateway.py@4059cf5，逐字节冻结）
            └── Current（gateway.py @ HEAD，空策略）
                        ↓
              legacy-live == current-live     ← A2 的**核心证明**
                        ↓
                  mismatches == 0
```

## 与 C2-A1 的分工（**不要混淆**）

```
A1  : Current  ──→  冻结 JSON      （重放：当前实现能否复现历史记录）
A2  : Legacy   ──→  Current        （差分：两条**真实执行**是否一致）
      + 两侧各自与冻结 JSON 的**锚点**一致性（辅助证据，不是核心证明）
```

A1 的 Golden helper（``c2_gateway_golden_lib``）在本文件中**不参与主执行路径**；
A2 的执行、实例化、比较全部由 ``c2_differential_lib`` 独立完成。

## trace 的定位

``PolicyTrace`` **不是** Legacy↔Current 的差分轴 —— legacy 没有该概念
（corpus 头 ``policy_execution = "absent"`` 是历史事实）。它只作为
**Current-only 辅助不变量**存在，用于证明"策略机制确实被求值过"，
**不得**被包装成第三个跨实现差分维度。

## 运行

    python3 tests/test_c2_a2_legacy_current_differential.py
或：
    python3 -m pytest labs/vehicle/uds-basic/tests/test_c2_a2_legacy_current_differential.py
或：
    bash labs/vehicle/uds-basic/tests/run_tests.sh unit
"""

import hashlib
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import c2_differential_lib as diff  # noqa: E402

#: 整套 20 条 case 的差分结果缓存（见 :func:`_suite`）。
_SUITE_CACHE = None

#: A0 冻结 corpus 的 LF 规范化 SHA-256（**与 C2-A0 结构测试中的常量同值**）。
#:
#: 刻意在 A2 侧**独立复述**一遍：使"A2 没有偷偷改写 Golden"这件事在本文件内
#: 自证，而不必依赖另一个测试文件。
FROZEN_CORPUS_LF_SHA256 = "d1d358927a4eb86e750fda13a961d935b1afccd3e01e50c26a696dcc7f43c208"

#: 冻结输入集的规模（精确相等 ⇒ "静默删减 case" 立刻失败）。
FROZEN_CASE_COUNT = 20


def _suite() -> tuple:
    """
    整套 20 条 case 的差分结果 + 两侧锚点（进程内只跑一次）。

    缓存只影响**执行次数**，不影响隔离性：每次 ``run_case`` 仍然各自构造
    Legacy / Current 的全新 ECU 实例。
    """
    global _SUITE_CACHE
    if _SUITE_CACHE is None:
        results, legacy_anchors, current_anchors = diff.run_all_with_anchors()
        _SUITE_CACHE = (results, legacy_anchors, current_anchors, diff.summarize_with_anchors(
            results, legacy_anchors, current_anchors
        ))
    return _SUITE_CACHE


def _cases_by_id() -> dict:
    return {case.case_id: case for case in diff.frozen_cases()}


# ======================================================================
# 结构性防伪 —— legacy 冻结副本与 current 实现确实是两份东西
# ======================================================================


class TestLegacyFrozenCopy(unittest.TestCase):
    """冻结副本的**来源可证**：哈希对得上、符号确实不同、git 可交叉校验。"""

    def test_vendor_path_is_the_tests_legacy_dir(self):
        self.assertEqual(
            os.path.relpath(diff.LEGACY_IMPL_PATH, _HERE),
            os.path.join("legacy", "c2_a0_gateway_legacy_impl.py"),
        )
        self.assertTrue(os.path.isfile(diff.LEGACY_IMPL_PATH))

    def test_vendor_blob_sha_matches_pinned_git_blob(self):
        """
        git blob SHA 必须等于 ``4059cf5`` 下 ``gateway.py`` 的 blob。

        该值由**内容**独立重算（``sha1("blob <len>\\0" + content)``），
        不依赖 git —— 因此在浅克隆 / 导出目录中同样成立。
        """
        self.assertEqual(diff.file_blob_sha(diff.LEGACY_IMPL_PATH), diff.LEGACY_SOURCE_BLOB_SHA)

    def test_vendor_lf_sha256_matches_pinned_value(self):
        self.assertEqual(diff.file_lf_sha256(diff.LEGACY_IMPL_PATH), diff.LEGACY_SOURCE_LF_SHA256)

    def test_git_cross_check_when_available(self):
        """
        git 可用时，冻结副本必须与 ``git show 4059cf5:<path>`` **逐字节一致**。

        git（或该 commit）不可用时**跳过**而非失败 —— A2 必须在无 git 环境下
        仍可执行。跳过时本用例只报告事实，不假装校验过。
        """
        check = diff.git_cross_check()
        if not check.available:
            self.skipTest(f"git 交叉校验不可用：{check.detail}")
        self.assertTrue(check.matches, check.detail)

    def test_vendor_contains_no_current_only_policy_symbols(self):
        """legacy 副本中**不得**出现任何 policy 符号（否则它就不是 pre-policy 实现）。"""
        self.assertEqual(
            diff.legacy_source_violations(),
            [],
            "冻结副本含 current-only policy 符号 —— 来源状态错误",
        )

    def test_current_implementation_keeps_the_policy_path(self):
        """反向：current 实现必须**保留** policy 路径，否则"两条路径"并非不同实现。"""
        self.assertEqual(diff.current_source_missing_symbols(), [])

    def test_two_paths_are_different_modules(self):
        legacy_module = diff.load_legacy_gateway()
        current_module = diff.load_current_gateway()
        self.assertIsNot(legacy_module, current_module)
        self.assertNotEqual(legacy_module.__name__, current_module.__name__)
        self.assertNotEqual(legacy_module.__file__, current_module.__file__)

        # policy API 落在 DiagnosticGateway **类**上（不是模块级属性）。
        self.assertTrue(hasattr(current_module.DiagnosticGateway, "route_with_trace"))
        self.assertTrue(hasattr(current_module.DiagnosticGateway, "_route_core"))
        self.assertFalse(hasattr(legacy_module.DiagnosticGateway, "route_with_trace"))
        self.assertFalse(hasattr(legacy_module.DiagnosticGateway, "_route_core"))
        self.assertFalse(hasattr(legacy_module, "DEFAULT_POLICY"))
        self.assertFalse(hasattr(legacy_module, "PolicyTrace"))


class TestControlledSharedDependencies(unittest.TestCase):
    """
    受控共享依赖的 pin —— "两条路径只差 gateway.py"必须是**可验证事实**。

    这些协议层 / ECU 基础设施模块被 Legacy 与 Current 共同调用；只要它们仍与
    ``4059cf5`` 逐字节相同，A2 的差分就**只**反映 gateway.py 的差异。
    """

    def test_no_shared_dependency_drift(self):
        drift = diff.shared_dependency_drift()
        self.assertEqual(
            drift,
            {},
            "协议层共享依赖已偏离 4059cf5 —— A2 的差分前提失效，"
            "必须重新评估（而不是静默更新 pin）",
        )

    def test_pinned_set_covers_the_ecu_package_except_the_subject(self):
        """
        pin 集合 = 整个 ecu 包的模块，**除**被差分的主体 ``gateway.py``。
        """
        names = set(diff.CONTROLLED_SHARED_DEPENDENCIES)
        on_disk = {
            name for name in os.listdir(os.path.dirname(diff.CURRENT_IMPL_PATH))
            if name.endswith(".py")
        }
        self.assertEqual(on_disk - names, {"gateway.py"})
        self.assertNotIn("gateway.py", names)


# ======================================================================
# 两条路径必须独立实例化、分别执行
# ======================================================================


class TestTwoPathsAreIndependentlyInstantiated(unittest.TestCase):
    """硬约束：不共用 gateway 对象、不共用 ECU 对象、不共用 EcuState。"""

    @classmethod
    def setUpClass(cls):
        cls.case = diff.frozen_cases()[0]
        cls.legacy = diff.run_legacy_execution(cls.case)
        cls.current = diff.run_current_execution(cls.case)

    def test_gateways_are_distinct_objects(self):
        self.assertIsNot(self.legacy.gateway, self.current.gateway)

    def test_ecu_objects_are_not_shared(self):
        legacy_ecus = self.legacy.gateway.ecus
        current_ecus = self.current.gateway.ecus
        self.assertEqual(len(legacy_ecus), len(current_ecus))
        for left, right in zip(legacy_ecus, current_ecus, strict=True):
            with self.subTest(ecu=left.name):
                self.assertIsNot(left, right)
                self.assertIsNot(left.state, right.state)
                self.assertIsNot(type(left), type(right))  # 两个模块各自的类
                self.assertEqual(left.name, right.name)

    def test_build_ecus_returns_fresh_instances_every_time(self):
        module = diff.load_legacy_gateway()
        first = diff.build_ecus(self.case, module)
        second = diff.build_ecus(self.case, module)
        self.assertIsNot(first[0], second[0])
        self.assertIsNot(first[0].state, second[0].state)
        self.assertEqual(first[0].name, second[0].name)

    def test_each_path_executes_exactly_once(self):
        self.assertEqual(self.legacy.executions, 1)
        self.assertEqual(self.current.executions, 1)

    def test_legacy_state_is_not_reused_by_current(self):
        """
        构造顺序保证：current 的 ECU 实例在 legacy 执行**之前**就已构造完毕，
        且是独立对象 —— 因此 legacy 的状态副作用**不可能**流入 current。
        """
        case = diff.frozen_cases()[0]
        legacy = diff.run_legacy_execution(case)
        current = diff.run_current_execution(case)
        for left, right in zip(legacy.gateway.ecus, current.gateway.ecus, strict=True):
            with self.subTest(ecu=left.name):
                self.assertIsNot(left.state, right.state)
                self.assertIsNot(left.state.did_overrides, right.state.did_overrides)

    def test_current_runs_under_the_production_default_policy(self):
        """**不为通过 A2 注入任何策略** —— current 必须跑在空策略（default-allow）下。"""
        module = diff.load_current_gateway()
        gateway = module.DiagnosticGateway(diff.build_ecus(self.case, module))
        self.assertIs(gateway.policy, module.DEFAULT_POLICY)

    def test_legacy_gateway_has_no_policy_parameter(self):
        """legacy 的 ``DiagnosticGateway`` 结构上不认识 policy（来源状态的事实）。"""
        module = diff.load_legacy_gateway()
        params = module.DiagnosticGateway.__init__.__code__.co_varnames
        self.assertNotIn("policy", params)


# ======================================================================
# 核心 gate —— Legacy live ↔ Current live
# ======================================================================


class TestDifferentialGate(unittest.TestCase):
    """
    **A2 的核心证明**：两条真实执行的行为一致。

    判定完全由 ``legacy != current`` 的差分决定，**不由**任何 Golden 锚点决定。
    """

    @classmethod
    def setUpClass(cls):
        cls.results, cls.legacy_anchors, cls.current_anchors, cls.summary = _suite()

    def test_input_set_is_the_twenty_frozen_cases(self):
        self.assertEqual(len(self.results), FROZEN_CASE_COUNT)

    def test_every_case_is_compared_by_both_paths(self):
        for result in self.results:
            with self.subTest(case_id=result.case_id):
                self.assertEqual(result.legacy.implementation, diff.IMPL_LEGACY)
                self.assertEqual(result.current.implementation, diff.IMPL_CURRENT)
                self.assertEqual(result.legacy.executions, 1)
                self.assertEqual(result.current.executions, 1)

    def test_response_matches_for_every_case(self):
        for result in self.results:
            with self.subTest(case_id=result.case_id):
                self.assertTrue(result.response_matches, result.failure_report())

    def test_state_matches_for_every_case(self):
        for result in self.results:
            with self.subTest(case_id=result.case_id):
                self.assertTrue(result.state_matches, result.failure_report())

    def test_no_case_is_mismatched(self):
        """gate 的最终判据：``mismatches == 0``。"""
        for result in self.results:
            with self.subTest(case_id=result.case_id):
                self.assertTrue(result.matched, result.failure_report())

    def test_differential_detects_fan_out_per_ecu_not_only_aggregate(self):
        """
        功能寻址扇出必须**逐 ECU**比较 —— 只比 aggregate（``results == []``）
        无法发现"错误扇出 / 漏扇出"。

        证据：F1–F4 的 response 都是 ``[]``（两侧相同，差分敏感性为零），
        差异只可能出现在逐 ECU 状态投影里。
        """
        by_id = _cases_by_id()
        functional_address = diff.load_frozen_corpus()["functional_address"]
        checked = 0
        for result in self.results:
            case = by_id[result.case_id]
            if case.can_id != functional_address or not case.payload:
                continue
            checked += 1
            with self.subTest(case_id=result.case_id):
                self.assertEqual(
                    result.legacy.results, (), "功能寻址恒不收集响应（aggregate 无差分敏感性）"
                )
                self.assertEqual(result.current.results, ())
                self.assertEqual(result.legacy.results, result.current.results)
                self.assertEqual(
                    len(result.legacy.state),
                    len(case.ecus),
                    "状态投影必须覆盖每一个 ECU（逐 ECU 比较的前提）",
                )
                self.assertTrue(result.state_matches, result.failure_report())
        self.assertGreater(checked, 0, "输入集中应当存在功能寻址扇出 case")

    def test_functional_fan_out_actually_reaches_every_ecu(self):
        """
        防止"两条路径**同向退化**"骗过差分。

        若广播在两侧都变成 no-op，则 response（两侧都是 ``[]``）与 state
        （两侧都没变）依然一致 —— 差分**照样通过**。因此必须另行断言
        "扇出**确实发生**"，且**实际到达的 ECU 集合**与冻结 Golden 记录的一致。

        这是对差分本身的**非平凡性**约束：差分只能证明"两者相同"，
        不能证明"两者都做了该做的事"。
        """
        by_id = _cases_by_id()
        functional_address = diff.load_frozen_corpus()["functional_address"]
        reached: set[str] = set()
        cases_with_change = 0

        for result in self.results:
            case = by_id[result.case_id]
            if case.can_id != functional_address or not case.payload:
                continue

            initial = {
                ecu["name"]: {
                    "session": ecu["state"]["session"],
                    "security_level": ecu["state"]["security_level"],
                    "did_overrides": dict(ecu["state"]["did_overrides"]),
                }
                for ecu in case.ecus
            }
            golden_changed = {
                name for name, state in case.expected_gateway_state.items() if state != initial[name]
            }
            live_changed = {
                name for name, state in result.legacy.state.items() if state != initial[name]
            }

            with self.subTest(case_id=result.case_id):
                self.assertEqual(
                    live_changed,
                    golden_changed,
                    "实际发生状态变化的 ECU 集合必须等于冻结 Golden 记录的集合 —— "
                    "否则扇出可能已同向退化，而差分对此不敏感",
                )
            if golden_changed:
                cases_with_change += 1
                reached |= live_changed

        self.assertGreater(cases_with_change, 0, "广播写入/会话切换 case 必须真实改变状态")
        self.assertEqual(len(reached), 3, "广播必须至少真正到达全部三个 ECU")

    def test_aggregate_counts_are_locked(self):
        """
        gate 的**汇总数字**（A2 报告直接引用的口径）。

        任何漂移（少比一条、多执行一次、出现 mismatch）都会使本用例失败。
        """
        summary = self.summary
        self.assertEqual(summary.cases, FROZEN_CASE_COUNT)
        self.assertEqual(summary.legacy_executions, FROZEN_CASE_COUNT)
        self.assertEqual(summary.current_executions, FROZEN_CASE_COUNT)
        self.assertEqual(summary.response_matches, FROZEN_CASE_COUNT)
        self.assertEqual(summary.state_matches, FROZEN_CASE_COUNT)
        self.assertEqual(summary.mismatches, 0)
        self.assertEqual(summary.mismatched_case_ids, ())
        self.assertTrue(summary.passed)

    def test_case_ids_are_unique_and_stable(self):
        ids = [result.case_id for result in self.results]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [case.case_id for case in diff.frozen_cases()])


# ======================================================================
# 历史锚点 —— 只是"各自与历史一致"的证据，**不是** A2 的核心证明
# ======================================================================


class TestGoldenAnchors(unittest.TestCase):
    """
    ```
    legacy-live  == frozen golden      ← 证明 legacy 重建**忠实**
    current-live == frozen golden      ← 回归锚点（与 C2-A1 同口径）
    ```

    这两项**不构成**"两条路径一致"的证明 —— 真正的证明是
    ``TestDifferentialGate`` 里的 ``legacy-live == current-live``。
    锚点的作用是：若差分通过但锚点失败，说明**两侧同时**偏离了历史事实
    （例如共享依赖或 harness 口径变化），是另一类缺陷。
    """

    @classmethod
    def setUpClass(cls):
        cls.results, cls.legacy_anchors, cls.current_anchors, cls.summary = _suite()

    def test_legacy_live_matches_frozen_golden_response(self):
        for anchor in self.legacy_anchors:
            with self.subTest(case_id=anchor.case_id):
                self.assertTrue(anchor.response_matches)

    def test_legacy_live_matches_frozen_golden_state(self):
        for anchor in self.legacy_anchors:
            with self.subTest(case_id=anchor.case_id):
                self.assertTrue(anchor.state_matches)

    def test_current_live_matches_frozen_golden_response(self):
        for anchor in self.current_anchors:
            with self.subTest(case_id=anchor.case_id):
                self.assertTrue(anchor.response_matches)

    def test_current_live_matches_frozen_golden_state(self):
        for anchor in self.current_anchors:
            with self.subTest(case_id=anchor.case_id):
                self.assertTrue(anchor.state_matches)

    def test_anchor_labels_are_explicit(self):
        """锚点必须自带标签，报告中不得被误读成"跨实现差分"。"""
        self.assertEqual(self.legacy_anchors[0].label, "legacy-vs-frozen-golden")
        self.assertEqual(self.current_anchors[0].label, "current-vs-frozen-golden")

    def test_anchor_counts_are_locked(self):
        self.assertEqual(self.summary.legacy_anchor_matches, FROZEN_CASE_COUNT)
        self.assertEqual(self.summary.current_anchor_matches, FROZEN_CASE_COUNT)


class TestFrozenInputSetIsUntouched(unittest.TestCase):
    """输入集只读：20 条、未被改写、未被扩写。"""

    def test_corpus_lf_sha256_is_unchanged(self):
        with open(diff.GOLDEN_PATH, "rb") as handle:
            data = handle.read().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(data).hexdigest(), FROZEN_CORPUS_LF_SHA256)

    def test_corpus_has_exactly_twenty_cases(self):
        self.assertEqual(len(diff.frozen_cases()), FROZEN_CASE_COUNT)

    def test_a2_defines_no_extra_inputs(self):
        """
        A2 **只**审计冻结输入 —— 本模块不得定义任何额外 case / SID / CAN ID。

        证据：case 集合与 corpus 完全相等（既没少，也没多）。
        """
        corpus_ids = [entry["case_id"] for entry in diff.load_frozen_corpus()["cases"]]
        self.assertEqual([case.case_id for case in diff.frozen_cases()], corpus_ids)


# ======================================================================
# 失败输出可审计
# ======================================================================


class TestFailureReportIsAuditable(unittest.TestCase):
    """
    比较器的失败输出必须能**直接定位**：
    case id / can_id / payload / mismatch 类型 / 两侧 response / 两侧 state /
    对应 ECU 与 field。

    本类用**合成执行结果**验证比较器本身（不触碰生产代码，也不制造真实失配）。
    """

    def _synthetic_case(self):
        return diff.FrozenCase(
            case_id="X-synthetic",
            description="合成 case：只为验证失败报告的定位能力",
            can_id=0x7E0,
            payload=bytes([0x22, 0xF1, 0x90]),
            ecus=(),
            expected_response=[],
            expected_gateway_state={},
        )

    def _synthetic_execution(self, implementation, results, state):
        return diff.Execution(implementation=implementation, results=results, state=state)

    def _mismatched_result(self):
        legacy = self._synthetic_execution(
            diff.IMPL_LEGACY,
            results=((0x7E8, bytes([0x62, 0xF1, 0x90])),),
            state={"ECU_A": {"session": 1, "security_level": 0, "did_overrides": {}}},
        )
        current = self._synthetic_execution(
            diff.IMPL_CURRENT,
            results=(),
            state={"ECU_A": {"session": 2, "security_level": 0, "did_overrides": {}}},
        )
        return diff.compare(self._synthetic_case(), legacy, current)

    def test_mismatch_is_detected(self):
        result = self._mismatched_result()
        self.assertFalse(result.matched)
        self.assertFalse(result.response_matches)
        self.assertFalse(result.state_matches)

    def test_report_locates_the_case(self):
        report = self._mismatched_result().failure_report()
        self.assertIn("case=X-synthetic", report)
        self.assertIn("0x7E0", report)
        self.assertIn("22 f1 90", report)

    def test_report_locates_the_response_diff(self):
        report = self._mismatched_result().failure_report()
        self.assertIn("response", report)
        self.assertIn("(0x7E8, 62 f1 90)", report)
        self.assertIn("<missing>", report)

    def test_report_locates_the_state_diff_by_ecu_and_field(self):
        report = self._mismatched_result().failure_report()
        self.assertIn("ECU_A.session", report)
        self.assertIn("legacy=1", report)
        self.assertIn("current=2", report)

    def test_response_diffs_are_positional(self):
        result = self._mismatched_result()
        self.assertEqual(len(result.response_diffs), 1)
        self.assertEqual(result.response_diffs[0].index, 0)
        self.assertIsNone(result.response_diffs[0].current)

    def test_matching_synthetic_case_produces_empty_diffs(self):
        """反向对照：一致时不得产生任何 diff（防止"永远报差异"的比较器）。"""
        execution_state = {"ECU_A": {"session": 1, "security_level": 0, "did_overrides": {}}}
        legacy = self._synthetic_execution(diff.IMPL_LEGACY, (), execution_state)
        current = self._synthetic_execution(diff.IMPL_CURRENT, (), execution_state)
        result = diff.compare(self._synthetic_case(), legacy, current)
        self.assertTrue(result.matched)
        self.assertEqual(result.response_diffs, ())
        self.assertEqual(result.state_diffs, ())

    def test_comparator_rejects_crossed_executions(self):
        """比较器必须拒绝把两条路径的 Execution 传反。"""
        state = {"ECU_A": {"session": 1, "security_level": 0, "did_overrides": {}}}
        legacy = self._synthetic_execution(diff.IMPL_LEGACY, (), state)
        current = self._synthetic_execution(diff.IMPL_CURRENT, (), state)
        with self.assertRaises(ValueError):
            diff.compare(self._synthetic_case(), current, legacy)


# ======================================================================
# trace —— Current-only 辅助证据（**不是**差分轴）
# ======================================================================


class TestCurrentOnlyTraceEvidence(unittest.TestCase):
    """
    trace 的定位：**Current 侧辅助不变量**。

    它证明"策略机制确实被求值过"，而**不是**第三个跨实现差分维度 ——
    legacy 侧结构上没有 trace（``Execution.trace is None``）。
    """

    @classmethod
    def setUpClass(cls):
        cls.results, _la, _ca, _s = _suite()
        cls.functional_address = diff.load_frozen_corpus()["functional_address"]
        cls.by_id = _cases_by_id()

    def test_legacy_side_has_no_trace(self):
        for result in self.results:
            with self.subTest(case_id=result.case_id):
                self.assertIsNone(
                    result.legacy.trace,
                    "legacy 没有 policy 概念 —— 其 Execution.trace 必须为 None",
                )

    def test_current_side_trace_shape_matches_the_input(self):
        for result in self.results:
            case = self.by_id[result.case_id]
            with self.subTest(case_id=result.case_id):
                decisions = result.current.trace.decisions
                if not case.payload:
                    self.assertEqual(decisions, (), "空 payload ⇒ 不进入策略（C2-OD-8）")
                elif case.can_id == self.functional_address:
                    self.assertEqual(
                        [d.target for d in decisions],
                        [ecu["request_id"] for ecu in case.ecus],
                        "functional ⇒ 逐 ECU 一条，顺序 == self.ecus 遍历顺序",
                    )
                elif case.can_id in case.request_ids:
                    self.assertEqual(len(decisions), 1, "physical 非空 payload ⇒ 恰好 1 条")
                else:
                    self.assertEqual(decisions, (), "未知物理 target ⇒ 不产生 decision")

    def test_all_decisions_are_allowed_under_the_empty_policy(self):
        total = 0
        for result in self.results:
            for decision in result.current.trace.decisions:
                total += 1
                with self.subTest(case_id=result.case_id, target=hex(decision.target)):
                    self.assertTrue(decision.allowed)
        self.assertGreater(total, 0, "空策略下仍必须有 decision 被真实求值")

    def test_trace_is_per_call(self):
        for result in self.results:
            with self.subTest(case_id=result.case_id):
                self.assertIsInstance(result.current.trace.decisions, tuple)


# ======================================================================
# 报告输出（非断言 —— 供人工审计与证据留档）
# ======================================================================


class TestEmitEvidenceReport(unittest.TestCase):
    """
    把 gate 的数字打印出来，便于人工审计与证据留档。

    默认只输出汇总块（保持测试入口输出简洁）；设 ``C2_A2_VERBOSE=1``
    可额外输出逐 case 明细。
    """

    def test_emit(self):
        results, _la, _ca, summary = _suite()
        lines = ["", "=== C2-A2 Legacy ↔ Current differential (20 frozen cases) ==="]
        lines.extend(summary.as_lines())
        if os.environ.get("C2_A2_VERBOSE") == "1":
            lines.append("")
            lines.extend(result.summary_line() for result in results)
        lines.append("")
        print("\n".join(lines))
        self.assertTrue(summary.passed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
