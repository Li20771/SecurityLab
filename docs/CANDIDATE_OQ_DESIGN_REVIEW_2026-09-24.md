# Candidate OQ Design Review — 2026-09-24

> **性质**：设计评审（design review），**不是开发任务**。
> 本文件**不实现任何候选 OQ**，不修改生产代码 / 测试 / shell / 依赖，
> 不修改任何既有状态文档。
>
> **本文件不选择 OQ8**：不排名、不给优先级、不写"最值得做/最佳/推荐"。
> 只做工程属性分析，并列出**下一步必须回答的设计问题**。

---

## 1. Scope and Baseline

### 1.1 评审对象

`docs/MAIN_CAPABILITY_INVENTORY_2026-09-24.md`（PR #23，已合入）§14 提出的
**12 个候选 OQ**：

```
OQ-C1  Permission / Session Framework        OQ-C7  Stateful UDS Fuzz
OQ-C2  Gateway Security Policy               OQ-C8  DoIP
OQ-C3  0x27 经 Gateway 透传                   OQ-C9  真实 Flash 抽象
OQ-C4  DTC Fault Injection Model             OQ-C10 0x28 CommunicationControl
OQ-C5  suppressPosRspMsgIndicationBit 统一化  OQ-C11 0x19 其余 reportType 扩展
OQ-C6  Firmware transfer security / ResumeFile + integrity   OQ-C12 e2e 进 CI
```

### 1.2 基线

| 项 | 值 |
|----|----|
| 评审分支 | `audit/candidate-oq-design-review-20260924`（从 `origin/main` 创建） |
| main HEAD | `4449c57f1f42762c8b36dc932310102e3a89a347`（PR #23 merge） |
| **生产代码基线** | **`ae22a556924d89d27e1d084ebcc5a4b4c56534f2`**（OQ7 / PR #21）—— **未**因 PR #22（docs-only）与 PR #23（audit docs）而改变 |
| 评审方式 | 读**实际代码**为准；文档仅作线索 |

### 1.3 每个候选的分析模板（14 项）

`当前实际缺口` / `当前代码位置` / `需要修改哪些层` / `是否修改既有行为` /
`是否改变已有接口` / `是否改变 EcuState` / `是否影响已有测试` /
`是否依赖其它候选` / `能否单独 feature branch` / `是否适合小 PR` /
`是否需要 Design Review 冻结契约` / `主要风险` / `如何验证` / `NON-GOALS`

工程规模只使用四档：`局部` / `中型` / `架构级` / `大型`。

---

## 2. Method and Evidence Rules

1. **依赖关系从代码判定**，不采信任何先入为主的关系图（§4 中每条依赖都标注依据）。
2. **不确定即标 unknown**，不猜测；无法验证的环境假设一律写 "feasibility unknown"。
3. 涉及"将改变既有行为"的结论，均给出 **file:line 或实测输出**。
4. 本评审**不冻结任何契约**，只指出哪些契约必须先冻结（§13）。

---

## 3. Cross-Cutting Code Facts (used by several candidates)

这些事实在多个候选中反复出现，先集中列出：

| # | 事实 | 证据 |
|---|------|------|
| F1 | `state.session` 在协议层**只有 1 处读取判断**（属 `0x2E`） | `uds_server.py:758`；实测默认会话下 `0x31/0x34/0x35/0x14/0x19/0x85` 全部正响应 |
| F2 | `state.security_level` 只有 2 处读取判断 | `uds_server.py:567`（`0x22`，per-DID）、`:763`（`0x2E`） |
| F3 | 权限判定是 **per-DID（payload 相关）**，不是 per-SID | `required_security_level(did)`（`uds_server.py:424-429`）；实测同一状态下 `22 F1 87 → 33` 而 `22 F1 90 → 正响应` |
| F4 | `0x2E` 的 NRC 优先级是 **DID 检查先于会话检查** | 实测：默认会话下 `2E FF FF → 7f 2e 31`（不是 `7f`）；已知 DID 才 `7f 2e 7f` |
| F5 | 无统一 session/permission framework；无装饰器、无 registry 级前置检查 | 全仓库 `state.session` / `state.security_level` 消费者仅 F1/F2 所列 |
| F6 | `0x27` **不在静态 `SERVICES` 表**，由入口层每请求注入 | `uds_server.py:2130-2145`（表内无 0x27）、`ecu.py:352-395` |
| F7 | 0x27 的注入工厂**本身是 state 无关的**（`state` 是参数） | `uds_server._make_security_access_handler(now, security_config) -> (request, state) -> bytes`（`uds_server.py:2091-2119`） |
| F8 | `gateway.py` **已持有每个 ECU 的 `EcuState`** | `gateway.py:43` `from uds_server import EcuState`；`:85` `state: EcuState = field(default_factory=EcuState)` |
| F9 | OQ7 的 `Transport` Protocol 是 **CAN ID 寻址**的 | `gateway_runtime.py`：`receive(timeout) -> tuple[int, bytes]`、`send(can_id, payload)` |
| F10 | 每 case **全新 ECU 进程**，状态不跨 case | `isotp_fuzz.py:429-439`（`execute_case`：`ecu = EcuProcess()`） |
| F11 | `case_schema.SCHEMA_VERSION = 1` 硬校验；**但未知键被容忍** | `case_schema.py:43`、`:290-293`（版本必须相等）、`:343` `normalized = dict(case)`（保留额外键） |
| F12 | 传输路径**零完整性校验** | 对 `uds_server.py`/`transfer_database.py`/`file_database.py` 检索 `crc32/sha256/md5/hmac/signature` = 0 匹配 |
| F13 | `uds_server.py`（2195 行）是**协议层唯一实现文件**，因此多数协议类候选都会改同一个文件 | `labs/vehicle/uds-basic/ecu/` 下 12 个模块，服务 handler 与 `EcuState` 全在 `uds_server.py` —— 这是"并行开发冲突"而非"语义依赖"（见 §17） |

---

## 4. Candidate Dependency Graph

**结论：12 个候选之间**几乎没有硬依赖**；主要关系是"共享同一修改点（文件级冲突）"
而非"语义前置"。** 下图按代码判定：

```
                    ┌──────────────────────────────────────────────┐
                    │ 独立（无前置依赖）                             │
                    └──────────────────────────────────────────────┘

  C5  suppress bit 统一化 ──┐
  C10 0x28 Communication   │
  C11 0x19 reportType 扩展  ├──> 彼此独立；C10/C11/C5 均为"协议层局部"
  C6  ResumeFile+integrity │
  C9  backend abstraction  │
  C12 e2e 进 CI            │   （C12 只碰 .github/ + 脚本，与其它全部解耦）
                            │
                    ┌───────┴──────────────────────────────────────┐
                    │ 条件依赖（取决于设计选择，不是代码强制）        │
                    └──────────────────────────────────────────────┘

  C4 DTC fault injection ──(条件)──> C1  ：若做成"协议能力"，需要授权语义
                                            ；若只做进程内 seam，则不需要
  C2 Gateway policy      ──(条件)──> C1  ：读 state 不需要 C1（F8 已可读）；
                                            但"策略语义与协议层一致"需要 C1
  C3 0x27 经 gateway      ──(可选)──> 共享 F7 的"注入组合只定义一处"决策

                    ┌──────────────────────────────────────────────┐
                    │ 文件级冲突风险（非语义依赖）                   │
                    └──────────────────────────────────────────────┘

  C1 ──╳── C5    两者都要改 dispatcher（handle_uds_request）→ 同点冲突
  C1 ──╳── C4    两者都可能动 registry / EcuState

                    ┌──────────────────────────────────────────────┐
                    │ 契约前置（必须先冻结，否则会破坏既有冻结物）    │
                    └──────────────────────────────────────────────┘

  C7 Stateful fuzz ──> OQ3 冻结契约（SCHEMA_VERSION=1 / frames[] 单请求语义 /
                        v1 golden 947 / replay 必需键集）
  C12 e2e 进 CI    ──> runner feasibility（未知）
```

**与直觉相反、需特别指出的四条**（均由代码判定）：

1. **C2 不依赖 C1**：`gateway.py` 已持有 `EcuState`（F8），因此网关策略层**今天就能**
   读到 session/security；依赖关系只存在于"策略语义要不要与协议层对齐"这一层，
   不存在于"能不能读到"这一层。
2. **但 C2 的「授权维度」依赖 C3**（**由代码判定，非直觉**）：网关路径用的是
   `uds_server` **静态** `SERVICES` 表（不含 `0x27`），而 `0x27` 是唯一能提升
   `security_level` 的服务 → 在 `0x27` 经网关可用之前，网关侧任何"基于安全等级的
   授权"**无法端到端行使**，只能靠测试直接改写 state。
   → 依赖边：`C2(授权维度) ──> C3`。
3. **C6 与 C9 互相独立**：ResumeFile 只需在 `EcuState` 内保存续传状态（现有 in-memory
   模型即可承载），**不需要**后端抽象；完整性校验同样可以只作用于内存缓冲。
   把二者捆在一起是**概念混同**而非代码约束（见 §10.1）。
4. **C8 无法直接复用 OQ7 的 `Transport` seam**：该 Protocol 是 CAN ID 寻址的（F9），
   DoIP 是 IP 寻址的诊断传输 —— 需要**新的**传输抽象或改变该 Protocol 的形状。

---

## 5. C1 — Permission / Session Framework（重点）

### 5.1 核心问题与结论

> **当前 SecurityLab 是否可以增加统一的 service access policy，
> 而不改变现有服务 handler 的业务逻辑？**

**结论：可以，但"不改变现有行为"这一条存在两处硬约束，必须先决策，不能默认无痛。**
约束不是来自 G1 的字段，而是来自**已有的 NRC 优先级**（F4）与**payload 相关的权限判定**（F3）。

### 5.2 14 项分析

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | 无统一策略层：`state.session` 仅 1 处读取（F1）、`security_level` 仅 2 处（F2）；无装饰器/无 registry 前置检查（F5）。会话目前实际只影响 `0x2E` 一个服务 |
| 2 当前代码位置 | `uds_server.handle_uds_request()`（`uds_server.py:2148-2183`）是唯一 dispatcher；`SERVICES`（`:2130-2145`）是纯 SID→handler 映射，**无 metadata**；权限概念现仅 `required_security_level(did)`（`:424-429`） |
| 3 需要修改哪些层 | 协议层 dispatcher + registry 结构（若选方案 B）；**不需要**改 transport / gateway / `ecu.py` 入口 |
| 4 是否修改既有行为 | **取决于设计**，且有两处**高风险点**（见 §5.3）：<br>① NRC 优先级：`2E <未知DID>` 在默认会话下当前是 `0x31`，若策略前置于 handler 会变成 `0x7F`（F4，**实测确认**）<br>② 权限粒度：`0x22` 的判定是 per-DID（F3），**SID 级 metadata 无法表达** |
| 5 是否改变已有接口 | 方案 A：不改公开接口（新增内部策略函数）。方案 B：改变 `SERVICES` 的**值类型**（handler → spec），是**公开结构变更**，会影响 `ecu.py:386-389` 的合并方式与 `handle_uds_request(services=...)` 的语义 |
| 6 是否改变 EcuState | **本候选自身不需要**新增字段（策略读取现有 `session`/`security_level`）。若引入新会话（如 programming 门控）才可能要字段 —— 属设计决策 |
| 7 是否影响已有测试 | 见 §5.4。**关键**：现有测试**并未固化** F4 的优先级，因此"改动不被测试发现"本身是风险 |
| 8 是否依赖其它候选 | **无硬依赖**。C4（若做成协议能力）与 C2（若要策略语义一致）会**反向依赖**本候选 |
| 9 能否单独 feature branch | 能（改协议层，边界清晰） |
| 10 是否适合小 PR | **不适合**：需先冻结策略模型与 NRC 优先级契约，再迁移；属**架构级** |
| 11 是否需要 Design Review 冻结契约 | **需要**，至少冻结三件：策略在 dispatcher 的**执行位置**、**NRC 优先级**（DID 检查 vs 会话检查）、**payload 相关策略**（per-DID）如何表达 |
| 12 主要风险 | ① 静默改变 NRC 优先级（现有测试**抓不到**，见 §5.4）；② 把"权限"抽象成 SID 级后，`0x22` 的 per-DID 语义被迫退化；③ 与 C5 抢同一个 dispatcher 修改点 |
| 13 如何验证 | 对**每个服务 × 每个会话 × 每个安全等级**建立期望 NRC 矩阵（现有行为作为 golden），迁移前后逐格对比；对 F4 补一条**新的**优先级断言 |
| 14 NON-GOALS | 不实现新的诊断服务；不引入真实密钥体系；不改 `0x27` 算法；不改 gateway 层；不做真实 ECU 权限模型 |

