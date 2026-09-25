#!/usr/bin/env python3
"""
C1 Phase 2 Migration A0 —— 0x22 legacy golden **捕获**脚本

设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md`` §13。

本脚本对 ``c1_golden_lib.build_cases()`` 定义的每条 case **实测一次**
（走当前生产路径 ``handle_uds_request``），把响应字节与最终状态投影写入
``tests/golden/c1_phase2_0x22_legacy.json``。

**期望值是实测得到的，不是手写的** —— 手写期望值只能证明"我同意我自己的假设"，
无法证明"迁移前后一致"（§13.1）。

用法：

    python3 tests/capture_c1_phase2_a0_golden.py

护栏（§13.4）：``_SPEC_EXECUTION`` **必须为空**。
非空意味着部分服务已切到 spec 执行路径，此时捕获到的**不是**纯 legacy oracle，
脚本会拒绝执行并退出 2。

重放与一致性检查见 ``test_c1_phase2_a0_golden.py``。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import c1_golden_lib as lib  # noqa: E402


def main() -> int:
    execution = lib.spec_execution_state()

    if execution:
        print("[FAIL] `_SPEC_EXECUTION` 非空，当前 dispatcher 已不是纯 legacy 模式：")
        print(f"       _SPEC_EXECUTION = {[hex(s) for s in execution]}")
        print("       §13.4 要求 golden 必须在 dispatcher_mode == 'legacy' 时捕获。")
        print("       拒绝写入，避免污染 legacy oracle。")
        return 2

    corpus = lib.build_corpus()
    lib.save_corpus(corpus)

    print("[ OK ] 已捕获 legacy golden corpus")
    print(f"       path        = {os.path.relpath(lib.CORPUS_PATH, os.path.dirname(lib.GOLDEN_DIR))}")
    print(f"       cases       = {len(corpus['cases'])}")
    print(f"       mode        = {corpus['dispatcher_mode']}")
    print(f"       _SPEC_EXEC  = {corpus['spec_execution_at_capture']}")
    print(f"       commit      = {corpus['captured_at_commit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
