# C1 Phase 2 Migration A3 —— 0x22 Full Regression Gate（证据报告）

> 状态：**A3 = PASS**（阻塞 gate 全部通过）
> 日期：2026-09-25
> 分支：`audit/oq-c1-phase2-a3-0x22-full-regression-20260925`
> 基线：`main = 56c8a074bc4e0eab4266c7c85f22954e630a7e0f`
> 上游设计：`docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md`（§11.1 A3 / §17 Exit Criteria）

**本报告只记录事实，不引入新的设计决策。**

A3 是**工程级全量回归闸门**：证明在 0x22 已切换到 ServiceSpec execution 的情况下，
整个现有回归网保持通过，且没有出现未预期的行为变化。

---

## 13.1 基线（执行前冻结状态）

### 仓库状态

| 项 | 值 |
|---|---|
| `main` commit | `56c8a074bc4e0eab4266c7c85f22954e630a7e0f` |
| A3 分支 | `audit/oq-c1-phase2-a3-0x22-full-regression-20260925` |
| A3 分支 HEAD（执行前） | `56c8a074bc4e0eab4266c7c85f22954e630a7e0f`（= main） |
| `git status --short` | 空 |
| `git diff --stat` | 空 |
| 已合并前置 PR | #30（Step 1）/ #31（A0）/ #32（A1）/ #33（A2）/ #34（docs sync） |

### C1 迁移状态（运行时实测，非文档引用）

```
_SPEC_EXECUTION       = frozenset({0x22})
  0x22 `in` 白名单    = True
  0x2E `in` 白名单    = False
  _guards_for(0x22)   → 1 个 guard（实际执行）
  _guards_for(0x2E)   → 0 个（不执行）
```

### 0x27 边界（运行时实测）

```
0x27 in SERVICE_SPECS    = False
0x27 in SPECS_EXCLUDED   = True
resolve_service_spec(0x27, None) = None
注入 handler 经归一化后 guards = ()
_security_services 现场求值 _now()      = True
_security_services 原对象引用 SECURITY_CONFIG = True
```

### C2 / C3 边界与 golden 冻结（blob 哈希）

| 文件 | blob（前 12 位） | 状态 |
|---|---|---|
| `ecu/ecu.py` | `710d6515e1e5` | IDENTICAL |
| `ecu/gateway.py` | `e64cd088fc86` | IDENTICAL |
| `ecu/gateway_runtime.py` | `85604271db06` | IDENTICAL |
| `ecu/service_spec.py` | `128f8e5c2875` | IDENTICAL |
| `tests/golden/c1_phase2_0x22_legacy.json` | `85c28dbdd3dd` | IDENTICAL |
| `tests/test_c1_contract_pins.py` | `46f14b13d94c` | IDENTICAL |

golden provenance 未变：`dispatcher_mode = legacy`、`spec_execution_at_capture = []`、92 条 case。

---

## 13.2 执行的命令

```bash
# 第一层
bash scripts/qa_fast.sh

# 第二层（vcan0 独占）
bash labs/vehicle/uds-basic/tests/run_tests.sh all

# 第三层
bash labs/vehicle/can-tools/tests/run_tests.sh all

# 第四层（vcan0 独占；未使用 --fuzz）
bash scripts/qa_extended.sh

# 第五层
bash scripts/healthcheck.sh --extended

# C1 专项
~/.venvs/network/bin/python labs/vehicle/uds-basic/tests/test_c1_contract_pins.py
~/.venvs/network/bin/python labs/vehicle/uds-basic/tests/test_c1_service_spec_unit.py
~/.venvs/network/bin/python labs/vehicle/uds-basic/tests/test_c1_phase2_framework_unit.py
~/.venvs/network/bin/python labs/vehicle/uds-basic/tests/test_c1_phase2_a0_golden.py
```

**未执行** `qa_extended.sh --fuzz`：fuzz 是独立长时专项，不属 A3 默认 blocking gate。

