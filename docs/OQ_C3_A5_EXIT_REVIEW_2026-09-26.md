# OQ C3 —— 0x27 SecurityAccess through Gateway（A5 退出评审）

> 状态：**C3-A5 = PASS** ／ **C3 EXIT = PASS**
> 日期：2026-09-26
> 基线：`main = b3e62836a3e73ad63c7f31f3139a26b1855f788a`（= C3-A4 Re-Audit merge commit，PR #57）
> 阶段性质：**Exit Review / Evidence Closure —— 只审查、核对、收口**

**本阶段只做：冻结项逐项核验 + 证据链闭环 + 退出判定 + 本报告。**
**不实现功能、不修 bug、不改 production、不改 Golden、不改任何历史阶段报告、不关闭 PR #55。**

---

## 0. A5 的边界（先声明）

```
做了：
    · 逐条核验 C3 Design Freeze（C3-OD-1…15 / C3-OQ-1…13 / C3-B1）
    · 建立 A0 → A1 → A2 → A3 → A4 → Remediation → Re-Audit 证据闭环表
    · 独立重算 Golden / pin / blob 摘要（不采信文档自述）
    · 运行一组 **A5 consistency check**（见 §12.3），标签明确、不冒充新证据
    · 产出本报告

没做：
    · 不重新跑 A3（不制造第二份 full regression）
    · 不修改任何历史阶段报告（含原 A4 BLOCKED 报告）
    · 不修改 production / Golden / C2-A2 / CI workflow / Design 文档
    · 不解决任何 deferred OQ
    · 不 merge / 不关闭 PR #55
```

**历史证据只读原则**：本报告**不回填**、**不改写**任何历史阶段的状态。
原 A4 在其报告中**永远**是 `BLOCKED`；Re-Audit 是**另一份**报告中的 `PASS`。

---

## 1. FINAL BASELINE

