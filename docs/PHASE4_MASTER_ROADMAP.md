# Phase 4 — Master Roadmap

> **性质**：`LONG-TERM ROADMAP / PLANNING BASELINE`（长期路线 / 规划基线）。
>
> **本文不是**：当前执行指令 · feature specification · OQ design · implementation task。
>
> **本文不启动任何 OQ。** 存在 roadmap **不**构成任何能力的实现授权。
>
> ```
> Roadmap is a planning baseline, not an automatic execution trigger.
> ```

---

## 0. 一句话现状

```
C3 = COMPLETE / EXIT PASS
Phase 4-A0 = COMPLETE
Phase 4-A1 Design Selection = PAUSED

没有已选定的 feature OQ。
没有任何实现被本文授权。
```

---

## 1. Baseline

| 项 | 值 |
|---|---|
| 固化分支 | `docs/phase4-master-roadmap-20260926` |
| `main` / `origin/main` | `d78b545d733f093ece8750187c6dc0a6e6a3ab86` |
| working tree | clean |
| 上一阶段产物 | `docs/POST_C3_CAPABILITY_INVENTORY_2026-09-26.md`（Phase 4-A0，PR #59） |
| 本文产物 | 仅本文档（`docs/PHASE4_MASTER_ROADMAP.md`） |

**本文的输入**：Phase 4-A0 Capability Inventory（PR #59）中经独立核验的
24 条 known gaps（G-01…G-24）、18 条文档不一致（DOC-DISC-1…18）、
2 条代码级观察（OBS-1/2）、1 条系统级观察（SYS-1），
以及由其推导的 21 条候选（NQ-01…NQ-21）。

**本文不做**：不排名、不推荐、不选择、不排优先级、不估算工期。

---

## 2. 总路线

```
C3 EXIT PASS
    ↓
Phase 4-A0  Capability Inventory            COMPLETE
    ↓
Phase 4-A1  Candidate / Design Selection    PAUSED   ← 当前停止点
    ↓
Phase 4-B   Core Protocol / Security / Transfer
    ↓
Phase 4-C   Fuzz / Campaign / CI / Gate
    ↓
Phase 4-D   Firmware / Embedded Linux
    ↓
Phase 4-E   Documentation / Engineering Close
    ↓
Final Capability Inventory
    ↓
Full Regression
    ↓
Architecture Audit
    ↓
Final Exit
```

> **B / C / D / E 不是执行顺序，是能力分区。**
> 各分区内的候选**彼此独立**，可按任意次序单独成 OQ；
> 分区之间也**没有**强制的先后依赖。
> 唯一的顺序约束来自技术依赖（见各分区的 "Design Review 前置" 标注）。

---

## 3. Phase 4-A —— Inventory 与 Selection

### 3.1 A0 = COMPLETE

```
产物   docs/POST_C3_CAPABILITY_INVENTORY_2026-09-26.md
PR     #59
merge  d78b545d733f093ece8750187c6dc0a6e6a3ab86
结果   POST-C3 INVENTORY = COMPLETE
```

盘点结论摘要（详见该文档 §10 / §14）：

```
VERIFIED            = 51
PARTIAL             = 10
INFRASTRUCTURE_ONLY =  6
NOT_IMPLEMENTED     =  5
OUT_OF_SCOPE        =  2
                      ────
合计能力条目         = 74

层级：CI-A = 4 · CI-M = 1 · MANUAL = 31 · 未标记 = 38
```

### 3.2 A1 = PAUSED

```
A1 = Candidate / Design Selection

状态：PAUSED —— Candidate Selection 尚未作为实现阶段启动。
```

**候选池**：

```
NQ-01 … NQ-21

= candidate pool only
= no ranking
= no recommendation
= no selected next OQ
```

> 候选池是一个**事实清单**，回答"从当前真实缺口出发，有哪些技术上可分离的方向存在"。
> 它**不回答**"下一步应该做哪一个"。后者是 Phase 4-A1 Design Selection 的工作。

