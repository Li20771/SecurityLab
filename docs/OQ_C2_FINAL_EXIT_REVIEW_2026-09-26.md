# OQ C2 —— Final Exit Review（C2-A5）

> 状态：**C2-A5 = PASS** ／ **C2_EXIT = PASS**
> 日期：2026-09-26
> 分支：`audit/oq-c2-a5-exit-review-20260926`
> 基线 / `main`：`8120b551d6c6318b09b0b4dc4db8a403d507f8b3`（= C2-A4 merge commit，PR #48）
> 设计出处：`docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md`

**本阶段只做退出审查与收口，不实现任何新功能。**
**不回写任何历史阶段报告** —— 历史报告中的 "NOT STARTED" 是其撰写时的事实陈述，
保持不变；C2 的最终状态以本文件为准。

**A5 回答的问题**：

> C2 Gateway Policy 从设计冻结到 A0/A1/A2/A3/A4 是否形成**完整、可审计、
> 无 blocking gap** 的迁移闭环？

---

## 1. Baseline

| 项 | 值 |
|---|---|
| `main` SHA | `8120b551d6c6318b09b0b4dc4db8a403d507f8b3` |
| 含义 | Merge PR #48 = **C2-A4 merge commit**；parents = `b24b816`（A3 merge）+ `db74f35`（A4 head） |
| A5 分支 | `audit/oq-c2-a5-exit-review-20260926`（从 `8120b55` 建立） |
| working tree（开始 / 本次审计后） | clean（`git status --short` 空） |
| 远程 `origin/main` | `8120b55…`（与本地一致） |

---

## 2. Stage Matrix（A0 … A4）

| 阶段 | 分支 commit | PR | merge commit | 结果 | 关键证据 |
|---|---|---|---|---|---|
| **设计** | `5d71bab` 等 | **#43** | `83d1f52` | **MERGED** | C2-OD-1…OD-9 + dimensions + 边界 全部 **FROZEN**（§16.1） |
| **C2-A0** | `4059cf5` | **#44** | `1b43b12` | **MERGED** | legacy golden corpus 20 条；捕获护栏 fail-closed |
| **C2-A1** | `38c65fd` + `720e3fe` | **#45** | `15288ae` | **MERGED** | 空策略 + per-call `PolicyTrace` 机制 |
| **C2-A2** | `0cfb560` | **#46** | `9909288` | **MERGED** | Legacy↔Current 差分对拍 |
| **C2-A3** | `10ba17b` | **#47** | `b24b816` | **MERGED** | Full regression 记录 |
| **C2-A4** | `a628aff` + `db74f35` | **#48** | `8120b55` | **MERGED** | 4 个 override seam 的 bypass 审计 |

**迁移链完整性（实测）**：上述 6 个 merge commit 与 8 个阶段 commit
**全部是 `main` 的祖先**（`git merge-base --is-ancestor` 逐一验证，无 MISSING）。

---

## 3. Evidence Chain：Design → Implementation → Differential → Regression → Bypass → Exit

```
Design        §16.1 冻结 12 项（OD-1…OD-9 + dimensions + C1/C2 边界 + 迁移策略 + C3 边界）
   │
   ▼
A0 Capture    在 **policy 机制尚不存在** 时捕获 legacy 行为 → 冻结 oracle
   │          （护栏：捕获脚本 fail-closed；corpus 头 policy_execution = "absent"）
   ▼
A1 Mechanism  引入**空策略 + per-call PolicyTrace**，并证明 default-allow 下
   │          wire / state **逐字节不变**（Golden 20/20 + 20/20）
   ▼
A2 Differen-  独立 Legacy 冻结实现 ↔ Current 生产实现的**真实执行差分**
   │  tial     （20 冻结输入；response 20/20、state 20/20、mismatches 0）
   ▼
A3 Regres-    full regression：UDS / CAN / QA / healthcheck / repro
   │  sion     证明 C2 合入后既有能力完整
   ▼
A4 Bypass     4 个 override seam 的 deny 不变量审计
   │          （四层证据 + functional 逐 ECU + 结构性不变量）
   ▼
A5 Exit       本文件：冻结项逐项核验 + 全链 scope 审计 + exit SHA 上重跑回归
```

