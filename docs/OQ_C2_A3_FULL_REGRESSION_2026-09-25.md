# OQ C2-A3 —— Full Regression（证据报告）

> 状态：**C2-A3 = PASS**
> 日期：2026-09-25
> 分支：`audit/oq-c2-a3-full-regression-20260925`
> 基线：`main = 9909288dcc3f5cbae87f2bb62c402177ba29a993`（PR #46 merge commit）
> 设计出处：`docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md` §13

**本阶段只回答一个问题**：

> **C2-A0 + A1 + A2 合入 main 后，现有 SecurityLab 能力是否仍然完整回归通过？**

**不做**：policy deny 新规则、bypass/override 审计、handler 绕过测试、transport bypass、
custom topology bypass、GatewayRuntime bypass、新 policy fixtures / behavior
—— 这些属 **C2-A4**。本阶段 **production / Golden / tests / CI 改动均为 0**。

---

## 1. Baseline

| 项 | 值 |
|---|---|
| `main` SHA（同步后真实远程） | `9909288dcc3f5cbae87f2bb62c402177ba29a993` |
| A2 merge commit | `9909288`（Merge PR #46），parents = `15288ae` + `0cfb560` |
| A2 内容 commit | `0cfb5601783a4a5a3a9f7c130a39dd64c1601935` |
| A3 分支 | `audit/oq-c2-a3-full-regression-20260925`（从 `9909288` 建立） |
| working tree（开始 / 结束） | clean |

### 1.1 基线同步（**未使用陈旧的本地 main ref**）

```bash
git fetch <https> +refs/heads/main:refs/remotes/origin/main   # 走单次命令级 proxy + gh token
git switch main
git reset --hard origin/main
```

同步前本地 `main` = `1b43b12`（陈旧，不含 A1/A2）；同步后 = `9909288`。
远程 `origin` 为 SSH 且本机私钥不可用，推送/拉取统一走
`git -c http.proxy=… -c credential.helper=…` 的**单次命令级**方案：
**未修改全局 git config、未修改系统代理、未读取或输出任何 token**。

### 1.2 A2 内容完整性（进入回归前先证明基线正确）

```
git diff --name-status 15288ae 9909288
    A docs/OQ_C2_A2_LEGACY_CURRENT_DIFFERENTIAL_2026-09-25.md
    A labs/vehicle/uds-basic/tests/c2_differential_lib.py
    A labs/vehicle/uds-basic/tests/legacy/c2_a0_gateway_legacy_impl.py
    M labs/vehicle/uds-basic/tests/run_tests.sh
    A labs/vehicle/uds-basic/tests/test_c2_a2_legacy_current_differential.py

git diff 15288ae 9909288 -- labs/vehicle/uds-basic/ecu/gateway.py   → 空
git diff 15288ae 9909288 -- labs/vehicle/uds-basic/tests/golden/    → 空
git diff 0cfb560 9909288                                            → 空（A2 原样进入 main）
```

| 对象 | blob | 钉住值 | 结论 |
|---|---|---|---|
| `tests/golden/c2_gateway_legacy.json` | `541395b82e74ce915830fea23369e21c65fe7b27` | `541395b8…` | MATCH |
| `tests/legacy/c2_a0_gateway_legacy_impl.py` | `e64cd088fc86bf9e6b1c5dfb2bb1b80cae5ab39a` | `e64cd088…`（= `4059cf5` blob） | MATCH |
| `ecu/gateway.py` @`15288ae` vs @`9909288` | `fb5090165c94c3e8900acdcd89979212b17afbde` | 同值 | A2 未改 production |

---

## 2. Test Matrix（完整回归项）

环境：WSL Ubuntu，venv Python 3.12.3（`~/.venvs/network/bin/python`），`vcan0` UP 且独占。