### 5.3 两种架构方案（**只比较，不选择**）

#### 方案 A — dispatcher 前置策略

```
request → parse SID(/subfunction) → access policy → handler
```

- 形态：`handle_uds_request()` 内先查一张策略表（SID → 允许的会话 / 所需安全等级），
  再调用 handler。
- **优点**：改动集中在一个函数；handler 不动；registry 结构不变。
- **硬约束**：
  - 对 `0x2E` 会在**未知 DID 检查之前**插入会话检查 → `2E FF FF` 默认会话由
    `0x31` 变 `0x7F`（F4，实测）。这是**可观测行为变更**。
  - 对 `0x22` 无法表达 per-DID（F3）：SID 级策略要么过松（放行全部读），
    要么过严（要求 LEVEL1 才能读任何 DID → 破坏公开 DID 读取）。
    → **必须**保留 handler 内的 per-DID 检查，形成"策略在两处"的状态。

#### 方案 B — registry metadata（ServiceSpec）

```
ServiceSpec(sid, allowed_sessions, required_security, handler, ...)
dispatcher → spec policy check → handler
```

- 形态：`SERVICES` 的 value 从 `Callable` 变为 `ServiceSpec`；策略字段成为声明式 metadata。
- **优点**：策略可枚举、可审计、可生成矩阵；新服务默认必须声明策略。
- **硬约束**：
  - **公开结构变更**：`SERVICES` 的类型变了 → `ecu.py:386-389` 的
    `{**uds_server.SERVICES, **_security_services()}` 合并逻辑、
    以及 `handle_uds_request(services=...)` 的契约都要跟着改。
  - 同样受 F4（优先级）与 F3（per-DID）约束；`0x22` 仍需 handler 内检查，
    除非把 `required_security` 升级为**可调用对象** `(request, state) -> int`
    —— 那样策略就**已经不是声明式 metadata**，而退化为回调（即方案 A 的变体）。
  - 迁移成本最高：需同时改 registry、dispatcher、`ecu.py`、以及所有
    依赖 `SERVICES` 结构的测试。
- **注意**：`0x27` 不在静态表中（F6），因此方案 B 还需说明
  **动态注入的服务如何声明策略**（否则 `0x27` 成为策略盲区）。

#### 与现有分层的兼容性（两方案共同）

- 两方案都**只动协议层**，不触碰 OQ7 已冻结的
  `gateway.py = pure routing` / `gateway_runtime.py = transport+lifecycle`。
- 两方案都**不影响** `0x27` 的注入边界（F6/F7）——除非把策略也做成注入项。

### 5.4 测试影响（关键：现有测试抓不到优先级变更）

| 相关测试 | 断言 | 若采用 dispatcher 前置策略 |
|----------|------|---------------------------|
| `test_services_extra_unit.py:124` `test_default_session_rejected` | 可写 DID + 默认会话 → `0x7F` | 仍通过 |
| `test_services_extra_unit.py:149` `test_unknown_did_out_of_range` | 未知 DID + **extended+unlocked** → `0x31` | 仍通过 |
| `test_services_extra_unit.py:130` `test_extended_locked_rejected` | extended+locked → `0x33` | 仍通过 |
| `test_multi_ecu_vcan.py` | `2E F1 02` 默认 → `0x7F`；extended+locked → `0x33` | 仍通过 |

**结论**：现有测试**没有任何一条**断言"默认会话 + 未知 DID"的组合
（F4 的实际优先级）。因此：

- 采用前置策略后，`2E FF FF`（默认会话）会**静默地**从 `0x31` 变为 `0x7F`；
- **全部 907 个用例仍会通过**（因为没人测它）。

→ 这正是 §5.2 第 11 项要求"**先冻结 NRC 优先级契约**"的原因：
必须先决定要保持 `0x31` 还是接受 `0x7F`，并补一条可执行的优先级断言。

**但 per-DID 语义反而是被测试保护的**（对比之下更显 F4 的缺口）：

| 契约 | 是否被测试固化 | 证据 |
|------|----------------|------|
| per-DID 权限粒度（F3） | **是**（e2e 5 例 + unit 2 例） | `test_e2e_vcan.py:1568`（默认会话被拒）、`:1576`（extended 未解锁被拒）、`:1586`（解锁可读）、`:1611`（整体拒绝）、`:1726`（锁定阻断）；`test_ecu_unit.py:893/899` |
| NRC 优先级（F4） | **否**（无任何用例） | 现有 0x2E 用例均在 extended(+unlocked) 下断言 `0x31`，或对**已知** DID 断言 `0x7F` |

即：**把权限从 per-DID 退化成 per-SID 会被测试抓到；把 `2E <未知DID>` 的
`0x31` 变成 `0x7F` 则不会。**

---

## 6. C2 — Gateway Security Policy（重点）

### 6.1 三个必答问题

**Q1：Gateway Runtime 当前只做什么？**
只做四件事：socket 生命周期、select 收发、调用 `DiagnosticGateway.route()`、
错误遏制与信号退出。**没有任何报文级策略**（无 ID 白/黑名单、无速率限制、
无授权）。`gateway_runtime.py` 的 `handle_request()` 在拿到 payload 后
**直接**交给 `route()`。

**Q2：policy 应该放在哪里？**
关键事实（F8）：`gateway.py` **已持有每个 ECU 的 `EcuState`**
（`gateway.py:43` 导入、`:85` 字段），因此**策略层今天就能读到 session/security**，
无需新管线。可能的落点有三处，各有代价：

| 落点 | 形态 | 代价 |
|------|------|------|
| `gateway.py`（协议层内） | 在 `route()` 内加策略判断 | **违反 OQ7 冻结语义**：`gateway.py` 会从"纯路由"变成"路由+策略" |
| `gateway_runtime.py`（transport 层内） | 在 `handle_request()` 里、`route()` 之前/之后插策略 | 违反 OQ7 的"transport 只管搬运"边界；且策略与 select 循环耦合 |
| **新策略层**（被 runtime 调用） | runtime 调 `route()` 前后调用独立 policy 对象 | 保持两层职责；但需定义调用点与短路语义（见下） |

**Q3：policy 是否应与 transport 分离？**
**是**——这是 OQ7 冻结分层（`gateway.py` = pure routing、
`gateway_runtime.py` = transport + lifecycle）的直接推论：
若把策略写进任何一层，都会使该层的既有描述失真。

### 6.1.1 关键代码事实（决定了本候选的真实难度）

| # | 事实 | 证据 | 含义 |
|---|------|------|------|
| G1 | 两个 gateway 模块中**零策略代码**：检索 `policy\|filter\|ratelimit\|allowlist\|denylist\|authoriz\|permission\|hook\|throttl` → **0 匹配** | `gateway.py`、`gateway_runtime.py` 全文 | 今天既无判定点，也**无配置载体** |
| G2 | **OQ7 的分层约束没有被任何自动化测试强制** | 全仓库唯一的"模块纯净度"用例是 `test_keygen_unit.py:264-265`（断言 `isotp`/`can` 不在 `sys.modules`）；**没有任何用例断言 `gateway.py` 的纯净性** | 分层只靠**文档 + 可检视的 import**；策略侵蚀 `gateway.py` **不会被 CI 抓到** |
| G3 | `gateway.py` 今天**没有任何时间依赖** | `gateway.py:39-43` 仅 import `collections.abc`/`dataclasses`/`uds_server` | 这正是它"可离线确定性单测"的来源；**限速必然引入时钟**，会破坏这一性质 |
| G4 | 仓库既有的时钟注入范式是**预先求值的 `now: float`**，不是 clock 函数 | `uds_server.py:2102-2110` 明文约束"`now` 必须是已经求值好的 float，不是 callable、不是时钟函数" | 限速若照此办理，需在**每请求**于某处求值 —— 与"纯函数"目标冲突 |
| G5 | **CAN 层没有可认证身份**：`Transport.receive()` 只返回 `(can_id, payload)` | `gateway_runtime.py:118-133` | "授权"只能键控于**可伪造的 CAN ID** 或 per-ECU 状态 |
| G6 | **"ID 过滤"在今天的设计下不可能意味着"先收未绑定 ID 再过滤"** | 内核 `CAN_ISOTP` 按 rxid 过滤，未绑定 ID **根本到不了用户态**（`gateway_runtime.py:31-33`、`:175-186`） | 若 C2 声称做 ID 过滤，必须说明它过滤的是**已绑定的那 4 个地址**，否则是空壳 |
| G7 | 策略拒绝对总线的表现与既有"未知 ID 丢弃"**完全一致** | `gateway.py:168-171` vs `gateway_runtime.py:456-461` | **策略 bug 可以完全不可观测** → §6.2 第 11 项的冻结项 |

### 6.2 14 项分析

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | 网关无任何报文级策略；`handle_request()` 拿到 payload 即 `route()` |
| 2 当前代码位置 | `gateway_runtime.py`（`GatewayRuntime.handle_request` 与 `_send`）、`gateway.py`（`DiagnosticGateway.route`） |
| 3 需要修改哪些层 | **新模块** + runtime 的调用点。理想情况下 `gateway.py` **不改** |
| 4 是否修改既有行为 | 取决于默认策略：若默认"全放行"，既有行为不变；若引入任何默认拒绝，则**改变**既有可达性 |
| 5 是否改变已有接口 | 若新增独立 policy 对象并作为可选参数注入 runtime → **加性**；若改 `route()` 签名 → 破坏 OQ7 冻结契约 |
| 6 是否改变 EcuState | 不需要（策略只读 `session`/`security_level`） |
| 7 是否影响已有测试 | `gateway_runtime.py` 相关：`test_gateway_runtime_unit.py`(53) + `test_gateway_runtime_vcan.py`(21)。若默认全放行 → 应全部保持通过；若引入默认拒绝 → 大面积失败 |
| 8 是否依赖其它候选 | **不硬依赖 C1**（F8 已可读 state）。仅"策略语义与协议层一致"这一点与 C1 相关 |
| 9 能否单独 feature branch | 能 |
| 10 是否适合小 PR | **不适合**：需先定义策略模型、默认值、短路语义；属**架构级** |
| 11 是否需要 Design Review 冻结契约 | **需要**：策略在 `route()` 前还是后？拒绝时是"不发帧"还是"发否定响应"（网关不是诊断实体，发 NRC 会假冒 ECU）？策略是否可读 UDS session/security（F8 使其可能，但语义上该不该）？ |
| 12 主要风险 | ① 破坏 OQ7 冻结分层；② **语义越权**：网关代替 ECU 回 NRC 会与"网关不收集响应"的既有语义冲突；③ 与 C3 争用 runtime 的装配点 |
| 13 如何验证 | 默认配置下 21+53 个既有用例全部不变；新增用例覆盖"拒绝时不产生任何帧"与"放行时逐字节不变" |
| 14 NON-GOALS | 不做真实防火墙；不做 ID 伪装/重放；不改 ISO-TP 传输；不动 `IsotpTransport`；不实现 DoIP 策略 |