每一环的证据都由**独立机制**给出，互不替代：

| 环 | 证据类型 | 不能被什么替代 |
|---|---|---|
| A1 | `Current ↔ 冻结 Golden` | 不能证明两条实现一致（无 Legacy 执行） |
| A2 | `Legacy-live ↔ Current-live` | 不能证明既有能力未回归（范围只到 gateway） |
| A3 | full regression | 不能证明策略不可被绕过 |
| A4 | 结构性不变量 + 四层 deny 证据 | 不能证明默认路径行为未变 |

---

## 4. Frozen Decisions Checklist（§16.1 逐项核验）

| ID | 冻结内容 | 实现证据（实测） | 结果 |
|---|---|---|---|
| **C2-OD-1** | 策略在 `route()` 内求值；位置 = 目标解析后、`process()` 前 | `gateway.py::_route_core` 内两处 `process()` 调用均受 `_allow()` 守卫（A4 F1/F1b AST 断言） | ✓ |
| **C2-OD-2** | default-allow；空策略 ≡ 当前行为 | `DEFAULT_POLICY = allow_all_policy`；A1 42/42；A2 20/20 + 20/20 | ✓ |
| **C2-OD-3** | 拒绝 = 静默丢弃 | A1 `TestDenySemantics`；A4 四层证据（trace/process/state/wire） | ✓ |
| **C2-OD-4** | 功能寻址逐 ECU 判定 | `gateway.py:303-305` 循环内逐目标 `_allow`；A4 B3/B4（process `[1,0,1]` / `[1,0,0]`） | ✓ |
| **C2-OD-5** | 判定单位 = `(target ECU, addressing_mode, SID)` | `GatewayPolicy = Callable[[VirtualEcu, str, int], bool]`；`self.policy(ecu, addressing_mode, sid)` | ✓ |
| **C2-OD-6** | override 不能绕过策略 | A4 58/58，覆盖 4 个 seam | ✓ |
| **C2-OD-7** | 用 SID 表达服务；不得为 0x27 预置分支 | `gateway.py` 中 0x27 / SecurityAccess **仅出现在注释与文档字符串**，无代码分支 | ✓ |
| **C2-OD-8** | 未知 target 与空 payload 不进入 policy；不判断 SID 是否实现 | `_allow()` 的 `sid is None` 提前返回；A1 + A4 显式测试（`trace.decisions == ()`） | ✓ |
| **C2-OD-9** | 决策仅通过显式 per-call trace 可见；`route()` 契约不变 | `route_with_trace()` 与 `route()` 共用 `_route_core`（A4 E2 core spy：`[False]` / `[False,True]`）；`route()` 仍返回 `list` | ✓ |
| **Dimensions** | `target` / `addressing_mode` / `SID`（不含 payload 内容、session、source） | 上表 OD-5 证据；A1 `test_policy_is_never_asked_about_ecu_internal_state` | ✓ |
| **C1 / C2 边界** | 需要读 `EcuState` 内部字段的规则属 C1 | 策略签名不含 `EcuState`（仅 `VirtualEcu` 作为 target 身份）；C1 面 12 模块全 UNCHANGED | ✓ |
| **迁移策略** | 沿用 C1 六段式 | A0→A1→A2→A3→A4→A5 一一对应，本表即为该链 | ✓ |
| **C3 边界** | C2 不碰 0x27 / SecurityAccess / keygen / `_now()` / C1 任何面 | `keygen.py`、`uds_server.py`、`service_spec.py`、`ecu.py` 等 12 个模块全 UNCHANGED | ✓ |

**12 / 12 FROZEN 项均有实现证据。**

---

## 5. Open Questions Checklist（§15）

### 5.1 RESOLVED

| # | 状态 | 验证 |
|---|---|---|
| **C2-OQ-5**（决策可观测性） | **RESOLVED — 由 C2-OD-9 冻结** | `route_with_trace()` 已实现并被 A1/A2/A4 使用；`route()` 契约未变（A4 E3c） |