环境：WSL venv，Python 3.12.3；`vcan0` 全程独占，各 CAN 门禁之间无并发，
每层执行前检查无残余 `ecu.py` / `gateway_runtime.py` 进程。

---

## 13.3 实际结果

| # | Gate | 实际用例数 | PASS / WARN / FAIL | 证据 |
|---|---|---|---|---|
| 1 | `qa_fast.sh` | 1100 | PASS 23 项 / WARN 0 / FAIL 0 | `QA FAST RESULT: PASS`；ruff `All checks passed`；mypy `no issues in 13 source files` |
| 2 | UDS `run_tests.sh all` | **1031** | **PASS 27 / WARN 0 / FAIL 0** | `PASS: 27  WARN: 0  FAIL: 0` |
| 2a | ├ UDS unit | 797 | PASS 15 项 / FAIL 0 | 15 个测试文件全 OK |
| 2b | └ UDS e2e | 234 | PASS 12 项 / FAIL 0 | 12 项全 OK（含 `vcan0 可用` 前置检查） |
| 3 | CAN-tools `run_tests.sh all` | **358** | **PASS 7 / WARN 0 / FAIL 0** | offline 4 项 + vcan 3 项全 OK |
| 4 | `qa_extended.sh` | （含 2 + 3） | **PASS** | `QA EXTENDED RESULT: PASS`；uds all / can-tools all / capture lab / yara 全 OK |
| 5 | `healthcheck.sh --extended` | 80 项 | **PASS 78 / WARN 2 / FAIL 0** | `PASS: 78  WARN: 2  FAIL: 0` |

### C1 专项

| 测试 | 结果 |
|---|---|
| `test_c1_contract_pins.py` | **29/29 PASS** |
| `test_c1_service_spec_unit.py` | **28/28 PASS** |
| `test_c1_phase2_framework_unit.py` | **41/41 PASS** |
| `test_c1_phase2_a0_golden.py` | **26/26 PASS** |
| golden replay response 对拍 | **92/92** |
| golden replay state 对拍 | **92/92** |

> A0/A2 golden corpus **已包含在完整 unit 回归内**（`run_tests.sh unit` 的
> "C1 phase 2 A0 legacy golden corpus replay passed"），并在本报告中单独复核。

---

## 13.4 测试数量：历史基线与当前基线的区别

设计冻结文档 §17 写的 `baseline regression 964 PASS` 是 **Phase 1 时期的历史基线**。
当前 main 已加入该文档写作时尚不存在的测试，因此**不得**把本轮结果记作 "964 PASS"。

```
historical design baseline = 964   （= Phase 1 unit 730 + e2e 234）
current A3 executed baseline = 1031  （= unit 797 + e2e 234）
```

**增量 +67 全部来自 Phase 2 新增测试**（Phase 1 的 730 中两者均不存在）：

| 来源（**unit 内部**） | 用例数 | 引入 |
|---|---|---|
| `test_c1_phase2_framework_unit.py` | 41 | Step 1 引入 26，A1 增至 41 |
| `test_c1_phase2_a0_golden.py` | 26 | A0 引入 22，A1 减 1、A2 增至 26 |
| 其余 13 个 unit 文件 | 730 − 29 − 28 = **673** | **未变**（与 Phase 1 逐文件一致） |

验证：

```
797 − 41 − 26 = 730          （回到 Phase 1 unit 基线）
730 + 234     = 964          （回到 Phase 1 总基线）
797 + 234     = 1031         （当前）
```

**e2e 稳定为 234，无变化。** 专项测试数量亦未变：contract pins 29、service-spec 28。

---

## 13.5 异常与环境说明（原样记录）

### WARN 1 —— `[WARN] Git working tree has uncommitted changes (by design during lab work)`

**非 A3 回归。** 由本次执行中的**工作区行尾规范化**引起：
为在 WSL 下运行 `*.sh`，将工作区 `.sh` 文件由 CRLF 转为 LF
（`core.autocrlf=true` 的 Windows 检出产物，见 Issue #28）。

