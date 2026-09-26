#!/usr/bin/env python3
"""
C3-A0 —— 0x27 SecurityAccess **实现前基线** 的结构测试与长期重放

设计出处：``docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md``
（§13 对拍模型 / §14.2 冻结类别 / C3-OD-15）。

## 本文件**不是** C3 的等价性对拍

A0 只保证：

    baseline 本身是**完整、有效、未被篡改**的，
    并且**忠实记录了 C3 实现之前的现状**。

**不**验证"Gateway 现在能用 0x27"（那是 C3-A1），
**不**验证"直连 ECU 与 Gateway 等价"（那是 C3-A2）。

## 两类 case 的重放语义**不同**（刻意分开，便于 C3-A1 精准迁移）

```
direct_ecu  （15 条）  C3 **不得**改变 ecu.py 的 0x27 行为
                       ⇒ 这些行必须**永久保持绿色**

gateway     （14 条）  其中带 frozen_boundary 标记的行记录的是 **C2 时代分层边界**
                       （Gateway 27 01 → 7F 27 11），C3-A1 **有意改变**它们
                       ⇒ 这些行是 C3-A1 的**迁移点**（C3-OD-15）
                       不带标记的行（未知 target / deny / 响应 ID 映射）
                       ⇒ 必须保持绿色
```

**A0 的 corpus 不含任何 A1 预期值** —— 它只记录"实现前是什么"。
`frozen_boundary` 字段只**描述这是 C2 时代边界**，**不预测**新取值。

运行：

    python3 tests/test_c3_a0_baseline.py
或：
    bash tests/run_tests.sh unit
"""

import hashlib
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import c3_baseline_lib as lib  # noqa: E402

#: 冻结的 corpus 规模（精确相等 ⇒ "静默删减"立刻失败）。
EXPECTED_CASE_COUNT = 29
EXPECTED_DIRECT_ECU_COUNT = 15
EXPECTED_GATEWAY_COUNT = 14

#: LF 规范化后的文件内容 SHA-256（平台无关；Windows 工作区可能是 CRLF）。
FROZEN_CORPUS_SHA256 = "f93b42f695d7ea2c91c730404eb02a0d7d27f4e65f09e4889d71cd02a4f8846c"

#: 记录 **C2 时代分层边界** 的 case（C3-A1 的迁移点）。
EXPECTED_FROZEN_BOUNDARY_CASES = (
    "G1-physical-27-on-ecu-a",
    "G3-old-pin-explicit",
    "G5-functional-27",
    "M1-physical-27-each-ecu",
    "M2-security-state-untouched-by-gateway-27",
    "M3-functional-27-state-effect",
    "M4-per-ecu-isolation-preserved",
    "C1-policy-allow-27",
    "C4-policy-dimensions",
    "X1-same-request-both-paths",
)

#: C2 时代 Gateway 对 0x27 的实际取值（旧 pin `7F 27 11` 所断言的事实）。
C2_ERA_GATEWAY_0X27_RESPONSE = "7f2711"

#: 设计文档 §14.2 要求的类别 → 代表性 case_id。
REQUIRED_CATEGORIES = {
    "27 01 RequestSeed": ("D1-request-seed",),
    "27 02 without seed": ("D2-send-key-without-seed",),
    "malformed length": ("D3-malformed-length-seed", "D4-malformed-length-key"),
    "unsupported subfunction": ("D5-unsupported-subfunction",),
    "wrong key": ("D6-wrong-key",),
    "third failure / lockout": ("D7-third-failure-lockout", "D8-lockout-expires"),
    "seed expiry": ("D9-seed-expiry",),
    "replay": ("D10-replay-consumed-seed",),
    "request seed while unlocked": ("D11-seed-while-unlocked",),
    "session switch": ("D12-session-switch-relocks",),
    "ECU reset": ("D13-ecu-reset-relocks",),
    "protected DID after unlock": (
        "D14-protected-did-while-locked",
        "D15-protected-did-after-unlock",
    ),
    "gateway physical": ("G1-physical-27-on-ecu-a", "G2-response-id-mapping"),
    "gateway old pin": ("G3-old-pin-explicit",),
    "unknown physical target": ("G4-unknown-physical-target",),
    "gateway functional": ("G5-functional-27",),
    "multi-ECU isolation": (
        "M1-physical-27-each-ecu",
        "M2-security-state-untouched-by-gateway-27",
        "M3-functional-27-state-effect",
        "M4-per-ecu-isolation-preserved",
    ),
    "C2 policy interaction": (
        "C1-policy-allow-27",
        "C2-policy-deny-27",
        "C3-policy-deny-is-silent-drop",
        "C4-policy-dimensions",
    ),
    "direct ↔ gateway comparison input": ("X1-same-request-both-paths",),
}