### 5.2 BLOCKING

```
当前无 BLOCKING 项。
```

**A5 独立复核**：本轮审计**未发现新的 blocking gap**。
四项"必须检查"（design / A0 / A1 / A2 / A3 / A4 / OQ / scope）均逐项通过。

### 5.3 DEFERRED（不构成 C2 completion blocker）

| # | 问题 | 为何不阻塞 | A5 复核 |
|---|---|---|---|
| **C2-OQ-1** | source / Tester identity | 需变更 `route()` 输入签名（接口变更）；C2 冻结 dimensions 不含 source | 未引入；`route(can_id, payload)` 签名未变 ✓ |
| **C2-OQ-2** | response 方向过滤 | OD-3 的"静默丢弃"使 deny 路径上**不存在**响应，故与 C2 deny 正交 | 未引入 egress 过滤 ✓ |
| **C2-OQ-3** | 策略表达载体 | 属实现形态选择，不改变 OD-1…OD-8 语义 | 载体仍为**源码常量 + 构造参数注入**；**未引入任何数据文件载体**（`git ls-files` 无 policy 数据文件） ✓ |
| **C2-OQ-4** | 多网关 / 中继跨网段策略 | 当前架构只有一个 `DiagnosticGateway` 实例 | 未引入多网关 ✓ |

> **结论**：4 项 deferred 均**未被 C2 实现意外引入或半实现**，
> 因此不构成 completion blocker。

---

## 6. Boundary Checklist

### 6.1 C1 / C2 / C3 边界

```
C1（ECU 内部授权语义）  → 需读 EcuState 内部字段的规则；C2 全链 **未触碰**
C2（Gateway 路由层授权）→ 策略模型 + 求值点 + 拒绝语义 + 逐 ECU 判定 + override 不变量
C3（0x27 / SecurityAccess）→ **未启动**；C2 **未**为其预置任何分支
```

**实测**：C2 全链（`f4ae37d..8120b55`）中，`ecu/` 目录下

```
UNCHANGED  uds_server.py        （C1 面：_SPEC_EXECUTION / SERVICE_SPECS / guard / handler）
UNCHANGED  service_spec.py      （C1 面）
UNCHANGED  ecu.py               （单 ECU 入口）
UNCHANGED  keygen.py            （0x27）
UNCHANGED  did_database.py / dtc_database.py / dtc_runtime.py /
           routine_database.py / transfer_database.py / file_database.py
UNCHANGED  uds_types.py
UNCHANGED  gateway_runtime.py   （runtime 层 —— 因此 A4 seam 3/4 的结论对既有实现成立）
MODIFIED   gateway.py           ← **唯一**变更的 production 文件（+193 / −6）
```

### 6.2 接口安全边界（与授权策略正交）

```
ALLOWED_INTERFACES / vcan0  = 环境安全边界（gateway_runtime.py，main() 强制）
Gateway authorization policy = 协议层授权（gateway.py，policy 求值）
```

二者**不同模块、不同机制**；A4 D3 实测证明"策略与 interface 取值正交"。
**A5 未把该环境边界表述为 C2 deny 问题。**

---

## 7. Golden Integrity

| 检查 | 结果 |
|---|---|
| corpus 的完整提交历史 | **仅 1 次提交**：`4059cf5 test(gateway): capture legacy policy oracle` |
| blob @ A0 / A1 / A2 / A3 / A4 / main | 全部 `541395b82e74ce915830fea23369e21c65fe7b27`（**逐阶段相同**） |
| LF SHA256（A0 结构测试硬编码值） | `d1d358927a4eb86e750fda13a961d935b1afccd3e01e50c26a696dcc7f43c208` —— **MATCH** |
| A2 legacy vendor blob @ A2 / A4 / main | 全部 `e64cd088fc86bf9e6b1c5dfb2bb1b80cae5ab39a`（= `4059cf5` 的 gateway.py blob） |
| 捕获护栏 | 仍在且仍 fail-closed（A0/A1 测试常驻断言） |