---

## 4. Phase 4-B —— Core Protocol / Security / Transfer

**能力分区**：UDS / ECU / Security / Transfer。

| 候选 | 能力 | 对应缺口 | 前置要求 |
|---|---|---|---|
| **NQ-01** | 0x28 CommunicationControl | G-05 | 无（可沿用既有服务模板） |
| **NQ-02** | 0x38 `modeOfOperation = 0x06`（ResumeFile） | G-06 | **须先重新冻结契约** |
| **NQ-03** | firmware transfer integrity / signature | G-06 相邻（传输路径零完整性校验） | **必须先 Design Review** |
| **NQ-04** | 0x19 其余 reportType | G-04 | 无 |
| **NQ-05** | unified Permission / Session 扩面 | G-01 / G-02 | **必须先 Design Review** |
| **NQ-06** | DTC fault injection model | G-03 | **必须先 Design Review** |
| **NQ-07** | suppressPosRspMsgIndicationBit 统一化 | G-09 | **若改变 wire 须先重新冻结契约** |
| **NQ-17** | 0x31 routineControlOptionRecord + status record | G-08 | **若改变 wire 须先重新冻结契约** |
| **NQ-21** | "0x39 RequestFileTransferExit" 语义澄清 | G-07 | **必须先澄清语义**（源码无对应物） |

**架构相关项（NQ-05 / NQ-06 / NQ-03）必须先 Design Review。**

**改变既有 wire / frozen contract 的项（NQ-02 / NQ-07 / NQ-17）
必须先重新冻结契约**，且必须显式列出受影响的冻结测试。

```
已知会被触碰的冻结断言（NQ-02）：
    labs/vehicle/uds-basic/tests/test_file_transfer_unit.py:295-297
    labs/vehicle/uds-basic/tests/test_file_transfer_unit.py:1016-1024
    labs/vehicle/uds-basic/tests/test_file_transfer_vcan.py:711-719
```

---

## 5. Phase 4-C —— Fuzz / Campaign / CI / Gate

**能力分区**：测试与门禁。

| 候选 | 能力 | 对应缺口 |
|---|---|---|
| **NQ-08** | Stateful UDS fuzz（多请求序列） | G-10 相邻 |
| **NQ-09** | ISO-TP 层完整 campaign 入口 | G-10 |
| **NQ-10** | e2e 进 CI | G-14 |
| **NQ-11** | repro / healthcheck / regression 进门禁 | G-13 |
| **NQ-12** | dependency lock 强制 | G-20 |

**NQ-08 必须先 Design Review**（需先冻结序列模型与 oracle）。

### 长期边界（**不因 Phase 4 存在而改变**）

```
CAN bus-level fuzz（仲裁 ID / 总线级 / 多节点）
    = OUT_OF_SCOPE
    = 不因为 Phase 4 的存在而自动重新纳入
```

---

## 6. Phase 4-D —— Firmware / Embedded Linux

| 候选 | 能力 | 对应缺口 |
|---|---|---|
| **NQ-13** | JFFS2 解包链 | G-15 |
| **NQ-14** | UBIFS / UBI 解包链 | G-16 |
| **NQ-15** | QEMU system-mode 固件仿真 | G-18 |
| **NQ-16** | 脚本化动态分析（strace / ltrace / qemu -strace） | G-19 |
| **NQ-20** | binfmt 系统侧部署对齐 | SYS-1 / G-23 |

**NQ-15 必须先 Design Review**（架构前置强：需先决定 kernel / rootfs / DTB 从何而来）。

### 长期边界（**不得违反**）

```
repository intentionally contains no real vendor firmware
firmware reproducibility is based on synthetic samples
real vendor firmware is never to be committed to the repository
```

依据（Phase 4-A0 核验）：