该规范化**对 git 内容不可见** —— 已验证工作区经 clean filter 的哈希与 HEAD blob 完全一致：

```
run_tests.sh                    IDENTICAL
scripts/repro/verify_gateway_runtime.sh  IDENTICAL
```

测量完成后已 `git checkout --` 复原，**A3 结束时工作树为 clean**。

### WARN 2 —— `[WARN] docker installed but engine not reachable`

**非 A3 回归。** Docker Desktop 未启动，属**环境级**缺失。
本仓库 A3 涉及的全部门禁（UDS / CAN-tools / capture-replay / YARA）均不依赖
Docker 引擎，故不影响判定。

### 已知环境问题（本轮未处理）

* **Issue #28**（Windows/WSL `*.sh` CRLF checkout）：本轮按既有方式以工作区规范化规避，
  **未修改仓库内容**，**未添加 `.gitattributes`**。
* 此前记录过的 e2e 瞬时启动超时（"虚拟 ECU 在 10.0s 内没有就绪"）：**本轮未复现**。
  UDS `all` 与 `qa_extended` 两次全量 e2e 均一次通过。

### 未发现

* 无 `FAIL`（五层 gate 全部 FAIL = 0）
* 无 golden mismatch
* 无非预期行为变化

---

## 13.6 C1 专项结论

```
0x22 remained spec-enabled        ✓  frozenset({0x22})，guards 实际执行（1 个）
0x2E remained disabled            ✓  不在白名单，_guards_for() = ()
0x27 unchanged                    ✓  ∉ SERVICE_SPECS、∈ SPECS_EXCLUDED；
                                     注入机制（_now() 现场求值 / SECURITY_CONFIG 原对象引用）未变
A2 golden unchanged               ✓  blob 85c28dbdd3dd 未变，92 条 case 原样
29 pins PASS                      ✓
28 service-spec PASS              ✓
A2 replay PASS                    ✓  92/92 response + 92/92 state
```

### 边界确认

```
ecu.py unchanged              ✓  710d6515e1e5
gateway.py unchanged          ✓  e64cd088fc86
gateway_runtime.py unchanged  ✓  85604271db06
golden corpus unchanged       ✓  85c28dbdd3dd
no extra SID enabled          ✓
no new session                ✓
no new authorization policy   ✓
no unrelated production change ✓（本阶段 production code 改动 = 0）
```

---

## 13.7 最终判定

所有阻塞 gate 满足：

| 判定项 | 结果 |
|---|---|
| C1 migration 状态（0x22 ON / 0x2E OFF / 0x27 unchanged） | PASS |
| 29 contract pins | PASS |
| 28 service-spec | PASS |
| Phase2 framework | PASS |
| 92 golden replay response comparison | PASS |
| 92 golden replay state comparison | PASS |
| UDS unit | PASS |
| UDS e2e | PASS |
| CAN-tools offline | PASS |
| CAN-tools malformed/vcan | PASS |
| qa_fast | PASS |
| qa_extended | PASS |
| healthcheck --extended | PASS（2 条环境级 WARN，非阻塞） |

```
A3 = PASS
```

### 本阶段范围

```
production files changed = 0
test files changed       = 0
golden changed           = NO
_SPEC_EXECUTION changed  = NO
```

本阶段**未**：修改生产逻辑 / 修改 `_SPEC_EXECUTION` / 启用 0x2E / 修改 0x27 /
删除 handler inline checks / 修改 29 pins / 修改或重新生成 golden / 放宽任何断言 /
迁移其他服务 / 处理 Issue #28 / 做无关 docs cleanup。

### 下一步

按冻结流程，**A3 之后是 A4**（"inline defensive checks 保留"的**确认**步骤，
不需要删除或修改代码），**A4 完成后 Migration A 才结束**，之后才允许进入 B0。

本阶段结束后**未**进入 A4 / B0 / Migration B / C2 / C3。