---

## 7. C3 — 0x27 经 Gateway 透传（重点）

### 7.1 核心问题与结论

> **如何做到每个 ECU 独立 SecurityAccess，同时不把时间/安全配置逻辑
> 塞回 gateway protocol layer？**

**结论：注入工厂本身已经是对的接缝（F7）——它是 state 无关的。
真正要决策的是"注入的**组合**放在哪一层"，而不是"能不能做 per-ECU"。**

### 7.2 现状（为什么今天不通）

| 层 | 有没有 per-ECU state | 有没有 0x27 |
|----|---------------------|-------------|
| `gateway.py` / `VirtualEcu` | **有**（`EcuState` per ECU，F8） | **没有**（用静态表，F6） |
| `ecu.py` 入口 | **没有**（用模块级单例 `STATE`） | **有**（每请求注入，F6） |

即：**能力在 A 处、状态在 B 处**，二者今天不重合。
`_make_security_access_handler` 的签名是 `(now, security_config) -> (request, state) -> bytes`
（F7）——它**接受 state 作为参数**，所以 per-ECU 在**协议层已经原生支持**。
唯一的缺口是：谁在什么时机把 `now` / `security_config` 绑进去。

### 7.3 14 项分析

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | gateway 路径下 `27 01` → `7f 27 11`（实测）；`gateway.py:63-70` 的注释说明这是**既有分层边界**，不是缺陷 |
| 2 当前代码位置 | `gateway.py:63-75`（`_default_handler_for`）、`:85`（`state` 字段）、`:88-97`（`__post_init__` 绑定 handler）；`uds_server.py:2091-2119`（工厂）；`ecu.py:352-395`（注入组合） |
| 3 需要修改哪些层 | 仅 `gateway.py` 的 handler 装配点（+ 可能的工厂可见性）。**不需要**动 `IsotpTransport` / `GatewayRuntime` / ISO-TP / UDS handler 本体 |
| 4 是否修改既有行为 | gateway 路径下 `0x27` 由 `0x11` 变为可用 → **是新增能力，不是破坏**；但需确认**不改变**其它服务在 gateway 下的行为（静态表其余 14 项保持不变） |
| 5 是否改变已有接口 | 视方案：若给 `VirtualEcu` 增加可选参数（如 `handler` 已存在，无需新参数则零接口变更）；若把 `_make_security_access_handler` 改公开，则是一次 **私有→公开的加性重命名** |
| 6 是否改变 EcuState | **不需要**。`0x27` 的 per-ECU 状态（`pending_seed`/`failed_attempts`/`lockout_until`/`seed_counter`）**已在 `EcuState` 中**，且每个 `VirtualEcu` 各持一个（F8） |
| 7 是否影响已有测试 | `test_gateway_runtime_vcan.py::test_security_access_is_not_reachable_through_the_gateway_layer`（`:644`）**显式固化**了"当前不可达"这一边界 → 本候选若实施，该用例**必须更新**。`test_gateway_unit.py`(13) / `test_gateway_runtime_unit.py`(53) 中不涉及 0x27，无影响 |
| 8 是否依赖其它候选 | **无**。与 C1 的共享点只有"注入组合只定义一处"这一原则，不是代码依赖 |
| 9 能否单独 feature branch | 能 |
| 10 是否适合小 PR | **是**（若只做装配；见 §7.4 方案 X/Y） |
| 11 是否需要 Design Review 冻结契约 | **需要**，但范围小：冻结"注入组合的唯一定义处"与"gateway 是否获得时钟/配置的可见性" |
| 12 主要风险 | ① **语义重复**：若 gateway 自行拼 `{**SERVICES, **_security_services()}`，就出现**第二处**注入组合逻辑（正是盘点 G4-1 记录的问题）；② 每 ECU 是否共享同一 `SECURITY_CONFIG`（若共享，锁定策略是全局配置、状态仍 per-ECU —— 需明确） |
| 13 如何验证 | gateway 路径下 `27 01/02` 全流程解锁 + 未解锁 `0x33`；**并在三个 ECU 上分别验证**失败计数/锁定**互不影响**（跨 ECU 隔离）；再加一条"其它 14 个服务行为不变"的回归 |
| 14 NON-GOALS | 不新增安全等级（Level 2）；不改 seed/key 算法；不改锁定时长语义；**不把时间/配置逻辑写进 `gateway.py`**；不动 `IsotpTransport` |

### 7.4 可选做法（**只列举，不选择**）

- **方案 X — 组合上移到 runtime（保持 `gateway.py` 纯净）**
  `gateway_runtime.py`（已 import `ecu`，见 F 系列）在装配 `VirtualEcu` 时注入一个
  "带 0x27 的 handler"。`gateway.py` 继续只认 `(payload) -> bytes`。
  → `gateway.py` 不获得时钟/配置知识；但注入组合的定义处仍需与 `ecu.py` 统一。

- **方案 Y — 在 `uds_server` 暴露唯一的组合函数**
  把"静态表 + 0x27 注入"的组合收进**一个**可复用函数（如
  `uds_server.build_service_table(now, config)`），`ecu.py` 与 gateway 装配**都调它**。
  → 注入语义**只定义一处**（直接消解 G4-1）；`gateway.py` 仍不碰时钟，
  由装配方（runtime 或测试）传入。

两方案的共同前提：**必须先决定 `now` 的来源**（进程级 `time.monotonic` 即可，
但测试要能 patch —— 现有测试用 `mock.patch.object(ecu, "_now", ...)`，
若装配移到 runtime，patch 点会改变 → 属需冻结的契约之一）。

---

## 8. C4 — DTC Fault Injection Model

### 8.0 关键补充事实

| # | 事实 | 证据 |
|---|------|------|
| H1 | **仓库把"不是协议能力"写成了设计声明** | `dtc_runtime.py:38-39`："本模块**不新增任何 UDS 服务**：fault generation 只经 Python API / test seam 完成，**不把它做成协议能力**。" → 选协议载体 = **刻意推翻这句话**，不是顺手扩展 |
| H2 | `dtc_runtime` 的**生产**调用点只有 `clear_dtc_status`（属 `0x14`） | `uds_server.py:896`、`:905`；`:50` 是唯一的 `import dtc_runtime` |
| H3 | `set_dtc_status` 的 **28 个调用点全部在 `test_dtc_unit.py`** | 该文件 1070–1499 行区间 |
| H4 | 有一条 e2e docstring **明确声明该转换在总线上不可达** | `test_dtc_vcan.py:464-470`："……本项目**刻意不提供**'触发故障'的……" → 若加协议载体，**该 docstring 变成假的** |
| H5 | **没有任何测试以可执行方式断言"没有 UDS 服务置位 status"** | 该不变量只存在于**散文**中 → 加协议载体**不会因为漏测而失败**，只会让文档变假 |
| H6 | 至少 **5 份文档**陈述该不变量 | `SECURITY_LAB_STATUS.md:105`/`:155`、`ARCHITECTURE.md:167`/`:172`、`AUTONOMOUS_BUILD_STATE.md:69`、`uds-basic/README.md:738`、`test_dtc_vcan.py:464-470` |
| H7 | 注入的故障**在 ECU 实例生命周期内永久**（`0x11` 复位刻意保留 `dtc_records`） | `uds_server.py:372-373` → 长驻 e2e ECU 进程会被"挂住" |

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | **没有任何"故障产生"机制**。`dtc_runtime.set_dtc_status()` 的调用点**全部在测试文件**（`tests/test_dtc_unit.py`，约 30 处）；生产代码对 `dtc_runtime` 的使用**只有** `clear_dtc_status()`（`uds_server.py:896`、`:905`，属 `0x14`）。**没有任何 UDS 服务会置位 DTC status** |
| 2 当前代码位置 | `dtc_runtime.py:89-173`（API）、`uds_server.py:927-1010`（`0x85` 只写 `dtc_setting_enabled`，不产生故障）、`dtc_database.py:54-58`（静态出厂 status） |
| 3 需要修改哪些层 | **取决于"是什么"**：<br>• 若仅"把 seam 变得受控/可配置" → 只动 `dtc_runtime`（+ 可能测试）<br>• 若成为**协议能力** → 动 `uds_server.py`（新 handler 或扩展既有服务）+ 可能 `EcuState` + registry |
| 4 是否修改既有行为 | 只做 seam 化 → **否**（生产路径无调用者，改 API 不影响协议行为）。做成协议能力 → **是**（新增可达路径） |
| 5 是否改变已有接口 | `dtc_runtime.set_dtc_status()` 的签名/校验语义若变，会影响到 `test_dtc_unit.py`(115)；协议路径另算 |
| 6 是否改变 EcuState | 只做 seam → 不需要（`dtc_records`/`dtc_setting_enabled` 已存在）。做成协议能力 → 可能要新增状态（如注入来源/计数） |
| 7 是否影响已有测试 | `test_dtc_unit.py`(115)、`test_dtc_vcan.py`(21)、`test_dtc_clear_unit.py`(39)、`test_dtc_clear_vcan.py`(11) —— 任何 `dtc_runtime` 校验语义变化都会触及 |
| 8 是否依赖其它候选 | **条件依赖 C1**：若做成协议能力，"谁有权注入故障"就是一个授权问题 → 需要 C1 的策略层（否则会新增一个**无门控**的强能力服务，与 `0x2E` 有门控的现状不一致） |
| 9 能否单独 feature branch | 能 |
| 10 是否适合小 PR | seam 化 → **局部**（可小 PR）；协议能力 → **中型**且带授权问题 |
| 11 是否需要 Design Review 冻结契约 | **需要**：这是"进程内测试接缝"还是"协议能力"？两者的验收标准完全不同 |
| 12 主要风险 | **越界成"新增 UDS 服务"**（盘点已把它列为风险）。若新增服务，需按项目规则"六件套齐备"（request/positive/NRC/state/unit/e2e），且必须回答授权门控 |
| 13 如何验证 | seam 化：单测覆盖受控边界（谁可调、什么范围）+ 断言生产代码零调用点不变。协议化：unit + e2e + NRC + 状态 + **授权**全链路 |
| 14 NON-GOALS | **不在本评审中选定任何协议载体（不预设新增哪个 SID）**；不做真实故障注入到硬件；不改 `0x19`/`0x14` 语义；不改 severity metadata |

> **概念边界（必须保持）**：`dtc_runtime.set_dtc_status()` 是**进程内 API / test seam**，
> **不是** UDS fault-injection service。任何把它描述成"已有故障注入能力"的说法都不准确。

---

## 9. C5 — suppressPosRspMsgIndicationBit 统一化（重点）

### 9.1 核心问题与结论

> **是否可以做成一个独立的小 OQ，而不碰其它服务业务逻辑？**

**结论：可以做成独立候选，但"小 PR"只在一种切法下成立——
即把它定义为"dispatcher 的统一请求预处理"，而不是"逐个服务补 suppress 分支"。**
后者会逐个改动服务 handler 的返回路径（业务逻辑），前者只动一处。

### 9.2 逐服务事实表（**统一化的第一道坎**）

