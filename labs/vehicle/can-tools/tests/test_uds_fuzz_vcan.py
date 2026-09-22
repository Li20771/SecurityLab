#!/usr/bin/env python3
"""
UDS fuzz v1 —— vcan0 小规模 smoke（**不是** full campaign）

真实 SocketCAN / ISO-TP 链路上的最小闭环：

    uds_fuzz.run_campaign(--max-cases 3)
     → isotp_fuzz.execute_case()（每 case 全新 ECU）
     → replay.py 精确重放落盘 case

本文件**刻意不写任何 ECU 生命周期代码**：`EcuProcess` / `RawChannel` /
recovery probe / kernel oracle / cleanup 全部来自既有引擎（Frozen Contract §14）。
本文件若自己起 ECU，就会与引擎的清理机制抢 vcan0。

覆盖（Frozen Contract §19）：

1. 小 campaign 端到端跑通，分类全部落在既有 8 类内
2. **fresh ECU / cleanup 证据**：每个 case 的结果字段完整
   （process_alive / recovery / kernel_oracle 三态齐全）
3. **oracle 未被改动**：CLASS_PRIORITY 与冻结值逐字相同
4. **去重生效**：executed == unique < generated
5. **replay 兼容**：落盘 case 可被既有 `replay.py` 精确重放，
   且 `--repeat` 得到同一分类（确定性）
6. **不污染仓库**：产物只写 mktemp 目录

前置：vcan0 已存在并 UP；本测试**独占总线**，勿与其它 CAN 测试并行。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_uds_fuzz_vcan.py
"""

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)
_FUZZ_DIR = os.path.join(_TOOLS_DIR, "fuzz")

