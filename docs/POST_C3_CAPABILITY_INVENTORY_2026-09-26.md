# Post-C3 Main Capability Inventory — 2026-09-26

> **性质**：只读能力盘点（read-only capability inventory）。Phase 4-A0。
> 本文件**不实现任何功能、不修改生产代码、不修改测试、不修改 Golden、
> 不修改 CI、不修改任何既有文档、不解决任何 deferred OQ**。
> 目的是为"下一阶段 OQ 的 Design Selection"提供事实依据；
> **本文不选择、不排序、不推荐、不排名** 任何候选。
>
> **本文件不写 `C4 = xxx`。** 下一阶段必须在本盘点之后另行进行 Design Selection。

---

## 1. FINAL BASELINE

| 项 | 值 |
|---|---|
| 盘点分支 | `audit/post-c3-capability-inventory-20260926` |
| `main` HEAD | `566063783aaa58f8bd3933b4174b61db018164d9` |
| `origin/main` | `566063783aaa58f8bd3933b4174b61db018164d9` |
| HEAD == origin/main | ✓ |
| working tree | **clean**（盘点开始时与结束时均 `git status --short` 为空） |
| 上一阶段 | **C3 = COMPLETE / EXITED**（C3-A5 Exit Review，PR #58） |
| 上一份同类盘点 | `docs/MAIN_CAPABILITY_INVENTORY_2026-09-24.md`（PR #23，**C1/C2/C3 之前**） |

**本盘点的证据规则**：

```
1  以当前 main 的真实源码 / 测试 / 脚本 / CI 配置为准，不以文档自述为准；
2  每条结论给出 file:line 或可复算的数值；
3  严格区分"工具存在"与"能力已由脚本化闭环验证"；
4  严格区分"已实现"与"已进 CI"；
5  无法判定者写 NOT FOUND / ABSENT，不猜测。
```

---

## 2. 状态取值（沿用 2026-09-24 盘点的口径，保持可比）

| 取值 | 含义 |
|------|------|
| `VERIFIED` | 有版本化的可执行入口，且断言了真实成功判据（实际运行过） |
| `PARTIAL` | 主体存在，但有明确、已记录的缺口或未覆盖面 |
| `NOT_IMPLEMENTED` | 仓库中不存在实现 |
| `OUT_OF_SCOPE` | 项目**明确声明**不在范围内（有书面依据） |
| `INFRASTRUCTURE_ONLY` | 只有环境/工具/占位存在，未形成可用链路 |

环境/工具类另标**证据强度**（沿用旧口径）：

| 级别 | 含义 |
|------|------|
| **E1** | 有版本化脚本**且断言了真实成功判据**（实际运行过） |
| **E2** | 有版本化的专用实验/配置产物，但**不是**验证入口 |
| **E3** | 仅存在性/存活探测 |
| **E4** | 仅文档或注释提及，无脚本化使用 |

运行层级另标：

| 标记 | 含义 |
|------|------|
| **CI-A** | 自动 CI 覆盖（`fast` job，push/PR 均触发） |
| **CI-M** | 仅 `workflow_dispatch` 覆盖（`extended` job） |
| **MANUAL** | 有可复现入口，但**无任何门禁调用** |
| **SMOKE** | 仅有受限 smoke，无完整 campaign 入口 |

---

## 3. 交付时间线（PR #1 … #58，全部为 merge commit）

```
第一轮（历史基线）
  PR #1   89a12a0  docs/engineering close

第二轮 OQ3–OQ7（UDS / DTC / Gateway Runtime）
  PR #10  abcb8b4  UDS MF-v1
  PR #12  c96f858  0x11 ECUReset 扩展（01/02/03）
  PR #13  3349d79  0x19 DTC severity（reportType 07/08/09）
  PR #16  a0f6725  DTC runtime state（dtc_runtime.py）
  PR #17  c112f022 0x85 ControlDTCSetting
  PR #21  ae22a556 Gateway Runtime v1（gateway_runtime.py）
  PR #23  4449c57  Main Capability Inventory（上一份盘点）
  PR #24  f1d2635  Candidate OQ Design Review（OQ-C1…C12）

C1 —— Permission / Session framework（ServiceSpec + guard）
  PR #25  a1de775  C1 设计评审与冻结
  PR #27  f04b0b2  Phase 1（ServiceSpec 模型引入，行为不变）
  PR #30  ff681c3  Phase 2 Step 1（dispatcher 走 resolve_service_spec）
  PR #31…#33       A0/A1/A2（0x22 legacy golden / 启用 / 对拍）
  PR #37…#39       B0/B1/B2（0x2E 70 条 legacy golden / 启用 / 对拍）
  PR #42  f4ae37d  C1 Phase 2 最终退出评审 = PASS

C2 —— Gateway Security Policy
  PR #43  83d1f52  C2 设计评审与冻结（OD-1…OD-9）
  PR #44…#48       A0…A4（golden / 空策略+trace / 对拍 / full regression / override-bypass）
  PR #49  8ed53fa  C2 最终退出评审 = **C2_EXIT = PASS**

C3 —— 0x27 SecurityAccess through Gateway
  PR #50  4cb8527  C3 设计评审与冻结（OD-1…OD-15）
  PR #51  a387084  A0 baseline
  PR #52  2733de7  A1 implementation（含 X1 裁决后的 pin rebaseline）
  PR #53  121b6f4  A2 双路径差分
  PR #54  5ebfaf3  A3 full regression
  PR #55  （未合并）原 A4 = **BLOCKED / DEFECT-1 FOUND** ← 历史证据，保持 OPEN
  PR #56  5559db0  DEFECT-1 remediation
  PR #57  b3e6283  A4 Re-Audit = PASS
  PR #58  5660637  A5 Exit Review = **C3 EXIT = PASS**

合计 merge commit = 48；其中生产代码 merge = 15（其余为 docs / audit / 测试）。
```

---

## 4. A. UDS / ECU 能力矩阵

**源码**：`labs/vehicle/uds-basic/ecu/uds_server.py`（协议层唯一实现文件）
**静态注册表 `SERVICES`**：14 个 SID（`uds_server.py:2157-2172`）
**动态注入**：`0x27`（唯一一个，见 §4.3）

### 4.1 逐服务矩阵（15 个 SID，全部 `implemented`）