def _sha256(path: str) -> str:
    """对文件的 **LF 规范化内容** 求 SHA-256（平台无关）。"""
    with open(path, "rb") as handle:
        data = handle.read().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


class TestCorpusIsFrozen(unittest.TestCase):
    """基线一旦捕获即冻结；C3-A1 只允许**迁移**边界行，不允许重新生成。"""

    @classmethod
    def setUpClass(cls):
        cls.corpus = lib.load_corpus()
        cls.entries = cls.corpus["cases"]

    def test_corpus_sha256_is_frozen(self):
        self.assertEqual(_sha256(lib.CORPUS_PATH), FROZEN_CORPUS_SHA256)

    def test_corpus_size_is_frozen(self):
        self.assertEqual(len(self.entries), EXPECTED_CASE_COUNT)

    def test_path_split_is_frozen(self):
        direct = [e for e in self.entries if e["path"] == lib.PATH_DIRECT_ECU]
        gateway = [e for e in self.entries if e["path"] == lib.PATH_GATEWAY]
        self.assertEqual(len(direct), EXPECTED_DIRECT_ECU_COUNT)
        self.assertEqual(len(gateway), EXPECTED_GATEWAY_COUNT)

    def test_case_ids_are_unique(self):
        ids = [e["case_id"] for e in self.entries]
        self.assertEqual(len(ids), len(set(ids)))


class TestCaptureProvenance(unittest.TestCase):
    """基线必须可追溯、且明确标注"C3 实现尚不存在"。"""

    @classmethod
    def setUpClass(cls):
        cls.corpus = lib.load_corpus()

    def test_schema_version(self):
        self.assertEqual(self.corpus["schema_version"], lib.SCHEMA_VERSION)

    def test_c3_implementation_was_absent_at_capture(self):
        """A0 的语义前提：捕获时 C3 尚未接线。"""
        self.assertFalse(self.corpus["c3_implementation_present"])

    def test_gateway_0x27_was_unreachable_at_capture(self):
        """A0 记录的核心事实：捕获时 Gateway 尚不能执行 0x27。"""
        self.assertFalse(self.corpus["gateway_0x27_reachable"])

    def test_captured_at_commit_is_a_real_sha(self):
        commit = self.corpus["captured_at_commit"]
        self.assertEqual(len(commit), 40)
        self.assertTrue(all(c in "0123456789abcdef" for c in commit))

    def test_c2_design_baseline_recorded(self):
        self.assertEqual(
            self.corpus["c2_design_baseline"],
            "4cb85274b35ab52fa7df06a346f7afe495ab6714",
        )

    def test_functional_address_recorded(self):
        from gateway import FUNCTIONAL_ADDRESS

        self.assertEqual(self.corpus["functional_address"], FUNCTIONAL_ADDRESS)

    def test_projection_fields_recorded(self):
        self.assertEqual(
            self.corpus["state_projection_fields"], list(lib.STATE_PROJECTION_FIELDS)
        )
        self.assertEqual(
            self.corpus["security_state_projection_fields"],
            list(lib.SECURITY_STATE_FIELDS),
        )

    def test_capture_guard_has_the_expected_markers(self):
        """护栏的 marker 集合必须恰好覆盖 C3-A1 的两个接线符号。"""
        self.assertEqual(
            lib.C3_IMPLEMENTATION_MARKERS,
            ("services_provider", "security_access_services"),
        )

    def test_capture_guard_is_closed_during_a0(self):
        """
        护栏当前必须为**否** —— A0 的语义前提是"C3 尚未接线"。

        一旦该断言失败，说明 production 已进入 C3-A1，A0 基线**不可再生成**；
        届时由 C3-A1 按 C3-OD-15 **迁移边界行**，而不是重新捕获整份 corpus。
        """
        self.assertFalse(
            lib.c3_implementation_present(),
            "A0 阶段 gateway.py 不应含 C3 接线标记；若为 True 说明已进入 C3-A1",
        )


