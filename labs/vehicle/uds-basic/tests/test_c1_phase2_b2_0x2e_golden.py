#!/usr/bin/env python3
"""
UDS Basic —— C1 Phase 2 Migration **B2**：0x2E golden replay 等价性验证

设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md``
§12（Migration B）/ §13（Golden Corpus）/ §14.2（StateSnapshot）。

## 本文件证明什么

    B0 冻结的 **legacy oracle**          tests/golden/c1_phase2_0x2e_legacy.json
            ↕  逐案 replay
    当前 **0x2E ServiceSpec execution**  dispatcher → resolve_service_spec(0x2E)
                                                  → ordered guards → handler

B1 之后 ``_SPEC_EXECUTION = {0x22, 0x2E}``，因此 replay **走的是 spec 路径**；
expected 则始终来自**已冻结并进入 main 的 B0 corpus**。

## 反 self-vs-self（本阶段最重要的约束）

expected 的来源**只能是**冻结的 golden 文件。禁止：

    actual   = build_corpus()      # 当前执行
    expected = build_corpus()      # 又由当前执行重算
    assert actual == expected

因此本文件**不调用** ``c1_golden_2e_lib.build_corpus()``，并有专门用例把该调用
变成一个**立刻失败**的事实（``test_replay_does_not_recompute_expected_from_current_execution``）。

## 与 B0 测试的职责分工

    B0  = frozen corpus / 定义完整性（结构性，不执行当前 dispatcher）
    B2  = legacy oracle ↔ 当前 spec execution 的等价性（本文件）

运行：

    python3 tests/test_c1_phase2_b2_0x2e_golden.py
或：
    bash tests/run_tests.sh unit
"""

import hashlib
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import c1_golden_2e_lib as lib2e  # noqa: E402
import c1_golden_lib as lib  # noqa: E402
from uds_server import _SPEC_EXECUTION  # noqa: E402

#: B0 冻结的 corpus 规模（**精确相等**）。
EXPECTED_CORPUS_SIZE = 70

SID_READ = 0x22
SID_WRITE = 0x2E
SID_SECURITY_ACCESS = 0x27

#: 两个 corpus 的**冻结 SHA256**（设计 §13.4：golden 不得被重新生成）。
#:
#: 硬编码在测试里，使"任何人改写 corpus"成为一个**立刻失败**的事实 ——
#: B2 的 expected 只有在文件逐字节未变时才有意义。
B0_CORPUS_SHA256 = "0ad204331c390a00f0906d294f6a8024a048082fb2266a69ff07e428b966f974"
A0_CORPUS_SHA256 = "d0d83244c85d7916924227c09dc3565ce33e5534e485650aa1f32efbd520cf79"

A0_CORPUS_PATH = os.path.join(lib.GOLDEN_DIR, "c1_phase2_0x22_legacy.json")


def _sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class TestB2Preconditions(unittest.TestCase):
    """§10 —— 当前运行的确实必须**是** 0x2E spec execution。"""

    def test_spec_execution_is_the_b1_whitelist(self):
        self.assertEqual(_SPEC_EXECUTION, frozenset({SID_READ, SID_WRITE}))

    def test_0x2E_is_enabled(self):
        self.assertIn(SID_WRITE, _SPEC_EXECUTION)

    def test_0x22_is_still_enabled(self):
        self.assertIn(SID_READ, _SPEC_EXECUTION)

    def test_0x27_is_untouched(self):
        """0x27 不在静态表内，也不在本阶段迁移范围内。"""
        from uds_server import SERVICE_SPECS, SPECS_EXCLUDED, resolve_service_spec

        self.assertNotIn(SID_WRITE, (SID_SECURITY_ACCESS,))
        self.assertNotIn(SID_SECURITY_ACCESS, SERVICE_SPECS)
        self.assertIn(SID_SECURITY_ACCESS, SPECS_EXCLUDED)
        self.assertNotIn(SID_SECURITY_ACCESS, _SPEC_EXECUTION)
        self.assertIsNone(resolve_service_spec(SID_SECURITY_ACCESS, None))

    def test_corpus_header_records_b0_facts(self):
        corpus = lib2e.load_corpus()
        self.assertEqual(corpus["schema_version"], lib2e.SCHEMA_VERSION)
        self.assertEqual(corpus["target_sid"], SID_WRITE)
        self.assertEqual(corpus["target_execution_mode"], lib.LEGACY_DISPATCHER_MODE)
        self.assertEqual(corpus["spec_execution_at_capture"], [SID_READ])