| # | Gate | Command | Result | Count | Evidence |
|---|---|---|---|---|---|
| 1 | C2-A0 | `python3 labs/vehicle/uds-basic/tests/test_c2_a0_gateway_golden.py` | **PASS** | 28 / 28 | `Ran 28 tests … OK` |
| 2 | C2-A1 | `…/test_c2_a1_empty_policy_trace.py` | **PASS** | 42 / 42 | `Ran 42 tests … OK` |
| 3 | C2-A2 | `…/test_c2_a2_legacy_current_differential.py` | **PASS** | 46 / 46 | `Ran 46 tests … OK`，skip 0 |
| 4 | UDS unit | `bash labs/vehicle/uds-basic/tests/run_tests.sh all` | **PASS** | 1017（21 文件） | 见 §3 |
| 5 | UDS e2e | 同上 | **PASS** | 234（11 文件） | 见 §3 |
| 6 | CAN offline | `bash labs/vehicle/can-tools/tests/run_tests.sh all` | **PASS** | 303（4 文件） | 见 §4 |
| 7 | CAN e2e/vcan | 同上 | **PASS** | 55（2 文件） | 见 §4 |
| 8 | qa_fast | `bash scripts/qa_fast.sh` | **PASS** | — | 见 §5 |
| 9 | qa_extended | `bash scripts/qa_extended.sh` | **PASS** | 1609 cases | 见 §6 |
| 10 | healthcheck | `bash scripts/healthcheck.sh` | **PASS**（75/2/0） | 77 项 | 见 §7 |
| 11 | healthcheck --extended | `bash scripts/healthcheck.sh --extended` | **PASS**（79/1/0） | 80 项 | 见 §7 |
| 12 | ARM | `bash scripts/repro/verify_arm.sh` | **PASS** | 6 / 6 | 见 §8 |
| 13 | QEMU-GDB | `bash scripts/repro/verify_qemu_gdb.sh` | **PASS** | 4 / 4 | 见 §8 |
| 14 | Ghidra | `bash scripts/repro/verify_ghidra.sh` | **PASS** | 8 / 8 | 见 §8 |
| 15 | fuzz smoke | `bash scripts/repro/verify_fuzz_smoke.sh` | **PASS** | 8 / 8 | 见 §8 |
| 16 | UDS fuzz smoke | `bash scripts/repro/verify_uds_fuzz_smoke.sh` | **PASS** | 10 / 10 | 见 §8 |
| 17 | Gateway Runtime | `bash scripts/repro/verify_gateway_runtime.sh` | **PASS** | 7 / 7 | 见 §8 |

> 全部 CAN / vcan0 相关项**串行**执行（vcan0 独占），无并发。

---

## 3. C2 Regression

```text
C2-A0 = 28 / 28 PASS   （legacy gateway golden corpus 结构测试）
C2-A1 = 42 / 42 PASS   （空策略 + per-call PolicyTrace 机制）
C2-A2 = 46 / 46 PASS   （legacy ↔ current 差分对拍）
```

A2 的 gate 汇总（合入 main 后复跑，数字与 A2 阶段报告一致）：

```
cases                = 20
legacy executions    = 20
current executions   = 20
response matches     = 20 / 20      ← legacy-live vs current-live
state    matches     = 20 / 20
legacy  anchor       = 20 / 20
current anchor       = 20 / 20
mismatches           = 0
```

---

## 4. UDS / CAN Full Regression

### 4.1 UDS Basic（`run_tests.sh all`）

```
PASS: 33   WARN: 0   FAIL: 0      （exit 0）
unit total : 1017 cases / 21 文件
e2e  total :  234 cases / 11 文件
合计       : 1251 cases
SKIP       : 0（无 skipped 记录）
```

unit 21 文件含 C2 三项（A0/A1/A2）与 C1 全部专项；e2e 11 文件全部在 vcan0 上真实运行
（`vcan0 可用` 为前置检查项，非测试文件）。

**口径对照**：A1 阶段 unit = 971；A2 新增 46 → **1017** ✓；e2e 234 自 C1 起未变 ✓。

### 4.2 CAN tools（`run_tests.sh all`）

```
PASS: 7    WARN: 0   FAIL: 0      （exit 0）
offline : 303 cases / 4 文件（parser 34 + ISO-TP option 50 + fuzz harness 99 + UDS fuzz offline 120）
vcan e2e:  55 cases / 2 文件（malformed ISO-TP 47 + UDS fuzz vcan smoke 8）
合计    : 358 cases
SKIP    : 0
```

---

## 5. Fast QA

```bash
bash scripts/qa_fast.sh     # exit 0
```

| 组件 | 结果 |
|---|---|
| `ruff` | **OK** |
| `mypy` | **OK** |
| UDS offline | **PASS 21 / FAIL 0** |
| CAN-tools offline | **PASS 4 / FAIL 0** |
| **overall** | **QA FAST RESULT: PASS** |

---

## 6. Extended QA

```bash
bash scripts/qa_extended.sh     # exit 0
```