class TestCoverageMatrix(unittest.TestCase):
    """设计 §14.2 的类别必须齐备，且不得出现未登记 case。"""

    @classmethod
    def setUpClass(cls):
        cls.by_id = {e["case_id"]: e for e in lib.load_corpus()["cases"]}

    def test_required_categories_are_present(self):
        for category, case_ids in REQUIRED_CATEGORIES.items():
            with self.subTest(category=category):
                for case_id in case_ids:
                    self.assertIn(case_id, self.by_id)

    def test_no_unexpected_cases(self):
        declared = {cid for ids in REQUIRED_CATEGORIES.values() for cid in ids}
        self.assertEqual(set(self.by_id), declared)


class TestC2EraBoundaryIsRecorded(unittest.TestCase):
    """
    **C3-B1 的基线侧证据**（C3-OD-15）。

    A0 冻结"C2 时代 Gateway 27 → 7F 27 11"这一实现前事实，
    使 C3-A1 改变它时**有据可依**、且改变是**显式评审事件**。
    """

    @classmethod
    def setUpClass(cls):
        cls.by_id = {e["case_id"]: e for e in lib.load_corpus()["cases"]}

    def test_boundary_cases_are_exactly_the_declared_set(self):
        marked = {e["case_id"] for e in self.by_id.values() if e["frozen_boundary"]}
        self.assertEqual(marked, set(EXPECTED_FROZEN_BOUNDARY_CASES))

    def test_boundary_marker_value(self):
        for case_id in EXPECTED_FROZEN_BOUNDARY_CASES:
            with self.subTest(case_id=case_id):
                self.assertEqual(
                    self.by_id[case_id]["frozen_boundary"],
                    lib.FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
                )

    def test_gateway_0x27_records_the_c2_era_response(self):
        """C2-B1 旧 pin 所断言的事实：Gateway 27 01 → 7F 27 11。"""
        for case_id in ("G1-physical-27-on-ecu-a", "G3-old-pin-explicit"):
            with self.subTest(case_id=case_id):
                responses = self.by_id[case_id]["expected"]["steps"][0]["responses"]
                self.assertEqual(len(responses), 1)
                self.assertEqual(responses[0][1], C2_ERA_GATEWAY_0X27_RESPONSE)

    def test_every_physical_gateway_27_uses_the_targets_response_id(self):
        """响应 ID 映射现状：回应落在目标 ECU 自己的 response_id 上。"""
        entry = self.by_id["M1-physical-27-each-ecu"]
        ids = [s["responses"][0][0] for s in entry["expected"]["steps"]]
        self.assertEqual(ids, [0x7E8, 0x7E9, 0x7EA])

    def test_unknown_target_and_deny_produce_no_frame(self):
        for case_id in ("G4-unknown-physical-target", "C2-policy-deny-27"):
            with self.subTest(case_id=case_id):
                responses = self.by_id[case_id]["expected"]["steps"][0]["responses"]
                self.assertEqual(responses, [])

    def test_policy_deny_is_recorded_as_silent_drop(self):
        entry = self.by_id["C3-policy-deny-is-silent-drop"]
        for step in entry["expected"]["steps"]:
            with self.subTest(request=step["request"]):
                self.assertEqual(step["responses"], [])
                self.assertTrue(
                    all(not d["allowed"] for d in step["policy_decisions"]),
                    "deny case 的 policy decision 必须为 allowed=False",
                )

    def test_policy_dimensions_are_the_frozen_triple(self):
        """策略输入维度 baseline：(target, addressing_mode, sid) —— 无第四维。"""
        for entry in self.by_id.values():
            if entry["path"] != lib.PATH_GATEWAY:
                continue
            for step in entry["expected"]["steps"]:
                for decision in step["policy_decisions"]:
                    with self.subTest(case_id=entry["case_id"]):
                        self.assertEqual(
                            set(decision),
                            {"target", "addressing_mode", "sid", "allowed"},
                        )