class TestGoldenFilesAreFrozen(unittest.TestCase):
    """
    §4 —— 两个 corpus 必须**逐字节**未变。

    硬编码 SHA256 使"corpus 被改写"成为一个立刻失败的事实：
    B2 的 expected 只有在文件未变时才有意义。
    """

    def test_b0_corpus_sha256_unchanged(self):
        self.assertEqual(_sha256(lib2e.CORPUS_PATH), B0_CORPUS_SHA256)

    def test_a0_corpus_sha256_unchanged(self):
        self.assertEqual(_sha256(A0_CORPUS_PATH), A0_CORPUS_SHA256)

    def test_b0_corpus_size(self):
        self.assertEqual(
            len(lib2e.load_corpus()["cases"]),
            EXPECTED_CORPUS_SIZE,
            f"B2 必须原样 replay B0 的 {EXPECTED_CORPUS_SIZE} 条 case",
        )


class TestB2Replay(unittest.TestCase):
    """
    §5–§8 —— 逐案 replay：**真实执行**记录 + response + state 三重比较。

    与 A2（``test_c1_phase2_a0_golden.py``）采用**同一套方法**：
    一个共享的真实遍历路径 ``_replay_all()``，三个维度共用它，
    并以 ``patch.object`` 记录 ``lib.execute`` 的**真实调用**。
    """

    @classmethod
    def setUpClass(cls):
        # expected 的**唯一**来源：冻结的 B0 golden 文件。
        cls.corpus = lib2e.load_corpus()
        cls.entries = cls.corpus["cases"]

    def _replay_all(self, check=None):
        """
        对 corpus 中**每一条** case 走一次真实 replay（``lib.execute``）。

        :param check: 可选回调 ``(entry, response, final_state) -> None``。
        :returns: 按 corpus 顺序记录的被遍历 ``case_id`` 列表。
        """
        visited = []
        for entry in self.entries:
            visited.append(entry["case_id"])
            case = lib.deserialize_case(entry)
            response, final_state = lib.execute(case)
            if check is not None:
                check(entry, response, final_state)
        return visited

    # ---- §6 真实执行：恰好一次、顺序一致 ----

    def test_every_case_is_executed_exactly_once_in_order(self):
        """
        ``lib.execute`` 的**真实调用记录**必须等于 corpus 顺序，且恰好 70 次。

        用**列表相等**（而非集合相等）断言，因此能同时检出：

        * 漏执行（少元素）
        * 重复执行（多元素 / 顺序错位）
        * 顺序变化
        * 跳过 case（被过滤）
        """
        executed = []
        original_execute = lib.execute

        def recording_execute(case):
            executed.append(case["case_id"])
            return original_execute(case)

        with patch.object(lib, "execute", side_effect=recording_execute):
            visited = self._replay_all()

        expected = [entry["case_id"] for entry in self.entries]

        self.assertEqual(
            len(executed),
            EXPECTED_CORPUS_SIZE,
            f"B2 replay 必须实际执行全部 {EXPECTED_CORPUS_SIZE} 条 case",
        )
        self.assertEqual(
            executed,
            expected,
            "B2 replay 的**实际执行**顺序必须与冻结 corpus 完全一致"
            "（列表相等同时排除漏执行 / 重复执行 / 顺序变化）",
        )
        self.assertEqual(len(set(executed)), EXPECTED_CORPUS_SIZE, "不得有重复执行")
        self.assertEqual(visited, executed, "遍历到的 ⇔ 真正执行到的")

    def test_replay_does_not_recompute_expected_from_current_execution(self):
        """
        反 self-vs-self：B2 的 expected **不得**由当前执行重算。

        把 ``lib2e.build_corpus()`` 替换为抛异常，若 replay 路径以任何方式
        依赖它（"当前执行产生 oracle"），本用例立刻失败。
        """
        with patch.object(
            lib2e,
            "build_corpus",
            side_effect=AssertionError(
                "B2 不得调用 build_corpus() —— expected 必须来自冻结的 golden 文件"
            ),
        ):
            self._replay_all()

    # ---- §7 response 等价性 ----

    def test_replay_response_bytes_match(self):
        """70/70：``actual_response == golden.legacy_response``。"""

        def check(entry, response, final_state):
            self.assertEqual(
                response.hex(),
                entry["legacy_response"],
                f"{entry['case_id']}：spec 路径响应与 B0 legacy oracle 不一致",
            )

        self._replay_all(check)

    # ---- §8 final state 等价性 ----

    def test_replay_state_projection_matches(self):
        """70/70：``actual_final_state == golden.legacy_final_state``。"""

        def check(entry, response, final_state):
            self.assertEqual(
                final_state,
                entry["legacy_final_state"],
                f"{entry['case_id']}：spec 路径的 final state 与 B0 legacy oracle 不一致",
            )

        self._replay_all(check)

    def test_state_projection_fields_are_the_frozen_three(self):
        """§8：比较范围**不得自行扩大** —— 仍是 B0 冻结的三字段投影。"""
        self.assertEqual(
            self.corpus["state_projection_fields"], list(lib.STATE_PROJECTION_FIELDS)
        )
        for entry in self.entries[:5]:
            with self.subTest(case_id=entry["case_id"]):
                self.assertEqual(
                    set(entry["legacy_final_state"]),
                    set(lib.STATE_PROJECTION_FIELDS),
                )


