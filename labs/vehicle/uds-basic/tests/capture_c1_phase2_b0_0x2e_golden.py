#!/usr/bin/env python3
"""
C1 Phase 2 Migration **B0** —— 0x2E legacy golden **捕获**脚本

设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md`` §12 / §13。

对 ``c1_golden_2e_lib.build_cases()`` 定义的每条 case **实测一次**
（走当前生产路径 ``handle_uds_request``），把响应字节与最终状态投影写入
``tests/golden/c1_phase2_0x2e_legacy.json``。

**期望值是实测得到的，不是手写的** —— 手写期望值只能证明"我同意我自己的假设"，
无法证明"迁移前后一致"（§13.1）。

用法：

    python3 tests/capture_c1_phase2_b0_0x2e_golden.py

## 护栏：target SID 专项（**不是** A0 的整表白名单检查）

A0 的护栏是"``_SPEC_EXECUTION`` 必须为空"，因为当时**所有**服务都是 legacy。
B0 时点 ``_SPEC_EXECUTION = {0x22}``，该条件已不成立，照搬会永远拒绝捕获。

B0 正确的条件是 **target SID 未迁移**：

    assert 0x2E not in _SPEC_EXECUTION

即：允许 0x22 已启用，但**禁止** 0x2E 已启用。若不成立则拒绝写入并 exit 2，
避免把"spec 路径"的输出误记为 legacy oracle 而污染 B2 的对拍基准。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import c1_golden_2e_lib as lib2e  # noqa: E402
import c1_golden_lib as lib  # noqa: E402


def main() -> int:
    execution = lib.spec_execution_state()

    # 护栏：target SID（0x2E）必须**未**迁移。
    if lib2e.SID_WRITE in execution:
        print("[FAIL] target SID 0x2E 已在 `_SPEC_EXECUTION` 中：")
        print(f"       _SPEC_EXECUTION = {[hex(s) for s in execution]}")
        print("       B0 的 oracle 必须是 **0x2E 的 legacy 路径**。")
        print("       拒绝写入，避免污染 B2 的对拍基准。")
        return 2

    corpus = lib2e.build_corpus()
    lib2e.save_corpus(corpus)

    print("[ OK ] 已捕获 0x2E legacy golden corpus")
    print(f"       path             = {os.path.relpath(lib2e.CORPUS_PATH, os.path.dirname(lib.GOLDEN_DIR))}")
    print(f"       target SID       = 0x{lib2e.SID_WRITE:02X}")
    print(f"       target execution = {lib2e.TARGET_EXECUTION_MODE}")
    print(f"       _SPEC_EXECUTION  = {[hex(s) for s in execution]}")
    print(f"       cases            = {len(corpus['cases'])}")
    print(f"       commit           = {corpus['captured_at_commit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