```
全仓库 tracked 二进制 / 镜像文件 = 0
labs/firmware/samples/images/   = 仅 .gitkeep
.gitignore 明确：samples/images/*  +  !…/.gitkeep
                  *.bin  *.img  /firmware/  pcaps/  artifacts/  tmp/
```

---

## 7. Phase 4-E —— Documentation / Engineering

| 候选 | 能力 | 对应缺口 |
|---|---|---|
| **NQ-18** | 状态类文档与源码重新同步 | DOC-DISC-1…18 |
| **NQ-19** | 仓库侧 fail-open 修复 | OBS-1 / OBS-2 |
| **NQ-20** | binfmt 系统侧部署对齐 | SYS-1 |

### 7.1 Backlog 来源

以下**全部来自** `docs/POST_C3_CAPABILITY_INVENTORY_2026-09-26.md`
（Phase 4-A0 / PR #59），本文不新增、不改写：

```
DOC-DISC-1 … DOC-DISC-18   文档不一致 18 条
                            （事实错误 6 · 陈旧计数与列举 6 · 覆盖缺失 2 ·
                              历史快照与阶段状态 2 · 口径歧义 1 · 其他 1）

OBS-1  labs/vehicle/can-tools/fuzz/replay.py:91-104
       is_testcase_file() 退化：any() 闸门后各路径同返 True，
       validate_case 的结论被丢弃

OBS-2  configs/binfmt/tests/selfheal_failclosed_test.sh:33-36
       flock 不可用时打印 [WARN] 后 exit 0 —— 唯一的 fail-open 测试入口

SYS-1  部署的 binfmt self-heal 为 v2.2（全文件 "fail-closed" 出现 0 次），
       仓库源为 v2.3 fail-closed ⇒ 版本错配已确认
```

> **这些是当前已知 backlog / observations，并不表示现在立即处理。**
> 它们是被登记的事实，不是被批准的工单。

### 7.2 关于 DOC-DISC 的处置原则

```
文档不一致的修复属于 NQ-18，是一个**独立候选**。
不得以"顺手修一下"的方式把文档重同步混入其它能力 OQ
——那会使该 OQ 的 diff 无法单纯归因。
```

---

## 8. C3 Deferred OQ —— 永久状态记录

```
C3-OQ-10 = DEFERRED    functional 0x27 空转副作用的专门抑制机制
C3-OQ-11 = DEFERRED    per-ECU SECURITY_CONFIG
C3-OQ-13 = DEFERRED    独立 runtime dependency carrier 层
```

**必须明确**：

```
它们不是 C3 incomplete。
C3 已 EXIT PASS（C3-A5 Exit Review，17/17 条件成立，PR #58）。
它们属于后续 architecture backlog。
```

**C3-OQ-13 的特别说明**：

```
当前没有第二个 dependency consumer。
不为抽象而抽象。
触发条件（若未来出现）：出现第二个需要请求时依赖的服务（如 0x29 / 0x28 等）。
```

**C3-A4 的历史边界（不得改写）**：

```
C3-A4 原（PR #55）        = BLOCKED / DEFECT-1 FOUND   ← 保持 OPEN，历史证据
DEFECT-1 remediation      = PR #56
C3-A4 Re-Audit（PR #57）  = PASS
最终 A4 结论              = PASS by remediation + re-audit
```

---

## 9. 项目长期边界

### 9.1 默认路线**不纳入**

```
真实 CAN       = 不纳入默认路线
真实 ECU       = 不纳入默认路线
真实车辆       = 不纳入默认路线
CAN bus-level fuzz = OUT_OF_SCOPE
```

### 9.2 安全实验默认环境

```
vcan0
virtual ECU
simulated CAN bus
local lab environment
```

### 9.3 禁止事项

```
✗ 将 synthetic ECU 描述成真实 ECU
✗ 将 vendor firmware 放入 Git
✗ 因 roadmap 自动扩大 scope
```