class TestB2WriteSideEffects(unittest.TestCase):
    """
    §8 —— 0x2E 是**写**服务：成功路径的副作用必须与 legacy oracle 一致。

    只比较 response 不足以发现"写入了错误的值"或"写入了错误的 DID"。
    """

    @classmethod
    def setUpClass(cls):
        cls.entries = lib2e.load_corpus()["cases"]

    def _execute(self, entry):
        return lib.execute(lib.deserialize_case(entry))

    def test_successful_writes_produce_the_oracle_side_effect(self):
        successes = [e for e in self.entries if e["legacy_response"].startswith("6e")]
        self.assertTrue(successes, "corpus 缺少成功写入用例")
        for entry in successes:
            with self.subTest(case_id=entry["case_id"]):
                _, final_state = self._execute(entry)
                self.assertEqual(
                    final_state["did_overrides"],
                    entry["legacy_final_state"]["did_overrides"],
                )
                # 成功路径必须真的改变了 did_overrides
                self.assertNotEqual(
                    entry["legacy_final_state"]["did_overrides"],
                    entry["initial_state"]["did_overrides"],
                )

    def test_failed_writes_have_no_side_effect(self):
        failures = [e for e in self.entries if e["legacy_response"].startswith("7f")]
        self.assertTrue(failures)
        for entry in failures:
            with self.subTest(case_id=entry["case_id"]):
                _, final_state = self._execute(entry)
                self.assertEqual(
                    final_state["did_overrides"],
                    entry["initial_state"]["did_overrides"],
                    "失败的 0x2E 不得产生 did_overrides 副作用",
                )

    def test_writable_did_0xF102_value_matches_oracle(self):
        """显式锁定可写 DID `0xF102` 的写入值（成功用例）。"""
        checked = 0
        for entry in self.entries:
            if entry["legacy_response"].startswith("6e"):
                did = int(entry["request"][2:6], 16)
                if did != 0xF102:
                    continue
                with self.subTest(case_id=entry["case_id"]):
                    _, final_state = self._execute(entry)
                    self.assertEqual(
                        final_state["did_overrides"].get("f102"),
                        entry["legacy_final_state"]["did_overrides"].get("f102"),
                    )
                    checked += 1
        self.assertGreater(checked, 0, "缺少 0xF102 的成功写入用例")

    def test_session_and_security_never_change(self):
        """0x2E 不修改 session / security_level —— 与 oracle 一致。"""
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                _, final_state = self._execute(entry)
                self.assertEqual(final_state["session"], entry["legacy_final_state"]["session"])
                self.assertEqual(
                    final_state["security_level"],
                    entry["legacy_final_state"]["security_level"],
                )


class TestB2CategorCoverage(unittest.TestCase):
    """§9 —— 必须原样 replay B0 的 70 条，不得增删或改输入。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = lib2e.load_corpus()["cases"]

    def _count(self, prefix):
        return sum(1 for e in self.entries if e["case_id"].startswith(prefix))

    def test_category_counts_are_unchanged(self):
        self.assertEqual(
            {
                "L": self._count("L"),
                "D": self._count("D"),
                "W": self._count("W"),
                "R": self._count("R"),
                "N": self._count("N"),
                "O": self._count("O"),
            },
            {"L": 18, "D": 12, "W": 6, "R": 18, "N": 10, "O": 6},
        )

    def test_total_matches(self):
        self.assertEqual(sum(
            self._count(p) for p in ("L", "D", "W", "R", "N", "O")
        ), EXPECTED_CORPUS_SIZE)

    def test_all_requests_target_0x2e(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                self.assertTrue(entry["request"].startswith("2e"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