| SID | 子功能 | 正响应 | NRC | 状态变更 | 离线覆盖 | e2e 覆盖 | 状态 |
|---|---|---|---|---|---|---|---|
| **0x10** DiagnosticSessionControl | 01/02/03（`uds_types.py:72`） | `50 <type> <P2:2> <P2*:2>` | 13, 12 | `session` + 经 `reset_security_state` 清 4 个安全字段 | `test_ecu_unit.py` 8+3 | `test_e2e_vcan.py` 等 | `VERIFIED` |
| **0x11** ECUReset | 01/02/03（`uds_types.py:106`） | `51 <type>` | 13, 12 | `_reset_volatile_state`（`:354`）→ 复位 **22** 字段 / **保留 5** 字段 | `TestEcuReset` 11 + `TestEcuResetEquivalence` 8 | 6 个 vcan 文件 | `VERIFIED` |
| **0x14** ClearDiagnosticInformation | groupOfDTC | `54`（不回显） | 13, 31 | `dtc_records` status → `0x00`（键保留） | `test_dtc_clear_unit.py` 39 | `test_dtc_clear_vcan.py` 11 | `VERIFIED` |
| **0x19** ReadDTCInformation | **01/02/07/08/09/0A**（`:985-990`） | `59 <availMask> …` | 13, 31, 12 | **无**（docstring `:1098`） | `test_dtc_unit.py` 115 | `test_dtc_vcan.py` 21 | `VERIFIED` |
| **0x22** ReadDataByIdentifier | 多 DID | `62 <DID:2><data>` ×N | 13, 31, **33** | 无（读） | `test_ecu_unit.py` 10+11 + C1 3 类 | `test_e2e_vcan.py` | `VERIFIED` |
| **0x27** SecurityAccess | **01/02**（`:628`） | `67 01 <seed:4>` / `67 02` | 13, 12, 37, 22, 24, 35, 36 | 7 个字段（`seed_counter` / `pending_seed` / … / `security_level`） | `test_ecu_unit.py` 59 + C3 4 套件 171 | `test_e2e_vcan.py` 8 类 | `VERIFIED` |
| **0x2E** WriteDataByIdentifier | DID | `6E <DID:2>` | 13, 31, **7F**, **33**, 22 | `did_overrides[did] = data` | `test_services_extra_unit.py` 10 + C1 4 套件 | `test_write_did_vcan.py` 8 | `VERIFIED` |
| **0x31** RoutineControl | 01/02/03（`:1259-1261`） | `71 01/02 <RID>` / `71 03 <RID><result:4>` | 13, 12, 31, 22 | `active_routines` / `routine_start_counts` | `test_routine_unit.py` 25 | `test_routine_vcan.py` 11 | `VERIFIED` |
| **0x34** RequestDownload | DFI=0x00 ALFID=0x23 | `74 20 00 80` | 13, 31×3, 22 | 6 个 `download_*` | `test_transfer_unit.py` 40 | `test_transfer_vcan.py` 18 | `VERIFIED` |
| **0x35** RequestUpload | 同上 | `75 20 00 80` | 13, 31×3, 22 | 5 个 `upload_*` | `test_upload_unit.py` 47 | `test_upload_vcan.py` 16 | `VERIFIED` |
| **0x36** TransferData | BSC | `76 <BSC> <data≤126>` / `76 <BSC>` | 13, **73**, 22, **24** | upload 2 字段 / download 3 字段 | 16 + 10 + 4 | 3 个 vcan 文件 | `VERIFIED` |
| **0x37** RequestTransferExit | — | `77` | 13, 22, **24** | 提交 `state.files` 或清 upload 状态 | 4+4 / 16+13 | 2 个 vcan 文件 | `VERIFIED` |
| **0x38** RequestFileTransfer | **01/02/03/04/05**（`:1790`） | `78 01\|03…` / `78 02` / `78 04 …` / `78 05 …` | 13, 31×12, 22 | `file_mode` / `file_path` / `files` / 6 或 5 字段 | `test_file_transfer_unit.py` **174** | `test_file_transfer_vcan.py` 41 | `VERIFIED` |
| **0x3E** TesterPresent | 0x00（`:792`） | `7E 00`；`3E 80` → `b""` | 13, 12 | **无**（docstring `:810-811`） | `TesterPresentTests` 7 | `test_write_did_vcan.py` 等 | `VERIFIED` |
| **0x85** ControlDTCSetting | 01/02（`:921`） | `C5 <type>` | 13, 12 | `dtc_setting_enabled`（门控 `dtc_runtime` 更新，**不**阻止 0x14） | 17 | `test_dtc_vcan.py` | `VERIFIED` |

**A5 抽样实测复核（本盘点，离线、只读）**：

```
31 01 20 01  ->  71 01 20 01          ✓ 0x31 已实现
27 01        ->  67 01 85 eb da 6b    ✓ 0x27 已实现
19 01 08     ->  59 ff 01 00 00       ✓ 0x19 已实现
```

### 4.2 明确记录的 scope boundary（源码原文，非推断）

| SID | 边界 | 出处 |
|---|---|---|
| 0x11 | 三个 resetType **状态等价**（experiment-defined，不造人为差异）；未实现 `0x04`/`0x05` | `uds_server.py:494-502` |
| 0x11 | `_reset_volatile_state` **不是完整硬件 volatile-memory 模型**（无电源域/硬件复位/NVM controller） | `:358-361` |
| 0x14 | 约定是**项目 profile**，不声称是 ISO 功能组语义的完整实现；**不设 session/security 门控** | `:842`, `:863-867` |
| 0x19 | **wire profile 刻意区别于 ISO 14229-1**：正响应第 2 字节是 availMask 而非 reportType 回显；suppress bit 不在范围内 | `:1077-1086`, `:1099` |
| 0x31 | 不实现 0x33/0x7E/0x7F；正响应**不携带** routineStatusRecord；无 routineControlOptionRecord | `:1299-1301` |
| 0x34 | memoryAddress 仅 metadata，**不访问真实地址**；全部是 synthetic in-memory（非真实 Flash） | `:233-234`, `:240` |
| 0x35 | 上限 `MAX_UPLOAD_SIZE = 32130`（BSC 单字节，255 块回绕歧义） | `:1460-1461` |
| 0x38 | 实验 profile，**不是** ISO 一比一；非法 mode → NRC 0x31 **而非** 0x12；`lengthFormatIdentifier` 固定 0x02 | `:1757-1758`, `:1773-1774`, `:1825` |
| 0x85 | **不"清 DTC"**；**不设 session/security 门控** | `:941-943`, `:956-958` |
| 0x27/keygen | seed-key **不具备密码学安全性**：LCG 混合、`SECRET` 为公开常数 | `keygen.py:11-16` |
| dispatcher | 刻意**尚未**包含 Permission 框架 / DID metadata | `uds_server.py:32-34` |

**已知 limitation（accepted）**：

```
functional 0x27 空转副作用 —— 功能寻址下每个 ECU 的 pending_seed/seed_counter
被推进，但 Tester 收不到任何响应；由 C3 显式记录为 KNOWN LIMITATION
（C3-OD-8 / C3-OQ-10 DEFERRED）。
```

### 4.3 静态注册表与动态注入

```
SERVICES        = 14 个 SID（0x10 0x11 0x14 0x19 0x22 0x2E 0x31 0x34 0x35 0x36 0x37 0x38 0x3E 0x85）
SERVICE_SPECS   = 14 个（与 SERVICES 集合相等的模块级守卫，uds_server.py:2350-2355）
SPECS_EXCLUDED  = {0x27: …}（fail-fast 守卫 :2382-2391）
_SPEC_EXECUTION = frozenset({0x22, 0x2E})（:2422）—— 仅这 2 个 SID 执行声明式 guards

唯一动态注入的 SID = 0x27。
注入定义唯一：uds_server.security_access_services()（:2124）
调用方：ecu.py:369、gateway_runtime.py:508
gateway.py / gateway_runtime.py 中无第二份 0x27 分支逻辑（已核验）
```

### 4.4 测试资产（本盘点独立复算）

```
labs/vehicle/uds-basic/tests/  test_*.py = 37 个文件 / 1488 个用例

  离线 unit：26 文件 / 1253 用例     ← run_tests.sh unit
  vcan e2e ：11 文件 /  235 用例     ← run_tests.sh e2e（需 vcan0 + root）

  run_tests.sh 引用数 = 37 ✓（与源码文件数一致，无遗漏）
```

