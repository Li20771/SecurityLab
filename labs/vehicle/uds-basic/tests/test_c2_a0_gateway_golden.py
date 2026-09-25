#!/usr/bin/env python3
"""
UDS Basic —— C2-A0：legacy Gateway golden corpus 结构测试

设计出处：``docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md``
§3 / §13。

## 本文件**不是** A2 的等价性对拍

A0 只保证：

    Golden 本身是**完整、有效、未被篡改**的

**不**验证"legacy ↔ 当前实现"的等价性 —— 那是 **A2**（需先由 A1 引入机制）。

## 本文件回答的问题

* corpus 是否**逐字节冻结**（LF 规范化 SHA256）？
* provenance 是否记录齐全（**policy 引入之前**的 legacy 快照）？
* 每个 case 是否**单请求**且**自带完整初始 ECU 配置**（可独立 replay）？
* 覆盖矩阵（物理 / 功能 / 未知 target / suppress / 隔离 / override / 畸形）是否齐备？

运行：

    python3 tests/test_c2_a0_gateway_golden.py
或：
    bash tests/run_tests.sh unit
"""

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import c2_gateway_golden_lib as lib  # noqa: E402

#: A0 冻结的 corpus 规模（**精确相等**：使"静默删减"立刻失败）。
EXPECTED_CORPUS_SIZE = 20

#: LF 规范化后的文件内容 SHA-256（**平台无关**）。
#:
#: 注意这**不是** Git 对象哈希 —— 后者是在对象头 + 内容上另算的对象 ID。
#: 之所以规范化行尾：Windows 的 ``core.autocrlf=true`` 会把仓库中的 LF 内容
#: 以 CRLF 写到工作区，直接哈希工作区字节会得到**平台相关**的值
#: （C1/B2 阶段曾因此在 Linux CI 上失败）。
FROZEN_CORPUS_SHA256 = "d1d358927a4eb86e750fda13a961d935b1afccd3e01e50c26a696dcc7f43c208"

#: 设计文档 §13 要求的覆盖类别 → 代表性 case_id。
REQUIRED_COVERAGE = {
    "physical positive": ("P1-physical-ecu-a", "P2-physical-ecu-b", "P3-physical-ecu-c"),
    "physical negative (ECU-rejected, not policy deny)": ("N1-unknown-did", "N2-protected-did-locked"),
    "suppress": ("S1-physical-suppress",),
    "unknown physical target": ("U1-unknown-target-7e5", "U2-unknown-target-7e3"),
    "functional": ("F1-functional-session", "F2-functional-read",
                   "F3-functional-suppress", "F4-functional-write"),
    "writable DID": ("W1-physical-write",),
    "multi-ECU isolation": ("I1-override-scoped-to-ecu-a", "I2-write-scoped-to-ecu-a"),
    "malformed / empty payload": ("M1-empty-payload", "M2-unregistered-sid"),
    "custom handler / topology": ("C1-custom-handler-in-default-topology",
                                  "C2-custom-topology-single-ecu"),
    "no-side-effect service": ("D1-tester-present",),
}


def _sha256(path: str) -> str:
    """对文件的**LF 规范化内容**求 SHA-256（平台无关）。"""
    with open(path, "rb") as handle:
        data = handle.read().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


class TestCorpusIsFrozen(unittest.TestCase):
    """§17 —— Golden 一旦捕获即冻结，此后不得重新生成。"""

    def test_corpus_sha256_is_frozen(self):
        self.assertEqual(_sha256(lib.CORPUS_PATH), FROZEN_CORPUS_SHA256)

    def test_corpus_size_is_frozen(self):
        self.assertEqual(len(lib.load_corpus()["cases"]), EXPECTED_CORPUS_SIZE)

    def test_case_ids_are_unique(self):
        ids = [e["case_id"] for e in lib.load_corpus()["cases"]]
        self.assertEqual(len(ids), len(set(ids)))