| SID | 服务 | 有子功能字节？ | byte[1] 的真实含义 | 处理 suppress？ | 今天发 `X 80` 的结果 |
|-----|------|----------------|--------------------|-----------------|----------------------|
| `0x10` | SessionControl | 是 | `session_type`（`:452`） | **否** | `7F 10 12` |
| `0x11` | ECUReset | 是 | `reset_type`（`:507`） | **否（明文未实现）** | `7F 11 12` |
| `0x14` | ClearDiagnosticInformation | **否** | `groupOfDTC`（3 字节，`:889`） | 否（n/a） | `7F 14 31` |
| `0x19` | ReadDTCInformation | 是 | `reportType`（`:1104`） | **否（明文未实现）** | `7F 19 12` |
| `0x22` | ReadDataByIdentifier | **否** | DID 列表（`:552`） | 否（n/a） | `7F 22 31` |
| `0x27` | SecurityAccess | 是 | `securityAccessType`（`:623`） | **否** | `7F 27 12` |
| `0x2E` | WriteDataByIdentifier | **否** | DID + data（`:750`） | 否（n/a） | `7F 2E 31` |
| `0x31` | RoutineControl | 是 | `sub_function`（`:1306`） | **否** | `7F 31 12` |
| `0x34` | RequestDownload | **否** | **DFI（数据字段）**（`:1411`） | 否（n/a） | `7F 34 31` |
| `0x35` | RequestUpload | **否** | **DFI（数据字段）**（`:1468`） | 否（n/a） | `7F 35 31` |
| `0x36` | TransferData | **否** | **BSC（数据字段）**（`:1585/1634`） | 否（n/a） | `7F 36 73`/`24`/`13` |
| `0x37` | RequestTransferExit | **否（根本没有 byte[1]）** | —— | 否（n/a） | `7F 37 13` |
| `0x38` | RequestFileTransfer | **否** | **`modeOfOperation`**（`:1895`） | **否（明文"绝不能"）** | `7F 38 31` |
| `0x3E` | TesterPresent | 是 | `subFunction`（`:816`） | **是（唯一）** | **不发帧（`b""`）** |
| `0x85` | ControlDTCSetting | 是 | `DTCSettingType`（`:966`） | **否（明文未实现）** | `7F 85 12` |

**关键结论（决定了"统一化"绝不是机械套用）**：

1. **只有 8 个服务真正有子功能字节**（`0x10/0x11/0x19/0x27/0x31/0x3E/0x85`，加
   `0x38` 的"控制字节但不是子功能"）。其余 **7 个服务**（`0x14/0x22/0x2E/0x34/0x35/
   0x36/0x37`）**根本没有子功能字节**。
2. **对 `0x34/0x35/0x36` 把最高位置 1 会"破坏数据"而不是"抑制响应"** —— 它们的
   byte[1] 是 DFI / BSC 等**数据字段**。这是一个**真实的踩坑点**。
3. **`0x38` 被明文排除**：`file_database.py:55-56` 写明"`modeOfOperation` **不是**
   UDS 子功能字节，非法 mode → `NRC 0x31`"；fuzz 侧还有专门的守护用例
   （`test_uds_fuzz_offline.py:516-518`："0x38 的控制字节是 **mode**，绝不能获得 suppress"）。
4. **fuzz 层已存在同源常量**：`uds_corpus.py:106` `SUPPRESS_SIDS = (0x3E,)`、
   `:94-100` `CONTROL_BYTE_SIDS`，注释里逐项说明各服务控制字节性质不同。

### 9.3 与既有测试/冻结契约的冲突（**本候选最大的隐藏成本**）

| 冲突 | 证据 | 后果 |
|------|------|------|
| **6 个 SID 的行为变更今天零测试覆盖** | 全仓库唯一以 `0x80` 作控制字节的用例是 `0x3E, 0x80`；未支持值枚举**刻意避开** `0x80`（`test_dtc_unit.py:355`、`:981-983`） | 改动**即使改错也会全绿** |
| **扩展 `SUPPRESS_SIDS` 会撞 OQ3 冻结 golden** | `test_uds_fuzz_offline.py:1621`（`v1_cases == 947`）、`:1632`（`V1_GOLDEN_DIGEST`，常量在 `:1593-1595`） | 受影响 seed 各自多出一条 `ctl=suppress` spec → **947 与 digest 同时失配**，除非**刻意重冻** OQ3 golden |
| `0x3E` 的既有语义必须逐字节不变 | `test_services_extra_unit.py:64-69`（`b""`）、`:77-82`（`3E 81` → `7F 3E 12`）；**总线级**：`test_write_did_vcan.py:147-150`（`assertIsNone`，真实链路静默） | 任何回归都会在最严格的一层被发现 |

### 9.3 14 项分析

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | 抑制响应只在 `0x3E` 生效；三个服务明文声明未实现；其余未处理也未声明 |
| 2 当前代码位置 | `uds_server.py:790-830`（0x3E 实现）、`:493`/`:958`/`:1097`（声明）、`:2148-2183`（dispatcher） |
| 3 需要修改哪些层 | 仅协议层 dispatcher（若走统一预处理）；若逐服务补，则改多个 handler |
| 4 是否修改既有行为 | **关键**：`0x3E` 的既有行为（`3E 80` → 返回 `b""` → 不发帧）**必须逐字节不变**；其它服务从"不抑制"变为"抑制"是**新增行为**（正响应消失）——是行为变更，需逐服务确认可接受 |
| 5 是否改变已有接口 | dispatcher 内部；不改 handler 签名（若走预处理） |
| 6 是否改变 EcuState | **不需要** |
| 7 是否影响已有测试 | `test_services_extra_unit.py:64` `test_suppress_bit_returns_empty`、`:77` `test_suppress_bit_with_nonzero_subfunction_still_rejected`；`test_write_did_vcan.py`(8) 含"3E 80 总线静默"；`test_e2e_vcan.py` 亦有 3E 80 用例。统一化**不得**破坏这些 |
| 8 是否依赖其它候选 | **无**。但与 C1 争用 dispatcher 同一修改点（F13/文件级冲突） |
| 9 能否单独 feature branch | 能 |
| 10 是否适合小 PR | **统一预处理 → 是（局部/中型）**；逐服务补 → 否（多服务业务逻辑改动） |
| 11 是否需要 Design Review 冻结契约 | **需要**：哪些服务**有资格**被 suppress（子功能语义表）？抑制是否只作用于**正响应**（否定响应是否也抑制）？功能寻址下 suppress 与"网关不收集响应"如何交互？空响应与抑制响应的区分（两者都返回 `b""`，但语义不同） |
| 12 主要风险 | ① 把 suppress 规则误套到 `modeOfOperation`（`0x38`）或无语义子功能的服务上 → 产生**静默吞掉正响应**的 bug；② `b""` 已是"抑制"的既有编码，统一化后"抑制"与"服务本身无响应"在返回码上**不可区分** → 需明确是否需要可区分的内部表示 |
| 13 如何验证 | ① `3E 80` 的既有断言全部不变；② 对每个"有资格"的服务补 `xx 8n` → 不发帧 的 e2e（总线静默）；③ 对 `0x38` 等**无资格**服务断言 suppress 位不生效（或按冻结契约处理） |
| 14 NON-GOALS | 不改 `0x3E` 既有语义；不实现新的响应类型；不改 ISO-TP 层；不做 `0x78` ResponsePending；不引入功能寻址的响应抑制（除非契约明确） |

### 9.4 最小可行切法（**只列举，不选择**）

- **切法 1 — dispatcher 预处理**：解析出（SID, subfunction）→ 查"该服务是否有 suppress 语义"
  → 若置位则**丢弃正响应**。要求：一张明确的"有资格服务"表 + 对 `b""` 语义的冻结。
- **切法 2 — 只补明文声明的那三个（`0x11`/`0x19`/`0x85`）**：范围最小、改动最局部，
  但会留下"其它服务仍未统一"的状态，与候选标题"统一化"不符。
- **切法 3 — 不做统一化，只补文档**：把"哪些服务支持 suppress"写成明确的不支持清单。
  （工程上最省，但等于放弃该候选的能力目标。）

---

## 10. C6 — Firmware transfer security / ResumeFile + integrity（重点）

### 10.1 四个概念必须分开（本节强制的边界）

| 概念 | 类别 | 当前状态 |
|------|------|----------|
| **ResumeFile**（`0x38` 的 `modeOfOperation = 0x06`） | **协议能力** | 未实现（已实现 mode `0x01`–`0x05`，`file_database.py:59-78`） |
| **完整性校验**（摘要/CRC/HMAC） | **安全属性**（作用于数据） | **完全不存在**（F12：传输路径零 `crc32/sha256/md5/hmac/signature`） |
| **后端抽象**（可插拔存储） | **架构能力** | 不存在（见 C9） |
| **真实 Flash**（设备/环境） | **环境能力** | 不存在（无 `/dev/mtd` 等） |

**它们之间没有必然的依赖**：ResumeFile 只需在 `EcuState` 内保存续传状态
（现有 in-memory 模型即可）；完整性校验可只作用于内存缓冲；
两者都**不需要**后端抽象或真实 Flash。把它们捆成一个候选是概念混同。

### 10.1.1 关键补充事实（含"会打破哪些冻结测试"）

| # | 事实 | 证据 |
|---|------|------|
| H1 | **实现 ResumeFile 会打破 3 个既有（冻结）测试** | `test_file_transfer_unit.py:1016-1024`（`0x06` 子例）、`:295-297`（`SUPPORTED_FILE_MODES == frozenset({1,2,3,4,5})`）、`test_file_transfer_vcan.py:711-719`（`0x06` 子例） |
| H2 | ResumeFile 所需的**每一项状态今天都被复位清空** | `EcuState` 的 `download_*`(6) / `upload_*`(5) / `file_mode` / `file_path` 全部由 `_reset_volatile_state` 清除（`uds_server.py:391-407`）；**唯一幸存者是 `files`**（`:408-410`），但它的文档语义是"已提交数据"，拿它存续传内容会与"中止不污染 `files`"的既有断言冲突 |
| H3 | **上行方向没有 buffer** | 上行内容由 `UPLOAD_SOURCE` 纯函数**即时重算**（`transfer_database.py:81`、`:84-103`；`uds_server.py:1618-1619`）→ 若做完整性，**ECU 只能对自己生成的数据做摘要**，上下行**不对称** |
| H4 | 若完整性校验**能拒绝**，则 `0x37` 的 `77` 期望会被大面积触及 | `test_file_transfer_unit.py` 约 5 处、`test_file_transfer_vcan.py` 约 9 处、`test_transfer_*`/`test_upload_*` 各若干 —— 良性路径**必须仍然返回 `77`** |
| H5 | 0x38 的 `mode` 非法值**必须映射到 `NRC 0x31` 而非 `0x12`** | `file_database.py:55-56`、`uds_server.py:1898-1902` |

**核心语义问题（代码无法回答，必须 design review 回答）**：
> 在一个**纯内存、单进程、无连接事件**的仿真里，"传输被中断"到底是什么？
> 没有"客户端断开"事件，`0x11` 复位又会清空一切 —— 若不改变复位契约，
> ResumeFile 能"续"的场景需要被**明确定义**，否则就是空壳。