**与 A3 快照的对账**：A3 记录 `unit 1200（25 文件）`；当前 `1253（26 文件）`。
差值 `+53 = +46（C3-A4 Re-Audit 新增文件）+ 7（DEFECT-1 修复为 test_gateway_unit 增补）`。✓ 可完全对账。

---

## 5. B. Gateway / C1 / C2 / C3

### 5.1 C1 —— Permission / Session 框架

| 项 | 值 |
|---|---|
| 交付 | `service_spec.py`（`ServiceSpec` / `Guard` 模型）+ `uds_server._build_service_specs()` |
| 迁移范围 | **仅 0x22 与 0x2E** 启用 guard 执行（`_SPEC_EXECUTION`） |
| guard 链 | 0x2E 六段有序：`length_format → known → session → security → writable → data_length`（`:2320-2326`） |
| 门控事实 | `state.session` 在协议层**仅 1 处**判断（0x2E）；`state.security_level` **仅 2 处**（0x22 per-DID、0x2E） |
| 退出 | C1 Phase 2 最终退出评审 = PASS（A0–A4 / B0–B4 全 PASS，Exit Criteria 1–10 = PASS） |
| 状态 | `VERIFIED`（framework 存在且已验证）+ **`PARTIAL`**（覆盖 14 个静态 SID 中的 2 个） |

**未覆盖面（明确）**：其余 12 个静态 SID 的 `guards=()`，即**没有授权 guard**
——这是 C1 的**刻意选择**（不改变既有行为），不是缺陷。

### 5.2 C2 —— Gateway Policy

| 项 | 值 |
|---|---|
| 冻结项 | **C2-OD-1 … OD-9 全部 FROZEN** |
| 机制 | `gateway.py`：`PolicyDecision` / `PolicyTrace` / `GatewayPolicy` / `DEFAULT_POLICY` |
| 求值点 | `_route_core()` 内，**目标解析之后、`target.process()` 之前**（`gateway.py:406-411`） |
| 判定单位 | `(target ECU, addressing_mode, SID)`——`PolicyDecision` 恰 4 字段 |
| 默认 | `allow_all_policy` 空策略 = **no-op**（与策略机制引入前逐字节相同） |
| 可观测性 | `route_with_trace()`（与 `route()` 共用同一 `_route_core`，非第二条路径） |
| 退出 | **C2_EXIT = PASS**（BLOCKING = 0） |
| 状态 | `VERIFIED`（机制）+ **`OUT_OF_SCOPE` 的部分**：**未部署任何实际策略**（默认空策略） |

### 5.3 C3 —— SecurityAccess through Gateway

| 项 | 值 |
|---|---|
| 冻结项 | **C3-OD-1 … OD-15**；BLOCKING = 0；RESOLVED = 2（C3-B1、C3-OQ-12）；DEFERRED = 3 |
| 机制 | per-request `services_provider`；`{**uds_server.SERVICES, **provider()}`（`gateway.py:192-200`） |
| 接线 | `build_runtime(..., wire_security_access=True)` **默认接线**（`gateway_runtime.py:527-537`） |
| handler 优先级 | framework default → provider **可**重绑；custom（构造期/构造后重赋）→ **不替换**（`gateway.py:300-310`，身份判定 `has_custom_handler`） |
| 顺序不变量 | `policy → provider → process`；计数 1-1-1 / 1-0-0 / 3-3-3 / 3-0-0 |
| AST 不变量 | `gateway.py` 的 `.process(` 调用点**恰 2 处**（行 411 / 396） |
| 退出 | **C3 EXIT = PASS**（A5 Exit Review，17/17 条件成立） |
| 状态 | `VERIFIED` |

**历史边界保持（不得改写）**：

```
C3-A4 原（PR #55）        = BLOCKED / DEFECT-1 FOUND   ← 保持 OPEN，历史证据
DEFECT-1 remediation      = PR #56（gateway.py + 7 个单元测试）
C3-A4 Re-Audit（PR #57）  = PASS
最终 A4 结论              = PASS by remediation + re-audit
```

### 5.4 其他 Gateway 事实

| 能力 | 状态 | 证据 |
|---|---|---|
| physical addressing | `VERIFIED` | 0x7E0→0x7E8 / 0x7E1→0x7E9 / 0x7E2→0x7EA |
| functional addressing | `VERIFIED` | 0x7DF 广播、逐 ECU 求值、**恒不收集响应**（C2-OD-4） |
| Gateway Runtime v1 | `VERIFIED`（MANUAL） | `gateway_runtime.py`；`verify_gateway_runtime.sh` 13 项断言；**不在任何门禁内** |
| vcan0 fail-closed | `VERIFIED` | `ALLOWED_INTERFACES = ("vcan0",)`（`:87`）；其它接口**拒绝启动**，发生在绑定 socket **之前** |
| policy 输入维度 | `VERIFIED` | 仍严格为 `(target, addressing_mode, SID)` |
| DoIP / 多进程 ECU orchestration / 真实 CAN | `OUT_OF_SCOPE` | 多处显式声明 |

---

## 6. C. CAN / ISO-TP / Fuzz / Replay / Capture

| # | 能力 | 状态 | 层级 | 证据 |
|---|---|---|---|---|
| C-1 | SocketCAN / vcan0 使用 | `VERIFIED` | CI-A（离线部分） | 单一收口 `parsers.py:21` `ALLOWED_INTERFACE="vcan0"` + `guard_interface()`（`:116-131`），被全部工具调用 |
| C-2 | can-utils | `INFRASTRUCTURE_ONLY` | MANUAL | 仅 `capture_lab.sh:31,45,55,58` 与 healthcheck 探测；**Python fuzz/replay 引擎不使用 can-utils** |
| C-3 | ISO-TP 发送/接收 | `VERIFIED` | CI-A（选项解析 50 例） | `isotp_send.py`；`Normal_11bits`、tx 0x7E0 / rx 0x7E8 |
| C-4 | ISO-TP malformed 用例 | `VERIFIED` | MANUAL | `test_isotp_malformed_vcan.py` **40** 例；绕过 ISO-TP 栈注入非法 PCI |
| C-5 | **CAN 层 fuzz（ID/DLC/总线级）** | **`OUT_OF_SCOPE`（非漏做）** | — | `fuzz/__init__.py:18-19` 显式声明；`INJECT_ID`/`OBSERVE_ID` 固定；`addressing` 类别为 reference-only |
| C-6 | UDS fuzz | `VERIFIED` | MANUAL | `uds_fuzz.py`；**唯一有真实 campaign 入口的层**（默认跑全部 2501 条去重候选） |
| C-7 | ISO-TP fuzz（含 MF） | `PARTIAL` / **SMOKE** | MANUAL | `isotp_fuzz.py`；**未给 `--smoke`/`--list` 时拒绝运行**（`:855-857` exit 2），smoke 上限 **30** case |
| C-8 | kernel oracle | `VERIFIED` | CI-A（离线断言） | dmesg 增量的**异常检测器**（非差分 oracle）；三态 `AVAILABLE/UNAVAILABLE/ERROR`；不可用时 `kernel_events is None`（≠ `[]`），fail-closed |
| C-9 | replay | `VERIFIED` | MANUAL | `replay.py`；**逐字节重放**（不重新生成变异）；`REPLAY_IDENTITY_FIELDS` 冻结 |
| C-10 | capture（python raw / BLF / bash 三段） | `VERIFIED` | MANUAL | `can_capture.py` / `can_capture_blf.py` / `capture_lab.sh`（三段均有 `diff` 或计数断言，`CAPTURE_LAB=PASS`） |
| C-11 | malformed cases / 负样本 | `VERIFIED` | MANUAL | 见 C-4 |
| C-12 | fuzz smoke | `VERIFIED` | MANUAL | `verify_fuzz_smoke.sh` 3 case + oracle 三态 + replay `issue(s)=0` |
| C-13 | **full fuzz campaign 边界** | **`PARTIAL`** | MANUAL | **UDS 层有**（默认全量 2501）；**ISO-TP 层没有**（结构上禁止扩容）。`fuzz-out/` 中 100/500 case 历史产物**无法由当前默认 CLI 路径复现**（30 上限） |

