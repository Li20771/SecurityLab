#!/usr/bin/env python3
"""
C2-A0 —— Legacy Gateway golden **捕获**脚本

设计出处：``docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md``
§3（当前 Gateway 行为）/ §13（迁移策略）。

对 ``c2_gateway_golden_lib.build_cases()`` 定义的每条 case **实测一次**
（走真实的 ``DiagnosticGateway.route()``），把路由结果与**逐 ECU 最终状态投影**
写入 ``tests/golden/c2_gateway_legacy.json``。

**期望值是实测得到的，不是手写的。**

用法：

    python3 tests/capture_c2_a0_gateway_golden.py

## 护栏（fail-closed）：policy 机制尚不存在

A0 的 Golden 是 **C2 policy 引入之前**的 legacy 行为快照。
若 ``gateway.py`` 中已经出现 policy 机制的符号
（``PolicyTrace`` / ``route_with_trace`` / ``policy_evaluate`` / ``GatewayPolicy``），
则当前路由路径**已不再是纯 legacy**，此时捕获会污染 oracle。

→ 脚本拒绝写入并 ``exit 2``。

**A0 之后禁止重新生成该 Golden**（设计文档 §13：capture 一次，此后只读）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import c2_gateway_golden_lib as lib  # noqa: E402


def main() -> int:
    # ---- 护栏：C2 policy 机制必须尚不存在 ----
    if lib.policy_execution_present():
        print("[FAIL] 检测到 Gateway policy 机制已存在（gateway.py 含 policy 符号）：")
        print("       A0 的 oracle 必须是 **policy 引入之前**的 legacy 行为。")
        print("       拒绝写入，避免污染 C2-A2 的对拍基准。")
        return 2

    corpus = lib.build_corpus()
    lib.save_corpus(corpus)

    print("[ OK ] 已捕获 legacy Gateway golden corpus")
    print(f"       path             = {os.path.relpath(lib.CORPUS_PATH, os.path.dirname(lib.GOLDEN_DIR))}")
    print(f"       gateway_mode     = {corpus['gateway_mode']}")
    print(f"       policy_execution = {corpus['policy_execution']}")
    print(f"       functional_addr  = 0x{corpus['functional_address']:03X}")
    print(f"       cases            = {len(corpus['cases'])}")
    print(f"       commit           = {corpus['captured_at_commit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