for _p in (_TOOLS_DIR, _FUZZ_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import case_schema as cs  # noqa: E402
import isotp_fuzz as engine  # noqa: E402
import replay  # noqa: E402
import uds_fuzz  # noqa: E402

#: v1 smoke 的 case 预算（**刻意很小**：这是 smoke，不是 campaign）
SMOKE_MAX_CASES = 3

#: 冻结的 oracle 分类与优先级（Frozen Contract §9：v1 不新增 class、不改优先级）
FROZEN_CLASS_PRIORITY = (
    "KERNEL_ANOMALY",
    "ECU_CRASH",
    "STATE_CORRUPTION",
    "HANG",
    "TRANSPORT_ERROR",
    "UDS_RESPONSE",
    "DISCARD",
    "NORMAL",
)

#: 每个 case 的结果必须携带的字段（证明完整生命周期 + oracle 都跑到了）
REQUIRED_RESULT_FIELDS = (
    "class", "process_alive", "process_returncode", "response_kind",
    "errno", "new_errno", "recovery", "kernel_oracle", "hung", "duration_ms",
)


def vcan_available() -> bool:
    return os.path.exists("/sys/class/net/vcan0")


@unittest.skipUnless(vcan_available(), "vcan0 不存在，跳过 UDS fuzz vcan smoke")
class TestUdsFuzzVcanSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.work = tempfile.mkdtemp(prefix="uds-fuzz-vcan-")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.work, ignore_errors=True)

    # -- offline 前置（每次跑都确认契约没被改动）----------------------

    def test_oracle_priority_is_unchanged(self) -> None:
        self.assertEqual(tuple(cs.CLASS_PRIORITY), FROZEN_CLASS_PRIORITY)

    def test_schema_version_is_unchanged(self) -> None:
        self.assertEqual(cs.SCHEMA_VERSION, 1)

    # -- campaign --------------------------------------------------------

    def test_small_campaign_runs_and_classifies(self) -> None:
        out_dir = os.path.join(self.work, "campaign")

        summary = uds_fuzz.run_campaign(
            campaign_seed=12345,
            max_cases=SMOKE_MAX_CASES,
            out_dir=out_dir,
        )

        self.assertEqual(summary["layer"], "uds-single-request")
        self.assertEqual(summary["selected"], SMOKE_MAX_CASES)

        # 去重确实生效：executed(selected) <= unique < generated
        self.assertLess(summary["unique"], summary["generated"])
        self.assertGreater(summary["duplicates"], 0)
        self.assertLessEqual(summary["selected"], summary["unique"])

        # 分类全部落在既有 8 类内（UDS 层未引入新 class）
        self.assertTrue(summary["classes"], "smoke 应至少产生一个分类结果")

        for name in summary["classes"]:
            with self.subTest(cls=name):
                self.assertIn(name, cs.CLASS_PRIORITY)

    def test_persisted_cases_carry_full_result(self) -> None:
        """落盘 case 的 result 字段完整 → 证明完整生命周期 + oracle 都跑到了。"""
        out_dir = os.path.join(self.work, "persisted")

        uds_fuzz.run_campaign(campaign_seed=12345, max_cases=1, out_dir=out_dir)

        paths, ignored = replay.collect_cases(out_dir)

        self.assertEqual(len(paths), 1, f"应恰好落盘 1 个 case；ignored={ignored}")

        case = replay.load_case(paths[0])
        cs.validate_case(case, require_result=True)

        result = case["result"]

        for field in REQUIRED_RESULT_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, result)

        # kernel oracle 必须是三态之一（不得伪报 0）
        self.assertIn(result["kernel_oracle"], cs.KERNEL_ORACLE_STATUSES)

        if result["kernel_oracle"] != cs.KERNEL_ORACLE_AVAILABLE:
            self.assertIsNone(result["kernel_events"],
                              "oracle 非 AVAILABLE 时 kernel_events 必须为 None")

    def test_summary_json_is_not_mistaken_for_a_case(self) -> None:
        """campaign summary 必须被 replay 的目录扫描识别为 metadata 并跳过。"""
        out_dir = os.path.join(self.work, "with-summary")

        uds_fuzz.run_campaign(campaign_seed=12345, max_cases=1, out_dir=out_dir)

        _, ignored = replay.collect_cases(out_dir)
        names = [os.path.basename(p) for p in ignored]

        self.assertIn("uds-campaign-summary.json", names)

    def test_replay_reproduces_same_class(self) -> None:
        """既有 replay.py 精确重放 UDS fuzz case，且重复执行分类一致。"""
        out_dir = os.path.join(self.work, "replay")

        uds_fuzz.run_campaign(campaign_seed=12345, max_cases=1, out_dir=out_dir)

        paths, _ = replay.collect_cases(out_dir)
        self.assertEqual(len(paths), 1)

        case = replay.load_case(paths[0])
        outcomes = replay.replay_case(case, repeat=2)

        self.assertEqual(len(outcomes), 2)

        classes = {outcome["result"]["class"] for outcome in outcomes}

        self.assertEqual(len(classes), 1,
                         f"同一 case 重复执行分类必须一致，实际 {classes}")

        for outcome in outcomes:
            self.assertTrue(outcome["replay_identity_match"])

    def test_campaign_is_deterministic_across_runs(self) -> None:
        """同 --seed / --max-cases 两次生成必须选出同一批 case（同一顺序）。"""
        first = uds_fuzz.run_campaign(campaign_seed=4242, max_cases=5,
                                      list_only=True)
        second = uds_fuzz.run_campaign(campaign_seed=4242, max_cases=5,
                                       list_only=True)

        self.assertEqual(first["order"], second["order"])
        self.assertEqual(len(first["order"]), 5)

    def test_ecudir_engine_constants_untouched(self) -> None:
        """引擎的 timeout / 观察窗口未被 UDS 层改动（复用而非重定义）。"""
        self.assertEqual(engine.CASE_TIMEOUT, 20.0)
        self.assertEqual(engine.OBSERVE_QUIET, 0.35)


if __name__ == "__main__":
    unittest.main(verbosity=2)