**确定性事实（重要）**：两个 fuzz 入口**均不使用 PRNG**；`--seed` 默认 12345 且**仅记录**；
`--max-cases` 是**确定性前缀**，不是迭代序截断。

**smoke vs campaign 的准确数值**：

```
isotp_fuzz.py --smoke（裸）         ≤ 30（build_smoke_cases() → cases[:30]）
isotp_fuzz.py 无 --smoke/--list     拒绝运行，exit 2
uds_fuzz.py（裸）                    --max-cases None = 全部 2501 条唯一候选
                                      （2784 候选 - 283 重复 = 2501）
```

---

## 7. D. Reverse / Firmware

### 7.1 工具存在性（WSL Ubuntu 实测）

```
存在：aarch64-linux-gnu-gcc 13.3.0 · arm-linux-gnueabihf-gcc 13.3.0
      qemu-arm / qemu-aarch64 / qemu-system-aarch64 8.2.2
      gdb · gdb-multiarch 15.1 · Ghidra 12.1.3（/opt/ghidra）
      binwalk 2.3.4 · unsquashfs/mksquashfs 4.6.1 · mtd-utils 2.2.0
      afl-fuzz/afl-gcc（afl++ 4.09c）· r2 5.5.0 · strace 6.8 · ltrace 0.7.3
      binutils 2.42 · yara 4.5.0 · patchelf · xxd · flock
不存在：jefferson · ubireader_extract_images / _files · qemu-arm-static
binfmt_misc：enabled，31 条目（WSLInterop + python3.12 + 29 个 qemu-*）
```

### 7.2 能力判定（**严格区分"工具在"与"闭环已验证"**）

| 能力 | 工具在? | 脚本化+断言闭环? | 状态 | 门禁 |
|---|---|---|---|---|
| AArch64 交叉编译 + 执行 | ✓ | ✓ `verify_arm.sh:52-94`（断言 `ARM_RUNTIME_OK 42`） | `VERIFIED` | **MANUAL** |
| armhf（qemu-arm） | ✓ | ✓ `verify_arm.sh:59-104` | `VERIFIED` | **MANUAL** |
| **ARM ELF 脚本化解汇编** | ✓ | ✓ **仅经 Ghidra**；objdump/r2 路径**无** | `PARTIAL` | MANUAL |
| QEMU **user-mode** | ✓ | ✓ | `VERIFIED` | MANUAL |
| QEMU **system-mode** | ✓（已安装） | ✗ **0 个 tracked 调用** | `NOT_IMPLEMENTED` | — |
| gdb-multiarch 远程调试 | ✓ | ✓ `verify_qemu_gdb.sh:74-97`（断点命中标记 + qemu rc=0） | `VERIFIED` | **MANUAL** |
| Ghidra headless | ✓ | ✓ `verify_ghidra.sh`（AARCH64 + `ERROR_COUNT=0` + 12 条反汇编） | `VERIFIED` | **MANUAL** |
| binfmt 透明执行 | ✓ | ✓ `verify_arm.sh:82` | `VERIFIED` | MANUAL |
| readelf / strings | ✓ | ✓ 真实使用（`analyze.sh:37,46` 等） | `VERIFIED` | MANUAL |
| objdump / nm | ✓ | ✗ 仅存在性检查（`healthcheck.sh:209`） | `INFRASTRUCTURE_ONLY`(E3) | — |
| radare2 / r2 | ✓ | ✗ 仅存在性检查 | `INFRASTRUCTURE_ONLY`(E3) | — |
| strace / ltrace | ✓ | ✗ 仅存在性检查 | `INFRASTRUCTURE_ONLY`(E3) | — |
| `qemu-<arch> -strace` | ✓ | ✗ 0 次调用 | `NOT_IMPLEMENTED` | — |
| binfmt additive + selfheal | ✓ | ✓ repo 侧 fail-closed + 3 场景回归 | `VERIFIED`(repo) | **MANUAL（无调用方）** |
| SquashFS build→identify→**extract** | ✓ | ✓ 但**仅对合成镜像** | `PARTIAL` | MANUAL |
| JFFS2 build + identify | ✓ | 文本输出，**无 PASS/FAIL 断言** | `PARTIAL` | MANUAL |
| **JFFS2 解包** | ✗（jefferson 缺失） | ✗ | `NOT_IMPLEMENTED` | — |
| UBIFS/UBI build + identify | ✓ | 同上，无断言 | `PARTIAL` | MANUAL |
| **UBIFS/UBI 解包** | ✗（ubireader 缺失） | ✗ | `NOT_IMPLEMENTED` | — |
| binwalk identify | ✓ | ✓（`analyze.sh:22`、`fs_formats.sh:23,29,44`） | `VERIFIED`（仅识别） | MANUAL |
| **binwalk `-e` 解包** | ✓ | ✗ **0 个 tracked 调用** | `NOT_IMPLEMENTED` | — |
| AFL++ 插桩→fuzz→复现 | ✓ | ✓ `labs/fuzzing/afl-demo/run.sh`（真实崩溃 **且** 两次信号一致） | `VERIFIED` | **MANUAL（无门禁）** |
| YARA TP/TN | ✓ | ✓ `labs/yara-basic/scan.sh` | `VERIFIED` | **CI-M**（`qa_extended.sh:102`） |

### 7.3 结构性事实（决定固件能力上限）