class TestCaptureProvenance(unittest.TestCase):
    """
    §14 —— Golden 必须可追溯到**哪一个源码状态**。

    核心是 ``captured_at_commit``；**不用** "captured_at = now" 作唯一 provenance。
    """

    @classmethod
    def setUpClass(cls):
        cls.corpus = lib.load_corpus()

    def test_schema_version(self):
        self.assertEqual(self.corpus["schema_version"], lib.SCHEMA_VERSION)

    def test_gateway_mode_is_legacy(self):
        self.assertEqual(self.corpus["gateway_mode"], lib.GATEWAY_MODE_LEGACY)

    def test_policy_execution_was_absent_at_capture(self):
        """A0 的前提：捕获时 C2 policy 机制**尚不存在**。"""
        self.assertEqual(self.corpus["policy_execution"], lib.POLICY_EXECUTION_ABSENT)

    def test_captured_at_commit_is_a_real_sha(self):
        commit = self.corpus["captured_at_commit"]
        self.assertEqual(len(commit), 40, "captured_at_commit 必须是完整 git SHA")
        self.assertTrue(all(c in "0123456789abcdef" for c in commit))

    def test_functional_address_recorded(self):
        from gateway import FUNCTIONAL_ADDRESS

        self.assertEqual(self.corpus["functional_address"], FUNCTIONAL_ADDRESS)

    def test_state_projection_fields(self):
        self.assertEqual(
            self.corpus["state_projection_fields"], list(lib.STATE_PROJECTION_FIELDS)
        )

    def test_policy_mechanism_still_absent_in_production(self):
        """
        A0 的 Golden 只有在"policy 机制尚不存在"时才代表 legacy 行为。

        若本用例失败 ⇒ 生产代码已引入 policy 符号 ⇒
        该 Golden 的 legacy 语义前提已变，**A1 必须现在处理**，
        而不是让 A0 的 corpus 被悄悄重新解读。
        """
        self.assertFalse(
            lib.policy_execution_present(),
            "gateway.py 已出现 policy 机制符号 —— A0 的 legacy 前提已改变",
        )