### 10.2 14 项分析

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | ① `0x38` 无 `modeOfOperation = 0x06`（ResumeFile）；② 传输数据无任何完整性校验；③ `0x34`–`0x37` 的传输状态**无"中断后可续"语义** |
| 2 当前代码位置 | `file_database.py:59-78`（mode 常量）、`uds_server.py:1788-1794`（`SUPPORTED_FILE_MODES`）、`:1851+`（`0x38` handler）、`:1558-1671`（`0x36`）、`:1672+`（`0x37`）；状态字段 `EcuState.download_*`(6) / `upload_*`(5) / `file_*`(3) |
| 3 需要修改哪些层 | 协议层（新增 mode + 续传状态机）+ 数据校验点（若做 integrity）。**不需要** transport / gateway |
| 4 是否修改既有行为 | **否**（新增 mode 不改 `0x01`–`0x05` 语义）；但若引入 integrity，则**既有成功路径会新增一个可能失败的门** → 是行为变更，需明确默认策略 |
| 5 是否改变已有接口 | `SUPPORTED_FILE_MODES` 集合扩大（是**契约集合**的变化）；`file_database` 新增常量。`handle_uds_request` 签名不变 |
| 6 是否改变 EcuState | **很可能需要**：续传需要保存"已接收到哪里/剩余长度/原请求参数"——现有 `download_expected_block`/`download_buffer` 在 `0x11` 复位时被清空，所以续传语义需明确"跨复位"还是"跨中断" |
| 7 是否影响已有测试 | `test_file_transfer_unit.py`(174)、`test_file_transfer_vcan.py`(41)、`test_transfer_unit.py`(40)、`test_upload_unit.py`(47) —— 集合扩大与新增 NRC 分支都需断言；若引入 integrity 门，影响面更大 |
| 8 是否依赖其它候选 | **ResumeFile 不依赖 C9**；**integrity 是独立属性**。若选"完整固件体系"路线才可能与 C9 合并 |
| 9 能否单独 feature branch | 能（且建议 ResumeFile 与 integrity **分成两次**，理由见 §10.3） |
| 10 是否适合小 PR | ResumeFile → **中型**；integrity → **中型**（需先定义"校验什么、算法、失败如何报告"） |
| 11 是否需要 Design Review 冻结契约 | **需要**：ResumeFile 保存什么状态、跨 `0x11` 复位如何处理、与 `0x36` BSC 块序如何交互；integrity 的算法、覆盖范围、失败时的 NRC/行为 |
| 12 主要风险 | ① **空壳声明**：只加常量不做状态机 = 声明了能力但不通；② ResumeFile 与现有"`0x11` 复位清 transfer 状态"的既有语义**直接冲突**（续传若跨复位，就违反了 `_reset_volatile_state` 的既有划分）；③ integrity 若默认开启会改变既有成功路径 |
| 13 如何验证 | ResumeFile：unit（状态机）+ e2e（中断→续传→完成，逐字节）；integrity：单元覆盖正常/篡改/长度不符，并断言**默认配置下既有用例全绿** |
| 14 NON-GOALS | 不做真实文件系统；不做真实 Flash；不做签名信任链/secure boot；不做压缩/加密（`0x38` 仍仅 DFI `00`）；**不把 ResumeFile 写成独立 SID**（它是 `0x38` 的 mode） |

### 10.3 为什么建议 ResumeFile 与 integrity 分开

1. **验收标准不同**：ResumeFile 的验收是"状态机 + 逐字节续传正确"；
   integrity 的验收是"篡改可检出 + 默认路径不变"。
2. **风险类型不同**：前者与**既有复位语义**冲突；后者与**既有成功路径**冲突。
3. **依赖不同**：integrity 与"校验值从哪来"有关（可能涉及 C9 或配置），ResumeFile 无关。
4. 合并成一个候选会造成"改一处、两套验收"，不利于小 PR 与回滚。

---

## 11. C7 — Stateful UDS Fuzz（重点）

### 11.1 核心问题与结论

> **Stateful fuzz 是否可以作为"新增测试维度"，还是会破坏 OQ3 v1 schema？**

**结论：不能作为"新增一个变异维度"处理。它至少要改三件事——case 语义、
执行模型、replay 契约——因此必须先做契约决策（version bump 或显式 v1 扩展）。**

理由（全部来自代码）：

1. 现有 4 个维度（`uds_len/uds_sid/uds_sub/uds_payload`）是**单请求内**的维度；
   stateful 是**跨请求**的，量纲不同（不是第 5 个维度的同类物）。
2. **一个 case = 一个请求**：`frames[]` 是"该单请求的帧序列"，
   由 `validate_frames()` 校验（`case_schema.py:322`），
   并被 OQ3 的 framing 契约与 v1 golden 依赖。
3. **执行模型是每 case 全新 ECU 进程**（F10）→ 跨请求状态**物理上无法保留**，
   `execute_case` 必须改。
4. **`replay.py` 要求固定键集**并原样重放 `frames[]` → 序列需要 replay 支持。
5. `SCHEMA_VERSION = 1` 是**硬校验**（F11，`case_schema.py:290-293`）。

### 11.2 OQ3 冻结物（本候选的"不能碰"清单）

| 冻结物 | 值 / 出处 |
|--------|-----------|
| SF 上限 | 7（`uds_corpus.py:63`） |
| MF 范围 | 8–128（`uds_corpus.py:72`） |
| 每 case 最大帧数 | 19（`uds_corpus.py:80`） |
| 变异算子 | M1–M7，**恰好七个**，全在 `uds_len` 维（`uds_mutators.py:123-131`） |
| 维度集 | `(uds_len, uds_sid, uds_sub, uds_payload)`，**不得新增第五维**（`uds_mutators.py:84`） |
| v1 golden | 947 case + 2 个冻结 digest（`scripts/repro/verify_uds_fuzz_smoke.sh`） |
| schema | `SCHEMA_VERSION = 1`（`case_schema.py:43`） |
| oracle | 三态 `AVAILABLE/UNAVAILABLE/ERROR`（`case_schema.py:98-118`） |
| 单请求语义 | 每 case 一个请求，全新 ECU（F10） |

### 11.3 14 项分析

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | 跨请求序列（如 `10 03 → 27 01 → 27 02 → 2E`）**完全没有覆盖**；多处明文 `OUT_OF_SCOPE`（`uds_mutators.py:8`、`uds_corpus.py:15-19/408`） |
| 2 当前代码位置 | `uds_corpus.py`（case 构造）、`uds_mutators.py`（维度/算子）、`case_schema.py`（契约）、`isotp_fuzz.py:429`（执行器）、`replay.py`（重放） |
| 3 需要修改哪些层 | **测试架构的三层全部**：case 表示（schema）、生成/变异、执行与重放；**生产代码零改动** |
| 4 是否修改既有行为 | **不修改生产行为**（fuzz 不改 ECU）。但会修改**冻结的测试契约**：若不 bump 版本，`frames[]` 将出现二义（"单请求"vs"序列"） |
| 5 是否改变已有接口 | **是**：`case_schema.validate_case` 的接受集合、`validate_frames` 语义、`replay.py` 的必需键集。都是**对外（对工具链）的契约** |
| 6 是否改变 EcuState | **不需要**（测试侧候选） |
| 7 是否影响已有测试 | `can-tools/tests/test_uds_fuzz_offline.py`(120) 与 `test_fuzz_offline.py`(99) 直接断言 schema/oracle/维度/算子集；`verify_uds_fuzz_smoke.sh` 断言 `(7,128,19)`、M1–M7、**v1 golden 947 + 2 digest**。任何 schema 变更都会触及这些断言 |
| 8 是否依赖其它候选 | **无**。但被 C6/C4 之类的"需要序列才能 fuzz 的能力"反向需要 |
| 9 能否单独 feature branch | 能（纯 `can-tools/` 范围） |
| 10 是否适合小 PR | **不适合**：属**架构级**测试改造 |
| 11 是否需要 Design Review 冻结契约 | **必须**。至少冻结：是否 `SCHEMA_VERSION → 2`？v1 生成路径是否逐字节冻结不动？sequence 的表示（新字段 vs 新 case 类型）？多请求场景下 oracle/分类语义？golden 如何并存（v1 保留 + v2 新增）？ |
| 12 主要风险 | ① **破坏 v1 golden**（若顺手改了 v1 生成路径，947 case 与 2 个 digest 立刻失配）；② 执行器改为"持久 ECU"后，**已有的"每 case 全新 ECU"隔离保证**会变化，可能导致既有分类结果不稳定；③ `replay.py` 的 `--repeat` 非确定性检测在序列下语义不同 |
| 13 如何验证 | ① 先证明 **v1 路径逐字节未变**（golden digest 与 947 不变）；② 新增 v2/序列 case 能确定性生成、去重、序列化-反序列化往返一致；③ replay 对序列 case 可精确重放且 `issue(s)=0`；④ 跨请求场景的**恢复探测**语义需重新定义并断言 |
| 14 NON-GOALS | 不新增第五个**单请求**维度；不改 M1–M7；不改 SF/MF/帧数界限；不做 coverage-guided；不做 CAN 层 fuzz；不动生产 ECU |

### 11.4 可选的扩展路径（**只列举，不选择**）

- **路径 1 — `SCHEMA_VERSION = 2`**：新增序列 case 类型；`validate_case` 接受 1 与 2；
  v1 生成路径**一字不动**（golden 保真）。成本：契约、执行器、replay 三处都要版本感知。
- **路径 2 — v1 加性扩展**：利用 F11（未知键被容忍）新增如 `sequence` 字段。
  **风险高**：`frames[]` 出现二义，且"未知键被容忍"是**实现细节而非承诺**，
  用它承载语义等于把未文档化的宽容当成契约。
- **路径 3 — 独立工具**：完全不进 v1 schema，另立一个序列 fuzz 入口与自己的
  case 格式。契约风险最低，但会形成**第二套 case/oracle 体系**（重复实现风险）。

---

## 12. C8 — DoIP

### 12.1 与现有栈的关系（这是本候选的核心）

| 维度 | 现状（SocketCAN 栈） | DoIP 需要 |
|------|---------------------|-----------|
| 传输 | `CAN_ISOTP` socket on `vcan0` | TCP/UDP（IP 网络） |
| 地址模型 | **CAN ID**（11-bit，`0x7E0`/`0x7E8`/`0x7DF`） | 逻辑地址 / IP+port（**完全不同的寻址维度**） |
| OQ7 的 `Transport` Protocol（F9） | `receive() -> (can_id, payload)`、`send(can_id, payload)` | **无法直接复用**：该 Protocol 是 CAN ID 寻址的（F9），DoIP 没有 CAN ID |
| 网关职责 | `DiagnosticGateway.route(can_id, payload)` | 需新的路由维度；`route()` 签名本身是 CAN ID 型 |
| 测试层 | vcan0 + 虚拟 ECU + raw CAN 抓包 | 需新测试层（回环 socket / 虚拟网络），**vcan0 无用** |
| 环境依赖 | `can-isotp` / `python-can` / 内核 `CAN_ISOTP` | 网络栈即可，但需新的仿真对端 |

> **全仓库检索确认：不存在任何 DoIP / automotive Ethernet / UDP 诊断代码**
> （`doip` 仅命中声明其缺席的文档；`13400` 唯一出处是合成固件里的一行配置文本）。

### 12.2 14 项分析

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | 完全没有（0 代码） |
| 2 当前代码位置 | 无。新增目录（如 `labs/vehicle/doip-basic/`） |
| 3 需要修改哪些层 | **全新传输层 + 新地址模型 + 新测试层 + 新环境依赖**；与现有栈**平行**而非嵌入 |
| 4 是否修改既有行为 | **否**（纯新增） |
| 5 是否改变已有接口 | 若不复用 `Transport`（F9），则**零改动既有接口**；若强行复用，则要改其形状 → 破坏 OQ7 冻结 |
| 6 是否改变 EcuState | 不需要 |
| 7 是否影响已有测试 | 不影响（新目录、新测试） |
| 8 是否依赖其它候选 | **无**。与全部候选解耦 |
| 9 能否单独 feature branch | 能 |
| 10 是否适合小 PR | **不适合**：**大型**（全新协议栈） |
| 11 是否需要 Design Review 冻结契约 | **需要**：DoIP 的地址模型如何映射到现有 ECU 身份？是否要复用 `DiagnosticGateway` 的路由语义（那就需要一个非 CAN-ID 的路由维度）？虚拟对端如何仿真？ |
| 12 主要风险 | **范围失控**：DoIP 含发现/路由激活/诊断报文多类报文，容易无边界的"实现一个协议栈"；且**无法复用 vcan0 既有的 E2E 与 fuzz 体系**（要新建一整套验证设施） |
| 13 如何验证 | 新目录内自建：最小闭环（发现 + 路由激活 + 一条 UDS 请求/响应）+ 独立 repro 脚本；**不接入 vcan0 体系** |
| 14 NON-GOALS | 不做真实车辆以太网；不做 TLS/安全传输；不做完整 DoIP 全报文族；不改 SocketCAN 栈；**不改 OQ7 的 `Transport` Protocol** |

