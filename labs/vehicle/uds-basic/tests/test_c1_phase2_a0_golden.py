#!/usr/bin/env python3
"""
UDS Basic —— C1 Phase 2 Migration A0：0x22 legacy golden **重放**测试

设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md`` §13 / §14。

本文件是 golden corpus 的**长期重放端**：它把 ``tests/golden/c1_phase2_0x22_legacy.json``
中的每条 case 重新执行一遍，并逐条比较

    response bytes          （§13）
    state projection        （§14.2：session / security_level / did_overrides）

**阶段职责分离**（设计 §11.1）：

```
捕获（A0）   capture_c1_phase2_a0_golden.py
             **必须**在 _SPEC_EXECUTION 为空时执行
             —— 该脚本自带 fail-closed 护栏，非空即 exit 2（§13.4）

重放（长期） 本文件
             把 corpus 当作**固定的 legacy oracle**，
             用**当前** dispatcher 路径重放并逐条比较
```

因此本文件**不**假定 ``_SPEC_EXECUTION`` 永远为空：

- **A0 / Step 1**：重放走 legacy 路径 → 比较证明 corpus **自洽**；
- **A1**：0x22 改走 spec 路径 → 比较即 **"legacy oracle ↔ spec 路径"的逐字节对拍**。

"捕获必须处于 legacy 模式"这一前置条件**只由捕获脚本把关**，不放在本文件 ——
否则阶段一推进，长期回归网就会因阶段变化而误报。

白名单本身的内容由 ``test_c1_phase2_framework_unit.py`` 以**显式阶段常量**断言。

运行：

    python3 tests/test_c1_phase2_a0_golden.py
或：
    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import c1_golden_lib as lib  # noqa: E402


class TestCorpusProvenance(unittest.TestCase):
    """
    corpus 的**来源**必须可追溯：它是在 legacy 模式下捕获的。

    注意：这里检查的是 corpus **头部记录的捕获时状态**（历史事实），
    而**不是**当前 ``_SPEC_EXECUTION`` 的值 —— 后者随迁移阶段推进而变，
    其对错由 ``test_c1_phase2_framework_unit.py`` 按阶段常量断言。

    "捕获时必须为 legacy" 的**实时**护栏保留在捕获脚本内（fail-closed，exit 2）。
    """

    def test_corpus_header_declares_legacy_mode(self):
        corpus = lib.load_corpus()
        self.assertEqual(corpus["dispatcher_mode"], lib.LEGACY_DISPATCHER_MODE)
        self.assertEqual(corpus["schema_version"], lib.SCHEMA_VERSION)
        self.assertEqual(corpus["sid"], lib.SID_READ)
        self.assertEqual(
            corpus["spec_execution_at_capture"],
            [],
            "corpus 捕获时 _SPEC_EXECUTION 必须为空",
        )

    def test_corpus_records_state_projection_fields(self):
        corpus = lib.load_corpus()
        self.assertEqual(corpus["state_projection_fields"], list(lib.STATE_PROJECTION_FIELDS))


class TestGoldenReplay(unittest.TestCase):
    """§13 / §14 —— 逐条重放并比较 response 与 state projection。"""

    @classmethod
    def setUpClass(cls):
        cls.corpus = lib.load_corpus()
        cls.entries = cls.corpus["cases"]

    def test_corpus_is_not_empty(self):
        self.assertGreater(len(self.entries), 0)

    def test_replay_response_bytes_match(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                case = lib.deserialize_case(entry)
                response, _ = lib.execute(case)
                self.assertEqual(
                    response.hex(),
                    entry["legacy_response"],
                    f"{entry['case_id']}：重放响应与 legacy golden 不一致",
                )

    def test_replay_state_projection_matches(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                case = lib.deserialize_case(entry)
                _, final_state = lib.execute(case)
                self.assertEqual(
                    final_state,
                    entry["legacy_final_state"],
                    f"{entry['case_id']}：重放后的状态投影与 golden 不一致",
                )

    def test_execution_is_deterministic(self):
        """同一 case 连跑两次必须完全一致（证明重放可复现，§13.5）。"""
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                case = lib.deserialize_case(entry)
                first = lib.execute(case)
                second = lib.execute(case)
                self.assertEqual(first, second)

    def test_case_ids_are_unique(self):
        ids = [entry["case_id"] for entry in self.entries]
        self.assertEqual(len(ids), len(set(ids)), "case_id 必须唯一")


class TestCaptureReplayRoundtrip(unittest.TestCase):
    """
    §八 —— **capture 后立即 replay 必须完全一致**。

    在进程内重新捕获一遍，与磁盘上的 corpus 逐条比对，证明捕获端与重放端
    共用同一套语义（``c1_golden_lib.execute``），不存在"写进去是一回事、
    读出来是另一回事"。
    """

    def test_fresh_capture_equals_stored_corpus(self):
        stored = lib.load_corpus()
        fresh = lib.build_corpus()

        stored_by_id = {entry["case_id"]: entry for entry in stored["cases"]}
        fresh_by_id = {entry["case_id"]: entry for entry in fresh["cases"]}

        self.assertEqual(set(stored_by_id), set(fresh_by_id), "case 集合必须一致")

        for case_id, stored_entry in stored_by_id.items():
            with self.subTest(case_id=case_id):
                fresh_entry = fresh_by_id[case_id]
                self.assertEqual(
                    fresh_entry["request"], stored_entry["request"], "请求定义不一致"
                )
                self.assertEqual(
                    fresh_entry["initial_state"],
                    stored_entry["initial_state"],
                    "initial_state 不一致",
                )
                self.assertEqual(
                    fresh_entry["legacy_response"],
                    stored_entry["legacy_response"],
                    "捕获响应与 corpus 不一致",
                )
                self.assertEqual(
                    fresh_entry["legacy_final_state"],
                    stored_entry["legacy_final_state"],
                    "捕获状态投影与 corpus 不一致",
                )


class TestCorpusCoverage(unittest.TestCase):
    """
    §11.3 —— corpus 必须覆盖规定的行为类别。

    这些是**结构性**断言（case 是否存在），值本身由重放测试保证。
    """

    @classmethod
    def setUpClass(cls):
        cls.corpus = lib.load_corpus()
        cls.by_id = {entry["case_id"]: entry for entry in cls.corpus["cases"]}

    def _responses(self, prefix):
        return [
            entry["legacy_response"]
            for case_id, entry in self.by_id.items()
            if case_id.startswith(prefix)
        ]

    def test_covers_malformed_lengths(self):
        """格式 / 长度非法（F 组）全部得到 0x13。"""
        responses = self._responses("F")
        self.assertTrue(responses, "缺少格式/长度用例")
        for response in responses:
            with self.subTest(response=response):
                self.assertEqual(response, "7f2213")

    def test_covers_every_did_in_all_states(self):
        """S 组：每个 DID × 6 种状态组合。"""
        self.assertEqual(
            len(self._responses("S")),
            len(lib.SINGLE_DID_DIDS) * len(lib.ALL_STATES),
        )

    def test_covers_multi_did_ordering(self):
        """M 组：多 DID 与顺序敏感场景。"""
        self.assertEqual(
            len(self._responses("M")),
            len(lib.MULTI_DID_REQUESTS) * len(lib.ALL_STATES),
        )

    def test_covers_did_overrides(self):
        """O 组：``did_overrides`` 预置。"""
        self.assertEqual(
            len(self._responses("O")),
            len(lib.OVERRIDE_CASES) * len(lib.OVERRIDE_STATES),
        )

    def test_covers_positive_responses(self):
        """必须存在**成功读取**的用例（否则 corpus 只覆盖失败路径）。"""
        positives = [r for r in self._all_responses() if r.startswith("62")]
        self.assertTrue(positives, "corpus 缺少正响应用例")

    def test_covers_every_nrc_in_scope(self):
        """0x22 可达的三种 NRC 都必须出现在 corpus 中。"""
        negatives = {r for r in self._all_responses() if r.startswith("7f")}
        self.assertIn("7f2213", negatives, "缺少 0x13 incorrectMessageLength")
        self.assertIn("7f2231", negatives, "缺少 0x31 requestOutOfRange")
        self.assertIn("7f2233", negatives, "缺少 0x33 securityAccessDenied")

    def test_covers_all_three_sessions(self):
        sessions = {
            entry["initial_state"]["session"] for entry in self.corpus["cases"]
        }
        self.assertEqual(sessions, {0x01, 0x02, 0x03})

    def test_covers_both_security_levels(self):
        levels = {
            entry["initial_state"]["security_level"] for entry in self.corpus["cases"]
        }
        self.assertEqual(levels, {0x00, 0x01})

    def _all_responses(self):
        return [entry["legacy_response"] for entry in self.corpus["cases"]]


class TestFrozenAnchors(unittest.TestCase):
    """
    设计 §11.3 / §15.2 的两个**冻结锚点**必须逐字节存在于 corpus 中。

    这两条同时被 29 contract pins 的 N2 与 Phase 1 的 service-spec tests 覆盖；
    此处从 **golden corpus** 一侧再锁定一次。
    """

    @classmethod
    def setUpClass(cls):
        cls.entries = lib.load_corpus()["cases"]

    def _find(self, request_hex, session, security):
        for entry in self.entries:
            state = entry["initial_state"]
            if (
                entry["request"] == request_hex
                and state["session"] == session
                and state["security_level"] == security
            ):
                return entry
        return None

    def test_frozen_anchor_protected_first_is_security_denied(self):
        """``22 F1 87 FF FF`` → ``7F 22 33``（受保护在前，33 先于 31）。"""
        entry = self._find("22f187ffff", 0x03, 0x00)
        self.assertIsNotNone(entry, "缺少冻结锚点 22 F1 87 FF FF")
        self.assertEqual(entry["legacy_response"], "7f2233")

    def test_frozen_anchor_unknown_first_is_out_of_range(self):
        """``22 FF FF F1 87`` → ``7F 22 31``（未知在前，31 先于 33）。"""
        entry = self._find("22fffff187", 0x03, 0x00)
        self.assertIsNotNone(entry, "缺少冻结锚点 22 FF FF F1 87")
        self.assertEqual(entry["legacy_response"], "7f2231")

    def test_frozen_anchors_differ_only_by_order(self):
        """两个锚点**只差顺序**，但 NRC 不同 —— 这正是顺序语义的核心证据。"""
        protected_first = self._find("22f187ffff", 0x03, 0x00)
        unknown_first = self._find("22fffff187", 0x03, 0x00)
        self.assertNotEqual(
            protected_first["legacy_response"], unknown_first["legacy_response"]
        )


class TestSingleRequestInvariant(unittest.TestCase):
    """
    §13.2 / §13.5 —— ``GoldenCase`` 是**单请求**结构。

    本文件不对 schema 做运行时形状校验（那是 ``capture`` 脚本与 code review 的职责），
    但锁住它的**可观测后果**：对同一条 case 连续执行不产生累积效应 ——
    即 state 确实每条都是新建的，而非复用。
    """

    def test_state_is_rebuilt_per_case(self):
        """连续执行 N 次的结果必须与单独执行一次相同（无跨 case 状态泄漏）。"""
        target = lib.deserialize_case(lib.load_corpus()["cases"][0])
        alone = lib.execute(target)

        for _ in range(3):
            lib.execute(target)

        self.assertEqual(lib.execute(target), alone)

    def test_each_case_uses_a_fresh_state_object(self):
        """``build_state`` 必须每次返回**不同**的实例，且不与 corpus 共享可变对象。"""
        initial = {"session": 0x03, "security_level": 0x00, "did_overrides": {"f102": "41"}}
        first = lib.build_state(initial)
        second = lib.build_state(initial)
        self.assertIsNot(first, second)
        self.assertIsNot(first.did_overrides, second.did_overrides)

        first.did_overrides[0x1234] = b"X"
        self.assertNotIn(0x1234, second.did_overrides)


if __name__ == "__main__":
    unittest.main(verbosity=2)
