# OQ C1 Phase 2 — B3 Full Regression（证据报告）

> 状态：**B3 = PASS**
> 日期：2026-09-25
> 分支：`audit/oq-c1-phase2-b3-full-regression-20260925`
> 基线：`main = 257dc1ef75aa30ad4d7157b3faf9c725c6eaa63b`（PR #39 merge commit）
> 设计出处：`docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md`
> 对照基线：`docs/OQ_C1_PHASE2_A3_FULL_REGRESSION_2026-09-25.md`

**本报告只记录事实。B3 是验证阶段，不是修复阶段。**

---

## 1. Scope

Migration B 的**全量回归闸门**。

```
B2 = equivalence proof      （legacy oracle ↔ spec execution）
B3 = full regression proof  （本文件）
B4 = inline defensive-check audit
```

当前生产执行配置：

```
0x22 = ServiceSpec execution
0x2E = ServiceSpec execution
0x27 = unchanged
```

---

## 2. Baseline

| 项 | 值 |
|---|---|
| `main` commit | `257dc1ef75aa30ad4d7157b3faf9c725c6eaa63b` |
| B3 分支 | `audit/oq-c1-phase2-b3-full-regression-20260925` |
| B3 分支起点 | `257dc1ef75aa30ad4d7157b3faf9c725c6eaa63b`（= main） |
| `working tree`（开始前） | clean |

```
_SPEC_EXECUTION = frozenset({0x22, 0x2E})      （uds_server.py:2397；运行时实测）
0x22 enabled = True    0x2E enabled = True
0x27 ∉ SERVICE_SPECS   ✓      0x27 ∈ SPECS_EXCLUDED  ✓
resolve_service_spec(0x27, None) is None  ✓
```

---

## 3. Frozen Golden Integrity

使用 B2 建立的 **LF 规范化后的文件内容 SHA-256** 口径：

```
A0  604d906e21906e2e6148977b5d5c162bdfb4ca41020fc51e610e42d3c8d07f8d   MATCH
B0  5b7d7e43f79590c8282937631764d2f4824f9c47c8a9134dc29a8a2b1d19f642   MATCH

unchanged = YES
git diff -- labs/vehicle/uds-basic/tests/golden/   → 无 diff
```

---

## 4. Regression Matrix

按 A3 已建立的门禁**逐项重跑**，覆盖面**未减少**。

| # | Gate | 入口 |
|---|---|---|
| A | UDS unit | `labs/vehicle/uds-basic/tests/run_tests.sh unit` |
| B | UDS e2e | `labs/vehicle/uds-basic/tests/run_tests.sh e2e` |
| C | CAN-tools | `labs/vehicle/can-tools/tests/run_tests.sh all` |
| D | qa_fast | `scripts/qa_fast.sh` |
| E | qa_extended | `scripts/qa_extended.sh`（**未**加 `--fuzz`） |
| F | healthcheck | `scripts/healthcheck.sh --extended` |
| G | C1 专项 | 6 个 C1 测试文件 + 两个 golden replay 复核 |

`vcan0` 全程独占；各 CAN 门禁之间无并发；每层执行前检查无残余
`ecu.py` / `gateway_runtime.py` 进程。

---

## 5. Results

| Gate | 结果 |
|---|---|
| **A. UDS unit** | **PASS** — total **862**，17 项 OK / FAIL 0 / WARN 0 |
| **B. UDS e2e** | **PASS** — total **234**，12 项 OK / FAIL 0 / WARN 0 |
| **C. CAN-tools all** | **PASS** — total **358**，7 项 OK / FAIL 0 / WARN 0 |
| **D. qa_fast** | **PASS** — 25 项 OK / FAIL 0，**1165** cases；ruff `All checks passed`；mypy `no issues in 13 source files` |
| **E. qa_extended** | **PASS** — `QA EXTENDED RESULT: PASS`（uds all / can-tools all / capture lab / yara 全 OK） |
| **F. healthcheck --extended** | **PASS** — **78 PASS / 2 WARN / 0 FAIL** |

```
code/test regression FAIL = 0
```

### G. C1 专项

| 测试 | 结果 |
|---|---|
| `test_c1_contract_pins.py` | **29/29 PASS** |
| `test_c1_service_spec_unit.py` | **28/28 PASS** |
| `test_c1_phase2_framework_unit.py` | **56/56 PASS** |
| `test_c1_phase2_a0_golden.py` | **26/26 PASS** |
| `test_c1_phase2_b0_0x2e_golden.py` | **30/30 PASS** |
| `test_c1_phase2_b2_0x2e_golden.py` | **20/20 PASS** |

golden replay 复核（**两个 target SID 各自独立**）：

```
0x22 (A0/A2)   cases=92   response=92/92   state=92/92
0x2E (B0/B2)   cases=70   response=70/70   state=70/70
```

---

## 6. Historical Baseline Comparison

```
A3 historical    A3（0x22-only，_SPEC_EXECUTION={0x22}）
B3 current       B3（0x22 + 0x2E，_SPEC_EXECUTION={0x22,0x2E}）
```

| Gate | A3 历史 | B3 当前 | Δ |
|---|---|---|---|
| UDS unit | 797（15 文件） | **862**（17 文件） | **+65** |
| UDS e2e | 234（12 项） | **234**（12 项） | **0** |
| UDS all | 1031 | **1096** | +65 |
| CAN-tools all | 358（7 项） | **358**（7 项） | **0** |
| qa_fast cases | 1100（23 项） | **1165**（25 项） | **+65** |
| qa_fast 项 | 23 | 25 | +2 |
| qa_extended | PASS | PASS | — |
| healthcheck | 78 / 2 / 0 | **78 / 2 / 0** | **0** |