```
Golden 自 A0 捕获后**从未**被修改或重新生成。
```

---

## 8. Production / Test Scope（C2 全链）

```
f4ae37d..8120b55 共 17 个文件：

  docs/  × 6     （设计评审 + A0/A1/A2/A3/A4 阶段报告）
  ecu/   × 1     gateway.py（+193 / −6）      ← 唯一 production 变更
  tests/ × 10    （golden corpus、捕获脚本、golden lib、A0/A1/A2/A4 测试、
                   A2 差分 lib、A2 legacy vendor、run_tests.sh +32）

  .github/workflows/  UNCHANGED（C2 全链未改 CI）
  docs/TEST_MATRIX.md UNCHANGED（按 A2 阶段约定）
  docs/SECURITY_LAB_STATUS.md / AUTONOMOUS_BUILD_STATE.md  UNCHANGED
```

**production diff 的口径（两者必须分清，不可混写）**：

```
C2 实施阶段累计 production diff（f4ae37d..8120b55） = +193 / −6
    仅 1 个文件：labs/vehicle/uds-basic/ecu/gateway.py
    来源：C2-A1 引入 policy 机制（A0 / A2 / A3 / A4 均未再改 production）

A5 本轮 production diff（8120b55..A5 HEAD）        = 0
    本阶段只新增一份退出审查文档，未产生任何 production 变更
```

> C2 的**累计** production 变更发生在 **C2-A1**；A0 捕获 oracle、A2 差分、
> A3 回归、A4 审计**均未修改 production**，A5 同样为 0。

---

## 9. Final Regression Status（在 exit SHA 上**重跑**，非引用历史）

环境：WSL Ubuntu，venv Python 3.12.3，`vcan0` UP 且**独占**（全部串行）。

| Gate | 结果 | 计数 |
|---|---|---|
| **C2-A0** | **PASS** | 28 / 28 |
| **C2-A1** | **PASS** | 42 / 42 |
| **C2-A2** | **PASS** | 46 / 46（response 20/20、state 20/20、mismatches **0**） |
| **C2-A4** | **PASS** | 58 / 58（skipped 0） |
| UDS unit | **PASS 22 / WARN 0 / FAIL 0** | 1075 cases |
| UDS all | **PASS 34 / WARN 0 / FAIL 0** | 1309 cases，**SKIP 0** |
| CAN all | **PASS 7 / WARN 0 / FAIL 0** | 358 cases，**SKIP 0** |
| `qa_fast.sh` | **PASS**（exit 0） | ruff OK / mypy OK / uds unit 22-0 / can-tools offline 4-0 |
| `qa_extended.sh` | **PASS**（exit 0） | 4/4 步骤；1667 cases；FAIL 0；真实 skip 0 |
| `healthcheck.sh` | **PASS 75 / WARN 2 / FAIL 0**（exit 0） | —— |
| `healthcheck.sh --extended` | **PASS 78 / WARN 2 / FAIL 0**（exit 0） | —— |
| `verify_arm.sh` | **PASS 6 / 0**（exit 0） | AArch64 编译 + **实际执行**（`ARM_RUNTIME_OK 42`） |
| `verify_qemu_gdb.sh` | **PASS 4 / 0**（exit 0） | qemu-aarch64 `-g` → gdb-multiarch **命中 main 断点** → 正常退出 |
| `verify_ghidra.sh` | **PASS 8 / 0**（exit 0） | `Architecture=AARCH64` / `ERROR_COUNT=0` / main 反汇编 12 条 |
| `verify_fuzz_smoke.sh` | **PASS 8 / 0**（exit 0） | oracle 三态 AVAILABLE=3 / replay issues=0 |
| `verify_uds_fuzz_smoke.sh` | **PASS 10 / 0**（exit 0） | v1 golden 947 + 边界接受/拒绝 + 3 case campaign |
| `verify_gateway_runtime.sh` | **PASS 7 / 0**（exit 0） | 守护进程就绪 → 端到端 → SIGTERM 优雅退出 → 无残余 |

```
FAIL = 0    SKIP = 0    BLOCKED = 0
```