```
**本仓库从未、且按设计不能包含真实固件镜像。**

  labs/firmware/samples/images/  =  仅 .gitkeep（101 字节）
  全仓库 tracked 二进制/镜像文件  =  0（.bin/.img/.elf/.squashfs/.jffs2/.ubifs/.ubi/… 全部 0 匹配）
  .gitignore 明确：`labs/firmware/samples/images/*` + `!…/.gitkeep`
                   `*.bin` `*.img` `/firmware/` `pcaps/` `artifacts/` `tmp/`
  原因（README 自述）："SecurityLab must not redistribute vendor images"

⇒ 任何固件能力的可复现性只能来自：
     (a) 合成构建脚本（build_fw.sh → 一个 34 行 C 文件），或
     (b) 使用者本地提供的镜像（gitignored）
```

**据此**：SquashFS 的"完整链"是**对合成镜像**成立；对真实厂商固件从未验证。

---

## 8. E. 工程基础设施

| # | 项 | 状态 | 层级 | 证据 / 边界 |
|---|---|---|---|---|
| E-1 | `qa_fast.sh` | `VERIFIED` | **CI-A** | 4 步：ruff（仅 uds-basic）/ mypy（仅 ecu）/ uds unit / can-tools offline；缺工具 → `exit 2` |
| E-2 | `qa_extended.sh` | `VERIFIED` | **CI-M（从未运行）** | 4 步 + 可选 `--fuzz`；vcan0 缺失且无 `sudo -n` → **fail-closed `exit 2`** |
| E-3 | `healthcheck.sh` | `VERIFIED` | **MANUAL** | FAIL 才非零；WARN 不影响 |
| E-4 | repro 脚本（6 个） | `VERIFIED` | **MANUAL（0 调用方）** | 统一退出码契约：`0` 通过 / `1` 验证失败 / `2` 环境不可用 |
| E-5 | CI（`.github/workflows/ci.yml`） | `PARTIAL` | — | 恰 **2 个 job**：`fast`（push/PR/dispatch）+ `extended`（**仅 workflow_dispatch**） |
| E-6 | Python 依赖 lock | **`PARTIAL`（未强制）** | — | `python-network-requirements.lock`（48 包）**存在但无任何消费者**；CI 安装的是 direct-pin 文件；仅 `healthcheck.sh:140-166` 对 **8 个直接 pin** 做精确校验 |
| E-7 | Git hygiene 检查 | **`PARTIAL`（最小）** | MANUAL | 全部内容仅 `healthcheck.sh:101-111`：① 是否在 git 仓库（fail）② 工作树是否干净（**warn**）。**无**远程/branch/ahead-behind/commit 内容检查 |
| E-8 | WSL 假设 | `VERIFIED` | — | `healthcheck.sh:44` 探测；仓库路径硬编码 `/mnt/d/SecurityLab`；`~/.venvs/network/bin` 约定 |
| E-9 | Docker | **`INFRASTRUCTURE_ONLY`（可选）** | MANUAL | **不是任何东西的依赖**；缺失 → 仅 1 行 WARN；无 Dockerfile / `docker run` / compose |
| E-10 | vcan0 生命周期 | `VERIFIED` | — | `configs/systemd/vcan0.service`（含 `modprobe can-isotp`）；消费者行为分三档：创建（qa_extended/CI-M）/ fail-closed exit 2（repro）/ WARN-skip（can-tools runner） |
| E-11 | binfmt self-heal | **`PARTIAL`** | MANUAL | repo 侧 v2.3 fail-closed + 3 场景回归（**无调用方**）；**系统侧部署为 v2.2，无 fail-closed 块**（见 §9.3） |
| E-12 | DSH runtime | `INFRASTRUCTURE_ONLY` | MANUAL | 外部 npm 包；`dsh-web.service` user 级、回环；healthcheck 仅存活探测 |
| E-13 | `nettools_check.sh` | `VERIFIED` | **MANUAL** | 仅回环；防火墙探测降级为 WARN |
| E-14 | conftest / pyproject | `VERIFIED` | — | `_vcan` 文件自动标 `pytest.mark.vcan`；**但 CI 不跑 pytest**（逐文件独立解释器执行） |

### 8.1 CI 的实际覆盖面（本盘点实测）

```
run 总数（最近 100 条）：push 64 + pull_request 36 = 100
workflow_dispatch 的 run 数 = 0

⇒ `extended` job 自建立以来 **从未运行过一次**。

fast job 实际执行：ruff / mypy / uds-basic unit(26 文件) / can-tools offline(4 文件)
未进 CI 的入口（两者均无任何引用）：
    scripts/repro/*.sh（6 个）· scripts/healthcheck.sh · scripts/nettools_check.sh
    configs/binfmt/tests/selfheal_failclosed_test.sh
    scripts/tests/healthcheck_qemu_fallback_test.sh
```

---

## 9. 文档一致性检查

### 9.1 DOC-DISC 汇总（**本阶段只记录，不修改任何文档**）

| ID | 类别 | 位置 | 文档断言 | 当前事实 |
|---|---|---|---|---|
| **DOC-DISC-1** | **事实错误** | `SECURITY_LAB_STATUS.md:73,156` | "**0x27 不经 gateway 层**…属**后续独立 OQ**" | C3 已实现并**默认接线**（`gateway_runtime.py:527-537`） |
| **DOC-DISC-2** | **事实错误** | `ARCHITECTURE.md:146-152` | "服务集…**不含 0x27**；让网关透传 0x27…属**后续独立 OQ**" | 同上，C3 已交付 |
| **DOC-DISC-3** | **事实错误** | `labs/vehicle/uds-basic/README.md:1253` | "未实现服务（**0x31**/未知 SID）统一返回 NRC 0x11" | 0x31 已实现：`31 01 20 01 → 71 01 20 01`（实测） |
| **DOC-DISC-4** | **事实错误** | `labs/vehicle/uds-basic/README.md:1335` | `isotp_client.py 27 01  # 未实现服务` | 0x27 已实现：`27 01 → 67 01 <seed>`（实测） |
| **DOC-DISC-5** | **事实错误** | `labs/vehicle/uds-basic/README.md:1438-1443`（§9.6） | `19 01 08` 的响应是 `7f 19 11`（"未实现服务"示例） | 0x19 已实现；实测响应 **`59 ff 01 00 00`**；`7f 19 11` 对该请求不可达 |
| **DOC-DISC-6** | **事实错误** | `uds_types.py:141-143` | "`NRC_SERVICE_NOT_SUPPORTED_IN_SESSION` …**暂未被门控逻辑使用**"；且称"当前 4 个服务" | 该常量**已在使用**（`uds_server.py:762`，0x2E session 门控）；服务数为 14/15 |
| **DOC-DISC-7** | 陈旧计数 | `ARCHITECTURE.md:64,99` | `test_gateway_unit 13` / "协议层路由（13 单元）" | 实测 **20**（PR #56 修复 +7，`gateway_unit.py:152`） |
| **DOC-DISC-8** | 陈旧计数 | `TEST_MATRIX.md:53` | `test_gateway_unit.py … 13` | 实测 **20** |
| **DOC-DISC-9** | 陈旧计数 | `TEST_MATRIX.md:65` | `test_gateway_runtime_vcan.py … 21` | 实测 **22** |
| **DOC-DISC-10** | 陈旧计数 | `labs/vehicle/uds-basic/README.md:1245,1291` | `test_ecu_unit` 覆盖 **106** 用例；`test_e2e_vcan` 覆盖 **65** 用例 | 实测 **116** / **69** |
| **DOC-DISC-11** | 陈旧列举 | `labs/vehicle/uds-basic/README.md:1258` | "`0x27` 的单元测试细分为**五个类**" | 紧随其后的表格有 **7** 行 |
| **DOC-DISC-12** | 陈旧列举 | `labs/vehicle/uds-basic/README.md:833` | NRC `0x12` 触发者仅列 `0x10`/`0x11`/`0x27` | 另由 **0x19**（`:1250`）、**0x31**（`:1313`）、**0x3E**（`:822`）、**0x85**（`:972`）产生 |
| **DOC-DISC-13** | 陈旧列举 | `uds_server.py:9-23` | "**十四个服务处理函数**"清单 | 清单列出 14 个但**含 0x27、不含 0x85**；而 `SERVICES` 是 14 个**含 0x85、不含 0x27** → 自相矛盾 |
| **DOC-DISC-14** | **覆盖缺失** | `docs/TEST_MATRIX.md` | 收录 **23** 个测试文件 | 源码有 **37** 个；缺的 **15** 个恰好**全部**是 C1(7)+C2(4)+C3(4) |
| **DOC-DISC-15** | **覆盖缺失** | `docs/TEST_MATRIX.md §8` | CI 映射停在 OQ6-B（`c112f022` / run `35859067322`） | 现基线 `5660637`；C1/C2/C3 的 CI run 一条未记 |
| **DOC-DISC-16** | 历史快照 | `docs/MAIN_CAPABILITY_INVENTORY_2026-09-24.md` | C1/C2/C3 **之前**的盘点（其 Gateway 章节已被超越） | 属**自证日期**的历史快照；本文为其 post-C3 重做 |
| **DOC-DISC-17** | 阶段状态过期 | `SECURITY_LAB_STATUS.md:20-31,47-51` | "当前生产代码基线 = `ae22a556…`"；"当前阶段…**下一项第二轮能力尚未确定**" | 现基线 `5660637`；其后已连续交付 C1 / C2 / C3 三个阶段 |
| **DOC-DISC-18** | 口径歧义 | `docs/` 全库 | "oracle" 一词指代两种**互不相干**的机制 | ① fuzz 的 kernel oracle = dmesg 增量**异常检测**（`isotp_fuzz.py:99-181`）② C2/C3 的 differential oracle = **Python A/B 等价**（`c2_differential_lib.py` / `c3_differential_lib.py`）。两者无连接 |