| 项 | 值 |
|---|---|
| `main` HEAD | `b3e62836a3e73ad63c7f31f3139a26b1855f788a` |
| `origin/main` | `b3e62836a3e73ad63c7f31f3139a26b1855f788a` |
| HEAD == origin/main | ✓ |
| working tree | **clean**（`git status --short` 空输出） |
| main post-merge CI | **SecurityLab CI · push · main · completed · success**（run `36236216269`） |
| 该 CI 的 job | `Fast QA (ruff + mypy + offline tests)` = completed/success；`Extended security tests (vcan0, manual)` = completed/**skipped**（手动触发型，非失败） |
| 本阶段新增 | 仅本文件 `docs/OQ_C3_A5_EXIT_REVIEW_2026-09-26.md` |

```
git log -8 --oneline --decorate
b3e6283 (HEAD -> main, origin/main, origin/HEAD) Merge pull request #57 from Li20771/audit/oq-c3-a4-r1-override-bypass-20260926
250475a (audit/oq-c3-a4-r1-override-bypass-20260926) test: re-audit C3 override and bypass boundaries
5559db0 Merge pull request #56 from Li20771/fix/c3-defect-1-handler-provider-precedence-20260926
02cf405 (fix/c3-defect-1-handler-provider-precedence-20260926) fix: preserve custom handlers with gateway provider
5ebfaf3 Merge pull request #54 from Li20771/audit/oq-c3-a3-full-regression-20260926
d066d3e (audit/oq-c3-a3-full-regression-20260926) docs: add C3-A3 full regression evidence
121b6f4 Merge pull request #53 from Li20771/audit/oq-c3-security-access-gateway-a2-differential-20260926
d1c50f8 (audit/oq-c3-security-access-gateway-a2-differential-20260926) docs(test): tighten C3-A2 evidence wording
```

---

## 2. EVIDENCE CHAIN（A0 → A4 阶段闭环表）

| Stage | Purpose | Evidence | Result | merge commit |
|---|---|---|---|---|
| **C3-Design** | architecture freeze | `OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md` | **PASS** | `4cb85274b35ab52fa7df06a346f7afe495ab6714`（PR #50） |
| **C3-A0** | pre-implementation baseline | C3-A0 golden（29 cases）+ 结构/replay 测试 | **PASS** | `a387084656e423eb2501625257a8c81e7952d559`（PR #51） |
| **C3-A1** | implementation（0x27 wiring） | 48 例专项 + 前置等价性（不一致 0/0） | **PASS**（含 1 项待裁决冲突 X1，见 §4.4） | `2733de7f51ceced41b87ad560faa04de2c4b987d`（PR #52） |
| **C3-A2** | Direct ↔ Gateway differential | 23 cases 四维差分 | **PASS** | `121b6f4675dfec20193e8c78d575c3011fd8fd39`（PR #53） |
| **C3-A3** | full regression | UDS/CAN/QA/healthcheck/repro 全量 | **PASS** | `5ebfaf349b42663a0a8df49fac93777d12fd9e9b`（PR #54） |
| **C3-A4（原）** | override / bypass audit | 4 seam + provider seam | **BLOCKED**（BLOCKER = 1，DEFECT-1） | **未合并** —— PR #55 `OPEN`，head `d0eb6720a2ebe0d34b7e25d3c8cf4802505b2994` |
| **DEFECT-1 Remediation** | production fix | `02cf405c73a03f545a0e259e55382ce1fb075f3c` | **PASS** | `5559db0326b58d60bfbaba9cf9d6351f185bf0d4`（PR #56） |
| **C3-A4 Re-Audit** | closure | 46 例专项 + 完整矩阵复验 | **PASS** | `b3e62836a3e73ad63c7f31f3139a26b1855f788a`（PR #57） |
| **C3-A5** | Exit Review | 本文件 | **PASS** | 本 PR |

**历史结构不得压缩**：

```
C3-A4 原 = BLOCKED  ──►  DEFECT-1  ──►  Remediation（PR #56）  ──►  A4 Re-Audit = PASS（PR #57）
```

⇒ **不得**被改写为 `A4 = PASS`。原 A4 的 `BLOCKED` 是本次审计发现的一部分，
它触发了一次真实的 production 修复 —— 这是 C3 的重要产出，不是污点。

---

## 3. C3 DESIGN FREEZE CLOSURE

来源：`docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md`
冻结修正 commit：`4616170ed51e6f5dbd83162534aee62bfa851fd6`（**docs-only**）
归属核实：该 commit 经 `--ancestry-path` 确认最早进入 **PR #50（C3 Design）** ⇒ 冻结内容在 Design 阶段内完成，**未被后续阶段回填**。

### 3.1 Open Question 分类核验（C3-OQ-1 … C3-OQ-13）

| OQ | 主题 | 冻结分类 | A5 核验 |
|---|---|---|---|
| C3-OQ-1 | functional addressing | **DECIDED**（ALLOW + KNOWN LIMITATION） | ✓ 实现与冻结一致（§3.2 第 12 项） |
| C3-OQ-2 | runtime dependency 单一 source of truth | **DECIDED** | ✓ clock = `ecu._now()`；config = `ecu.SECURITY_CONFIG` 原对象引用 |
| C3-OQ-3 | 是否移动 / 提取 SECURITY_CONFIG | **DECIDED：不移动** | ✓ `SECURITY_CONFIG` 仍在 `ecu.py` 模块级 |
| C3-OQ-4 | 0x27 是否进入 SERVICE_SPECS | **DECIDED：不进入** | ✓ 见 §3.2 第 4 项 |
| C3-OQ-5 | C2 policy 输入是否保持完全不变 | **DECIDED：完全不变** | ✓ `PolicyDecision` 仍仅 4 字段（target/mode/sid/allowed） |
| C3-OQ-6 | reset / session transition 复用 | **DECIDED** | ✓ 无新增 reset 分支 |
| C3-OQ-7 | 多 ECU security state per ECU | **DECIDED：是** | ✓ A2 state 22/22；每 ECU 独立 `EcuState` |
| C3-OQ-8 | 是否需要新的 override audit | **DECIDED：需要** | ✓ C3-A4 已执行（含 provider seam） |
| C3-OQ-9 | Direct ↔ Gateway 等价性证明方式 | **DECIDED**（双路径差分） | ✓ A2 以 23 cases 执行 |
| C3-OQ-10 | functional 0x27 空转副作用专门抑制 | **DEFERRED** | ✓ 仍 deferred（§6） |
| C3-OQ-11 | per-ECU SECURITY_CONFIG | **DEFERRED** | ✓ 仍 deferred（§6） |
| C3-OQ-12 | provider 返回值可否覆盖 0x27 以外服务 | **RESOLVED**（允许 + 10 条限制） | ✓ 10 条限制逐条见 §3.3 |
| C3-OQ-13 | 独立 runtime dependency carrier 层 | **DEFERRED** | ✓ 仍 deferred（§6） |

```
BLOCKING = 0        ✓
DECIDED  = 9        （OQ-1 … OQ-9）
RESOLVED = 1        （OQ-12）
DEFERRED = 3        （OQ-10 / OQ-11 / OQ-13）
合计     = 13       ✓ 与 OQ 编号 1…13 完全对齐
```

**C3-B1 = RESOLVED**（跨阶段契约交接，非设计缺口）
⇒ `test_gateway_runtime_vcan.py::test_security_access_is_not_reachable_through_the_gateway_layer`
属 C2 历史边界契约，C3 有意改变它；更新动作由 C3-OD-15 授权并限定为 C3-A1 范围。
核验：该 pin 已在 C3-A1 更新为"Gateway 0x27 正常可达"契约 ✓（A1 专项 48/48 覆盖）

**BLOCKING = 0 已成立** ⇒ §20 第 1 项满足。

### 3.2 Architecture Freeze 逐项核验（12 项，**A5 独立复算**）

| # | 冻结项 | 核验方法与结果 | 判定 |
|---|---|---|---|
| 1 | 0x27 状态机唯一来源 `uds_server.service_security_access()` | 全仓 grep：定义**仅** `uds_server.py:589` 一处；调用点**仅** `uds_server.py:2114`（`_make_security_access_handler` 内）。`gateway.py` / `gateway_runtime.py` 中**无**第二份 0x27 分支逻辑 | ✓ |
| 2 | dependency binding = `uds_server.security_access_services()` | 定义**仅** `uds_server.py:2124` 一处；调用方 `ecu.py:369`、`gateway_runtime.py:508`。两条路径共用同一定义 | ✓ |
| 3 | keygen = `generate_seed` / `derive_key` / `verify_key` | `keygen.py:108 / 152 / 189`（另有 `seed_is_valid:136`）；未见第二份种子/密钥派生 | ✓ |
| 4 | 0x27 不进入静态 `SERVICES` / `SERVICE_SPECS` | 运行时实测：`SERVICES` = **14 项**，`0x27 in SERVICES = False`；`SERVICE_SPECS` = 14 项，同样不含 0x27；`SPECS_EXCLUDED[0x27]` 存在；模块级守卫 `if 0x27 not in SPECS_EXCLUDED: raise ValueError`（`uds_server.py:2388`）；`_SPEC_EXECUTION = {0x22, 0x2E}` | ✓ |
| 5 | Gateway provider **每请求**动态获取 | `gateway.py:192-200`：`services={**uds_server.SERVICES, **services_provider()}` 位于 `handle()` 函数体内 ⇒ 每次 `process()` 现场调用，无缓存 | ✓ |
| 6 | provider 只做 dependency binding | `ServicesProvider` 契约注释（`gateway.py:65-76`）+ C3-A4 显式审计（限制 1） | ✓ |
| 7 | C2 policy 仍只看 `(target ECU, addressing_mode, SID)` | `PolicyDecision` 字段恰为 `target / addressing_mode / sid / allowed`；`GatewayPolicy = Callable[[VirtualEcu, str, int], bool]` | ✓ |
| 8 | policy 在 `process()` **之前** | `gateway.py:406-411`：`if not self._allow(...): return []` 先于 `response = target.process(payload)` | ✓ |
| 9 | custom handler 不得被 gateway-level provider 静默覆盖 | `gateway.py:300-310`：`if ecu.services_provider is not None or ecu.has_custom_handler: continue`（**C3-DEFECT-1 修复**，身份判定 `handler is not default_handler`） | ✓ |
| 10 | default handler 可以被 provider 重新绑定 | 同一代码块：非 custom 时执行 `ecu.handler = _default_handler_for(...)` 并同步 `ecu.default_handler` | ✓ |
| 11 | per-ECU security state 严格隔离 | 每 `VirtualEcu` 持有独立 `EcuState`；A2 逐 ECU state 22/22；A4 逐个 ECU 归因 ✓ | ✓ |
| 12 | functional 0x27 不增加特殊机制，保留已知空转副作用 | `gateway.py` 中**无**任何 0x27 / SID 特例分支；`_route_core` 仅区分 functional 地址与 request_id；A2 functional Gateway 恒空 2/2 且逐 ECU 状态等价 | ✓ |

**与 freeze 不一致的事实 = 0。**

### 3.3 C3-OQ-12 冻结的 10 条限制（C3-OD-14）核验

| # | 限制 | 核验 |
|---|---|---|
| 1 | provider 仅负责 runtime dependency binding | ✓（provider 只返回 `{sid: handler}`，不接触 policy / `EcuState`） |
| 2 | 不得改变 C1 ServiceSpec / guard 语义 | ✓（A4 Re-Audit 第 5 项：C1 guard 未被 same-SID override 绕过） |
| 3 | 不得改变 C2 policy 维度 | ✓（`PolicyDecision` 字段未增） |
| 4 | C2 policy 仍严格为 `(target, addressing_mode, SID)` | ✓ |
| 5 | 0x27 状态机仍只有 `uds_server.service_security_access()` | ✓（§3.2 第 1 项） |
| 6 | keygen 仍只有 3 个函数 | ✓（§3.2 第 3 项） |
| 7 | `now` 必须每次请求重新求值 | ✓（§3.2 第 5 项） |
| 8 | 不得缓存旧 `now` | ✓（无缓存，无 `functools.lru_cache` / 闭包快照） |
| 9 | `SECURITY_CONFIG` 保持对象引用语义 | ✓（`ecu.py:371` 传 `security_config=SECURITY_CONFIG`，无 copy） |
| 10 | C3-A4 必须显式审计 provider override / same-SID override | ✓（C3-A4 Re-Audit 第 4/5 项） |

---

## 4. 各阶段 CLOSURE

### 4.1 C3-A0 CLOSURE

```
C3-A0 = 35 / 35 PASS      ✓ A5 consistency check 实测（见 §12.3）
C3-A0 Golden 未被后续修改  ✓
```

**Golden 完整性（A5 独立重算，不采信文档）**：

| 项 | 文档记录值 | A5 实测值 | 一致 |
|---|---|---|---|
| 路径 | `labs/vehicle/uds-basic/tests/golden/c3_security_access_baseline.json` | 同 | ✓ |
| git blob | `d211f53adfa74e59ae54f3609c83f86665b23efb` | `d211f53adfa74e59ae54f3609c83f86665b23efb` | ✓ |
| LF SHA-256 | `f93b42f695d7ea2c91c730404eb02a0d7d27f4e65f09e4889d71cd02a4f8846c` | `f93b42f695d7ea2c91c730404eb02a0d7d27f4e65f09e4889d71cd02a4f8846c` | ✓ |
| LF 行数 | `2850` | `2850` | ✓ |
| `captured_at_commit` | `4cb85274b35ab52fa7df06a346f7afe495ab6714` | 同（C3 Design merge） | ✓ |
| cases | `29`（direct 15 / gateway 14） | 结构测试通过 | ✓ |

**未被后续修改的证据（git 层面）**：
```
git log 8ed53fa…HEAD -- <c3 golden>
⇒ 仅 c817fda "test(c3): capture security access pre-implementation baseline"
⇒ 该文件自捕获后**零次修改**
```

**口径说明（避免误读）**：
A0 报告 §8 记录的是 **A0 阶段当时**的运行结果 `Ran 33 tests … OK`。
A1 阶段把该测试文件按迁移边界**扩充 2 例**（`777b756` 修改 `test_c3_a0_baseline.py`），
故后续阶段一致引用 `35 / 35`。**Golden 本身未变**，变的是其结构/replay 测试覆盖。
两者不矛盾。

### 4.2 C3-A1 CLOSURE

```
C3-A1 = 48 / 48 PASS      ✓ A5 consistency check 实测
```

**X1 rebaseline 核验（三个 shared dependency pin）**：

| 文件 | 文档记录（A3 §6.2） | A5 实测（`git rev-parse HEAD:<path>`） | 一致 |
|---|---|---|---|
| `ecu.py` | `0817e17be858ce7eec132733418b72199de10e50` | `0817e17be858ce7eec132733418b72199de10e50` | ✓ |
| `gateway_runtime.py` | `0c12cb7bc06771daa72732d5422c94c05cb3fe79` | `0c12cb7bc06771daa72732d5422c94c05cb3fe79` | ✓ |
| `uds_server.py` | `94c74e126d4114587f8a7888845ef1111990074f` | `94c74e126d4114587f8a7888845ef1111990074f` | ✓ |

`tests/c2_differential_lib.py:156-170` 的 `CONTROLLED_SHARED_DEPENDENCIES` 表中，
三条目**已带上 `# C3-A1 rebaseline（原 <旧 blob>）` 注释**并指向上述新值；
其余 9 个条目仍指向 4059cf5 时代的 blob。
⇒ **未重新 rebaseline**：本阶段未再修改任何 pin。✓

**X1 的处置链（事实记录）**：

```
A1 报告写作时（commit 777b756 加入 A1 文档）
    → 报告 §8 记录 fail-closed 触发，并列出 X1-a / X1-b / X1-c 三个选项
    → 报告 §8.4 明写"我未采取任何一项 —— 等你裁决"
    → 报告 §11 row 12 标记为"✗ 未授权事项（§8）"

用户裁决 = X1-a（授权在 C3-A1 内更新 pin，并把重新评估理由写入 A2 文档补充说明）

commit 39e976f "test(c2): rebaseline shared dependencies for C3-A1"
    → 经 --ancestry-path 确认属 **PR #52（C3-A1）**
    → 只改 pin 表 + 在 C2-A2 报告追加 Addendum
    → 未改 C2-A2 测试、未改 golden、未改 legacy frozen copy、未删除 fail-closed 检查
```

⇒ **X1 已按 X1-a 闭环**，且 A1 分支最终以绿色 CI 合并。

**A5 观察（非阻塞，见 §13 DOC-DISC-3）**：A1 报告是**裁决前快照**，其 §11 row 12
仍显示"未授权事项"。该文档加入后**从未被修改**（`git log` 显示仅 A，无 M）。
按 A5 原则**不修改历史报告**。

### 4.3 C3-A2 CLOSURE

```
C3-A2 = 42 / 42 PASS      ✓ A5 consistency check 实测
cases  = 23
```

**三项等效性口径必须分类陈述（不得合并）**：

| 维度 | 判据 | 值 |
|---|---|---|
| physical | response **逐字节**等价 | **20 / 20** |
| functional | Gateway response **恒空**（C2-OD-4 冻结语义） | **2 / 2** |
| state | 逐 ECU state 等价（3 常规字段 + 5 个 0x27 字段） | **22 / 22** |
| — | **mismatches** | **0** |

```
policy_deny cases = 1
```

⇒ **禁止**写成 `response equivalence = 22/22`。A2 文档 §5.1 已显式把该写法列为**禁止项**
（"因此**不应**把两种语义笼统写成'response 22/22 等价'"），A5 核验该禁止性表述**仍然存在** ✓
（`docs/OQ_C3_A2_DIRECT_GATEWAY_DIFFERENTIAL_2026-09-26.md:156 / :169`）

**文档改动归因**：C3-A2 文档在 C3 期间被修改**一次**（`d1c50f8 "docs(test): tighten C3-A2 evidence wording"`），
经 `--ancestry-path` 确认属 **PR #53（C3-A2 自身阶段）**，属该阶段内的口径收紧，
**未被 A3/A4/A5 回填** ✓

### 4.4 C3-A3 CLOSURE

```
A3 = PASS      （来源：docs/OQ_C3_A3_FULL_REGRESSION_2026-09-26.md，A5 未重跑 A3）
```

历史实测（A3 报告 §4 RESULT TABLE 原始记录）：

```
A. UDS all        PASS 37 / WARN 0 / FAIL 0     1435 cases     exit 0
B. CAN all        PASS  7 / WARN 0 / FAIL 0      358 cases     exit 0
C. qa_fast        PASS（ruff OK / mypy OK / uds unit PASS 25 FAIL 0 / can-tools offline PASS 4 FAIL 0）  exit 0
D. qa_extended    PASS（4/4 步骤全 OK）          1793 cases     exit 0
E. healthcheck    PASS 75 / WARN 2 / FAIL 0       77 项        exit 0
E′. --extended    PASS 78 / WARN 2 / FAIL 0       80 项        exit 0
F. repro          6 个 verify_*.sh 全部 exit 0，FAIL 0         exit 0 ×6
G. C3 回归        35 + 48 + 42 + 46 = 171，全部 OK             exit 0 ×4

C3-A0 = PASS / C3-A1 = PASS / C3-A2 = PASS / C2-A2 = PASS（46/46）
new WARN = 0
new FAIL = 0
BLOCKER  = 0
```

**已知环境 WARN（保持原分类，**不重新包装成 C3 defect**）**：

```
① healthcheck "Git working tree has uncommitted changes"
   —— WSL 侧行尾假阳性（历史 Issue #28 同源）；工作区内容与 HEAD 逐字节一致，
      差异只来自 WSL 的 core.autocrlf 未设
② healthcheck "docker installed but engine not reachable"
   —— Docker Desktop 未运行

两个 WARN 均在 healthcheck 内部，且 C2-A2/A3 或 C3-A0/A1/A2 阶段**即有记录**，
非 C3 引入。**未把 WARN 当作 PASS**。
```

⇒ **A3 未产生新的 unresolved blocker。** ✓

---

## 5. A4 BLOCKER HISTORY（原文保留，不改写）

**来源**：`docs/OQ_C3_A4_OVERRIDE_BYPASS_AUDIT_2026-09-26.md`
**位置**：**不在 main 上** —— 该文件仅存在于 PR #55 分支
（`audit/oq-c3-a4-override-bypass-20260926`，head `d0eb6720a2ebe0d34b7e25d3c8cf4802505b2994`，PR #55 状态 `OPEN`）
**blob**：`3d64f5fccdc9afaf131a1f59584e853d6a9ea936`

```
状态：**C3-A4 = BLOCKED**
原因：**未发现任何 policy / C1 guard bypass**，但在审计 `services_provider` 与既有
      `handler` seam 的交互时发现 **1 个真实生产缺陷**；按 A4 规则**不在本阶段修 production**。
```

原 A4 结论表（11 项）：

```
 1  C2-A4 四层证据矩阵复验通过（4 seam）                    ✓
 2  新增 services_provider seam 的四层证据                  ✓
 3  DENY 时 provider == 0（physical / functional 全 deny）   ✓
 4  DENY 时 process()==0 / state unchanged / wire [] / decision False  ✓
 5  provider 不改变 policy 求值次数与顺序（1-1-1 / 1-0-0 / 3-3-3 / 3-0-0）  ✓
 6  same-SID override 不能扩大授权边界                       ✓
 7  C1 guard 未被 same-SID override 绕过                     ✓
 8  build_runtime 接线 provider 未制造第二条 bypass path      ✓
 9  源码级不变量保持（含 process() 调用点恰 2 处）             ✓
10  未发现任何 policy / C1 guard bypass                      ✓
11  services_provider 与 handler seam 的交互                  ✗ **DEFECT-1（BLOCKER = 1）**
```

### 5.1 DEFECT-1 的性质（**关键：不是 policy bypass**）

```
标题：网关级 `services_provider` 静默覆盖 ECU 自带的 custom handler

**授权影响：无**（不是 bypass；policy 与 C1 guard 均仍生效）
**性质**：**覆盖（override）语义回归** —— 破坏 C2-A4 已审计的 seam-1
          （"handler 注入可替换实现"）

触发形态：
  A  VirtualEcu(handler=custom, services_provider=prov)                       → 按设计工作 ✓
  B  VirtualEcu(handler=custom) + DiagnosticGateway(..., services_provider=prov) → **被静默覆盖** ✗
  C  VirtualEcu(默认) + DiagnosticGateway(..., services_provider=prov)          → 走默认 handler ✓

根因（gateway.py::DiagnosticGateway.__init__，C3-A1 新增）：
    if ecu.handler is not None:          ← 无法区分
        ecu.handler = _default_handler_for(...)
      "未接线的默认 handler"          → 应当重绑
      "用户显式注入的 custom handler"  → **不得覆盖**

影响面：生产路径（build_runtime → build_default_gateway(provider)）= 形态 C ⇒ **不受影响**
        ⇒ 该缺陷此前**潜伏**，由原 A4 **首次记录**。
```

### 5.2 原 A4 的处置合规性

```
1  停止修复                 ✓ 未修改任何 production 文件
2  记录 reproduction        ✓ 报告 + 失败用例
3  报告标记 BLOCKER/DEFECT  ✓ BLOCKER = 1
4  不在 A4 分支修生产代码    ✓
```

⇒ 原 A4 的 `BLOCKED` 是**规则驱动**的正确结论，不是失败。**本报告不改写它。**

---

## 6. DEFECT-1 REMEDIATION

| 项 | 值 |
|---|---|
| fix commit | `02cf405c73a03f545a0e259e55382ce1fb075f3c` "fix: preserve custom handlers with gateway provider" |
| 变更文件 | `labs/vehicle/uds-basic/ecu/gateway.py`（+38 行）、`labs/vehicle/uds-basic/tests/test_gateway_unit.py`（+182 行） |
| merge commit | `5559db0326b58d60bfbaba9cf9d6351f185bf0d4`（**PR #56**） |
| merge parents | `5ebfaf3`（原 A4 基线 / C3-A3 merge）+ `02cf405`（fix） |
| 修复后 main CI | success（run `36234296601`，35s） |

**修复机制（A5 在 HEAD 上复核源码）**：

```python
# gateway.py:223-250
default_handler: UdsHandler | None = field(default=None, repr=False, compare=False)
# 刻意用**身份**而非 boolean：boolean 会在 ecu.handler = … 事后赋值后过期

@property
def has_custom_handler(self) -> bool:
    return self.handler is not self.default_handler

# gateway.py:300-310
if services_provider is not None:
    for ecu in self.ecus:
        if ecu.services_provider is not None or ecu.has_custom_handler:
            continue
        ecu.services_provider = services_provider
        ecu.handler = _default_handler_for(ecu.state, services_provider)
        ecu.default_handler = ecu.handler
```

**Re-Audit 未再修改 production**（A5 独立核验）：
```
git log --oneline 02cf405..HEAD -- labs/vehicle/uds-basic/ecu/
⇒ 空输出

git rev-parse 02cf405:labs/vehicle/uds-basic/ecu/gateway.py = 34444bcc63f68d8fa0763dc4caa46be026a8c52c
git rev-parse HEAD:labs/vehicle/uds-basic/ecu/gateway.py    = 34444bcc63f68d8fa0763dc4caa46be026a8c52c
⇒ blob 逐字节一致 ✓
```

---

## 7. C3-A4 RE-AUDIT CLOSURE

**来源**：`docs/OQ_C3_A4_OVERRIDE_BYPASS_REAUDIT_2026-09-26.md`（在 main 上）
**归因**：`git diff --stat 5559db0 b3e62836` 显示 PR #57 只触及 3 个文件
（Re-Audit 报告 + `run_tests.sh` +8 行 + `test_c3_a4_override_bypass.py` 新增 1017 行）
⇒ **production / Golden 零改动** ✓

### 7.1 计数核验

| 项 | 报告值 | A5 实测 | 一致 |
|---|---|---|---|
| C3-A4 Re-Audit 专项 | 46 / 46 | **Ran 46 … OK** | ✓ |
| C2-A4 | 58 / 58 | **Ran 58 … OK** | ✓ |
| C3-A0 | 35 / 35 | **Ran 35 … OK** | ✓ |
| C3-A1 | 48 / 48 | **Ran 48 … OK** | ✓ |
| C3-A2 | 42 / 42 | **Ran 42 … OK** | ✓ |
| C2-A2 | 46 / 46 | **Ran 46 … OK** | ✓ |
| qa_fast | PASS | main post-merge CI `Fast QA` = **success** | ✓ |
| new WARN | 0 | — | ✓ |
| FAIL | 0 | 全部 exit 0 | ✓ |

测试用例数独立复核：`test_c3_a4_override_bypass.py` 中 `def test_` = **46** ✓

### 7.2 DEFECT-1 修复的五种场景（报告 §3.2 / §5）

```
A  构造期同时给出     VirtualEcu(handler=custom, services_provider=prov)          → 保留 ✓
B  网关级 provider    VirtualEcu(handler=custom) + gw(services_provider=prov)     → 保留 ✓（custom 被调用 1 次）
C  默认 handler       VirtualEcu(默认) + gw(services_provider=prov)               → 正确重绑 ✓（0x27 → 67 01）
D  构造后重赋（新增） ecu.handler = custom → gw(services_provider=prov)            → 保留 ✓（custom 被调用 1 次）

优先级（报告 §5）：
    framework default handler   → provider **可以**重绑
    custom handler（构造期）     → **不替换**
    custom handler（构造后重赋） → **不替换**

custom + provider + deny → policy 仍阻断：
    decision False ／ process 0 ／ custom 0 次 ／ provider 0 次 ／ state unchanged ／ wire []
    （C3-A4 Re-Audit 用例 test_deny_blocks_the_custom_handler 在 main 上存在并通过）
```

### 7.3 授权边界 10 项（A5 逐项对照 Re-Audit 报告 §15）

| # | 用户要求的核验项 | Re-Audit 报告对应 | 判定 |
|---|---|---|---|
| 1 | C2-A4 四个旧 seam 未被破坏 | §15 row 6（C2 四 seam 全部复验）+ C2-A4 58/58 | ✓ |
| 2 | provider physical allow / deny 正确 | §15 row 2 | ✓ |
| 3 | provider functional allow / deny 正确 | §15 row 3 | ✓ |
| 4 | functional mixed 按 ECU 归因正确 | §15 row 3 | ✓ |
| 5 | same-SID 0x27 override 不得扩大 policy | §15 row 4 | ✓ |
| 6 | C1 guard 不得被 same-SID override 绕过 | §15 row 5 | ✓ |
| 7 | `build_runtime` 不得制造 bypass | §15 row 7 | ✓ |
| 8 | policy order / count 正确 | §15 row 8 | ✓ |
| 9 | AST source invariants 正确 | §15 row 9 + **A5 独立复算**（见下） | ✓ |
| 10 | negative controls 有效 | §15 row 10 | ✓ |

**计数（三数字依次为 `policy / provider / process`）**：

```
physical allow   = 1-1-1      ✓
physical deny    = 1-0-0      ✓
functional allow = 3-3-3      ✓
functional deny  = 3-0-0      ✓
```

顺序证明：**provider 只在 `process()` 之内被调用，而 `process()` 只在 policy 允许之后被调用**
⇒ `policy → provider → process` 不可能反向。

**AST 不变量（A5 独立复算，非采信报告）**：

```
python -c "ast 解析 gateway.py，统计 Call(func=Attribute(attr='process'))"
⇒ AST .process( call sites = 2 -> lines [411, 396]
   receivers = ['ecu', 'target']
```
⇒ 与 C2-A4 的 F1 不变量（恰好 2 处，且均受 `_allow` 守卫）**一致** ✓

### 7.4 Re-Audit 的 diff 声明（A5 复核）

```
production modifications = 0     ✓ 见 §6（gateway.py blob 一致）
Golden diff              = 0     ✓ 见 §8.1
Design diff              = 0     ✓ 见 §3（Design 文档最后改动为 4616170，属 PR #50）
C2-A2 diff               = 0     ✓ 见 §8.2（测试 / golden / legacy 未动）
C3-A0/A1/A2 diff         = 0     ✓ 既有测试语义未改
CI workflow diff         = 0     ✓ 见 §8.3
原 A4 报告 diff           = 0     ✓ 见 §5（文件不在 main，分支 head 未变）
```

---

## 8. DEFERRED OQ STATUS

**A5 不允许擅自解决 deferred OQ。** 核验结论：**三项仍全部为 deferred，且未被误标 completed。**

| OQ | 主题 | 状态 | A5 核验证据 |
|---|---|---|---|
| **C3-OQ-10** | functional 0x27 空转副作用的专门抑制机制 | **DEFERRED** | `gateway.py` 中**无**任何 SID 特例分支；`_route_core` 只区分 functional 地址与 `request_id`。设计 §10.3 的 KNOWN LIMITATION 原样保留（功能寻址 0x27 恒空响应、逐 ECU seed 状态被推进） |
| **C3-OQ-11** | per-ECU `SECURITY_CONFIG` | **DEFERRED** | `ecu.py:305` 仍是模块级**单一** `SECURITY_CONFIG` dict；无 per-ECU 配置结构 |
| **C3-OQ-13** | 独立 runtime dependency carrier 层（候选 D） | **DEFERRED** | C3 期间 `labs/vehicle/uds-basic/ecu/` **未新增任何文件**（`git log --diff-filter=A` 空输出）；无 carrier 模块 |

⇒ 三项均**不构成阻塞**，且**没有为了"看起来完整"而引入代码**。✓

---

## 9. SECURITY / SCOPE BOUNDARY

**C3 从未违反以下边界**（核验方式：源码关键字扫描 + 运行时接口默认值 + CI 配置）：

```
✓ 不连接真实 CAN
✓ 不操作真实 ECU
✓ 不发送真实诊断 / 控制 / 刷写报文
✓ 只使用 vcan0 + virtual ECU
```

`gateway_runtime.py` 证据：

```
DEFAULT_INTERFACE = "vcan0"
接口白名单（只允许 vcan0）由**命令行入口** main() 强制（fail-closed）
```

**C3 没有引入**（扫描结果均为空）：

```
✗ 真实 vehicle interface
✗ 新的 CAN interface
✗ 真实 firmware flashing
✗ DoIP
✗ multi-process ECU orchestration
```

---

## 10. PRODUCTION / GOLDEN / CI INTEGRITY

### 10.1 相对最后一次生产修复 merge 后基线的逐项核对

| 对象 | 结论 | 证据 |
|---|---|---|
| `ecu/`（production） | **C3 期间仅 2 次改动，均已授权并合并** | `777b756`（A1 实现，PR #52）、`02cf405`（DEFECT-1 修复，PR #56）；此后**零改动** |
| `tests/golden/` | **未改** | `c3_security_access_baseline.json` blob+LF SHA 与记录值一致（§4.1）；`c2_gateway_legacy.json` blob `541395b8…` 与 A3 记录一致 |
| `tests/legacy/` | **未改** | `c2_a0_gateway_legacy_impl.py` blob `e64cd088…` 与 A3 记录一致 |
| C2 differential | **测试/golden/legacy 未改**；仅 pin 表按裁决 X1-a 受控迁移 | `git log 8ed53fa..HEAD` 对前两者为空；`c2_differential_lib.py` 仅 `39e976f`，±33/-4 行 |
| C3 A0/A1/A2 测试 | **语义未改** | A0 测试仅 A1 迁移时 +2 例（已记录）；A2 测试无后续改动 |
| CI workflow | **未改** | `git log 8ed53fa..HEAD -- .github/` 空输出 |
| Design docs | **未回填** | Design 文档最后改动 `4616170`，属 PR #50；C3-A2 文档最后改动 `d1c50f8`，属 PR #53 |

### 10.2 A4 Re-Audit 自身的声明仍然成立

```
production modifications = 0     ✓（gateway.py blob 在 02cf405 与 HEAD 之间逐字节一致）
```

### 10.3 A5 consistency check（**不是**新的 A3 / A4 证据）

按本阶段规则，**没有重跑 A3**，只运行了一组**只读一致性检查**，
用于把"文档自述"替换为"本阶段实测"。**标签明确：以下为 A5 consistency check，不冒充新证据。**

```
环境：WSL2 / ~/.venvs/network/bin/python (3.12.3) / /mnt/d/SecurityLab @ b3e62836
命令：python <test>.py（逐个，离线，无需 vcan0）

test_c3_a0_baseline.py                     Ran 35 tests … OK    exit 0
test_c3_a1_gateway_security_access.py      Ran 48 tests … OK    exit 0
test_c3_a2_direct_gateway_differential.py  Ran 42 tests … OK    exit 0
test_c3_a4_override_bypass.py              Ran 46 tests … OK    exit 0
test_c2_a4_override_bypass.py              Ran 58 tests … OK    exit 0
test_c2_a2_legacy_current_differential.py  Ran 46 tests … OK    exit 0

合计 275 例，FAIL 0，skipped 0
```

补充只读核验（均不修改仓库）：

```
· golden / legacy blob 与 LF SHA-256 重算            → 与记录值逐字节一致
· 三个 shared dependency pin（git rev-parse）        → 与 A1 rebaseline 值一致
· gateway.py AST：.process( 调用点 = 2（行 411/396）  → C2-A4 F1 不变量成立
· 静态注册表运行时实测：SERVICES=14（无 0x27）        → C3-OD-6 成立
· SPECS_EXCLUDED / _SPEC_EXECUTION 实测              → 与冻结一致
```

**未做的事**：未重跑 A3 全量（不制造第二份 full regression）；
未把上述结果标为 A3/A4 证据。

### 10.4 CI 最终状态

```
PR #57 checks      : Fast QA = pass（32s / 36s 两次）；Extended（vcan0, manual）= skipping
main post-merge CI : SecurityLab CI · push · main · completed · success（run 36236216269）
                     ├─ Fast QA (ruff + mypy + offline tests)       completed / success
                     └─ Extended security tests (vcan0, manual)     completed / skipped
```

---

## 11. GIT HISTORY INTEGRITY

主线结构（A5 核验，**未被伪造**）：

```
4cb8527  Merge PR #50   C3-Design
   ↓
a387084  Merge PR #51   C3-A0
   ↓
2733de7  Merge PR #52   C3-A1（含 777b756 实现 + 39e976f X1-a rebaseline）
   ↓
121b6f4  Merge PR #53   C3-A2（含 d1c50f8 口径收紧）
   ↓
5ebfaf3  Merge PR #54   C3-A3
   ↓
d0eb672  ✗ 未合并 —— PR #55 **C3-A4 = BLOCKED**（历史证据，保持 OPEN）
   ↓
02cf405 → 5559db0  Merge PR #56   DEFECT-1 Remediation
   ↓
250475a → b3e62836 Merge PR #57   C3-A4 Re-Audit = PASS
   ↓
        b3e62836 = 当前 main（= 本阶段基线）
```

**核验点**：

```
✓ PR #55 仍为 OPEN；其分支 head 仍为 d0eb6720a2ebe0d34b7e25d3c8cf4802505b2994（未变）
✓ 原 A4 报告 blob 仍为 3d64f5fccdc9afaf131a1f59584e853d6a9ea936（未被改写）
✓ 原 A4 报告**不在 main 上**（只在 PR #55 分支）—— 这是历史事实，不是缺失
✓ A4 的历史状态**未被**回填为 PASS
✓ 无 force-push / 无历史重写（合并均为 merge commit）
```

### 11.1 PR #55 的处置

```
状态：**OPEN（保持）**
性质：**historical audit / superseded by remediation + re-audit**
处置：A5 **不 merge**、**不关闭**、**不删除历史来源**

（是否关闭 PR #55 属**独立的 GitHub housekeeping**，
  与 C3 Exit evidence **无关**，不得与退出判定混为一谈。）
```

---

## 12. UNRESOLVED BLOCKERS

```
未解决的 production defect      = 0
未解决的 policy bypass          = 0
未解决的 C1 guard bypass        = 0
未解决的 Design BLOCKING        = 0

BLOCKER = 0
```

### 12.1 记录项（非阻塞，**本阶段不修改历史报告**）

| ID | 内容 | 影响退出判定？ | 处置 |
|---|---|---|---|
| **DOC-DISC-1** | A3 已记录：C3-A2 报告 §11 记 `run_tests.sh unit = 1177 cases`，A3 实测 `1200` | **否** —— 纯 doc 计数不一致，不影响任何 PASS 判定 | 沿用 A3 处置：**只记录、不修改** |
| **DOC-DISC-2**（A5 新增） | Design 文档 §附表末行 `DECIDED（OQ）= 8`，但同一行列举 **9** 个 OQ ID（OQ-1…OQ-9）；按正文逐条统计应为 **9** | **否** —— 分项合计 9+1+3 = 13 与 OQ 编号 1…13 完全对齐；BLOCKING=0 / RESOLVED=2 / DEFERRED=3 三项汇总**均正确** | **只记录、不修改**（属计数笔误，非结论错误） |
| **DOC-DISC-3**（A5 新增） | A1 报告是 **X1 裁决前快照**：§4 称"存在 1 项未授权事项，导致 CI 变红"、§11 row 12 标 `✗ 未授权事项`。事实上 X1 已按 **X1-a** 在同一 PR #52 内闭环（`39e976f`），且 A1 文档加入后从未修改 | **否** —— 实际结果（pin 已受控迁移、CI 绿）与 A3 §6.2 记录一致；A5 已独立重算三个 pin 与文档值一致 | **只记录、不修改**（保留历史快照原貌） |

**处置原则**：以上三项均为**文档层面的表述/计数偏差**，
不改变任何阶段的实测结论、不影响任何冻结项、不构成安全或授权风险。
按 A5 规则（"不为修历史报告而修改历史证据"）**一律只记录、不修改**。

### 12.2 Deferred 项（非阻塞，不擅自解决）

```
C3-OQ-10 / C3-OQ-11 / C3-OQ-13  =  仍 DEFERRED（见 §8）
```

---

## 13. FINAL EXIT CRITERIA

逐项判定（**不使用整体印象**，每项附证据出处）：

| # | 条件 | 判定 | 证据 |
|---|---|---|---|
| 1 | Design BLOCKING = 0 | ✓ | §3.1；`4616170`（PR #50） |
| 2 | A0 PASS | ✓ | §4.1；A5 实测 35/35；golden blob+LF SHA 一致 |
| 3 | A1 PASS | ✓ | §4.2；A5 实测 48/48；三 pin 与 rebaseline 值一致 |
| 4 | A2 PASS | ✓ | §4.3；A5 实测 42/42；23 cases；口径分类保持 |
| 5 | A3 PASS | ✓ | §4.4；A3 全量记录；BLOCKER = 0 |
| 6 | 原 A4 blocker 已通过 remediation 关闭 | ✓ | §5 / §6；`02cf405` → PR #56 |
| 7 | A4 Re-Audit PASS | ✓ | §7；A5 实测 46/46 |
| 8 | 无 unresolved production defect | ✓ | §12；BLOCKER = 0 |
| 9 | 无 policy bypass | ✓ | §7.3；C2-A4 58/58；Re-Audit 第 10 项 |
| 10 | 无 C1 guard bypass | ✓ | §7.3 第 6 项 |
| 11 | Golden 未被偷偷改写 | ✓ | §4.1 / §10.1；重算一致，且 golden 自捕获后零次修改 |
| 12 | Design 未被偷偷改写 | ✓ | §3；最后改动 `4616170` 属 PR #50 |
| 13 | C2-A2 未被偷偷修改 | ✓ | §10.1；测试/golden/legacy 未动，pin 按 X1-a 受控迁移 |
| 14 | C3 deferred OQ 未被误标 completed | ✓ | §8；三项仍 deferred，无对应代码 |
| 15 | 当前 main clean | ✓ | §1；`git status --short` 空 |
| 16 | main / origin/main 一致 | ✓ | §1；均为 `b3e62836…` |
| 17 | post-merge CI SUCCESS | ✓ | §1 / §10.4；run `36236216269` success |

```
17 / 17 条件成立
BLOCKING = 0
未满足项 = 0
```

---

## 14. FINAL EXIT DECISION

```
C3-A5  = PASS
C3 EXIT = PASS
C3      = COMPLETE / EXITED
```

**依据**：

```
1  C3 Design Freeze 完成（C3-OD-1…15 / C3-OQ-1…13 / C3-B1 全部核验一致，BLOCKING = 0）
2  A0 → A1 → A2 → A3 → A4 → Remediation → Re-Audit 证据链完整闭合
3  12 项架构冻结项逐项与源码一致，不一致事实 = 0
4  原 A4 的 BLOCKED 经 DEFECT-1 修复（PR #56）与 Re-Audit（PR #57）真实关闭
5  10 项授权边界全部成立；policy / C1 guard bypass = 0
6  Golden / Design / C2-A2 / CI 完整性全部保持
7  deferred OQ 保持 deferred，未被误标
8  main clean 且与 origin/main 一致，post-merge CI SUCCESS
9  未解决 blocker = 0
```

### 14.1 阶段边界（**不得混淆**）

```
C3-A4 原（PR #55）      = **BLOCKED**          ← 历史事实，永久保留，不改写
DEFECT-1                = **FOUND → FIXED**    ← PR #56
C3-A4 Re-Audit（PR #57） = **PASS**             ← 独立报告独立结论
C3-A5（本文件）          = **Exit Review**
C3 EXIT                 = **PASS**             ← 仅在 A5 全部条件满足后成立
```

### 14.2 本阶段之后

```
不自动启动下一轮 OQ。
C3 到此 COMPLETE / EXITED。
后续动议（如需）须作为**独立阶段**另行立项：
    · C3-OQ-10（functional 0x27 空转副作用抑制机制）
    · C3-OQ-11（per-ECU SECURITY_CONFIG）
    · C3-OQ-13（独立 runtime dependency carrier 层）
    · PR #55 的 GitHub housekeeping（与 Exit evidence 无关）
```

---

## 附：本阶段产物与合规声明

```
新增：
    docs/OQ_C3_A5_EXIT_REVIEW_2026-09-26.md（本文件）

未修改：
    ✗ production（ecu/**）
    ✗ Golden（tests/golden/**）
    ✗ legacy（tests/legacy/**）
    ✗ 任何既有测试（tests/**）
    ✗ 任何历史阶段报告（含原 A4 BLOCKED 报告）
    ✗ CI workflow（.github/**）
    ✗ Design 文档

未执行：
    ✗ merge / close PR #55
    ✗ 重新 rebaseline
    ✗ 重跑 A3 全量并冒充新证据
    ✗ 解决任何 deferred OQ
```

**安全边界**：本阶段全部操作均为本地只读分析 + 文档撰写，
使用 vcan0 / virtual ECU / 离线测试，**未接触真实 CAN、真实 ECU 或真实车辆**。