**NOT RUN（显式声明，不得计为 PASS）**：

```
qa_extended.sh --fuzz = NOT RUN
    原因：它是**可选长时 CAN fuzz campaign**（smoke 30 / 长测 100·500·1000），
          不属于 C2 exit blocker，也非 CI 覆盖范围。
    （`verify_fuzz_smoke.sh` 与 `verify_uds_fuzz_smoke.sh` 的**短程** fuzz
     闭环已实际执行并通过，见上表。）
```

> 同理，"工具存在"类检查（healthcheck 的 `tool_present` 系列）与"功能闭环"
> （repro 的真实编译→执行→断点命中→反汇编产出）在本文档中严格区分，不混同。

---

## 10. Known Non-Blocking Observations（记录，**不在本阶段修复**）

### 10.1 设计 §17 记录的既有问题（A5 复核：**均保持原状，未被误修**）

| # | 观察 | 复核结果 |
|---|---|---|
| 1 | `ALLOWED_INTERFACES` 仅在 `main()` 强制；`build_runtime()` 不检查 | 仍在 `main()` 侧（`gateway_runtime.py` 校验处）；`build_runtime()` 仍无检查 ✓ |
| 2 | `DiagnosticGateway.__init__` 不拒绝 `request_id == functional_address` | 仍只校验 request_id 重复 ✓（该语义 A4 B5 已显式记录） |
| 3 | 网关默认 handler 用 `services=None` ⇒ 网关路径无 0x27 | 仍为 `uds_server.handle_uds_request(payload, state)`，无 `services=` ✓（**非缺陷**，C3 议题） |
| 4 | 无其它 gateway 路由层正确性缺陷 | A5 复核同意 ✓ |

### 10.2 其它非阻塞观察

| # | 观察 | 分类 |
|---|---|---|
| 1 | **`DEFAULT_POLICY` 为 default-allow**（C2-OD-2 的刻意取舍）：机制缺省不 fail-closed，以换取"空策略零行为变更"这一迁移前提 | **设计取舍**（§16.3 已声明）；非缺陷 |
| 2 | 冻结输入集 20 条**未覆盖 `0x11` 与 physical `0x10`**（gateway 默认 handler 支持但 golden 未含） | 输入集覆盖边界（A2 §11 已记录）；A2 刻意不扩写输入 |
| 3 | response ordering 结构上退化（physical ≤1 条 / functional 恒 0 条），故不比较 | 模型事实（A2 §5.2） |
| 4 | Legacy 是 git 历史中的**冻结实现**，不是并存的运行时路径 | A2 §3.1 已准确定性 |
| 5 | A2 的受控共享依赖 pin 会随协议层演进而 **fail-closed** | 期望行为；C3 若改协议层需重新评估而非静默更新 pin |
| 6 | **Windows/WSL 行尾（CRLF）环境问题**（历史 Issue #28 同源）：`git reset --hard` 在 Windows 侧按 `core.autocrlf=true` 重写工作区，使被改动过的 `.sh` 变为 CRLF 而无法在 Linux bash 执行 | 环境级；**本轮再次出现**（`run_tests.sh`），处置方式与历史一致：单次命令级 `core.autocrlf=false` 重新物化，**blob 与 index 完全一致**（`d85f9b03…`）、`git status` clean、`git diff` 0 行 —— **未修改任何提交内容、未改配置、未加 `.gitattributes`** |
| 7 | WSL 侧 `git status` 会把 CRLF 文本文件报为 modified（Windows 侧报 clean），导致 healthcheck 的 git 判定读数不一致 | 与 #6 同源；**两次读数都不代表内容变化**（`git -c core.autocrlf=true diff` = 0） |
| 8 | `healthcheck` 的 2 个 WARN：git working tree（#6/#7 所致）+ docker engine 未运行 | 历史/环境级；与 A3/A4 同类，**非新增** |
| 9 | `qa_extended.sh --fuzz`（可选长时 fuzz smoke）未纳入本次 | 显式可选长测；不影响 C2 结论 |
| 10 | 若干阶段报告仍留有撰写当时的 "NEXT STAGE = NOT STARTED" 表述 | **刻意不回写**（历史事实陈述）；C2 最终状态以本文件为准 |