**未发现**：任何文档声称某个 SID 存在而源码中不存在；任何源码 SID 在 README §4 与 `SECURITY_LAB_STATUS.md:155` 的服务表中缺失（两表均正确列出全部 15 个）。

**根因说明（非借口，是事实）**：DOC-DISC-1…2、7、14、15、17 全部指向同一模式
——**C1/C2/C3 三个阶段（PR #25 … #58，共 34 个 merge）没有同步更新 4 份"状态类"文档**
（`SECURITY_LAB_STATUS.md` / `ARCHITECTURE.md` / `TEST_MATRIX.md` / `README.md`）。
DOC-DISC-3…5、10…13 是更早的遗留（OQ 阶段之后未回改的举例与计数）。

### 9.2 代码级观察（**非文档问题；本阶段不修复**）

| ID | 位置 | 事实 | 影响 |
|---|---|---|---|
| **OBS-1** | `labs/vehicle/can-tools/fuzz/replay.py:91-104` | `is_testcase_file()` 在 `any()` 闸门之后**每条路径都 `return True`**；`:99-102` 的 `validate_case` 会执行但结论被丢弃（两分支同返 `True`） | 良性（`load_case()` 仍会校验），但 docstring 所述"存在性 + 严格校验"两段式**并不成立** |
| **OBS-2** | `configs/binfmt/tests/selfheal_failclosed_test.sh:33-36` | `flock` 不可用时打印 `[WARN]` 后 **`exit 0`** | 在一个**专为验证 fail-closed 而存在**的测试里，跳过被报成成功——本仓库唯一一处 fail-open 的测试入口 |

### 9.3 系统级观察（**仓库之外；本阶段不修复**）

| ID | 事实 |
|---|---|
| **SYS-1** | **部署的 binfmt self-heal 是 v2.2，仓库源是 v2.3 fail-closed。** 仓库 `configs/binfmt/binfmt-selfheal.sh` 头部 = `(v2.3, 2026-09-21)`，md5 `66a1f0757e0ee109b7ec2a44b938e6ab`；`/usr/local/sbin/binfmt-selfheal.sh` 头部 = `(v2.2, 2026-09-21)`，且全文件 `fail-closed` 出现 **0 次** ⇒ 运行中的副本**没有** "绝不回落 systemd-binfmt" 的保证块。helper `binfmt-register-additive.py` 两侧一致。 |
| | `SECURITY_LAB_STATUS.md:208-209` 把该事项列为"待你处理"的**未确认**项；本盘点将其**确认为真实的版本错配**（不只是未确认）。 |

---

## 10. 能力计数汇总

**计数口径**（使数字可复算）：以本文各矩阵中**逐行**为一个能力条目，
按该行最终标注的状态归入下表的五档之一；同一能力不重复计入两档。

**计数复算表**（逐章行数，使上表可被独立重算）：

| 章节 | 行数 | VERIFIED | PARTIAL | INFRA_ONLY | NOT_IMPL | OUT_OF_SCOPE |
|---|---|---|---|---|---|---|
| §4.1 UDS 15 个 SID | 15 | 15 | – | – | – | – |
| §5.1 C1 | 1 | 1 | – | – | – | – |
| §5.2 C2 | 1 | 1 | – | – | – | – |
| §5.3 C3 | 1 | 1 | – | – | – | – |
| §5.4 其他 Gateway | 6 | 5 | – | – | – | 1 |
| §6 CAN / ISO-TP / fuzz | 13 | 9 | 2 | 1 | – | 1 |
| §7.2 Reverse / Firmware | 23 | 11 | 4 | 3 | 5 | – |
| §8 工程基础设施 | 14 | 8 | 4 | 2 | – | – |
| **合计** | **74** | **51** | **10** | **6** | **5** | **2** |

**计数规则**（避免歧义）：

```
· 一行 = 一个能力条目，只计入一个档位；
· 双标注行（§5.1 / §5.2）按**主体**状态计入（框架/机制已 VERIFIED），
  其覆盖缺口另记于 §11 Known gaps，不重复计入 PARTIAL；
· §5.3 的历史边界（原 A4 = BLOCKED）不另计条目 —— 它不是"当前能力"。
```

其中按**运行层级**（与状态正交，逐行标记）：

```
CI-A（自动 CI 覆盖）        =  4  （§6 的 C-1 / C-3 / C-8 + §8 的 E-1）
CI-M（仅 dispatch 覆盖）    =  1  （YARA via qa_extended —— 而 extended 从未运行过）
MANUAL（有入口，无门禁）     = 31  （§5.4:1 · §6:9 · §7.2:14 · §8:7）
未标记层级                  = 38  （§4.1 的 15 个 SID 不按层标记 + 其余不适用项）

其中 ISO-TP fuzz 层（C-7）的层级标记为 MANUAL，但其**规模**受限（SMOKE）。
```

**测试资产**：

```
uds-basic   37 文件 / 1488 用例（离线 1253 + e2e 235）
can-tools    4 离线文件 / 303 用例
C1/C2/C3 专项用例合计 = 29+28+56+26+30+20+39 + 28+42+46+58 + 35+48+42+46 = 573
```

---

## 11. Known gaps（当前 main 的真实缺口）