| 步骤 | 结果 |
|---|---|
| [1/4] UDS 全套（unit + vcan0 e2e） | **OK**（PASS 33 / WARN 0 / FAIL 0） |
| [2/4] CAN tools 全套（离线 + 畸形 ISO-TP） | **OK**（PASS 7 / WARN 0 / FAIL 0） |
| [3/4] capture → save → replay 闭环（log/BLF/PCAP） | **OK** |
| [4/4] YARA 真阳性 / 真阴性演练 | **OK** |
| **overall** | **QA EXTENDED RESULT: PASS** |

```
内层合计 cases = 1609（1251 UDS + 358 CAN）
真实 skip      = 0
WARN           = 0
```

> `--fuzz`（长时 CAN fuzz smoke 30）**未执行** —— 属可选长测，本次未纳入。

---

## 7. Healthcheck

### 7.1 快速（`bash scripts/healthcheck.sh`，exit 0）

```
PASS: 75   WARN: 2   FAIL: 0
```

| WARN | 分类 | 说明 |
|---|---|---|
| `Git working tree has uncommitted changes (by design during lab work)` | **历史 WARN（环境级）** | 见 §7.3：Windows/WSL 行尾差异导致的**假阳性**，零内容差异 |
| `docker installed but engine not reachable (start Docker Desktop…)` | **历史 WARN（环境级）** | 历史记录中已有 "Docker（不通记 WARN）" 同类；Docker Desktop 未运行 |

### 7.2 扩展（`bash scripts/healthcheck.sh --extended`，exit 0）

```
PASS: 79   WARN: 1   FAIL: 0
```

| WARN | 分类 |
|---|---|
| `docker installed but engine not reachable` | 历史 WARN（环境级） |

> 扩展跑中 Git 段显示 `Git working tree clean`（与快速跑不一致）—— 这是
> **git 索引 stat cache 的瞬时效应**（见 §7.3），非环境改善；随后从 WSL 复测
> 又回到 49 个"modified"。**两次读数都不表示内容变化。**

### 7.3 行尾（EOL）环境问题 —— 与历史 Issue #28 同源

**现象**：`git reset --hard` 在 Windows 侧按 `core.autocrlf=true` 重写工作区，
使 `labs/vehicle/uds-basic/tests/run_tests.sh` 与
`scripts/repro/verify_gateway_runtime.sh` 变为 CRLF，Linux bash 报
`$'\r': command not found`，脚本**无法执行**。

**诊断（关键：这不是内容问题）**：

```
git ls-files --eol  →  索引侧全部 i/lf（184 文件，提交内容无任何 CRLF）
Windows 侧 core.autocrlf = true      WSL 侧 core.autocrlf = 未设置
Windows: git status --porcelain → 0
WSL    : git status --porcelain → 49（全部是 CRLF 文本文件，0 个 .sh）
WSL    : git -c core.autocrlf=true diff --name-only HEAD → 0   ← 规范化后零内容差异
git diff --cached → 0（索引 == HEAD）
```

**处置**（与本项目历史处置一致）：仅对这 2 个脚本用**单次命令级**
`git -c core.autocrlf=false checkout -- <paths>` 重新物化，随后
`git hash-object` 与 index blob **完全一致**（`a0934a13…` / `d2a7b06b…`），
`git status` clean、`git diff` 0 行。

