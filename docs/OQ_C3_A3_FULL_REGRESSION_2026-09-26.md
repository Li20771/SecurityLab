# OQ C3-A3 —— Full Regression（证据报告）

> 状态：**C3-A3 = PASS** ／ **Full regression = PASS**
> 日期：2026-09-26
> 分支：`audit/oq-c3-a3-full-regression-20260926`
> 基线：`main = 121b6f4675dfec20193e8c78d575c3011fd8fd39`（= C3-A2 merge commit，PR #53）

## 1. BASELINE

```
BASE SHA = 121b6f4675dfec20193e8c78d575c3011fd8fd39
```

| 检查 | 结果 |
|---|---|
| `HEAD` | `121b6f4675dfec20193e8c78d575c3011fd8fd39` ✓ 与基线一致 |
| `working tree`（开始前） | clean |
| `git diff --stat 121b6f4 -- labs/vehicle/uds-basic/ecu/` | **空**（无意外生产变化） |
| `git log -5` | `121b6f4`(merge #53) → `d1c50f8` → `c6adc2d` → `2733de7`(merge #52) → `39e976f` |

## 2. SCOPE

```
**Full regression evidence only。**

本阶段：执行既有 gate、记录真实结果、新增本报告。
本阶段**未**：修改 production / Golden / legacy / C2-A2 lib / C2·C3 测试语义 /
              设计文档 / CI；未做 A4 审计；未做 A5 exit review。
```

## 3. TEST MATRIX

```
A  UDS full              bash labs/vehicle/uds-basic/tests/run_tests.sh all
B  CAN / can-tools full  bash labs/vehicle/can-tools/tests/run_tests.sh all
C  QA fast               bash scripts/qa_fast.sh
D  Extended QA           bash scripts/qa_extended.sh
E  Healthcheck           bash scripts/healthcheck.sh        ／ --extended
F  Repro gates           scripts/repro/verify_{arm,qemu_gdb,ghidra,
                         fuzz_smoke,gateway_runtime,uds_fuzz_smoke}.sh
G  C3 key regression     C3-A0 / C3-A1 / C3-A2 / C2-A2
```

**vcan0 纪律**：所有涉及 vcan0 的项**串行**执行（`run_tests.sh all` → `can-tools all`
→ `qa_extended` → `--extended healthcheck` → repro 中的 vcan 项），无并发。
执行前后 `vcan0` 均为 `UP`，无残留进程、无残留临时目录。

## 4. RESULT TABLE

| Gate | 命令 | 结果 | 计数 | exit |
|---|---|---|---|---|
| **A. UDS all** | `run_tests.sh all` | **PASS 37 / WARN 0 / FAIL 0** | unit 1200（25 文件）+ e2e 235（11 文件）= **1435**；真实 skip **0** | **0** |
| **B. CAN all** | `can-tools/tests/run_tests.sh all` | **PASS 7 / WARN 0 / FAIL 0** | **358**；真实 skip **0** | **0** |
| **C. qa_fast** | `scripts/qa_fast.sh` | **PASS**（ruff OK / mypy OK / uds unit PASS 25 FAIL 0 / can-tools offline PASS 4 FAIL 0） | —— | **0** |
| **D. qa_extended** | `scripts/qa_extended.sh` | **PASS**（4/4 步骤全 OK） | **1793**；真实 skip **0** | **0** |
| **E. healthcheck** | `scripts/healthcheck.sh` | **PASS 75 / WARN 2 / FAIL 0** | 77 项 | **0** |
| **E′. healthcheck --extended** | `scripts/healthcheck.sh --extended` | **PASS 78 / WARN 2 / FAIL 0** | 80 项 | **0** |
| **F. repro** | 6 个 `verify_*.sh` | **全部 exit 0，FAIL 0** | PASS 6+4+8+8+7+10 = **43** | **0** ×6 |
| **G. C3 回归** | 4 个专项 | **全部 OK** | 35 + 48 + 42 + 46 = **171** | **0** ×4 |

### 4.1 D. qa_extended 步骤级

```
[1/4] UDS 全套（单元 + vcan0 端到端）   → [ OK ] uds all        （PASS 37 / FAIL 0）
[2/4] CAN tools 全套（离线 + 畸形 ISO-TP）→ [ OK ] can-tools all （PASS 7 / FAIL 0）
[3/4] capture → save → replay 闭环       → [ OK ] capture lab
[4/4] YARA 真阳性 / 真阴性演练            → [ OK ] yara
QA EXTENDED RESULT: PASS        exit 0
```

### 4.2 F. repro 逐项（**功能闭环**证据，非"工具存在"）

| 脚本 | 结果 | 关键证据 |
|---|---|---|
| `verify_arm.sh` | **PASS 6 / FAIL 0** | AArch64 静态编译 + **实际执行**（`ARM_RUNTIME_OK 42`）；armhf + qemu-arm |
| `verify_qemu_gdb.sh` | **PASS 4 / FAIL 0** | qemu-aarch64 `-g` → gdb-multiarch remote **命中 main 断点** → continue 正常退出 |
| `verify_ghidra.sh` | **PASS 8 / FAIL 0** | analyzeHeadless 分析 rc=0 → `Architecture=AARCH64` → `ERROR_COUNT=0` → main 反汇编 |
| `verify_fuzz_smoke.sh` | **PASS 8 / FAIL 0** | 3 case + oracle 三态 + `replay --all` issues=0 |
| `verify_gateway_runtime.sh` | **PASS 7 / FAIL 0** | 守护进程就绪 → 端到端全过 → SIGTERM 优雅退出（0）→ 无残余 |
| `verify_uds_fuzz_smoke.sh` | **PASS 10 / FAIL 0** | v1 golden 947 + 边界接受/拒绝 + 3 case vcan campaign + replay issues=0 |

```
NOT RUN：无。全部 6 个脚本**实际执行**且 exit 0。
```

## 5. C3-SPECIFIC REGRESSION

| Gate | 结果 | 要求的计数 | 符合 |
|---|---|---|---|
| **C3-A0** | `Ran 35 tests … OK` | —— | ✓ |
| **C3-A1** | `Ran 48 tests … OK` | **48/48** | ✓ |
| **C3-A2** | `Ran 42 tests … OK` | **42/42** | ✓ |
| **C2-A2** | `Ran 46 tests … OK` | **46/46** | ✓ |

### 5.1 Gate 汇总（口径与各阶段报告一致）

```
C3-A2：cases = 23
       physical  response **逐字节相等**        20 / 20
       functional Gateway 恒空（C2-OD-4）        2 / 2
       state    matches                        22 / 22
       mismatches                               0

C2-A2：cases = 20 ／ legacy 20 ／ current 20
       response matches 20/20 ／ state matches 20/20
       legacy anchor 20/20 ／ current anchor 20/20
       mismatches 0
```

### 5.2 C3-A0 Golden digest（**未变**）

```
HEAD blob  = d211f53adfa74e59ae54f3609c83f86665b23efb
LF SHA256  = f93b42f695d7ea2c91c730404eb02a0d7d27f4e65f09e4889d71cd02a4f8846c
             （与 A0 测试中硬编码的冻结值一致 ⇒ pin 仍绿）
```

## 6. INVARIANT CHECK

相对基线 `121b6f4`（内容级，Windows 侧 `core.autocrlf=true`）：

| 路径 | 结果 |
|---|---|
| `labs/vehicle/uds-basic/ecu/` | **UNCHANGED** |
| `labs/vehicle/uds-basic/tests/golden/` | **UNCHANGED** |
| `labs/vehicle/uds-basic/tests/legacy/` | **UNCHANGED** |
| `labs/vehicle/uds-basic/tests/c2_differential_lib.py` | **UNCHANGED** |
| `.github/workflows/` | **UNCHANGED** |
| `docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md` | **UNCHANGED** |
| `docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md` | **UNCHANGED** |

### 6.1 关键 blob（`git rev-parse HEAD:<path>`，无歧义）

```
C2 golden             541395b82e74ce915830fea23369e21c65fe7b27
C3-A0 golden          d211f53adfa74e59ae54f3609c83f86665b23efb
legacy frozen impl    e64cd088fc86bf9e6b1c5dfb2bb1b80cae5ab39a
ecu/gateway.py        06455bf630257ab255079765af90c22d26f4c2c8
```

### 6.2 C2-A2 shared dependency pin —— **未被 A3 改动**

```
ecu.py              0817e17be858ce7eec132733418b72199de10e50   （= C3-A1 rebaseline 值）
gateway_runtime.py  0c12cb7bc06771daa72732d5422c94c05cb3fe79   （同上）
uds_server.py       94c74e126d4114587f8a7888845ef1111990074f   （同上）
```

### 6.3 测量口径说明（**方法学警示，非缺陷**）

```
同一 CRLF 工作区文件：
    Windows 侧 `git hash-object`（core.autocrlf=true ⇒ 做 LF 归一）→ 等于 HEAD blob
    WSL 侧     `git hash-object`（未设 autocrlf ⇒ 按原始字节哈希）  → **不同的 blob**

本次审计中曾因此得到 C3-A0 golden 的 "c4135b6d…"，经核对为**测量误差**：
committed blob 实为 d211f53a…，与 A0 报告一致。
⇒ 做 blob 证据应使用 `git rev-parse HEAD:<path>`（已提交值，无歧义）。
```

## 7. WARN CLASSIFICATION

| 类别 | 数量 | 明细 |
|---|---|---|
| **已知 WARN（环境级，历史同类）** | **2** | ① healthcheck `Git working tree has uncommitted changes` —— WSL 侧行尾假阳性（历史 Issue #28 同源）：工作区内容与 HEAD 逐字节一致，差异只来自 WSL 的 `core.autocrlf` 未设 ② healthcheck `docker installed but engine not reachable` —— Docker Desktop 未运行 |
| **新 WARN** | **0** | —— |

> **两个 WARN 都出现在 healthcheck 内部，且都不是 C3 引入的**：
> 工作树 WARN 在 C2-A2/A3 阶段即有记录；docker WARN 在 C3-A0/A1/A2 阶段即有记录。
> **未把 WARN 当作 PASS**，也未把环境 WARN 写成 C3 defect。

## 8. BLOCKER / DEFECT

```
BLOCKER = 0
```

### 8.1 记录项：DOC-DISC-1（文档计数不一致，**非阻塞**）

```
现象
    docs/OQ_C3_A2_DIRECT_GATEWAY_DIFFERENTIAL_2026-09-26.md:288
    commit c6adc2d 的 message
        "run_tests.sh unit = PASS 25 / WARN 0 / FAIL 0（**1177 cases**）"

实测（可复现）
    bash labs/vehicle/uds-basic/tests/run_tests.sh unit
        → 25 个测试文件，逐文件 "Ran N tests" 求和 = **1200**

推导核对
    C2-A4 时 1075
    C3-A0 +33                       → 1108
    C3-A1 +48（新增）+2（A0 迁移）   → 1158
    C3-A2 +42                       → **1200**   ✓
```

**定性**：这是 **A2 阶段的文档计数错误**，不是 A3 的发现，也不影响任何 gate 判据
（A2 的判据是 `PASS 25 / FAIL 0`，二者均成立）。

**A3 处置**：按本阶段规则（"不修复顺手发现的非 A3 问题"）**只记录、不修改**。
是否修正 A2 报告请由后续阶段或人工决定。

### 8.2 记录项：`[FAIL]` 字样出现在日志中但**不是失败**

```
`bash .../run_tests.sh all` 与 `qa_fast.sh` 的日志中会出现：

    [FAIL] 检测到 C3 接线标记已存在于 gateway.py：

这来自 **C3-A0 的捕获护栏**（`capture_c3_a0_baseline.py` 在 C3 接线后
**按设计拒绝写入**并把该提示打印到 stdout），由 A0 测试用例
`test_capture_script_refuses_to_regenerate_the_baseline` 断言其返回 2。

**它不是测试失败** —— 同一日志的 Summary 均为 `FAIL: 0`。
本报告显式标注，避免被后续读者误读为回归失败。
```

## 9. CONCLUSION

| # | 判据 | 结果 |
|---|---|---|
| 1 | 基线正确（HEAD = 121b6f4、working tree clean、ecu/ 无意外变化） | ✓ |
| 2 | UDS full regression `FAIL = 0` | ✓ 1435 cases，skip 0 |
| 3 | CAN full regression `FAIL = 0` | ✓ 358 cases，skip 0 |
| 4 | `qa_fast` PASS | ✓ exit 0 |
| 5 | `qa_extended` 无未解释 FAIL | ✓ exit 0，1793 cases |
| 6 | healthcheck（含 --extended）无新 FAIL | ✓ FAIL 0；WARN 均为已知环境级 |
| 7 | repro 全部实际执行项无 FAIL | ✓ 6/6 exit 0，FAIL 0 |
| 8 | C3-A0 / A1 / A2 / C2-A2 专项全过 | ✓ 35 / 48 / 42 / 46 |
| 9 | production / Golden / legacy / pin / 设计 / CI 不变 | ✓ 全部 UNCHANGED |
| 10 | BLOCKER = 0 | ✓（1 项文档计数记录，非阻塞） |

```
C3-A3 = PASS
Full regression = PASS
```

### 9.1 本阶段**不**得出的结论

```
A3 PASS ≠ C3 EXIT PASS

A3 只能回答"C3-A1/A2 合入后现有能力是否仍完整回归通过"。
C3 是否最终 EXIT 由 **C3-A5 Exit Review** 判定 —— 本阶段不预先占用该结论。
```

## 10. 本阶段产物

```
新增（唯一）  docs/OQ_C3_A3_FULL_REGRESSION_2026-09-26.md

production diff = 0 ／ Golden diff = 0 ／ legacy diff = 0
C2-A2 shared pin diff = 0 ／ C2·C3 设计文档 diff = 0 ／ CI diff = 0
旧测试语义 diff = 0
```

## 11. Next Stage

```
C3-A3（本文档）  ← PASS
      ↓
C3-A4  Override / bypass audit（含 C3-A1 新增的 provider seam）
      ↓
C3-A5  Exit Review
```

**A3 完成后不自动进入 A4。**