```
G-01  统一 Permission / Session 框架仅覆盖 14 个静态 SID 中的 2 个（0x22 / 0x2E）
G-02  12 个静态 SID 无授权 guard（0x11 / 0x14 / 0x19 / 0x31 / 0x34-0x38 / 0x3E / 0x85 等）
G-03  无任何 UDS 服务能产生 DTC 故障 —— fault generation 不是协议能力
      ⇒ "故障发生 → DTC 置位 → 清除" 全链无法在线上验证
G-04  0x19 仅 6 个 reportType（01/02/07/08/09/0A）
G-05  0x28 CommunicationControl 未实现
G-06  0x38 modeOfOperation 仅 01–05；0x06（ResumeFile）未实现，且受 3 处冻结测试约束
G-07  "0x39 RequestFileTransferExit" 仅存在于路线图文字中，源码无对应实现
G-08  0x31 无 routineControlOptionRecord / 正响应无 status record
G-09  suppressPosRspMsgIndicationBit 未统一（0x3E 与 0x85 各自处理/声明不支持）
G-10  ISO-TP 层无完整 fuzz campaign 入口（结构上被 30-case 上限禁止）
G-11  CAN 层（仲裁 ID / 总线级 / 多节点）无 fuzz —— 显式 OUT_OF_SCOPE
G-12  100/500 case 的历史 campaign 产物无法由当前默认 CLI 复现
G-13  6 个 repro 脚本 + healthcheck + nettools + 2 个回归测试 **无任何门禁调用**
G-14  `extended` CI job 自建立以来从未运行（0 次 workflow_dispatch）
G-15  JFFS2 解包缺失（jefferson 未安装，无解包代码）
G-16  UBIFS / UBI 解包缺失（ubireader 未安装，无解包代码）
G-17  binwalk `-e` 未脚本化（仅识别）
G-18  QEMU system-mode 完全缺失（0 个 tracked 调用）
G-19  r2 / objdump / nm / strace / ltrace 仅存在性检查，无脚本化使用
G-20  传递依赖 lock 文件存在但**无消费者**，CI 不强制
G-21  Git hygiene 检查为最小集（仅"在仓库内 + 工作树干净(WARN)"）
G-22  仓库按设计不含真实固件镜像 ⇒ 固件链只能对合成样本成立
G-23  部署的 binfmt self-heal 为 v2.2（无 fail-closed），与仓库 v2.3 不一致
G-24  `0x19` 其余 reportType、0x38 与 ISO 的多处 profile 偏差（已在源码显式记录）
```

---

## 12. Deferred items（保持 deferred，本阶段**不解决**）

### 12.1 C3 遗留 DEFERRED OQ

| ID | 主题 | 状态 |
|---|---|---|
| **C3-OQ-10** | functional 0x27 空转副作用的专门抑制机制 | **DEFERRED**（核验：`gateway.py` 中无任何 SID 特例分支） |
| **C3-OQ-11** | per-ECU `SECURITY_CONFIG` | **DEFERRED**（核验：`ecu.py:305` 仍为单一模块级 dict） |
| **C3-OQ-13** | 独立 runtime dependency carrier 层 | **DEFERRED**（核验：C3 期间 `ecu/` 未新增任何文件） |

### 12.2 更早记录的 deferred / roadmap

```
CANDIDATE_OQ_DESIGN_REVIEW_2026-09-24.md 的 12 个候选（OQ-C1…C12）中：
    已完成：OQ-C2 → C2（Gateway Security Policy）
            OQ-C3 → C3（0x27 经 Gateway）
            OQ-C1 → C1（**部分**：框架已建，仅覆盖 2 个 SID）
    仍开放：OQ-C4 / C5 / C6 / C7 / C8 / C9 / C10 / C11 / C12
```

**注意**：以上"仍开放"仅是**历史候选清单的残余项登记**，
**不构成下一步必须做它们的任何推论** —— 见 §13。

---

## 13. Candidate Next-OQ Matrix

> **本表只列候选，不排名、不打分、不推荐、不选 Winner。**
> 候选**重新从当前 main 的能力盘点推导**，而不是因为旧 roadmap 写过某项就保留它。
> 旧 roadmap 中的 OQ-C4…C12 若在本次盘点的缺口清单（§11）中**找不到对应事实依据**，
> 则不予收录。

### 13.1 表头字段说明

```
Candidate ID                 候选编号（本表自编，与任何历史编号无继承关系）
Capability                   候选要建立的能力
Current gap                  当前缺口的**可核对事实**（指向 §11 条目）
Existing dependency          已存在的依赖（代码位置 / 工具 / 契约）
Production impact            对 production 的影响面（局部 / 中型 / 架构级 / 大型）
Test impact                  对既有测试的影响（是否触及冻结测试）
Security significance        安全语义上的意义
Architectural dependency     是否依赖尚未决定的前置架构决策
Small-OQ-able                能否独立形成一个小 OQ
Design-Review-first          是否必须先做 Design Review 冻结契约
```

### 13.2 候选矩阵

| ID | Capability | Current gap | Existing dependency | Production impact | Test impact | Security significance | Architectural dependency | Small-OQ-able | Design-Review-first |
|---|---|---|---|---|---|---|---|---|---|
| **NQ-01** | 0x28 CommunicationControl | G-05：源码无 0x28 | 无；纯协议层新增 | 局部（`uds_server.py`） | 新增用例；不触冻结测试 | 通信控制是"拒绝服务/降级"类语义的协议面 | 无 | 是 | 否（可沿用既有服务模板） |
| **NQ-02** | 0x38 `modeOfOperation = 0x06`（ResumeFile） | G-06 | `file_database.py:59-78`（模式 01–05） | 中型 | **触及 3 处冻结断言**（`test_file_transfer_unit.py:295-297,1016-1024`、`test_file_transfer_vcan.py:711-719`） | 续传语义涉及传输完整性 | 无 | 否（需解冻既有契约） | **是** |
| **NQ-03** | firmware transfer 完整性 / 签名校验 | G-06 相邻；`F12`：传输路径零完整性校验（`crc32/sha256/hmac/signature` = 0 匹配） | 无 | 架构级 | 新增 | 固件传输的完整性是安全关键面 | 需先决定"完整性属于协议层还是入口层" | 否 | **是** |
| **NQ-04** | 0x19 其余 reportType 扩展 | G-04：仅 01/02/07/08/09/0A | `dtc_database.py` / `dtc_runtime.py` / `DTC_METADATA` | 局部 | 新增用例；既有 6 个 reportType 的 wire profile 已冻结 | 诊断数据暴露面 | 无 | 是 | 否 |
| **NQ-05** | 统一 Permission / Session 框架（扩到全部 SID） | G-01 / G-02 | `service_spec.py` + `_SPEC_EXECUTION` + `_guards_for()` | 架构级 | **可能触及既有 NRC 序列**（"顺序即语义"） | 授权语义的统一化 | 需先冻结"哪些 SID 该有会话/安全门控" | 否 | **是** |
| **NQ-06** | DTC fault injection model（协议能力或 test seam） | G-03：无服务能产生 DTC | `dtc_runtime.set_dtc_status()`（目前**无 tracked 调用点**） | 中型 | 新增 | 决定"故障链"能否端到端验证 | 需先决定它是**协议能力**还是**测试 seam** | 是（若仅 seam） | **是**（若是协议能力） |
| **NQ-07** | suppressPosRspMsgIndicationBit 统一化 | G-09 | 0x3E 已实现抑制；0x85 显式声明不支持 | 局部～中型 | **可能改变既有正响应字节** | 响应抑制影响测试器行为 | 无 | 是 | 是（若改变既有 wire 行为） |
| **NQ-08** | Stateful UDS fuzz（多请求序列） | G-10 相邻；OQ3 显式把 stateful 列为 OUT OF SCOPE | `uds_fuzz.py`（无状态、每 case 新 ECU 进程，`isotp_fuzz.py:429-439`） | 中型 | 新增；不触既有 | 序列级缺陷（会话/安全状态跨请求）只有 stateful 能发现 | 需先冻结"序列模型与 oracle" | 否 | **是** |
| **NQ-09** | ISO-TP 层完整 campaign 入口 | G-10：结构上被 30-case 上限禁止 | `isotp_fuzz.py`（已具备 `generate_cases` 与 `--max-cases`，仅缺放开路径） | 局部 | 不触既有语义 | 提升 ISO-TP 分帧层覆盖面 | 无 | **是** | 否 |
| **NQ-10** | e2e 进 CI | G-14：`extended` 从未运行；vcan 需 root | `ci.yml`（`extended` job 已具备 vcan 创建代码） | 无（CI 配置） | 无 | 提升回归可信度（当前 e2e 235 例**零自动覆盖**） | 无 | 是 | 否 |
| **NQ-11** | repro / healthcheck / 回归测试进门禁 | G-13：0 调用方 | 6 个 `scripts/repro/*` 已有 E1 断言与 0/1/2 退出码契约 | 无（脚本/CI） | 无 | 把"配方已版本化"升级为"被自动重跑" | 无 | 是 | 否 |
| **NQ-12** | 传递依赖 lock 强制 | G-20 | `python-network-requirements.lock`（48 包，已存在） | 无 | 无 | 供应链可复现性 | 无 | 是 | 否 |
| **NQ-13** | JFFS2 解包链 | G-15 | `fs_formats.sh` 已能 build + identify | 局部（脚本 + 可选工具） | 新增 | 固件取证覆盖面 | 依赖外部工具（jefferson）可用性 | 是 | 否 |
| **NQ-14** | UBIFS / UBI 解包链 | G-16 | 同上 | 局部 | 新增 | 同上 | 同上（ubireader） | 是 | 否 |
| **NQ-15** | QEMU system-mode 固件仿真 | G-18 | `qemu-system-aarch64` 已安装；`labs/firmware/synthetic-ecu` 已有合成镜像构建链 | 架构级 | 新增 | 从静态分析走向动态固件分析的关键一步 | **强**：需先决定 rootfs / kernel / DTB 从何而来（仓库**不含**真实镜像，G-22） | 否 | **是** |
| **NQ-16** | 动态分析脚本化（strace/ltrace/qemu -strace） | G-19 | 工具均已安装；`tmp/audit/reverse_dyn_check.sh`（**gitignored**）曾做过 | 局部 | 新增 | 运行期行为观测 | 无 | 是 | 否 |
| **NQ-17** | 0x31 routineControlOptionRecord + status record | G-08 | `routine_database.py` | 局部～中型 | **会改变既有正响应字节** | 字节级兼容性 | 无 | 是 | 是（若改变 wire） |
| **NQ-18** | 状态类文档与源码重新同步 | DOC-DISC-1…18（本文 §9.1） | 4 份状态文档 | 无（docs） | 无 | 无（可核对性） | 无 | 是 | 否 |
| **NQ-19** | 仓库侧 fail-open 修复（OBS-1 / OBS-2） | §9.2 | `replay.py` / `selfheal_failclosed_test.sh` | 局部 | 可能新增断言 | fail-closed 属性的一致性 | 无 | 是 | 否 |
| **NQ-20** | 部署 binfmt v2.2 → v2.3 | SYS-1（G-23） | 仓库源已就绪（v2.3） | 无（系统侧部署） | 无 | 恢复 fail-closed 保证 | 需 sudo 部署确认 | 是 | 否 |
| **NQ-21** | 0x39 RequestFileTransferExit | G-07：**仅存在于路线图文字，源码无对应物** | 无 | 待定 | 待定 | 待定 | **需先定义它到底是什么**（非 ISO 标准 SID） | 否 | **是**（先澄清语义） |