### Δ 来源（+65，全部为 C1 Phase 2 新增测试）

| 来源 | A3 | B3 | Δ |
|---|---|---|---|
| `test_c1_phase2_framework_unit.py` | 41 | 56 | +15（B1 的 `TestB1_0x2EGuardActivation`） |
| `test_c1_phase2_b0_0x2e_golden.py` | — | 30 | +30（B0 新增） |
| `test_c1_phase2_b2_0x2e_golden.py` | — | 20 | +20（B2 新增） |

验证：`797 + 15 + 30 + 20 = 862` ✓

**未删除任何既有测试**；**e2e 稳定 234、CAN-tools 稳定 358、healthcheck 稳定 78/2/0**
（与 A3 逐项相同）。即：**原始基线用例全部通过，增量只来自新阶段测试**。

---

## 7. C1 Specific Regression Checks

### 0x22

```
ServiceSpec execution = ON      （_guards_for() 返回 1 个 guard）
A0 golden             = unchanged（SHA-256 MATCH）
A0 structural/replay  = 26/26 PASS
A0/A2 replay          = 92/92 response + 92/92 state
```

### 0x2E

```
ServiceSpec execution = ON      （_guards_for() 返回 6 个 guard）
B0 frozen corpus test = 30/30 PASS   （定义完整性；不执行当前 dispatcher）
B2 golden replay      = 20/20 PASS   （legacy oracle ↔ spec execution）
0x2E replay           = 70/70 response + 70/70 state
相关 e2e              = `0x2E/0x3E write-DID e2e tests` OK
```

### 0x27

```
0x27 ∉ SERVICE_SPECS      ✓      （uds_server.py:2335 / :2435）
0x27 ∈ SPECS_EXCLUDED     ✓      （:2363 护栏 "必须显式出现在 SPECS_EXCLUDED 中"）
resolve_service_spec(0x27, None) is None  ✓
动态注入 / SECURITY_CONFIG / _now() 现场求值语义 = 未改动
```

**B3 未把 0x27 带入迁移。**

### Gateway

```
gateway.py            UNCHANGED
gateway_runtime.py    UNCHANGED
相关测试              unit `multi-ECU gateway routing` OK
                      unit `gateway runtime (dispatch/lifecycle/error containment)` OK
                      e2e  `gateway runtime daemon` OK
```

---

## 8. Environment Warnings

healthcheck 报 **2 条 WARN**，与 A3 完全相同：

```
[WARN] Git working tree has uncommitted changes (by design during lab work)
[WARN] docker installed but engine not reachable
```

**类别判定（§12 Type C：环境问题，非代码回归）**

1. **工作区非 clean** —— 由本次 WSL 执行所需的**工作区行尾规范化**引起
   （`*.sh` 由 CRLF 转 LF；见 Issue #28）。该规范化**对 git 内容不可见**，
   测量完成后已 `git checkout --` 复原，**B3 结束时工作树为 clean**。
2. **Docker engine 不可达** —— Docker Desktop 未启动。本轮全部门禁
   （UDS / CAN-tools / capture-replay / YARA）均**不依赖** Docker 引擎。

两条均为**环境级、非阻断**；**没有**把环境不可用伪装成 PASS，也**没有**
把已知环境 WARN 描述成代码回归。

```
failures（代码级）= 0
```

---

## 9. Git Diff

```
git status        → clean
git diff --check  → 空

production diff   = 0
    uds_server.py / ecu.py / gateway.py / gateway_runtime.py / service_spec.py   全 UNCHANGED

golden diff       = 0
    labs/vehicle/uds-basic/tests/golden/  → 无 diff

test / docs diff  = 仅本报告（新增）
```

**B3 全程未修改任何生产逻辑、任何测试断言、任何 golden。**

---

## 10. CI

PR 上运行 Fast QA（ruff + mypy + offline tests）；结果记录于 PR。

---

## 11. Conclusion

| # | 硬门槛 | 结果 |
|---|---|---|
| 1 | 所有 established full-regression gates 执行完成 | ✓（A–G 全覆盖，未减少 A3 覆盖面） |
| 2 | code/test regression FAIL = 0 | ✓ |
| 3 | required UDS regression = PASS | ✓（unit 862 / e2e 234） |
| 4 | required CAN-tools regression = PASS | ✓（358） |
| 5 | qa_fast = PASS | ✓ |
| 6 | qa_extended = PASS | ✓ |
| 7 | healthcheck = 0 FAIL | ✓（78 PASS / 2 环境 WARN） |
| 8 | A0 Golden unchanged | ✓ |
| 9 | B0 Golden unchanged | ✓ |
| 10 | production diff = 0 | ✓ |
| 11 | 0x27 unchanged | ✓ |
| 12 | gateway unchanged | ✓ |
| 13 | CI PASS | ✓ |

```
B3 = PASS
```

在 `_SPEC_EXECUTION = {0x22, 0x2E}` 的最终双迁移状态下，
**SecurityLab 既有回归基线全部通过，且无任何生产代码改动。**

---

## 12. Next Stage

```
B4 = NOT STARTED   （inline defensive-check audit）
C2 = NOT STARTED
C3 = NOT STARTED

0x22 = ServiceSpec execution
0x2E = ServiceSpec execution
0x27 = unchanged

Migration B = IN PROGRESS
```

**B3 结束后不自动进入 B4。**