class TestDirectEcuBaselineReplay(unittest.TestCase):
    """
    直连 ECU 基线的**长期重放端**。

    C3 **不得**改变 ``ecu.py`` 的 0x27 行为 ⇒ 这些行必须**永久绿色**。
    """

    @classmethod
    def setUpClass(cls):
        cls.entries = [
            e for e in lib.load_corpus()["cases"] if e["path"] == lib.PATH_DIRECT_ECU
        ]

    def test_direct_ecu_cases_replay_byte_for_byte(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                actual = lib.execute(entry)
                self.assertEqual(
                    [s["request"] for s in actual["steps"]],
                    [s["request"] for s in entry["expected"]["steps"]],
                    "展开后的请求序列必须与基线一致",
                )
                self.assertEqual(
                    [s["responses"] for s in actual["steps"]],
                    [s["responses"] for s in entry["expected"]["steps"]],
                    "直连 ECU 的 0x27 响应必须与实现前基线逐字节相同",
                )

    def test_direct_ecu_state_replays(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                actual = lib.execute(entry)
                self.assertEqual(actual["state"], entry["expected"]["state"])

    def test_direct_ecu_security_state_replays(self):
        """0x27 安全状态（pending_seed / failed_attempts / lockout / counter）必须复现。"""
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                actual = lib.execute(entry)
                self.assertEqual(
                    actual["security_state"], entry["expected"]["security_state"]
                )

    def test_aggregate_direct_ecu_counts_are_stable(self):
        """汇总数字常驻锁定 —— 任何漂移立刻可见。"""
        counts = {
            "cases": 0,
            "positive_seed": 0,
            "nrc_24": 0,
            "nrc_13": 0,
            "nrc_12": 0,
            "nrc_22": 0,
            "nrc_33": 0,
            "nrc_35": 0,
            "nrc_36": 0,
            "nrc_37": 0,
        }
        for entry in self.entries:
            counts["cases"] += 1
            for step in entry["expected"]["steps"]:
                for _rid, payload in step["responses"]:
                    if payload.startswith("6701"):
                        counts["positive_seed"] += 1
                    for key, nrc in (
                        ("nrc_24", "7f2724"),
                        ("nrc_13", "7f2713"),
                        ("nrc_12", "7f2712"),
                        ("nrc_22", "7f2722"),
                        ("nrc_33", "7f2233"),
                        ("nrc_35", "7f2735"),
                        ("nrc_36", "7f2736"),
                        ("nrc_37", "7f2737"),
                    ):
                        if payload == nrc:
                            counts[key] += 1
        self.assertEqual(counts["cases"], EXPECTED_DIRECT_ECU_COUNT)
        for key, value in counts.items():
            with self.subTest(metric=key):
                self.assertGreater(value, 0, f"{key} 在基线中必须至少出现一次")


class TestGatewayBaselineReplay(unittest.TestCase):
    """
    Gateway 基线的重放端 —— **分为两组，迁移点明确**。

    .. important::
       ``test_non_boundary_gateway_cases_replay`` 与
       ``test_c2_era_boundary_cases_are_still_the_pre_c3_fact`` 是**两个不同的断言**：

       * 前者覆盖"与 0x27 可达性无关"的 Gateway 行为（未知 target / deny / 响应 ID 映射），
         C3 **不得**改变它们；
       * 后者锁定"C3 实现前"的事实。C3-A1 **会**使其失败 —— 这正是设计预期
         （C3-OD-15），届时由 C3-A1 把**边界行**迁移为"Gateway 0x27 正常可达"契约，
         而**不是**重新生成整个 corpus。
    """

    @classmethod
    def setUpClass(cls):
        cls.entries = [
            e for e in lib.load_corpus()["cases"] if e["path"] == lib.PATH_GATEWAY
        ]

    def test_non_boundary_gateway_cases_replay(self):
        """与 0x27 可达性无关的 Gateway 行为 —— C3 必须保持不变。"""
        non_boundary = [e for e in self.entries if not e["frozen_boundary"]]
        self.assertTrue(non_boundary, "应当存在非边界 gateway case")
        for entry in non_boundary:
            with self.subTest(case_id=entry["case_id"]):
                actual = lib.execute(entry)
                self.assertEqual(
                    [s["responses"] for s in actual["steps"]],
                    [s["responses"] for s in entry["expected"]["steps"]],
                )
                self.assertEqual(actual["state"], entry["expected"]["state"])
                self.assertEqual(
                    actual["security_state"], entry["expected"]["security_state"]
                )

    def test_c2_era_boundary_cases_are_still_the_pre_c3_fact(self):
        """
        锁定 **C3 实现前** 的 Gateway 事实。

        C3-A1 落地后本用例**预期失败** —— 这是设计中的迁移信号（C3-OD-15），
        不是回归。迁移方式见类 docstring。
        """
        boundary = [e for e in self.entries if e["frozen_boundary"]]
        self.assertEqual(len(boundary), len(EXPECTED_FROZEN_BOUNDARY_CASES))
        for entry in boundary:
            with self.subTest(case_id=entry["case_id"]):
                actual = lib.execute(entry)
                self.assertEqual(
                    [s["responses"] for s in actual["steps"]],
                    [s["responses"] for s in entry["expected"]["steps"]],
                )

    def test_boundary_cases_never_return_a_seed_pre_c3(self):
        """实现前的强断言：Gateway 路径**不产生任何** 0x27 肯定响应。"""
        for entry in self.entries:
            if not entry["frozen_boundary"]:
                continue
            for step in entry["expected"]["steps"]:
                for _rid, payload in step["responses"]:
                    with self.subTest(case_id=entry["case_id"]):
                        self.assertFalse(
                            payload.startswith("6701") or payload.startswith("6702"),
                            "C3 实现前 Gateway 不应产生 0x27 肯定响应",
                        )

    def test_gateway_security_state_is_pristine_pre_c3(self):
        """实现前事实：Gateway 路径下**没有任何** ECU 的 0x27 状态被触碰。"""
        for entry in self.entries:
            for ecu_name, sec in entry["expected"]["security_state"].items():
                with self.subTest(case_id=entry["case_id"], ecu=ecu_name):
                    self.assertIsNone(sec["pending_seed"])
                    self.assertEqual(sec["failed_attempts"], 0)
                    self.assertIsNone(sec["lockout_until"])
                    self.assertEqual(sec["seed_counter"], 0)
                    self.assertFalse(sec["seed_consumed"])


class TestBaselineHasNoA1Expectations(unittest.TestCase):
    """
    **反向自证**：baseline 不得混入任何 A1 预期行为。

    证据：实现前的 Gateway 事实被如实记录为"不可达"，
    且 corpus 头部显式声明 ``gateway_0x27_reachable = false``。
    """

    @classmethod
    def setUpClass(cls):
        cls.corpus = lib.load_corpus()

    def test_corpus_declares_unreachable_not_expected_reachable(self):
        self.assertFalse(self.corpus["gateway_0x27_reachable"])
        self.assertNotIn("expected_after_c3", self.corpus)
        self.assertNotIn("a1_expected", self.corpus)

    def test_no_case_predicts_a_future_value(self):
        """case 内不得出现任何"改版后应为什么"的字段。"""
        for entry in self.corpus["cases"]:
            with self.subTest(case_id=entry["case_id"]):
                for forbidden in ("expected_after_c3", "a1_expected", "predicted"):
                    self.assertNotIn(forbidden, entry)

    def test_frozen_boundary_only_describes_never_predicts(self):
        """``frozen_boundary`` 是**描述性**标记，取值只能是 C2 时代边界名。"""
        for entry in self.corpus["cases"]:
            marker = entry["frozen_boundary"]
            with self.subTest(case_id=entry["case_id"]):
                self.assertIn(
                    marker,
                    (None, lib.FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