**C3 deferred OQ（C3-OQ-10 / 11 / 13）未列入上表**：它们在 A5 已核验为
"不构成阻塞、且未被误标 completed"。若未来要做，须作为**独立阶段**立项，
且 C3-OQ-13 在候选 D 的评估中已被判定"当前规模下过度抽象"
（触发条件：**出现第二个依赖消费者**，当前不存在）。

### 13.3 矩阵的使用边界

```
本矩阵**不是**路线图，**不是**优先级排序，**不是**推荐。
它只回答："从当前真实缺口出发，有哪些**技术上可分离**的候选方向存在？"

下一步（Design Selection）需要独立进行，且至少要重新回答：
    · 哪些候选有真实的**安全语义**收益，而非仅覆盖面扩张？
    · 哪些候选会触碰**已冻结**的契约（NQ-02 / 05 / 07 / 17 已标记）？
    · 哪些候选依赖尚未决定的**架构前置**（NQ-03 / 05 / 06 / 08 / 15 / 21）？
```

---

## 14. 结论

```
POST-C3 INVENTORY = COMPLETE

Implemented capability count     = 51  （VERIFIED，占 74 条的 68.9%）
Partial capability count         = 10
Manual-only capability count     = 31  （有可复现入口，但无任何门禁调用）
Infrastructure-only count        =  6
Not-implemented count            =  5
Out-of-scope count               =  2
                                   ─────
总计（能力条目）                  = 74  （计数规则与复算表见 §10）

Known gaps                       = 24  （G-01 … G-24）
Deferred items                   =  3  （C3-OQ-10 / 11 / 13，全部仍 deferred）
                                    + 9  （旧候选 OQ-C4…C12 残余登记，不构成方向推论）
Documentation discrepancies      = 18  （DOC-DISC-1 … DOC-DISC-18）
                                     + 2 代码级观察（OBS-1/2）
                                     + 1 系统级观察（SYS-1）
Candidate next OQs               = 21  （NQ-01 … NQ-21，只列不选）
```

### 14.1 本次盘点最重要的三条事实

```
FACT-1  C1/C2/C3 三个阶段（PR #25…#58，34 个 merge）**未同步更新任何状态类文档**。
        SECURITY_LAB_STATUS.md / ARCHITECTURE.md / TEST_MATRIX.md / README.md
        四份文档中，**没有一处**提及 C1 / C2 / C3。
        其中 5 条已从"陈旧"恶化为"与源码相反"的事实错误（DOC-DISC-1…5）。
        —— 这不是文档格式问题，而是"状态看板与实际能力脱节"。

FACT-2  所有需要 vcan0 的能力（UDS 全部 15 个服务的 e2e、fuzz、replay、capture、
        Gateway Runtime、ISO-TP malformed）合计 235 + 数百用例，
        **实际自动覆盖 = 0**。唯一的 e2e 门禁（extended job）
        自建立以来 **从未运行过一次**（100 个 run 中 0 个 workflow_dispatch）。

FACT-3  仓库按设计**不含任何真实固件镜像**（0 个 tracked 二进制；
        samples/images/ 仅 .gitkeep）。因此固件分析链的可复现性上限
        是"对合成样本成立"；JFFS2/UBIFS 连解包都没有（工具缺失 + 无代码）。
```

### 14.2 本阶段未做的事（合规声明）

```
✗ 未修改 production / tests / Golden / Design 文档 / CI workflow
✗ 未解决任何 deferred OQ
✗ 未触碰 PR #55（保持 OPEN，历史 A4 BLOCKED 证据）
✗ 未重跑 A3（不制造第二份 full regression）
✗ 未创建 feature branch
✗ 未修改 §9 中记录的任何文档 —— 一致性检查**只记录、不修复**
✓ 新增仅 1 个文件：本盘点报告
```

**安全边界**：本盘点全程只读；未连接真实 CAN / 真实 ECU / 真实车辆；
未运行任何 vcan0 长测或 fuzz campaign；未修改系统配置。

---

```
NEXT STAGE = NOT SELECTED
```

下一阶段必须在本盘点之后**另行进行 Design Selection**；
本文件不预设、不暗示、不推荐任何候选。