> **未修改任何提交内容、未修改任何配置、未新增 `.gitattributes`。**
> 历史依据：`docs/OQ_C1_PHASE2_FINAL_EXIT_REVIEW_2026-09-25.md:223` **Issue #28**
> —— "Windows/WSL `*.sh` CRLF 检出；表现为 healthcheck 的 `Git working tree has
> uncommitted changes` WARN｜环境级；测量期间以工作区规范化规避，**未修改仓库内容**，
> **未加 `.gitattributes`**"。
>
> **结论：本次 WARN 与历史同类，不是 A3 新增 WARN。**

**未被写进 PASS 的项**：healthcheck 的 `tool_present` 系列是**工具存在性**检查
（"工具存在"），与 §8 repro 的**功能闭环**（真实编译→执行→断点命中→反汇编产出）
是两类证据，报告中不混同。

---

## 8. 可复现手工能力（repro entrypoints）

全部**串行**执行，退出码语义：`0`=全过 / `1`=验证失败 / `2`=依赖缺失。

| 脚本 | 退出码 | 结果 | 关键证据（功能闭环，非工具存在） |
|---|---|---|---|
| `verify_arm.sh` | 0 | **PASS 6 / FAIL 0** | AArch64 静态编译 + **实际执行**（`ARM_RUNTIME_OK 42`）；armhf 编译 + qemu-arm 执行 |
| `verify_qemu_gdb.sh` | 0 | **PASS 4 / FAIL 0** | qemu-aarch64 `-g` 挂起 → gdb-multiarch remote **命中 main 断点** → continue 正常退出 |
| `verify_ghidra.sh` | 0 | **PASS 8 / FAIL 0** | analyzeHeadless 导入+分析（rc=0）→ `Architecture=AARCH64` → `ERROR_COUNT=0` → **main 反汇编 12 条** |
| `verify_fuzz_smoke.sh` | 0 | **PASS 8 / FAIL 0** | 3 case 生成 + oracle 三态（AVAILABLE 3）+ `replay --all` issues=0 |
| `verify_uds_fuzz_smoke.sh` | 0 | **PASS 10 / FAIL 0** | 离线契约检查（v1 golden **947** + 边界 7/8/128 接受、129/200/300 拒绝）+ 3 case vcan campaign（1 SF / 1 MF / 1 malformed，含 MF basic 真打 ECU）+ replay issues=0 |
| `verify_gateway_runtime.sh` | 0 | **PASS 7 / FAIL 0** | 守护进程启动就绪 → 端到端检查全过 → SIGTERM 退出码 0 → 无残余进程 |

**NOT RUN / SKIP / BLOCKED**：无。6 个脚本**全部实际执行**并全部 `exit 0`。

---

## 9. CI

```text
A3 did not modify CI.
CI coverage remains the existing Fast runner scope.
Extended/manual capabilities remain separate.
```

* `.github/workflows/ci.yml` 在 `15288ae..9909288` 与 A3 分支上均 **UNCHANGED**。
* CI 实际覆盖：`fast` job（`bash scripts/qa_fast.sh`），在 push 与 pull_request 上运行；
  `extended` job 仅 `workflow_dispatch` 手动触发。
* 本报告中 §6 / §7 / §8 的 extended / healthcheck / repro 能力**不属 CI 覆盖范围**，
  由本地 WSL 环境实测，**不声称 CI 跑过**。

---

## 10. Scope

```text
production changes      = 0   （ecu/** 相对 15288ae 与 0cfb560 均 UNCHANGED）
Golden changes          = 0   （tests/golden/ 未变；corpus blob 541395b8… 未变）
test semantic changes   = 0   （A0/A1/A2 测试与 c2_differential_lib 相对 0cfb560 UNCHANGED）
CI changes              = 0   （.github/workflows/ 未变）
```

```
git status --short   → 空
git diff --check     → 空（无空白错误）
git diff --stat      → 空
```

A3 本阶段唯一新增物：**本报告文件**。

---

## 11. WARN / SKIP / BLOCKED / FAIL 汇总

| 类别 | 数量 | 明细 |
|---|---|---|
| **FAIL** | **0** | —— |
| **新 WARN** | **0** | —— |
| 历史 WARN（环境级） | 2 | (1) git working tree（EOL 假阳性，Issue #28 同类）；(2) docker engine not reachable |
| SKIP | 0 | 所有测试文件均实际执行；无 skipped |
| BLOCKED | 0 | —— |
| NOT RUN | 1（显式可选） | `qa_extended.sh --fuzz`（长时 fuzz smoke，可选，本次未纳入） |

---

## 12. 通过条件逐项

| 条件 | 结果 |
|---|---|
| C2-A0 PASS | ✓ 28/28 |
| C2-A1 PASS | ✓ 42/42 |
| C2-A2 PASS | ✓ 46/46（mismatches 0） |
| UDS full regression `FAIL = 0` | ✓ 1251 cases / FAIL 0 |
| CAN full regression `FAIL = 0` | ✓ 358 cases / FAIL 0 |
| `qa_fast` PASS | ✓ |
| `qa_extended` 无未解释 FAIL | ✓ 0 FAIL |
| `healthcheck` 无新 FAIL | ✓ FAIL 0；WARN 均为历史环境级 |
| repro 全部实际执行项无 FAIL | ✓ 6/6 脚本 exit 0，FAIL 0 |

```
C2-A3 = PASS
```

---

## 13. 下一步

```
C2-A4 = NOT STARTED   （override / bypass 不变量审计，§12）
C2-A5 = NOT STARTED   （exit review）
C3    = NOT STARTED
```

**A3 完成后不自动进入 A4。**