### 9.4 既有代码级边界（不得回退）

```
gateway_runtime.py  ALLOWED_INTERFACES = ("vcan0",)   —— 非 vcan0 拒绝启动
can-tools/parsers.py ALLOWED_INTERFACE  = "vcan0"     —— 全部工具统一收口
```

---

## 10. 统一 OQ 开发方法

每一项能力沿用**同一套固定工作模式**（C1 / C2 / C3 已验证）：

```
one capability
    ↓
one Design Review           冻结契约、列 NON-GOAL、决定 BLOCKING/DEFERRED
    ↓
one OQ
    ↓
A0  Baseline                改动之前的证据快照（golden）
    ↓
A1  Implementation          机制 + 接线，零行为漂移
    ↓
A2  Differential / Compatibility   与冻结基线逐字节对拍
    ↓
A3  Full Regression         全量门禁
    ↓
A4  Override / Bypass Audit 授权边界与绕过面
    ↓
A5  Exit Review             冻结项逐项核验 + 退出判定
    ↓
Merge
```

**纪律要求**：

```
· 不一次并行开发大量 OQ。每项能力独立、可验证、可审计。
· 一个阶段只承担单一证据职责（设计不证明实现、A3 不证明绕过面）。
· 每个阶段只读既有证据链路，不为"显得完整"重造上一阶段的证据。
· A4 发现真实缺陷时：停止修复 → 记录 → 标记 BLOCKER → 另行 remediation。
  不在审计阶段修 production。
· 历史阶段报告**只读**。发现问题时记录，不回填、不改写。
```

---

## 11. CURRENT EXECUTION STATUS

```
Phase 4-A0 = COMPLETE
Phase 4-A1 = PAUSED

No feature OQ currently selected
No implementation currently authorized by this roadmap alone

Resume point:
    Phase 4-A1 Design Selection
```

```
Roadmap is a planning baseline, not an automatic execution trigger.
```

---

## 附：候选池总索引（仅索引，无排名）

| 分区 | 候选 |
|---|---|
| 4-B Core Protocol / Security / Transfer | NQ-01 · NQ-02 · NQ-03 · NQ-04 · NQ-05 · NQ-06 · NQ-07 · NQ-17 · NQ-21 |
| 4-C Fuzz / Campaign / CI / Gate | NQ-08 · NQ-09 · NQ-10 · NQ-11 · NQ-12 |
| 4-D Firmware / Embedded Linux | NQ-13 · NQ-14 · NQ-15 · NQ-16 · NQ-20 |
| 4-E Documentation / Engineering | NQ-18 · NQ-19 · NQ-20 |

> NQ-20 同时出现在 4-D 与 4-E：其**能力对象**属固件/嵌入式环境（binfmt 透明执行），
> 其**工作性质**属工程对齐（系统侧部署）。若未来选中，须在 Design Review 中
> 明确归入哪一个分区，避免重复计入。

**候选项的完整属性**（Current gap / Existing dependency / Production impact /
Test impact / Security significance / Architectural dependency /
Small-OQ-able / Design-Review-first）见
`docs/POST_C3_CAPABILITY_INVENTORY_2026-09-26.md` §13.2 —— 本文不复制该表，
以免出现两份可能漂移的副本。

---

## 附：本文的合规声明

```
新增    仅 docs/PHASE4_MASTER_ROADMAP.md
未修改  production / tests / Golden / CI workflow / configs
未修改  PR #55
未修改  任何历史 C1/C2/C3 报告
未修改  POST_C3_CAPABILITY_INVENTORY_2026-09-26.md
未修改  SECURITY_LAB_STATUS / ARCHITECTURE / TEST_MATRIX / README
        （本次是"规划固化"，**不是**文档重同步 —— 后者是 NQ-18）

安全边界：本文为纯文档，未接触真实 CAN / ECU / 车辆，未修改系统配置。
```