class TestSingleRequestInvariant(unittest.TestCase):
    """§4 —— 一个 GoldenCase = 恰好一次 ``route()``；无 sequence / steps。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = lib.load_corpus()["cases"]

    def test_no_sequence_or_steps(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                self.assertNotIn("sequence", entry)
                self.assertNotIn("steps", entry)

    def test_every_case_carries_full_initial_ecu_config(self):
        """每 case 自带**完整** ECU 配置 ⇒ 可独立 replay，不依赖前一个 case。"""
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                self.assertTrue(entry["ecus"])
                for ecu in entry["ecus"]:
                    self.assertIn("name", ecu)
                    self.assertIn("request_id", ecu)
                    self.assertIn("response_id", ecu)
                    self.assertIn("handler", ecu)
                    self.assertIn("state", ecu)
                    self.assertIn("session", ecu["state"])
                    self.assertIn("security_level", ecu["state"])
                    self.assertIn("did_overrides", ecu["state"])

    def test_every_case_has_response_and_state(self):
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                self.assertIn("expected_response", entry)
                self.assertIn("expected_gateway_state", entry)

    def test_gateway_state_covers_every_ecu(self):
        """状态投影必须**逐 ECU**记录，而非只记录"某个 ECU"。"""
        for entry in self.entries:
            with self.subTest(case_id=entry["case_id"]):
                declared = {ecu["name"] for ecu in entry["ecus"]}
                recorded = set(entry["expected_gateway_state"])
                self.assertEqual(declared, recorded)

    def test_state_projection_has_all_three_fields(self):
        for entry in self.entries:
            for name, state in entry["expected_gateway_state"].items():
                with self.subTest(case_id=entry["case_id"], ecu=name):
                    self.assertEqual(set(state), {"session", "security_level", "did_overrides"})


class TestCoverageMatrix(unittest.TestCase):
    """§6 —— 覆盖矩阵必须齐备（设计文档 §13 的 Golden 覆盖要求）。"""

    @classmethod
    def setUpClass(cls):
        cls.by_id = {e["case_id"]: e for e in lib.load_corpus()["cases"]}

    def test_required_categories_are_present(self):
        for category, case_ids in REQUIRED_COVERAGE.items():
            with self.subTest(category=category):
                for case_id in case_ids:
                    self.assertIn(case_id, self.by_id)

    def test_no_unexpected_cases(self):
        """反向：corpus 中不得出现未在覆盖矩阵中登记的 case。"""
        declared = {cid for ids in REQUIRED_COVERAGE.values() for cid in ids}
        self.assertEqual(set(self.by_id), declared)

    # ---- 关键语义锚点（A0 记录的是事实，此处锁定其形状）----

    def test_functional_broadcast_never_collects_responses(self):
        """功能寻址**恒**返回 ``[]`` —— 包括会产生正响应的服务。"""
        for case_id in REQUIRED_COVERAGE["functional"]:
            with self.subTest(case_id=case_id):
                self.assertEqual(self.by_id[case_id]["expected_response"], [])

    def test_functional_session_broadcast_changes_every_ecu(self):
        entry = self.by_id["F1-functional-session"]
        self.assertEqual(len(entry["expected_gateway_state"]), 3)
        for name, state in entry["expected_gateway_state"].items():
            with self.subTest(ecu=name):
                self.assertNotEqual(state["session"], 1, "广播后会话应已切换")

    def test_functional_write_reaches_every_ecu(self):
        """扇出证据：广播写入应改变**全部** ECU 的 did_overrides。"""
        entry = self.by_id["F4-functional-write"]
        written = [
            name
            for name, state in entry["expected_gateway_state"].items()
            if state["did_overrides"]
        ]
        self.assertEqual(len(written), 3, "功能寻址写入必须扇出到全部 ECU")

    def test_physical_write_is_scoped_to_one_ecu(self):
        """隔离证据：物理写入只改变目标 ECU。"""
        for case_id in ("W1-physical-write", "I2-write-scoped-to-ecu-a"):
            entry = self.by_id[case_id]
            with self.subTest(case_id=case_id):
                self.assertEqual(len(entry["expected_response"]), 1)
                response_id = entry["expected_response"][0][0]
                self.assertEqual(response_id, 0x7E8, "只应回应 ECU_A 的 response_id")

    def test_unknown_target_produces_no_response(self):
        for case_id in REQUIRED_COVERAGE["unknown physical target"]:
            with self.subTest(case_id=case_id):
                self.assertEqual(self.by_id[case_id]["expected_response"], [])

    def test_negative_responses_are_forwarded_not_dropped(self):
        """
        ECU 的负响应**必须被转发** —— 它与"Gateway 静默丢弃"语义不同。

        （``N*`` 是 **Gateway 放行、ECU 拒绝**，不是 C2 的 deny。）
        """
        for case_id in REQUIRED_COVERAGE["physical negative (ECU-rejected, not policy deny)"]:
            entry = self.by_id[case_id]
            with self.subTest(case_id=case_id):
                self.assertEqual(len(entry["expected_response"]), 1)
                self.assertTrue(entry["expected_response"][0][1].startswith("7f"))

    def test_custom_handler_override_is_recorded(self):
        for case_id in REQUIRED_COVERAGE["custom handler / topology"]:
            entry = self.by_id[case_id]
            with self.subTest(case_id=case_id):
                self.assertTrue(
                    any(ecu["handler"] == lib.HANDLER_ECHO_77 for ecu in entry["ecus"])
                )
                self.assertTrue(entry["expected_response"])


class TestCaptureIsReproducibleButNotTheOracle(unittest.TestCase):
    """
    §19 —— capture 阶段允许"当前 legacy Gateway 产生 Golden"；
    但 A0 之后**禁止**重新生成 expected。

    本类只做**结构级**的独立 replay 检查（不比较 expected 值）：
    证明每个 case 都能从自己的 ``ecus`` 配置**独立构造**出网关 ——
    即 corpus 是自包含的，不依赖 case 之间的执行顺序。
    """

    def test_every_case_builds_its_own_gateway(self):
        for entry in lib.load_corpus()["cases"]:
            with self.subTest(case_id=entry["case_id"]):
                case = lib.deserialize_case(entry)
                gw = lib.build_gateway(case)
                self.assertEqual(
                    [e.name for e in gw.ecus], [e["name"] for e in case["ecus"]]
                )

    def test_build_gateway_returns_independent_instances(self):
        entry = lib.load_corpus()["cases"][0]
        case = lib.deserialize_case(entry)
        first, second = lib.build_gateway(case), lib.build_gateway(case)
        self.assertIsNot(first, second)
        self.assertIsNot(first.ecus[0].state, second.ecus[0].state)

    def test_handlers_are_reconstructed_deterministically(self):
        """具名 handler kind 必须可确定性重建（lambda 不可序列化）。"""
        entry = next(
            e for e in lib.load_corpus()["cases"] if e["case_id"] == "C2-custom-topology-single-ecu"
        )
        first = lib.build_gateway(lib.deserialize_case(entry))
        self.assertEqual(first.ecus[0].process(b"\x01\x02"), b"\x77\x01\x02")


if __name__ == "__main__":
    unittest.main(verbosity=2)