> 注：DoIP 的**发现报文**与**路由激活**是否需要，属 design review 应冻结的范围，
> 本评审不代为决定。

---

## 13. C9 — 真实 Flash 抽象

### 13.0 关键补充事实

| # | 事实 | 证据 | 含义 |
|---|------|------|------|
| H1 | 现有 sink **只有两个**，且都是进程内对象 | `EcuState.download_buffer`（`bytearray`，`uds_server.py:250`）、`EcuState.files`（`dict[str,bytes]`，`:307`） | 穷举检索确认**没有第三个 sink，也没有任何磁盘/设备通路** |
| H2 | **抽象必须定义在"提交语义"这一点上** | 唯一落地点是 `uds_server.py:1732-1733`（`0x37` 内） | 今天的"**只有**完整传输 + 正常 `0x37` 才提交"是内存设计的**直接后果**（由 `test_file_transfer_unit.py:20-22` 断言）；真实后端会首次引入**部分写入**，该性质必须**重建**（暂存+切换） |
| H3 | **`memoryAddress` 今天只是元数据** | `download_address`/`upload_address` 从不用于索引（buffer 纯追加 `:1661`；上行是 `sent` 的纯函数 `:1619`） | 抽象必须决定：地址成为**后端偏移（语义变更）**还是保持元数据 |
| H4 | **上限是协议可见量且被测试固化** | `MAX_DOWNLOAD_SIZE`（`transfer_database.py:61`）、`MAX_UPLOAD_SIZE`（`:77`）、`MAX_FILE_SIZE`（`file_database.py:88`）；断言于 `test_transfer_unit.py:110-111`、`test_upload_unit.py:149`、`test_file_transfer_unit.py:276-277` | 后端驱动的上限会与这些常量**冲突** |
| H5 | **注入式后端无法经网关路径抵达** | `gateway.py:63-75` 的工厂**只接收 `state`**，无注入参数 | 这是"抽象挂在 `state` 上"vs"经 `services=` 注入"的**代码级分界线**（后者是 `0x27` 先例） |
| H6 | 爆炸半径：**261 离线 + 75 vcan** | `test_transfer_unit.py`(40)+`test_upload_unit.py`(47)+`test_file_transfer_unit.py`(174)；vcan 18+16+41 | 其中 `test_file_transfer_unit.py` 有 **76 行**直接改写 `state.files` → 这些用例**预设了"存储归 state 所有"** |

### 13.1 14 项分析

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | `0x34`–`0x38` 全部落在内存：`0x36` → `state.download_buffer`（`bytearray`）→ `0x37` 时（仅 addFile/replaceFile）写入 `state.files[path]`（Python dict）。**不落盘、不持久**；plain `0x34` 在 `0x37` **不提交任何东西**（`uds_server.py:1731`） |
| 2 当前代码位置 | `transfer_database.py`、`file_database.py`、`uds_server.py`（`0x34`–`0x38` handler 与 `EcuState` 传输字段） |
| 3 需要修改哪些层 | 传输 handler 的**数据落点**（引入后端接口）+ 可能的配置/装配层 |
| 4 是否修改既有行为 | 若默认后端 = 现有内存实现 → **否**；若默认改为文件 → **是**（`files` 语义、`0x11` 复位保留语义都会变） |
| 5 是否改变已有接口 | 新增后端接口（加性）；`EcuState` 的 `download_buffer`/`files` 若要迁出，是**结构变更** |
| 6 是否改变 EcuState | **可能**：若后端外置，`download_buffer`/`files` 可能移出 `EcuState` → 影响 `_reset_volatile_state` 的"22 复位 / 5 保留"划分（`files` 是刻意保留的 5 个之一） |
| 7 是否影响已有测试 | `test_transfer_unit.py`(40)、`test_upload_unit.py`(47)、`test_file_transfer_unit.py`(174) + 对应 vcan e2e(18/16/41) —— 都会读到数据落点 |
| 8 是否依赖其它候选 | **无**（与 C6 独立，见 §10.1） |
| 9 能否单独 feature branch | 能 |
| 10 是否适合小 PR | **中型**（若只做"接口 + 现有内存实现作为默认后端"）；**架构级**（若要动 `EcuState` 的复位划分） |
| 11 是否需要 Design Review 冻结契约 | **需要**："真实"的判定标准（文件？块设备？仿真？）；后端接口边界（只存字节，还是也管地址/大小/提交语义？）；`files` 的"非易失替身"语义在真实后端下如何保持 |
| 12 主要风险 | ① **触碰复位语义**（`files` 刻意保留是既有设计，见 `_reset_volatile_state` 文档串）；② 若后端可写真实路径 → **安全边界风险**（必须在 vcan0/虚拟范围内，不得写真实设备） |
| 13 如何验证 | 后端契约单测（同一组用例跑两个后端 → 行为一致）；既有 174+41 用例在默认后端下**全绿** |
| 14 NON-GOALS | **不接触真实 Flash 设备**；不做真实刷写；不做签名/信任链；不改 `0x34`–`0x38` 的 wire 语义 |

---

## 14. C10 / C11 — 小型协议扩展

### 14.0 两个候选共同的"上游未知"

| 候选 | 仓库**没有**的东西 | 后果 |
|------|--------------------|------|
| C10 | **零个 `0x28` 子功能取值**（无 `ControlType`/`communicationType` 常量、名称或数值；`0x28` 的出现**全部是散文**或 fuzz 的 `UNIMPL_SIDS`） | 子功能集合**必须**在 design review 决定，**无可继承** |
| C11 | **零个缺失 reportType 的名称或语义**（只有数字枚举，见 §14.0.1） | count 型 / record 型的归类**必须**引用外部标准，**不可从仓库推断** |

### 14.0.1 C11：被"冻结为必须拒绝"的 reportType 全集

三个测试**刻意**把"未实现"固化为 `0x12`：

| 位置 | 断言的 reportType | 子例数 |
|------|-------------------|--------|
| `test_dtc_unit.py:354-360` | `0x03, 0x7F, 0x04, 0x06, 0x0B, 0xFF` | 6 |
| `test_dtc_unit.py:977-988` | `0x03, 0x04, 0x05, 0x06, 0x0B, 0x0C, 0x0D, 0x0E, 0x14, 0x15, 0x1A, 0x7F` | 12 |
| `test_dtc_vcan.py:415-421` | `0x03, 0x7F` | 2 |

→ **可实现的"缺失"集** = `0x03, 0x04, 0x05, 0x06, 0x0B, 0x0C, 0x0D, 0x0E, 0x14, 0x15, 0x1A`；
**必须保持 `0x12`** 的 = `0x7F, 0xFF`。
第二个测试的 docstring 自述其目的正是"本次未实现的 reportType **不得误入成功路径**"
—— 也就是说，**这三个测试就是为这次变更准备的守门员**，更新它们是可预期、
但**必须显式说明**的工作。

### 14.1 C10 — `0x28` CommunicationControl

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | `0x28` **不在 `SERVICES`**（`uds_server.py:2130-2145`）→ 未知 SID → `NRC 0x11` |
| 2 当前代码位置 | 无 handler；需插在 `uds_server.py`；状态可能进 `EcuState` |
| 3 需要修改哪些层 | 仅协议层（handler + registry）；若需要"禁止发送"语义，可能触及 transport 行为 |
| 4 是否修改既有行为 | **否**（新增 SID；既有 SID 行为不变） |
| 5 是否改变已有接口 | `SERVICES` 增加一项（表的**内容**变化，结构不变） |
| 6 是否改变 EcuState | **很可能需要**（通信控制状态）。新增字段会影响 `_reset_volatile_state` 的"22 复位 / 5 保留"计数与划分 |
| 7 是否影响已有测试 | **不打破任何既有测试**：未知 SID 测试用 `0x99`（`test_ecu_unit.py:821`）/`0xFF`（`:826`、`test_gateway_runtime_vcan.py:122`）；**没有任何测试枚举 `SERVICES` 的键集**。唯一涟漪是 fuzz 侧 `UNIMPL_SIDS`（见下） |
| 8 是否依赖其它候选 | **无** |
| 9 能否单独 feature branch | 能 |
| 10 是否适合小 PR | **局部**（小 PR 合适）—— 但"通信控制是否真的影响总线发送"是范围决定点 |
| 11 是否需要 Design Review 冻结契约 | **需要（小范围）**：实现哪些子功能？是否真的抑制本 ECU 发送（在单进程三 ECU 模型下如何表现）？ |
| 12 主要风险 | 若声称"控制通信"却不影响发送 → **空壳**；若影响发送 → 与网关"不收集响应/不回帧"语义交互复杂 |
| 13 如何验证 | unit（子功能/NRC/长度/状态）+ e2e（真实链路上"禁止发送"可观测）+ 复位语义断言 |
| 14 NON-GOALS | 不实现全部子功能；不改网关广播语义；不做真实总线静默攻击 |

#### C10/C11 共同的"涟漪效应"注：新增 SID 会触及 fuzz 侧的冻结常量

`labs/vehicle/can-tools/fuzz/uds_mutators.py:91`：

```python
UNIMPL_SIDS: Tuple[int, ...] = (0x06, 0x28, 0x39, 0x85)
```

由 `test_uds_fuzz_offline.py:449` **断言冻结**（`assertEqual(set(...), {0x06, 0x28, 0x39, 0x85})`），
其 docstring 自述"取自 README 已文档化的'尚未实现'清单，**不自行发明**"。

**该常量已经是陈旧的**：它列出的 `0x85` **已实现**（OQ6-B，注册于 `uds_server.py:2144`）。
根因可追溯到 README 中仍把 `0x85` 列为未实现的漂移（盘点 §12.1 D12）。

→ 含义：**任何新增 SID 的候选（C4 若走协议载体、C10）都会撞上这个常量**，
并且需要一并回答"它到底要不要跟踪 `SERVICES`"（当前它是**文档快照**，
不是 `SERVICES` 的活体投影）。本评审**只记录**，不在本 PR 修改任何代码或该常量。

### 14.2 C11 — `0x19` 其余 reportType 扩展

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | 仅 **`01`/`02`/`07`/`08`/`09`/`0A`** 六个有实现分支（`uds_server.py:1106-1215`；常量 `:1131-1136`）。其余（如 `03/04/05/06/0B–0E/14/15/16/1A`）**未实现** → `NRC 0x12` |
| 2 当前代码位置 | `service_read_dtc_information`（`uds_server.py:1049-1280`）；数据模型 `dtc_database.py`（`DTC_DATABASE`/`DTC_METADATA`）+ `EcuState.dtc_records` |
| 3 需要修改哪些层 | **仅协议层**（0x19 handler 内新增分支）；多数可**直接复用**现有 DTC 模型 |
| 4 是否修改既有行为 | **否**（新增 reportType 分支；既有六个分支不变） |
| 5 是否改变已有接口 | 无（handler 签名不变） |
| 6 是否改变 EcuState | **不需要**（DTC 状态字段已足够） |
| 7 是否影响已有测试 | **必须显式修改 3 个冻结测试**（见 §14.0.1）：每实现一个当前缺失的 reportType，对应子例即失败。这是**可预期的**工作，但属**评审事件**而非顺带改动。另：`test_dtc_unit.py:990-997`（NRC 不改 state）与 `:999`（`TestReadDtcWireProfile`）必须保持通过 |
| 8 是否依赖其它候选 | **无**。若扩到"需要新数据来源"的 reportType（如快照记录），则可能需要 C4 |
| 9 能否单独 feature branch | 能 |
| 10 是否适合小 PR | **局部**，且**可分批**（逐 reportType 一个小 PR，每个都能独立验证）—— 这是 12 个候选中**最容易切分**的 |
| 11 是否需要 Design Review 冻结契约 | **需要（每批）**：wire profile 是否沿用既有约定（**正响应不回显 reportType**、count 型不输出 availability mask、记录字段序）—— 既有约定已由 OQ5 冻结，新 reportType 必须沿用 |
| 12 主要风险 | ① 破坏既有 wire profile 约定；② 误以为需要新数据模型（多数不需要）；③ 批次过多导致碎片化 |
| 13 如何验证 | 每个 reportType：request / positive（逐字节）/ NRC / 状态不变性 / unit / e2e —— 与既有六个分支同标准 |
| 14 NON-GOALS | 不改既有六个 reportType；不改 mask/severity 语义；不引入快照/扩展记录数据源（除非单独决定）；不做 DTC 功能组（那是 G1-5） |