### 10.3 A4 审计过程中被发现并已修正的问题（记录，体现流程有效性）

A4 首版中 C2 / D2 的第 1 层证据由**另一个新建网关**的 `route_with_trace()` 事后复算，
与"四层证据必须来自同一次 DENY 请求"不一致。已在 A4 第二个 commit（`db74f35`）修正为
**测试侧 `_allow()` observation seam**，并新增 `TestAllowObservationSeam`（4 例）
先行证明 seam 自身忠实。

```
属"审计发现 → 修正 → 再验证"的正常闭环，非遗留缺陷。
```

---

## 11. Exit Decision

### 11.1 Blocking Criteria（全部满足方可 PASS）

| # | 判据 | 结果 |
|---|---|---|
| 1 | Design Freeze 完整（OD-1…OD-9 + dimensions + 边界） | ✓ 12/12 有实现证据 |
| 2 | C2-A0 冻结 oracle 来源可证、未被后续修改 | ✓ 仅 1 次提交；blob 逐阶段相同 |
| 3 | C2-A1 空策略零行为变更 | ✓ 42/42；A2 差分 20/20 + 20/20 |
| 4 | C2-A2 独立双实现差分 + Golden 仅作 anchor | ✓ mismatches 0；anchor 独立于 gate |
| 5 | C2-A3 full regression 无 FAIL | ✓ 本轮重跑：FAIL 0 |
| 6 | C2-A4 四个 seam 的 deny 不变量成立 | ✓ 58/58，含结构性不变量 |
| 7 | 无 BLOCKING Open Question | ✓ §15.2 空；A5 未发现新 gap |
| 8 | C1 未被修改 | ✓ 12 模块 UNCHANGED |
| 9 | C3 / 0x27 未启动 | ✓ `keygen.py` UNCHANGED；`gateway.py` 无 0x27 分支 |
| 10 | Golden 未被修改 | ✓ blob + LF SHA256 均 MATCH |
| 11 | production 变更受控且范围明确 | ✓ 全链仅 `gateway.py` 一个文件 |
| 12 | CI 未被修改 | ✓ `.github/workflows` UNCHANGED |
| 13 | 回归无新增 FAIL、无 SKIP 被伪装成 PASS | ✓ FAIL 0 / SKIP 0；WARN 均已归因 |
| 14 | 未修复 §17 非阻塞环境问题、未扩展 policy semantics | ✓ 全部保持原状 |

### 11.2 Deferred Items（不阻塞）

```
C2-OQ-1  source / Tester identity        → 未来架构（需接口变更）
C2-OQ-2  response 方向过滤                → 未来架构（egress policy）
C2-OQ-3  策略表达载体                     → C2 实现阶段形态选择（当前：源码常量 + 构造注入）
C2-OQ-4  多网关 / 中继跨网段策略           → out of scope
```

### 11.3 结论

```
C2-A5    = PASS
C2_EXIT  = PASS
```

C2 Gateway Policy 从设计冻结到 A0/A1/A2/A3/A4 形成了**完整、可审计、无 blocking gap**
的迁移闭环：

```
Design（冻结） → A0（捕获 oracle） → A1（机制 + 零行为变更）
              → A2（双实现差分） → A3（full regression）
              → A4（override / bypass 不变量） → A5（退出审查）
```

**保持当前 `main` 不再修改，等待下一阶段规划。本阶段未启动 C3。**

---

## 12. 本阶段产物

```
新增（唯一）  docs/OQ_C2_FINAL_EXIT_REVIEW_2026-09-26.md
```

```
A5 本轮 production diff = 0    （未修改任何 ecu/ 文件；C2 累计 +193/−6 见 §8）
Golden diff     = 0
测试 diff        = 0    （未修改 A0/A1/A2/A4 任何测试）
CI diff         = 0
历史报告回写      = 0    （A0/A1/A2/A3/A4 报告原文未动）
```
