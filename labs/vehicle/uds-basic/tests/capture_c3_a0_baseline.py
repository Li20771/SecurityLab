#!/usr/bin/env python3
"""
C3-A0 —— 0x27 SecurityAccess **实现前基线** 的捕获脚本

设计出处：``docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md``
（§13 对拍模型 / §14.2 冻结类别）。

对 ``c3_baseline_lib.build_cases()`` 定义的每条 case **实测一次**，把
response / 逐 ECU 常规状态 / 逐 ECU 安全状态 / policy decision 写入
``tests/golden/c3_security_access_baseline.json``。

**期望值是实测得到的，不是手写的。**

用法：

    python3 tests/capture_c3_a0_baseline.py

## 护栏（fail-closed）：C3 实现尚不存在

C3-A0 的基线是 **C3 接线之前**的行为快照。若 ``gateway.py`` 中已经出现
C3-A1 的接线标记（``services_provider`` / ``security_access_services``），
则当前路径**已不再是"实现前"**，此时捕获会污染 C3-A1/A2 的对比基准。

→ 脚本拒绝写入并 ``exit 2``。

**A0 之后禁止重新生成该基线**（设计 §14.2：capture 一次，此后只读）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import c3_baseline_lib as lib  # noqa: E402


def main() -> int:
    # ---- 护栏：C3 实现必须尚不存在 ----
    if lib.c3_implementation_present():
        print("[FAIL] 检测到 C3 接线标记已存在于 gateway.py：")
        print("       A0 的基线必须是 **C3 接线之前** 的实现前行为。")
        print("       拒绝写入，避免污染 C3-A1/A2 的对比基准。")
        return 2

    corpus = lib.build_corpus()
    lib.save_corpus(corpus)

    direct = sum(1 for c in corpus["cases"] if c["path"] == lib.PATH_DIRECT_ECU)
    gateway = sum(1 for c in corpus["cases"] if c["path"] == lib.PATH_GATEWAY)

    print("[ OK ] 已捕获 0x27 SecurityAccess 实现前基线")
    print(f"       path             = {os.path.relpath(lib.CORPUS_PATH, os.path.dirname(lib.GOLDEN_DIR))}")
    print(f"       cases            = {len(corpus['cases'])}（direct_ecu={direct} / gateway={gateway}）")
    print(f"       schema_version   = {corpus['schema_version']}")
    print(f"       gateway_0x27_reachable = {corpus['gateway_0x27_reachable']}")
    print(f"       base_clock       = {corpus['base_clock']}")
    print(f"       commit           = {corpus['captured_at_commit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