---

## 15. C12 — e2e 进 CI（feasibility-gated）

### 15.1 范围澄清（重要）

C12 **不是**"从头把 vcan0 加进 CI"。`ci.yml:48-84` 的 extended job **已经存在**，
并且**已经包含**完整的 vcan0 准备步骤与 `qa_extended.sh` 调用：

```
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set vcan0 up
sudo apt-get install -y --no-install-recommends can-utils tshark yara
bash scripts/qa_extended.sh
```

真正的缺口是**触发条件**：`ci.yml:52`
`if: github.event_name == 'workflow_dispatch' && inputs.run_extended`。

### 15.2 硬证据：该 job **从未在真实 runner 上执行过**

| 项 | 值 |
|----|----|
| 全部 workflow runs | **89 个** = 57 push + 32 pull_request + **0 workflow_dispatch** |
| 最近一次 run 的 job 结论 | `Fast QA` = success；`Extended` = **skipped** |
| Fast job 实际墙钟 | **33–46 s** |

→ 因此 `ci.yml:8-9` 的"GitHub runner 内核通常带 vcan 模块"**是假设，不是证据**。

### 15.3 14 项分析

| 项 | 结论 |
|----|------|
| 1 当前实际缺口 | e2e 层不进自动门禁；其触发被 `ci.yml:52` 关闭；extended 从未实跑（见 §15.2） |
| 2 当前代码位置 | `.github/workflows/ci.yml:48-84`（job 定义、触发、vcan 准备、`qa_extended.sh` 调用）；`scripts/qa_extended.sh:46-78`（vcan0 前置）、`:81`/`:88`/`:95`/`:102`（四道门）；`labs/vehicle/uds-basic/tests/run_tests.sh`（tier 划分） |
| 3 需要修改哪些层 | **CI workflow（触发条件）** + **新增 runner 可行性探针脚本** + 文档。**不碰任何 ECU / 协议 / 测试代码** |
| 4 是否修改既有行为 | 虚拟 ECU 运行时行为：**否**。但**改变"哪些 tier 是合并门禁"**，并使 `TEST_MATRIX` / `MAIN_CAPABILITY_INVENTORY` 中"e2e 一律 Local verified"的表述失效 → 属**语义状态变更** |
| 5 是否改变已有接口 | workflow 的 `on:` 与 `run_extended` 输入可不变；`qa_extended.sh` CLI 不变。**但 runner 级要求新增**：vcan / `CAN_ISOTP` / 免密 sudo |
| 6 是否改变 EcuState | **否**（不碰 Python） |
| 7 是否影响已有测试 | 既有测试文件**零改动**；但被提升后会新跑 uds e2e **234** 例（11 文件）+ can-tools vcan **≈48** 例 + `capture_lab.sh` + YARA |
| 8 是否依赖其它候选 | **无代码依赖**。反向：**C5 的总线级验证质量依赖"e2e 在某个地方能跑"**（单向的验证价值关系，非依赖） |
| 9 能否单独 feature branch | **能，且隔离度最高**（只碰 `.github/` + 新脚本 + 文档；与 ECU 侧任何候选都不冲突） |
| 10 是否适合小 PR | **PR 本身小**（探针 + 触发行）；**但可行性问题不小** —— 决策重、代码轻 |
| 11 是否需要 Design Review 冻结契约 | **需要**：把哪个 tier 作为门禁（uds e2e 单独 / 含 can-tools malformed / 含 capture+YARA）？触发语义（push to main / PR / 两者）？**失败语义**（见 §15.4）？探针是门禁步骤还是信息性 job？时长预算？ |
| 12 主要风险 | ① **静默变绿**（§15.4，最严重）；② 未验证的 runner 能力；③ 时长（§15.5）；④ 总线的**独占性**要求（`qa_extended.sh:15-16` 明示不得并发）→ 禁止在同一 runner 上分片并行；⑤ extended 依赖 fast（`ci.yml:51`），提升后**每个 PR 的延迟与抖动面都会变化**，且**没有任何 CI-flake 历史可参考** |
| 13 如何验证 | 见 §15.6 的探针设计 |
| 14 NON-GOALS | **本 PR 不改 CI**；不把长 fuzz（30/100/500/1000）加进 CI（`ci.yml:12-13` 已排除）；不改任何测试/ECU/协议；不改 `run_tests.sh` 的 tier 语义；不把 `healthcheck.sh` / `scripts/repro/*` 加进 CI；不在探针通过前把 e2e 设为阻塞门禁；不用 `continue-on-error` 掩盖失败；不改 `vcan0` 权限/MTU/systemd 单元 |

### 15.4 **最严重的风险：静默变绿**

`labs/vehicle/uds-basic/tests/run_tests.sh:187-198` 在 **vcan0 不存在/未 UP** 时
**只 warn 并跳过整个 e2e tier，然后返回 PASS**。

而 `scripts/qa_extended.sh:46-60` 在无法建立 vcan0 时 **fail-closed（`exit 2`）**。

> 两者语义相反。若 CI 走的是前者，**job 可以在执行 0 个 e2e 用例的情况下显示绿色**
> —— 这正是"假 PASS"。**必须在契约里冻结由哪一条路径主导**，否则 C12 会制造
> 一个比"没有 e2e"更糟的状态：**看起来有 e2e 覆盖，实际为零**。

### 15.5 时长（估算，非实测）

仓库内**仅有三处**逐文件时长记录：

```
test_multi_ecu_vcan.py        4 例 /  5.2 s
test_gateway_runtime_vcan.py 21 例 / 23.3 s
can-tools malformed 系列      47 例 / ~149.6 s
```

按 1.1–3.2 s/例 外推，uds-basic e2e 234 例 ≈ **260–470 s**，加 malformed ≈150 s、
`test_uds_fuzz_vcan.py`、`capture_lab.sh`、YARA、`apt-get install`（`ci.yml:61-67`）
与 pip 安装 → **总计约数分钟到数十分钟（估算值，不是测量值）**。
对照：当前 fast job 实测 **33–46 s**。

### 15.6 Runner 可行性探针（**capability = feasibility unknown**）

> 本评审**不主张** GitHub hosted runner 能 `modprobe vcan`、能创建 `vcan0`、
> 或能绑定 `CAN_ISOTP`。仓库中**没有任何证据**支持或反对，且该 job **从未跑过**。

**探针应逐项独立记录结果（不要首错即停，目标是拿到完整能力画像）**：

1. 内核/模块：`uname -r`；`modinfo vcan`；`modinfo can`/`can_raw`/`can-isotp`；
   `ls /lib/modules/$(uname -r)/kernel/net/can/`
2. 权限路径：`id -u`；**`sudo -n true`**（免密 sudo —— `qa_extended.sh:52` 与
   `ci.yml:55-58` 都依赖它）
3. 接口创建：`sudo modprobe vcan` → `sudo ip link add dev vcan0 type vcan` →
   `sudo ip link set vcan0 up` → `ip -details link show vcan0` →
   `cat /sys/class/net/vcan0/operstate`（`qa_extended.sh:64-67` 据此判断）
4. **`CAN_ISOTP` socket 绑定** —— **最关键的未知项**：`vcan` 可用与 `can-isotp`
   可用是**两个可分离的事实**；需实际 `bind()` 一个 `AF_CAN`/`CAN_ISOTP` socket
5. 最小端到端：一条 `3E 00 → 7E 00`（单个 ECU 子进程），证明整条栈可用，
   **而不是**先跑 234 例

**失败时的安全行为**：探针只读仓库；仅在**临时 runner VM** 上创建 `vcan0`；
任一步失败即非 0 退出；**且只有探针通过，才把 e2e 加入门禁**。
探针失败时，本次交付物退化为"探针 + 记录证据 + 文档更新"——**不引入任何掩盖机制**
（无 `continue-on-error`、无重试掩盖）。

**本 PR 不做任何 CI 变更。**

---

## 16. 候选分类（按工程属性，**非优先级**）

| 类别 | 候选 | 判据（代码级） |
|------|------|----------------|
| **A. 架构级**（引入新横切概念 / 契约层） | **C1**、**C2** | C1：新增策略抽象 + 需冻结 NRC 优先级与 per-DID 表达；C2：给"只做搬运"的层引入新概念 + 新配置载体 + 时钟 + 分层文本需改 + 87 用例在爆炸半径内 |
| **B. 局部**（单服务 / 单文件，additive，不破坏既有测试） | **C10**（状态-only）、**C3** | C10：一个 handler + 一个字段 + 一个 registry 条目，**零既有测试失败**；C3：只动 gateway 装配点，`EcuState` 无需改（`0x27` 状态已 per-ECU） |
| **C. 中型**（跨多服务 / 需改冻结测试 / 需外部语义） | **C4**、**C5**、**C6**、**C7**、**C9**、**C11** | C4：seam 化=局部，协议化=中型且带授权问题；C5：6 个 SID 的 wire 行为 + 撞 OQ3 golden；C6：**打破 3 个冻结测试** + 上行无 buffer 不对称；C7：schema + 执行器 + replay 三处契约；C9：261+75 用例在半径内 + 提交语义重建；C11：**3 个冻结测试** + 需外部语义 |
| **D. 大型 / 新体系** | **C8** | 全新传输栈 + 新地址模型 + 新测试层 + **无法复用** OQ7 的 CAN ID 型 `Transport`（F9） |
| **E. Feasibility-gated**（先证明环境，再决定是否做） | **C12** | 交付物小，但 `capability = unknown`，且 extended job **从未跑过**（89 runs / 0 dispatch） |

**交叉分类（同一候选的两种形态可能落在不同类别）**：

| 候选 | 形态 1 | 形态 2 |
|------|--------|--------|
| C4 | 仅 seam 化 → **局部** | 协议能力 → **中型**（+授权则趋架构级） |
| C10 | 仅状态 → **局部** | 需真正抑制总线发送 → **中型**（伸进 OQ7 冻结的 transport 层） |
| C9 | 抽象 + 内存默认后端 → **中型** | 含真实设备后端 → **大型**（且本环境**无验证路径**） |
| C12 | 仅探针 + 记录 → 小 | 探针通过后提升门禁 → **中型**（改变合并门禁拓扑） |

**前置依赖（代码判定）**：`C2(授权维度) → C3`；`C4(协议形态) →(条件) C1`。
其余候选之间**无硬依赖**，只有**文件级冲突**（见 §17）。

---

## 17. 设计门槛表

