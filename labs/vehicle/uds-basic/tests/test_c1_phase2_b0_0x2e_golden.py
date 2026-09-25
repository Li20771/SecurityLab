#!/usr/bin/env python3
"""
UDS Basic —— C1 Phase 2 Migration **B0**：0x2E legacy golden corpus 结构测试

设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md``
§12（Migration B）/ §13（Golden Corpus）/ §13.5（单请求不变式）/ §14.2（StateSnapshot）。

## 本文件**不是** B2 对拍

B0 只做 **legacy capture**。因此这里**不**验证
"0x2E spec 路径 ↔ legacy oracle" 的等价性 —— 那属 **B2**（需先由 B1 开启 0x2E）。

本文件验证的是 **corpus 自身的结构与来源**：

- target SID 确为 0x2E，且捕获时 **0x2E 未迁移**（`0x2E ∉ spec_execution_at_capture`）；
- 全部 case 是**单请求**（无 sequence / steps），前置条件全部由 `initial_state` 给出；
- 每条 case 都有 response 与 final state projection；
- 两个**冻结锚点**的实测值正确；
- corpus 自洽（捕获端与重放端语义相同、执行确定）。

运行：

    python3 tests/test_c1_phase2_b0_0x2e_golden.py
或：
    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import c1_golden_2e_lib as lib2e  # noqa: E402
import c1_golden_lib as lib  # noqa: E402
from did_database import WRITABLE_DIDS  # noqa: E402

#: B0 捕获并冻结的 corpus 规模。**精确相等**，使"静默删减 corpus"立刻失败。
EXPECTED_CORPUS_SIZE = 70

SID_WRITE = 0x2E

SESSION_DEFAULT = 0x01
SECURITY_LOCKED = 0x00

#: 两个**冻结锚点**（设计 §12.3 / §12.4）。
ANCHOR_UNKNOWN_BEFORE_SESSION = "2effff58"  # → 7F 2E 31（rung② 先于 rung③）
ANCHOR_WRITABLE_IN_DEFAULT = "2ef10258"  # → 7F 2E 7F（rung③ DEFAULT 会话）


class TestCorpusProvenance(unittest.TestCase):
    """
    corpus 的**来源**必须可追溯：它捕获的是 0x2E 的 legacy 路径。

    注意与 A0 的区别：A0 时**整个白名单为空**；B0 时 `_SPEC_EXECUTION = {0x22}`，
    因此不能断言"白名单为空"，只能断言 **target SID 未迁移**。
    """

    @classmethod
    def setUpClass(cls):
        cls.corpus = lib2e.load_corpus()

    def test_schema_version_is_b0_version(self):
        self.assertEqual(self.corpus["schema_version"], lib2e.SCHEMA_VERSION)

    def test_target_sid_is_0x2e(self):
        self.assertEqual(self.corpus["sid"], SID_WRITE)
        self.assertEqual(self.corpus["target_sid"], SID_WRITE)
        self.assertEqual(self.corpus["sid_hex"], "0x2E")

    def test_target_execution_mode_is_legacy(self):
        self.assertEqual(self.corpus["target_execution_mode"], lib.LEGACY_DISPATCHER_MODE)
        self.assertEqual(self.corpus["dispatcher_mode"], lib.LEGACY_DISPATCHER_MODE)

    def test_target_sid_was_not_migrated_at_capture(self):
        """
        **B0 的 oracle 前提**：捕获时 0x2E 不在 `_SPEC_EXECUTION` 中。

        若为假，捕获到的就不是 legacy 输出，B2 的对拍基准被污染。
        """
        self.assertNotIn(
            SID_WRITE,
            self.corpus["spec_execution_at_capture"],
            "0x2E 捕获时必须未迁移",
        )

    def test_0x22_was_already_migrated_at_capture(self):
        """记录历史事实：B0 发生在 Migration A 之后，0x22 已迁移。"""
        self.assertIn(0x22, self.corpus["spec_execution_at_capture"])

    def test_0x2e_is_still_not_migrated_now(self):
        """当前运行时白名单中 0x2E 仍须缺席（B0 不开启 0x2E）。"""
        from uds_server import _SPEC_EXECUTION

        self.assertNotIn(SID_WRITE, _SPEC_EXECUTION)

    def test_state_projection_fields(self):
        self.assertEqual(
            self.corpus["state_projection_fields"],
            list(lib.STATE_PROJECTION_FIELDS),
        )


class TestSingleRequestInvariant(unittest.TestCase):
    """§13.2 / §13.5 —— 每条 case 必须是**单请求**，无 sequence / steps。"""

    @classmethod
    def setUpClass(cls):
        cls.corpus = lib2e.load_corpus()
        cls.entries = cls.corpus["cases"]

    def test_corpus_size_is_frozen(self):
        self.assertEqual(
            len(self.entries),
            EXPECTED_CORPUS_SIZE,
            f"0x2E golden corpus 必须保持冻结的 {EXPECTED_CORPUS_SIZE} 条 case",
        )

    def test_no_sequence_or_steps_fields(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                self.assertNotIn("sequence", entry)
                self.assertNotIn("steps", entry)

    def test_every_request_targets_0x2e(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                self.assertTrue(
                    entry["request"].startswith("2e"),
                    "每条 B0 case 的 request 必须以 0x2E 开头",
                )

    def test_every_case_has_initial_state(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                state = entry["initial_state"]
                self.assertIn("session", state)
                self.assertIn("security_level", state)
                self.assertIn("did_overrides", state)

    def test_every_case_has_response_and_final_state(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                self.assertIn("legacy_response", entry)
                self.assertIn("legacy_final_state", entry)
                self.assertTrue(entry["legacy_response"])

    def test_case_ids_are_unique(self):
        ids = [entry["case_id"] for entry in self.entries]
        self.assertEqual(len(ids), len(set(ids)))


class TestFrozenAnchors(unittest.TestCase):
    """
    §12.3 / §12.4 —— 两个**冻结锚点**必须逐字节存在于 corpus 中。

    验证的是"捕获下来的值"，不重跑请求（B0 不做对拍）。
    """

    @classmethod
    def setUpClass(cls):
        cls.entries = lib2e.load_corpus()["cases"]

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

    def test_anchor_unknown_did_precedes_session_gate(self):
        """``2E FF FF 58`` + DEFAULT + LOCKED → ``7F 2E 31``（**rung② 先于 rung③**）。"""
        entry = self._find(ANCHOR_UNKNOWN_BEFORE_SESSION, SESSION_DEFAULT, SECURITY_LOCKED)
        self.assertIsNotNone(entry, "缺少冻结锚点 2E FF FF 58 + DEFAULT")
        self.assertEqual(entry["legacy_response"], "7f2e31")

    def test_anchor_writable_did_in_default_session(self):
        """``2E F1 02 58`` + DEFAULT + LOCKED → ``7F 2E 7F``（rung③）。"""
        entry = self._find(ANCHOR_WRITABLE_IN_DEFAULT, SESSION_DEFAULT, SECURITY_LOCKED)
        self.assertIsNotNone(entry, "缺少冻结锚点 2E F1 02 58 + DEFAULT")
        self.assertEqual(entry["legacy_response"], "7f2e7f")

    def test_anchors_differ_despite_same_state(self):
        """两条锚点在同一状态下结果不同 —— 差异**只能**来自 payload（DID 是否已知）。"""
        unknown_first = self._find(
            ANCHOR_UNKNOWN_BEFORE_SESSION, SESSION_DEFAULT, SECURITY_LOCKED
        )
        writable = self._find(
            ANCHOR_WRITABLE_IN_DEFAULT, SESSION_DEFAULT, SECURITY_LOCKED
        )
        self.assertNotEqual(
            unknown_first["legacy_response"], writable["legacy_response"]
        )


class TestRungPrecedence(unittest.TestCase):
    """
    §12.4 —— rung 之间的优先关系（`13 → 31 → 7F → 33 → 22 → 13`）。

    从 corpus 的**记录值**断言，不重跑请求。
    """

    @classmethod
    def setUpClass(cls):
        cls.entries = lib2e.load_corpus()["cases"]

    def _responses(self, prefix):
        return [
            e["legacy_response"]
            for e in self.entries
            if e["case_id"].startswith(prefix)
        ]

    def test_rung1_malformed_lengths_all_0x13(self):
        responses = self._responses("L")
        self.assertEqual(len(responses), 18)
        for response in responses:
            with self.subTest(response=response):
                self.assertEqual(response, "7f2e13")

    def test_rung2_unknown_dids_all_0x31(self):
        responses = self._responses("D")
        self.assertEqual(len(responses), 12)
        for response in responses:
            with self.subTest(response=response):
                self.assertEqual(response, "7f2e31")

    def test_rung5_readonly_dids_never_reach_write(self):
        """只读 DID 不可能返回正响应 `6E`。"""
        for response in self._responses("R"):
            with self.subTest(response=response):
                self.assertFalse(response.startswith("6e"))

    def test_positive_responses_only_for_writable_did(self):
        """
        正响应只能来自 **可写 DID**（`WRITABLE_DIDS`）的成功写入。

        这里按 **request 里的 DID** 判定，而不是按 case_id 前缀 ——
        W / N / O1 三组都用可写 DID，硬编码前缀会漏掉 N 组。
        """
        positives = [e for e in self.entries if e["legacy_response"].startswith("6e")]
        self.assertTrue(positives, "corpus 缺少成功写入用例")
        for entry in positives:
            with self.subTest(case_id=entry["case_id"]):
                did = int(entry["request"][2:6], 16)
                self.assertIn(
                    did,
                    WRITABLE_DIDS,
                    f"非可写 DID 0x{did:04X} 不应产生正响应：{entry['case_id']}",
                )

    def test_covers_all_six_rung_nrcs(self):
        """corpus 必须覆盖 0x2E 可达的全部 NRC。"""
        negatives = {
            e["legacy_response"] for e in self.entries if e["legacy_response"].startswith("7f")
        }
        for nrc in ("7f2e13", "7f2e22", "7f2e31", "7f2e33", "7f2e7f"):
            with self.subTest(nrc=nrc):
                self.assertIn(nrc, negatives)


class TestCaptureReplayRoundtrip(unittest.TestCase):
    """
    corpus **自洽性**：在进程内重新捕获一遍，与磁盘上的 corpus 逐条比对。

    这证明捕获端与重放端共用同一套语义（`c1_golden_lib.execute`），
    且执行是确定的。

    .. note::

       **这不是 B2 的 spec 对拍。** 当前 `0x2E ∉ _SPEC_EXECUTION`，
       两侧走的都是同一条 legacy 路径。B1 开启 0x2E 之后，
       同一条 case 才会经 spec 路径执行 —— 那属 B2。
    """

    def test_fresh_capture_equals_stored_corpus(self):
        stored = lib2e.load_corpus()
        fresh = lib2e.build_corpus()

        stored_by_id = {e["case_id"]: e for e in stored["cases"]}
        fresh_by_id = {e["case_id"]: e for e in fresh["cases"]}

        self.assertEqual(set(stored_by_id), set(fresh_by_id))
        for case_id, stored_entry in stored_by_id.items():
            with self.subTest(case_id=case_id):
                fresh_entry = fresh_by_id[case_id]
                self.assertEqual(fresh_entry["request"], stored_entry["request"])
                self.assertEqual(
                    fresh_entry["initial_state"], stored_entry["initial_state"]
                )
                self.assertEqual(
                    fresh_entry["legacy_response"], stored_entry["legacy_response"]
                )
                self.assertEqual(
                    fresh_entry["legacy_final_state"],
                    stored_entry["legacy_final_state"],
                )

    def test_state_is_rebuilt_per_case(self):
        """§23 —— 每条 case 用**全新** state，成功写入不污染后续 case。"""
        entries = lib2e.load_corpus()["cases"]
        target = lib.deserialize_case(entries[0])
        alone = lib.execute(target)

        for _ in range(3):
            lib.execute(target)

        self.assertEqual(lib.execute(target), alone)

    def test_build_state_returns_independent_instances(self):
        initial = {
            "session": 0x03,
            "security_level": 0x01,
            "did_overrides": {"f102": "41"},
        }
        first = lib.build_state(initial)
        second = lib.build_state(initial)
        self.assertIsNot(first, second)
        self.assertIsNot(first.did_overrides, second.did_overrides)


class TestStateSideEffectsAreCaptured(unittest.TestCase):
    """
    §14 —— 0x2E 是**写**服务：成功路径会改 `did_overrides`，失败路径不应改。

    因此 `legacy_final_state` 不是可有可无的字段，而是 B2 必须比较的一半。
    """

    @classmethod
    def setUpClass(cls):
        cls.entries = lib2e.load_corpus()["cases"]

    def test_successful_write_changes_did_overrides(self):
        successes = [e for e in self.entries if e["legacy_response"].startswith("6e")]
        self.assertTrue(successes)
        for entry in successes:
            with self.subTest(case_id=entry["case_id"]):
                self.assertNotEqual(
                    entry["legacy_final_state"]["did_overrides"],
                    entry["initial_state"]["did_overrides"],
                    "成功写入必须体现为 final state 的 did_overrides 变化",
                )

    def test_failed_write_does_not_touch_did_overrides(self):
        failures = [e for e in self.entries if e["legacy_response"].startswith("7f")]
        self.assertTrue(failures)
        for entry in failures:
            with self.subTest(case_id=entry["case_id"]):
                self.assertEqual(
                    entry["legacy_final_state"]["did_overrides"],
                    entry["initial_state"]["did_overrides"],
                    "失败的 0x2E 不得产生 did_overrides 副作用",
                )

    def test_session_and_security_never_change(self):
        """0x2E 不修改 session / security_level。"""
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                self.assertEqual(
                    entry["legacy_final_state"]["session"],
                    entry["initial_state"]["session"],
                )
                self.assertEqual(
                    entry["legacy_final_state"]["security_level"],
                    entry["initial_state"]["security_level"],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