| Candidate | 代码依赖 | 架构影响 | 契约风险 | 前置 Design Review | 测试难度 | 独立 OQ |
|-----------|----------|----------|----------|--------------------|----------|---------|
| **C1** Permission/Session Framework | 无 | 协议层 dispatcher + registry 结构 | **高**：NRC 优先级（F4，**无测试保护**）与 per-DID 表达（F3，**有测试保护**） | **必须**（策略位置 / 优先级 / payload 相关策略） | 高（需全服务 × 会话 × 安全矩阵） | 是（架构级） |
| **C2** Gateway Security Policy | 授权维度→C3 | 新增策略层；**不改则须严守 OQ7 分层** | **高**：拒绝与"未路由"总线表现相同（G7）→ 不可观测 | **必须**（求值位置 / 默认值 / 配置载体 / 时钟） | 中（有 `FakeTransport` 接缝） | 是（架构级） |
| **C3** 0x27 经 Gateway 透传 | 无 | 仅 gateway 装配点（`gateway.py:63-97`） | 中：注入组合"只定义一处"；patch 点可能改变 | **需要（小范围）** | 中（需跨 3 ECU 隔离验证） | 是（局部） |
| **C4** DTC Fault Injection Model | 条件→C1 | `dtc_runtime`（seam）/ `uds_server`+`EcuState`（协议） | **高**：推翻 `dtc_runtime.py:38-39` 的设计声明 + 5 份文档不变量变假 | **必须**（协议 or seam？谁可注入？） | 中（seam 已有 28 处调用范式） | 是 |
| **C5** suppress 统一化 | 无 | 协议层 dispatcher | **高**：**撞 OQ3 冻结 golden（947 + digest）** + 6 个 SID 零覆盖 | **必须**（有资格服务集 / 优先级 / `b""` 语义） | 中-高 | 是（中型） |
| **C6** ResumeFile + integrity | 无（**不依赖 C9**） | `file_database` + `0x36/0x37` + `EcuState` | **高**：**打破 3 个冻结测试** + 可能改 `_reset_volatile_state` 的 22/5 划分 + 上行/下行不对称 | **必须**（"中断"的定义 / 续传状态 / 校验语义） | 高 | 是（建议**拆成两次**） |
| **C7** Stateful UDS Fuzz | OQ3 冻结契约 | 测试架构三层 | **高**：`SCHEMA_VERSION` 与 v1 golden | **必须**（版本策略 / 序列表示 / oracle 语义） | 高 | 是（架构级测试） |
| **C8** DoIP | 无 | 全新栈 | 中（不碰既有） | **必须且先于代码**（地址模型 / 复用点 / 标准来源 / 安全边界对应物） | 高（无既有接缝） | 是（大型） |
| **C9** 真实 Flash 抽象 | 无（**不依赖 C6**） | 存储缝 + `uds_server` 调用点 | **中-高**：提交语义重建（部分写入）+ 上限常量 + `files` 的非易失语义 | **必须**（后端接口面 / 缝的位置 / 提交语义） | 中（261+75 保持网） | 是（中型） |
| **C10** 0x28 | 无 | 协议层（+ transport 若需抑制发送） | **低**（additive，零测试失败） | **需要（小）**：子功能集**仓库内不存在** | 低 | 是（局部） |
| **C11** 0x19 reportType | 无 | 协议层（+ `dtc_database` 视类型） | **中**：3 个冻结测试 + wire profile 不得分叉 | **必须**（reportType 集 + count/record 归类**需外部标准**） | 低-中 | 是（可分批） |
| **C12** e2e 进 CI | 无 | CI 触发 + 新探针脚本 | **中**：**静默变绿**（skip 路径返回 PASS） | **必须**（门禁范围 / 触发 / 失败语义） | 低 | 是（feasibility-gated） |

**文件级冲突矩阵**（并行开发注意，非语义依赖）：

```
uds_server.py   ← C1, C4(协议), C5, C6, C9, C10, C11   ← 七个候选争用同一文件（F13）
dtc_database.py ← C4, C11（若需新静态数据）
file_database.py← C6, C9
EcuState        ← C1?, C4?, C6, C9?, C10
can-tools/fuzz/ ← C5, C7, 以及任何新增 SID 的候选（UNIMPL_SIDS）
.github/        ← C12（唯一，零冲突）
gateway.py      ← C3（唯一）
gateway_runtime.py ← C2, C3
```

---

## 18. Design Review Questions

以下问题是**必须先回答**、且**无法从代码推断**的。按候选归组，不排优先级。

### 18.1 策略与授权（C1 / C2 / C3）

1. Session policy 应该是 **registry metadata（ServiceSpec）** 还是 **dispatcher 前置检查**？（§5.3 两方案）
2. Permission 是否应与 session framework **同一层**？还是 per-DID 权限（F3）继续留在 handler 内？
3. **NRC 优先级（F4）要保持 `0x31` 还是接受变成 `0x7F`**？（当前无测试保护，必须显式决定并补断言）
4. Gateway policy 是否**允许**读取 UDS session/security 状态？（F8 使其**技术上可能**）
5. Gateway policy 的拒绝，在总线上是**静默丢帧**还是**产生响应**？（今天只有两类出口）
6. Gateway policy 放进哪一层，才不破坏 OQ7 的 `gateway.py` = pure routing？（且该分层**无测试强制**，见 G2）
7. 0x27 经 gateway 透传，是否需要**改变 `gateway.py`**？还是只改装配方？
8. "注入组合只定义一处"如何落地（方案 X / 方案 Y），且 `mock.patch.object(ecu, "_now")` 的既有 patch 点如何保持？

### 18.2 状态与故障（C4 / C10）

9. DTC fault injection 是**协议能力**还是**受控进程内 seam**？（后者是否等于 no-op？）
10. 若为协议能力：**谁有权注入**？（若答"进程内任何人"，则候选塌缩为 no-op）
11. 注入的故障在 `0x11` 复位后**仍保留**（H7）——这是期望语义吗？
12. `0x28` 实现**哪些子功能**？（仓库内**零**可继承取值）
13. `0x28` 是**仅状态**还是**真正抑制总线发送**？（前者有"空壳"风险，后者伸进冻结的 transport 层）
14. `0x28` 新字段在 `_reset_volatile_state` 的 22/5 划分中归哪一侧？（先例 `0x85` 的理由在 `:411-415`）

### 18.3 传输与固件（C6 / C9）

15. **ResumeFile 需要保存什么状态**？且它如何与 `_reset_volatile_state` 的既有划分共存？
16. 在纯内存单进程模型里，**"传输被中断"的定义是什么**？（无连接事件）
17. Firmware integrity 是**传输层属性**还是 **payload / backend 属性**？（上行无 buffer → 上下行不对称）
18. integrity **能否拒绝**？若能，`0x37` 的 20 处 `77` 期望如何保持？
19. C9 的后端接口边界：**地址解码 / 大小计量 / 提交语义**哪些进抽象，哪些留在协议常量？
20. C9 的"真实"判定标准是什么？（抽象本体 or 真实设备后端？后者本环境无验证路径）

### 18.4 测试与门禁（C5 / C7 / C11 / C12）

21. C5：**哪些服务有资格获得 suppress**？（§9.2 已给出逐服务事实表）
22. C5：是否**同时扩展 `SUPPRESS_SIDS`**？若是，**OQ3 v1 golden 是否重冻**？
23. C7：Stateful fuzz 需要**新 schema version** 吗？还是显式 v1 扩展？v1 golden 如何并存？
24. C7：多请求场景下的 **oracle / 分类语义**是什么？
25. C11：要实现的 **reportType 集**，以及每个是 count 型还是 record 型？（**需外部标准**）
26. C11：新的 reportType 是否沿用"**正响应不回显 reportType**"的既有 profile？
27. C12：**e2e CI 的 runner feasibility 如何证明**？（§15.6 探针）
28. C12：CI 走 `qa_extended.sh` 的 **fail-closed** 还是 `run_tests.sh` 的 **warn-and-skip**？（§15.4 静默变绿）

---

## 19. 结论（中性）

**本评审不选择 OQ8。** 以下只是工程属性归类与前置条件陈述。

**哪些候选属于架构级**
`C1`、`C2`。二者都引入新的横切概念，并都要求先冻结语义契约；
且 C1 触及一个**当前无测试保护**的既有行为（NRC 优先级，F4）。

**哪些属于小型协议扩展**
`C10`（状态-only）、`C11`（可分批）、`C3`。三者都是 additive、不改既有可见行为、
且（C10 与 C3）不打破任何既有测试。C11 需显式更新 3 个冻结测试。

**哪些属于中型**
`C4`（协议形态）、`C5`、`C6`、`C7`、`C9`。共同特征：**要动冻结契约或跨多个用例**。

**哪些属于新体系**
`C8`（DoIP）：全新传输栈，**无法复用** OQ7 的 CAN ID 型 `Transport`，
且既有的 vcan0 验证设施（fuzz / repro / capture）对其**全部不适用**。

**哪些需要 feasibility probe**
`C12`。其交付物小，但**环境能力未知**，且 extended job **从未在真实 runner 上执行过**
（89 runs / 0 dispatch）。**必须先证明 runner 能力，再决定是否纳入自动门禁。**

**哪些候选存在前置依赖**
- `C2` 的**授权维度**依赖 `C3`（网关路径无法合法提升 `security_level`）；
- `C4` 的**协议形态**条件依赖 `C1`（授权语义）；
- 其余候选之间**无硬依赖**，只有**文件级冲突**（§17，`uds_server.py` 被 7 个候选争用）。

**哪些设计问题必须先冻结**
- C1：策略位置 + **NRC 优先级** + per-DID 表达方式（§18.1 Q1–Q3）；
- C2：策略求值位置 + 默认值 + 拒绝的总线表现（§18.1 Q4–Q6）；
- C5：**有资格服务集** + 是否重冻 OQ3 golden（§18.4 Q21–Q22）；
- C6：**"中断"的定义** + 续传状态与复位契约的关系（§18.3 Q15–Q16）；
- C7：**schema 版本策略** + oracle 语义（§18.4 Q23–Q24）；
- C12：**失败语义**（fail-closed vs warn-and-skip）（§18.4 Q28）。

**一句话事实**：12 个候选中，**6 个会触及既有冻结契约或既有测试**
（C4/C5/C6/C7/C9/C11），**2 个引入新的横切抽象**（C1/C2），
**1 个是全新体系**（C8），**1 个是环境可行性问题**（C12），
**3 个是干净的 additive 扩展**（C3/C10/C11 的部分）。

---

## 20. Evidence Index

> 本评审的每条结论都可回溯到下列位置之一。凡未在此列的"环境类"断言，
> 一律在本文件中标为 unknown 而非事实。

| 主题 | 证据位置 |
|------|----------|
| dispatcher 与 registry | `labs/vehicle/uds-basic/ecu/uds_server.py:2130-2183` |
| 会话/安全消费者（F1/F2） | `uds_server.py:758`（session）、`:567`、`:763`（security） |
| per-DID 权限（F3） | `uds_server.py:424-429`（`required_security_level`）、`:567-574`（0x22 判定） |
| 0x2E 门控链与 NRC 顺序（F4） | `uds_server.py:750-787`；实测见 §5.3 |
| 0x27 不在静态表（F6） | `uds_server.py:2121-2145`（含注释）、`ecu.py:352-395` |
| 0x27 注入工厂（F7） | `uds_server.py:2091-2119` |
| gateway 持有 EcuState（F8） | `gateway.py:43`、`:85` |
| OQ7 Transport Protocol 形状（F9） | `gateway_runtime.py`（`class Transport(Protocol)`） |
| 每 case 全新 ECU（F10） | `isotp_fuzz.py:429-439` |
| schema 版本与未知键（F11） | `case_schema.py:43`、`:281-345` |
| 传输路径无完整性校验（F12） | `uds_server.py` / `transfer_database.py` / `file_database.py` 检索 `crc32/sha256/md5/hmac/signature` = 0 |
| OQ3 冻结常量 | `uds_corpus.py:63,72,80`；`uds_mutators.py:84,123-131`；`scripts/repro/verify_uds_fuzz_smoke.sh` |
| 0x38 mode 常量（`0x06` 未实现） | `file_database.py:55-78`、`uds_server.py:1788-1794` |
| 既有测试对候选的影响 | `labs/vehicle/uds-basic/tests/*.py`、`labs/vehicle/can-tools/tests/*.py` |
| CI 现状 | `.github/workflows/ci.yml`、`scripts/qa_fast.sh`、`scripts/qa_extended.sh` |
| 能力盘点（输入材料） | `docs/MAIN_CAPABILITY_INVENTORY_2026-09-24.md` |
