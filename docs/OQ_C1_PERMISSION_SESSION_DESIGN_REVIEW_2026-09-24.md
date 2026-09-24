# OQ-C1 Permission / Session Framework — Design Review & Design Freeze (2026-09-24)

> **性质**：设计评审 + 设计冻结（design freeze）。**不是实现**。
> 本文件**不修改任何生产代码 / 测试 / 业务行为**，只新增本设计文档。
> 所有结论均落到**当前真实源码与实测证据**上；凡无法从现有证据决定者，
> 明确标为 `OPEN DECISION`，不强行拍板。

---

## 1. Scope

### 1.1 本设计要解决的问题

当前项目**已存在**会话与安全的**状态与局部判断**：

```
state.session                      (EcuState 公有字段)
state.security_level               (EcuState 公有字段)
required_security_level(did)       (DID 级权限函数)
0x27 SecurityAccess                (唯一能提升 security_level 的服务)
0x22 的 per-DID security           (读取受保护 DID)
0x2E 的 session + security 门控     (唯一做会话门控的服务)
```

但**不存在**统一的：

```
Session Policy / Permission Policy / Service Authorization Matrix
```

C1 的目标**不是"增加几个会话"**，而是设计一套**统一、可扩展、可验证**的授权框架，
并明确回答：

```
request → session context → security context → service permission policy → handler
```

**最终由哪一层负责权限判断。**

### 1.2 本文件的交付物

一份**可直接实施的冻结设计**：policy 位置、数据结构、session 表示、权限粒度、
NRC 优先级、0x22/0x2E/0x27 兼容方式、gateway 边界、迁移策略、测试策略、
跨 OQ 依赖、风险、以及显式列出的 `OPEN DECISION`。

### 1.3 基线（本次评审的唯一代码基线）

| 项 | 值 |
|----|----|
| 分支 | `audit/oq-c1-permission-session-design-20260924` |
| 基线提交 | `02d8d991c1b4cd2b2574a1685226480e7a57dcc6`（= PR #24 head） |
| **其 tree 与 `origin/main` 逐字节相同** | `02d8d99^{tree}` = `a9a909fd8bfdaae2eefbc03578e176b48a5b14d0` = `main` 的 tree |
| 该提交与 main 的关系 | 它是 main HEAD（PR #24 merge）的**第二父提交** → 即 main 的祖先 |
| production-code baseline | `ae22a556924d89d27e1d084ebcc5a4b4c56534f2`（OQ7 / PR #21） |
| 说明 | **本文件只新增文档，不改 `main`** |

> **基线获取方式说明（如实记录）**：本轮 `git fetch` 多次因网络失败
> （`Failed to connect to github.com:443` / `Recv failure: Connection was reset`）。
> 为不伪造提交，本次**没有**重建 main 的 commit 对象，而是选取了一个
> **tree 与 main 完全相同、且本身是 main 祖先**的真实上游提交（`02d8d99`，PR #24 head）
> 作为分支起点 —— 该选择同时保证：(a) 工作内容与 main 一致；
> (b) PR 的 `merge-base` 就是 `02d8d99`，因此 **PR diff 只会包含本设计文档一个文件**。
> 上述 tree 等价性已用 `git rev-parse` 与 GitHub API 双向核对。

---

## 2. Current Architecture Evidence

### 2.1 注册表与派发（唯一的服务入口）

```python
# uds_server.py:2130-2145  —— 静态表，纯 SID → handler，无任何 metadata
SERVICES = { 0x10: ..., 0x11: ..., 0x14: ..., 0x19: ..., 0x22: ..., 0x2E: ...,
             0x31: ..., 0x34: ..., 0x35: ..., 0x36: ..., 0x37: ..., 0x38: ...,
             0x3E: ..., 0x85: ... }        # 14 项；0x27 不在表内
```

```python
# uds_server.py:2148-2182  —— 唯一的 dispatcher
def handle_uds_request(request, state, *, services=None) -> bytes:
    if not request:                       # :2164-2167  空请求 → 7F 00 13
        return negative_response(0x00, NRC_INCORRECT_MESSAGE_LENGTH)
    service = request[0]                  # :2169
    table = SERVICES if services is None else services
    handler = table.get(service)          # :2173
    if handler is None:                   # :2175-2180 未注册 SID → 7F <sid> 11
        return negative_response(service, NRC_SERVICE_NOT_SUPPORTED)
    return handler(request, state)        # :2182  ← 直接调用，无任何前置检查
```

**关键事实**：dispatcher **没有任何授权前置层**（`F1`）。它只做三件事：
空请求判定、查表、未注册 SID 判定。**所有会话/安全判断都在 handler 内部。**

### 2.2 状态模型

`uds_server.EcuState`（**没有**独立的 `ecu_state.py`），27 字段，与授权相关者：

```python
session: int = uds_types.SESSION_DEFAULT              # :177  公有字段，可直接赋值
security_level: int = uds_types.SECURITY_LOCKED       # :180  公有字段，可直接赋值
pending_seed / seed_issued_at / seed_consumed         # :183/:186/:189  0x27 用
failed_attempts / lockout_until / seed_counter        # :192/:195/:198  0x27 用
```

- `SUPPORTED_SESSIONS = frozenset({0x01, 0x02, 0x03})`（`uds_types.py:72-80`）。
- **会话与安全等级是普通公有字段，没有 property / 无校验 setter** → **可被直接赋值**
  （生产代码 `uds_server.py:381/459/346/710` 直接写；测试亦直接写，见 §13.3）。

### 2.3 会话切换语义（0x10）

```python
# uds_server.py:452-463
session_type = request[1]
if session_type not in uds_types.SUPPORTED_SESSIONS:
    return negative_response(0x10, NRC_SUBFUNCTION_NOT_SUPPORTED)   # → 0x12
state.session = session_type
reset_security_state(state)          # ← 切会话即把 security_level 归 LOCKED（:346）
```

**实测确认**：`10 03` 后 `security_level` 归 `0x00`（切会话重置解锁）。
`SESSION_TIMING`（`:133-137`）为每个会话给出固定 `(P2, P2*)`，仅用于响应字节。

### 2.4 授权相关的全部消费者（**穷举**）

| 消费者 | 位置 | 说明 |
|--------|------|------|
| `state.session` **读取** | `uds_server.py:758` | **全仓库唯一一处**，属 `0x2E` |
| `state.session` **写入** | `:381`（复位）、`:459`（0x10） | —— |
| `state.security_level` **读取** | `:567`（`0x22`，per-DID）、`:657`（`0x27` 自身）、`:763`（`0x2E`） | 3 处 |
| `state.security_level` **写入** | `:346`（复位为 LOCKED）、`:710`（解锁为 LEVEL1） | —— |
| `required_security_level(did)` | `uds_server.py:424-429` | `did in PROTECTED_DIDS → LEVEL1`，否则 `LOCKED` |

**结论（`F2`）** —— 本设计**区分两个口径**，二者数字不同，**不得混用**：

| 口径 | 定义 | 计数 | 成员 |
|------|------|------|------|
| **authorization-logic consumers** | 任何读取 `session` / `security_level` 做判断的服务 | **3** | `0x22`（per-DID 安全）、`0x27`（**自身 SecurityAccess 状态机**）、`0x2E`（会话 + 安全） |
| **session/security service gates** | 把 session/security 作为**普通服务前置门控**的服务 | **2** | `0x22`、`0x2E` |

> **`0x27` 的不同之处**：它读取 `security_level` 是为了判断"已解锁时不得再要 seed"
> （`uds_server.py:657`）—— 这是**它自身状态机的一部分**，而**不是**"某个服务被授权门控"。
> 因此它计入"authorization-logic consumers"，**不计入**"service gates"。
> 相应地，C1 的 **service authorization 框架不覆盖 `0x27` 的自身状态机**（§12）。

**其余 12 个服务的 NRC 集合里完全没有授权类 NRC。**

### 2.5 完整的 NRC 生成点清单（机械提取，共 85 处）

| SID | NRC 集合（按源码出现顺序） | 授权类 NRC |
|-----|----------------------------|-----------|
| `0x00`（空请求） | `13` | — |
| `0x10` | `13` → `12` | 无 |
| `0x11` | `13` → `12` | 无 |
| `0x14` | `13` → `31` | 无 |
| `0x19` | `13`×7 → `31` → `12` | 无 |
| `0x22` | `13` → （每 DID：`31` → `33`） | **`33`** |
| `0x27` | `13` → `12` → `13`×2 → `37` → `22` → `24`×2 → `36` → `35` | 自身状态机 |
| `0x2E` | `13` → `31` → **`7F`** → **`33`** → `22` → `13` | **`7F` + `33`** |
| `0x31` | `13` → `12` → `31` → `22` → `22` | 无 |
| `0x34` | `13` → `31`×3 → `22` | 无 |
| `0x35` | `13` → `31`×3 → `22` | 无 |
| `0x36` | `13`×4 → `73`×2 → `22` → `24`×2 | 无 |
| `0x37` | `13` → `22`×2 → `24` | 无 |
| `0x38` | `13`×3 → `31`×9 → `22`×2 | 无 |
| `0x3E` | `13` → `12` | 无 |
| `0x85` | `13` → `12` | 无 |
| 未注册 SID | `11`（dispatcher `:2178`） | — |

（`13`=incorrectMessageLength、`12`=subFunctionNotSupported、`31`=requestOutOfRange、
`33`=securityAccessDenied、`7F`=serviceNotSupportedInActiveSession、
`22`=conditionsNotCorrect、`24`=requestSequenceError、`35`=invalidKey、
`36`=exceedNumberOfAttempts、`37`=requiredTimeDelayNotExpired、`73`=wrongBlockSequenceCounter、
`11`=serviceNotSupported。）

### 2.6 Gateway 边界（OQ7 冻结）

| 层 | 文件 | 职责 | 是否含授权 |
|----|------|------|-----------|
| 协议核心 | `gateway.py` | 纯路由：`DiagnosticGateway.route(can_id, payload) -> list[RouteResult]`；**不 import isotp/socket** | **无** |
| 实时 runtime | `gateway_runtime.py` | transport + 生命周期 + 错误遏制 | **无**（`handle_request()` 拿到 payload 即 `route()`） |

- `gateway.py:43` `from uds_server import EcuState`；`:85` `state: EcuState` → **网关层已持有每 ECU 的 `EcuState`**，因此**技术上今天就能读到 session/security**。
- `gateway.py:63-75` 的默认 handler 工厂绑定 **静态 `SERVICES` 表**（不含 `0x27`）。
- **功能寻址 `0x7DF`**：`route()` 对每个 ECU 调 `process(payload)`（`:162-165`），
  各 ECU 用自己的 `state` 处理 → **会话变化在各 ECU 独立发生，网关不传播、不收集响应**。

---

## 3. Problems

| # | 问题 | 证据 |
|---|------|------|
| P1 | **无统一授权层**：授权判断散落在 **3 个** authorization-logic consumers（`0x22`/`0x27`/`0x2E`，其中仅 **2 个**是普通服务门控）内部，其余 12 个服务完全没有 | §2.4/§2.5 |
| P2 | **会话几乎是装饰性的**：`state.session` 全仓库只有 1 处读取（`0x2E`） | §2.4（实测：默认会话下 `0x31/0x34/0x35/0x14/0x19/0x85` 全部正响应） |
| P3 | **授权矩阵不存在**，只能靠读源码推断"哪个服务在什么条件下被允许" | 无任何声明式载体 |
| P4 | **NRC 优先级只存在于代码顺序中**，无文档、且**部分无测试保护** | §9、§13.4 |
| P5 | `dispatcher` 无前置层，**新增服务的授权语义默认为"无"** | §2.1 |
| P6 | `required_security_level(did)` 是**唯一的权限抽象**，且仅覆盖"读 DID 是否需要解锁" | `:424-429` |

---

## 4. Design Goals

| # | 目标 |
|---|------|
| G1 | 提供**统一、可枚举、可验证**的服务授权模型（矩阵是**可执行**的，不只是文档） |
| G2 | **零可观测行为变更**：迁移前后逐服务逐请求的响应字节必须完全一致 |
| G3 | 能表达**现有全部真实行为** —— 包括 per-DID、payload 相关、顺序相关的判定 |
| G4 | 新服务默认必须声明授权语义（消除 P5 的"默认无授权"） |
| G5 | 保持 OQ7 冻结的分层：`gateway.py` 仍是纯路由，**C1 不改 gateway** |
| G6 | 保持 `0x27` 的注入边界（在 `ecu.py`），C1 只定义接口不搬动注入 |
| G7 | 迁移**可分批**、每批可独立验证与回滚 |

---

## 5. Non-Goals

```
真实密钥体系 / 证书 / HSM / 真实 ECU 权限模型
新增会话类型（除非有真实需求 —— 本设计不新增）
新增 UDS 服务或新增子功能
把授权下沉/上移到 gateway（那是 C2 的范围）
让网关透传 0x27（那是 C3 的范围）
实现 DTC fault injection 的授权（那是 C4 的范围）
修改 ISO-TP / transport / SocketCAN 任何一层
修改既有 UDS 服务的响应字节或 NRC 值
```

**明确不做的"看起来完整"的事**：不因为"理论上某些服务也该有会话限制"就给
`0x14/0x19/0x31/0x34–0x38/0x3E/0x85` **新增**限制 —— 当前源码没有这些语义，
任何新增都必须显式标注为 `NEW POLICY`（见 §8.3）。

---

## 6. Session Model

### 6.1 表示（**FROZEN**）

| 问题 | 决定 | 理由 |
|------|------|------|
| 用什么表示 | **保持 `int` + `frozenset`**（沿用 `uds_types.SESSION_*` 与 `SUPPORTED_SESSIONS`） | 零行为风险；不引入 enum/class 只会增加迁移面；现有 4 个模块与全部测试依赖 `int` |
| 唯一来源 | `EcuState.session`（**唯一事实源**，保持现状） | 现状即如此（无模块级变量） |
| 是否属于 `EcuState` | **是**（保持现状） | 每 ECU 独立实例 → 天然 per-ECU 隔离 |
| 是否允许直接赋值 | **允许**（保持现状，**不加 property/校验**） | 生产代码与测试均直接赋值；加校验会改变既有语义（如复位路径） |
| 由谁修改 | **仅 `0x10` handler**（`:459`）与**复位路径**（`:381`） | 现状即如此；C1 不新增写入口 |
| ECUReset 后 | **回 `SESSION_DEFAULT`**（`_reset_volatile_state`） | 现状即如此，且**必须保持** |
| 每 ECU 是否独立 | **是**（各自 `EcuState`） | 已由 gateway 单测 + 多 ECU e2e 固化 |
| 功能寻址广播时是否"传播" | **不传播**；`0x7DF` 让**各 ECU 各自**执行 0x10 | `gateway.py:162-165`；OQ7 e2e 已证明"三个 ECU 都执行且状态各自独立" |

### 6.2 会话集合（**FROZEN：不新增**）

```
SESSION_DEFAULT     = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03
```

**维持三个，不新增。** 理由：当前**没有任何**需求或测试要求第四个会话；
"补齐会话"属于典型的"为了看起来完整而增加不存在的需求"，本设计明确拒绝。
若未来出现真实需求（例如某服务只能在某新会话下调用），按 §14 的迁移流程新增，
并同步矩阵与测试。

### 6.3 会话语义在 C1 中的角色

C1 **不改变**"会话能做什么"，只把"哪些服务在哪些会话下被允许"**显式化**。
当前唯一真实存在的会话约束是 `0x2E` 的 `!= DEFAULT`（`:758`）。

---

## 7. Permission Model

### 7.1 三种粒度的可行性比较（**基于真实代码，不是理论**）

| 方案 | 能否表达现有真实行为？ | 证据 |
|------|------------------------|------|
| **A. SID 级** | **不能** | `0x22` 的判定是 **per-DID**：同一状态下 `22 F1 87 → 7f 22 33` 而 `22 F1 90 → 正响应`（实测）。SID 级只能"全放行"或"全拒绝"，二者都会改变行为 |
| **B. SID + SubFunction** | **不足** | `0x22` 没有子功能字节（其 byte[1..] 是 DID 列表，`uds_server.py:552`）；`0x2E` 同理（`:750`）。二者恰恰是**唯一有授权判断**的服务 |
| **C. SID + payload（DID / record / operation）** | **必需** | 只有 C 能表达 `0x22`/`0x2E` 的真实判定 |

### 7.2 **FROZEN：粒度**

> **以 SID 为注册单位，但允许每个服务声明"payload 相关的有序判定规则"。**
> 即：`ServiceSpec` 承载 **SID 级的粗粒度声明**（用于矩阵与文档），
> 同时承载 **有序的 `guards` 列表**（可读取完整 `request` 与 `state`），
> 由它表达 per-DID / payload 相关 / 顺序相关的真实判定。

**A 与 B 单独都不够；C 是必需的**；但 C 不意味着"为每个 payload 值建表" ——
它意味着**判定可以是 payload 的函数**，这正是 `0x22` 已经做的事。

### 7.3 **FROZEN：policy 在协议层，且不是 dispatcher 全局前置**

这是本设计最关键的结论，由两条**实测**证据强制：

**证据 1 —— 0x22 的 NRC 依赖 DID 在请求中的顺序：**

```
22 F1 87 FF FF   (受保护 DID 在前, 未解锁) -> 7f 22 33   (securityAccessDenied)
22 FF FF F1 87   (未知 DID 在前,   未解锁) -> 7f 22 31   (requestOutOfRange)
```

→ 同一个请求集合，仅**顺序不同**，NRC 不同。一个"先解析全部 DID 再统一判定"的
全局前置层**必然**改变其中一种情况。

**证据 2 —— 0x2E 的会话门控在未知 DID 判定之后：**

```
2E FF FF  (未知 DID, 默认会话) -> 7f 2e 31
2E F1 02  (已知 DID, 默认会话) -> 7f 2e 7f
```

→ 若把 session authorization 放到 dispatcher 最前面，`2E FF FF` 会由 `31` 变 `7F`。

**因此（FROZEN）**：

```
dispatcher 保持"查表 → 调用 handler"，不插入全局授权前置层
每个 handler 的判定点由该服务 ServiceSpec 的**有序 guards** 表达
guards 在 handler 内部按序求值（语义等价于今天的 inline 顺序）
```

这既满足 G1（统一模型）又满足 G3（能表达 payload 相关）与 G2（零行为变更）。

### 7.4 policy 数据结构（**FROZEN 形状，命名可讨论**）

```python
# 每个 guard：返回 NRC 表示拒绝，返回 None 表示通过
Guard = Callable[[bytes, "EcuState"], int | None]

@dataclass(frozen=True)
class ServiceSpec:
    sid: int
    handler: Callable[[bytes, "EcuState"], bytes]

    # --- 可执行部分：按序求值，**顺序即语义** ---
    guards: tuple[Guard, ...] = ()

    # --- 声明部分：供矩阵生成/审计/文档；不参与执行顺序 ---
    allowed_sessions: frozenset[int] | None = None      # None = 该服务无会话约束
    required_security: int | None = SECURITY_LOCKED     # None = 无法用单一等级概括（见 §18.0 OD-2）
    payload_scoped: bool = False     # True = 单靠声明字段不足以完整描述其授权行为
```

**设计要点**：

1. **顺序在数据里**（`guards` 是 tuple）→ 可枚举、可测试、可对拍。
2. `allowed_sessions` / `required_security` 是**声明**，用于生成 §8 的矩阵与审计；
   **执行顺序由 `guards` 决定**。二者的一致性**按服务类别**分别保证（§18.0 OD-2）：
   非 payload-scoped 用矩阵逐格对拍；payload-scoped 只验证"声明没有假装完整"。
3. `payload_scoped=True` 表示"**单靠声明字段不足以完整描述该服务的授权行为**"，
   防止读者误以为矩阵已完整描述该服务。**它不蕴含 `required_security is None`**
   —— 例如 `0x2E` 整体确实要求 `LEVEL1`（合法声明），但其完整授权链仍依赖
   payload 与**顺序**，故同时也标 `payload_scoped=True`（§18.0 OD-2 有完整说明）。
4. **不引入 `SessionRule`/`SecurityRule`/`PermissionRule` 等额外类型** ——
   它们是 A/B 粒度的产物，而 §7.2 已证明需要 payload 级表达，用 guard 函数统一即可。

---

## 8. Authorization Matrix

### 8.1 声明部分（当前真实行为，逐服务）

| SID | 服务 | 会话约束 | 安全约束 | payload 相关 | 授权类 NRC |
|-----|------|----------|----------|--------------|-----------|
| `0x10` | DiagnosticSessionControl | 无 | 无 | 否 | — |
| `0x11` | ECUReset | 无 | 无 | 否 | — |
| `0x14` | ClearDiagnosticInformation | 无 | 无 | 否 | — |
| `0x19` | ReadDTCInformation | 无 | 无 | 否（reportType 是格式） | — |
| `0x22` | ReadDataByIdentifier | 无 | **per-DID**（`PROTECTED_DIDS` → LEVEL1） | **是** | **`33`** |
| `0x27` | SecurityAccess | 无（自身状态机） | 自身状态机 | 是（子功能） | `22/24/35/36/37` |
| `0x2E` | WriteDataByIdentifier | **`!= DEFAULT`** | **`>= LEVEL1`** | **是** | **`7F`、`33`** |
| `0x31` | RoutineControl | 无 | 无 | 否 | — |
| `0x34` | RequestDownload | 无 | 无 | 否 | — |
| `0x35` | RequestUpload | 无 | 无 | 否 | — |
| `0x36` | TransferData | 无 | 无 | 否 | — |
| `0x37` | RequestTransferExit | 无 | 无 | 否 | — |
| `0x38` | RequestFileTransfer | 无 | 无 | 否（mode 是格式） | — |
| `0x3E` | TesterPresent | 无 | 无 | 否 | — |
| `0x85` | ControlDTCSetting | 无 | 无 | 否 | — |

**这张表本身就是 C1 的主要交付物之一**：它把"哪些服务有授权语义"从**读源码推断**
变成**可枚举的事实**。它同时暴露出 P2 —— 按 §2.4 的两个口径：
**authorization-logic consumers = 3**（`0x22`/`0x27`/`0x2E`），
其中真正构成**普通服务门控**的只有 **2** 个（`0x22`/`0x2E`；
`0x27` 的读取属其自身状态机，见 §2.4 注）。

### 8.2 可执行部分（现有守卫的完整枚举）

| SID | 有序 guards（= 今天的 inline 顺序） | NRC |
|-----|-------------------------------------|-----|
| `0x22` | ① 长度（`len>=3` 且 `(len-1)%2==0`）<br>② **逐 DID 循环**：未知 DID → `31`；`security_level < required_security_level(did)` → `33` | `13`、`31`、`33` |
| `0x2E` | ① `len >= 4`<br>② DID 已在 `DID_DATABASE`<br>③ `session != DEFAULT`<br>④ `security_level >= LEVEL1`<br>⑤ DID ∈ `WRITABLE_DIDS`<br>⑥ 长度 ∈ `WRITABLE_DID_LENGTHS[did]` | `13`、`31`、`7F`、`33`、`22`、`13` |
| `0x27` | ① 可取子功能字节<br>② 子功能 ∈ {01,02}<br>③ 子功能对应长度<br>④ 锁定状态<br>⑤ 已解锁时不得再 RequestSeed | `13`、`12`、`13`、`37`、`22` |
| 其余 12 个 | **`guards = ()`**（无授权守卫；其 NRC 全部为格式/范围/序列类） | — |

`0x22` 的第 ② 条是**单个 guard 内部的循环**，其"短路顺序即 DID 顺序"的语义
必须在迁移后**逐字节保持**（见 §10、§13.4）。

### 8.3 明确标记为 `NEW POLICY` 的项（本设计**不**引入）

下列"理论上可以加"的限制**当前源码不存在**，本设计**不添加**：

```
0x14 / 0x19 / 0x31 / 0x34 / 0x35 / 0x36 / 0x37 / 0x38 / 0x3E / 0x85
    → 不加会话限制、不加安全限制（当前均为无条件可调用）
0x22 → 不加会话限制（当前只受 per-DID 安全约束）
0x27 → 不改其状态机、不改锁定语义、不改 seed 生命周期
```

> 若未来决定为上述任一服务**新增**策略，必须在矩阵中标 `NEW POLICY`、
> 明确写为**行为变更**、并同步更新测试与 `TEST_MATRIX`/`STATUS` 文档。
> **不得**在 C1 中"顺便"添加。

---

## 9. NRC Precedence（**FROZEN**）

### 9.1 冻结内容

> **NRC 优先级是"每服务、有序、且对 `0x22` 而言 payload 顺序相关"的既有契约。**
> C1 **不重新定义**它，而是把它**显式化并冻结**为可执行、可对拍的形式。

### 9.2 冻结的优先级表（实证）

| SID | 冻结顺序 | 实测锚点 |
|-----|----------|----------|
| `0x2E` | `13`（格式）→ `31`（未知 DID）→ **`7F`（会话）** → `33`（安全）→ `22`（只读 DID）→ `13`（长度） | `2E FF FF → 7f 2e 31`；`2E F1 02 → 7f 2e 7f`；extended+locked → `33`；unlocked+只读 → `22`；长度越界 → `13` |
| `0x22` | `13`（整体格式）→ 逐 DID〔`31`（未知）→ `33`（安全）〕 | `22 F1 87 FF FF → 33`；`22 FF FF F1 87 → 31`；`22 F1 → 13` |
| `0x27` | `13` → `12` → `13` → `37`（锁定）→ `22`（已解锁再要 seed）→ `24` → `35`/`36` | 源码 `:612-720` 的顺序注释 |
| 其余 | 各自源码顺序（**全部为格式/范围/序列类**，无授权类） | §2.5 |

### 9.3 **FROZEN：validation ordering 与 authorization ordering 的关系**

```
每个服务内部：
    format / length guards   （先）
        ↓
    authorization guards     （后，或在 handler 决定的同一位置）
        ↓
    handler 业务逻辑
```

**但"哪个先"由该服务的既有顺序决定，不由全局规则决定**：

- `0x2E`：格式 → **未知 DID（业务）** → **会话（授权）** → **安全（授权）** → …
  → 即"部分业务判定先于授权判定"。
- `0x22`：格式 → **逐 DID 的（业务 + 授权交替）** → 授权与业务**交织**。

**这正是"不能把授权统一前置"的根本原因**（§7.3）。任何声称"NRC 优先级可以
统一为：格式 → 会话 → 安全 → 业务"的方案，都会改变上述已实测的行为。

---

## 10. 0x22 Compatibility（**FROZEN**）

| 问题 | 决定 |
|------|------|
| `required_security_level(did)` 是否保留？ | **保留**，作为 `0x22` 的一个 guard 的实现细节（**不删除、不改变语义**） |
| 是否迁移到统一 policy？ | **部分**：`0x22` 的服务级声明进入 `ServiceSpec`（`payload_scoped=True`、**`required_security=None`** —— per-DID 权限无法用单一等级概括），但**具体判定仍调用 `required_security_level(did)`** |
| API 怎么变 | `required_security_level(did)` **签名与语义完全不变**；新增的只是"它被哪个 guard 调用" |
| 行为怎么保持 | guard 内部**逐 DID 循环 + 短路返回**，与今天 `:552-580` 的顺序逐字节一致 |
| 测试怎么迁移 | **不迁移、不修改**任何既有测试（G2）。新增对拍测试（§13）验证字节等价 |
| 是否存在过渡层 | 不需要过渡层 —— `0x22` 是**一次原子迁移**（单个服务、单个 guard） |

**必须保持的三个已实测行为**（迁移后逐项对拍）：

```
22 F1 87 FF FF  → 7f 22 33     (DID 顺序敏感)
22 FF FF F1 87  → 7f 22 31
22 F1 90        → 62 f1 90 ... (公开 DID 在未解锁时可读)
```

**all-or-nothing 语义**（`uds_server.py:534-536` 文档串）必须保持：
任一 DID 失败即整体失败，**不返回部分数据**。

---

## 11. 0x2E Compatibility（**FROZEN**）

| 问题 | 决定 |
|------|------|
| 六个 rung 如何迁移 | **一对一映射为 6 个有序 guards**，顺序不变 |
| 是否复用统一 helper | 是：④（安全等级）与 `0x22` 的 `33` 判定共享同一个 `require_security(state, level)` 语义，但**调用点各自保留**（因为位置不同） |
| 会话判定 | ③ 使用 `require_session(state, {EXTENDED, PROGRAMMING})` 的**等价语义**（当前实现是 `session == DEFAULT → 拒绝`，二者在三个会话集合下等价；实现时**保持 `== DEFAULT` 的写法**以避免引入新语义） |
| API 怎么变 | `service_write_data_by_identifier(request, state)` **签名不变** |
| 行为怎么保持 | 6 个 rung 的顺序与 NRC 值逐字节不变（§9.2 的 6 个实测锚点） |
| 测试怎么迁移 | **不修改**既有测试；新增对拍测试 |
| 过渡层 | 不需要 —— 一次原子迁移 |

**注意（易错点）**：③ 的当前实现是 `if state.session == SESSION_DEFAULT: → 0x7F`，
**不是** `if state.session not in allowed: → 0x7F`。在只有三个会话、且
`SUPPORTED_SESSIONS` 已确保 `session` 只能取三值的前提下二者等价，
但**写法不同**；迁移时必须保留 `== DEFAULT` 形式，或在矩阵中显式声明
"allowed = {EXTENDED, PROGRAMMING}" 并用对拍测试证明等价。

---

## 12. 0x27 Integration Boundary（**FROZEN**）

### 12.1 边界

```
0x27 handler                     ← 保持现状：状态机 + 自身 NRC
    ↓ 写 state.security_level
EcuState.security_level          ← C1 读取的唯一安全事实源
    ↓
permission evaluation (guards)   ← C1 的消费者
```

**分工**：

| 归 `0x27`（**不改**） | 归 C1 |
|-----------------------|-------|
| seed/key 算法与生命周期 | **读取** `security_level` 做授权判定 |
| 失败计数 / 锁定 / 过期 | 把"某服务需要某等级"显式化为 guard |
| `27 01` 已解锁 → `0x22` | 定义 `security_level` 的**语义契约**（等级比较 `>=`） |
| 提升 `security_level` | 不写 `security_level`（C1 **不新增任何写入口**） |

### 12.2 与 gateway 的关系（**FROZEN：C1 不做此事**）

`0x27` **不在静态 `SERVICES` 表**内，由 `ecu.py` 每请求注入
（`uds_server.py:2121-2130` 注释 + `ecu.py:352-395`）。
**C1 不改变这个边界，也不让 0x27 经网关可达** —— 那是 **C3** 的范围。

C1 只承诺一件事：**当 `security_level` 被（任何合法途径）提升后，
统一 permission evaluation 能正确消费它。** 至于"经网关能否提升"，
C1 明确留给 C3（见 §16）。

---

## 13. Gateway Boundary（**FROZEN**）

### 13.1 两个概念必须分开

| 概念 | 归属 | 内容 |
|------|------|------|
| **ECU service authorization** | **C1** | "这个 ECU 是否允许这台 Tester 调用这个服务/子功能/DID" —— 取决于 `EcuState` |
| **Gateway policy** | **C2** | 网关作为**网络中间设备**的报文级策略（ID 过滤、限速、转发授权）—— 不取决于任何 ECU 的会话状态 |

> **C1 的 permission framework 属于 ECU service authorization，不属于 gateway policy。**
> 二者**不得混为一谈**。

### 13.2 三种关系的比较（**FROZEN：选 A**）

| 方案 | 描述 | 评价 |
|------|------|------|
| **A（FROZEN）** | permission 在 ECU（协议层），gateway 完全不参与 | ✅ 保持 OQ7 分层；✅ C1 改动面仅限 `uds_server.py`；✅ 与"C1 = ECU 服务授权"的定义一致 |
| B | permission 在 gateway | ❌ 把 ECU 的会话/安全语义搬到网络层；会破坏 `gateway.py` 纯路由；且**与 C1 的定义不符**（那是 C2） |
| C | gateway policy + ECU authorization 双层 | ⚠️ 长期形态可能需要（若将来有真实网关策略需求），但**属于 C2 与 C1 的叠加**，不是 C1 的内容；本设计**不排除**它，但**不在 C1 中实现** |

### 13.3 具体问题回答

| 问题 | 回答（FROZEN） |
|------|----------------|
| physical request | gateway 按 CAN ID 路由到唯一 ECU → 该 ECU 用自己的 `state` 做授权判定 |
| functional request (`0x7DF`) | gateway 对**每个** ECU 调 `process()` → **各 ECU 各自独立判定与执行**；网关不收集响应，因此**不存在跨 ECU 的授权结果聚合** |
| gateway forwarding | 网关无授权判断，纯路由（现状保持） |
| ECU session | 每 ECU 独立，`C1` 只读取自己的 `state` |
| ECU security level | 同上 |
| C1 何时可能影响网关路径 | **只有通过 `VirtualEcu.handler` 的行为间接影响**（因为 handler 内含 guards），网关代码本身**无改动** |

**显式声明**：`gateway.py` 在 C1 中**零改动**；OQ7 冻结的
"`gateway.py` = pure routing（不 import isotp/socket）" 与
"`gateway_runtime.py` = transport + lifecycle" **均不受影响**。

---

## 14. Migration Strategy

> **两个阶段的划分（FROZEN）**：
>
> ```
> Phase 1  =  Build complete ServiceSpec model, but keep legacy execution path
> Phase 2  =  Dispatcher cutover + byte-for-byte per-service migration
> ```
>
> **核心区别**：Phase 1 里**模型是完整的**（含真实的 guards），但
> **执行权威仍然是旧的 `SERVICES → handler`**；Phase 2 才把执行权切换给 `guards`。

### Phase 1 — 建立**完整**模型，但保留旧执行路径（**零可观测行为变更**）

1. 引入 `ServiceSpec`（§7.4）。dispatcher **不变**：仍查 `dict[int, Callable]`。
   新增一个**平行的** `SERVICE_SPECS: dict[int, ServiceSpec]` 作为**模型**。

2. **15 个已实现服务全部构造完整 spec**（不是占位、不是"后填"）。

3. **`guards` 不再"全部为空"** —— 按服务的真实授权语义分别构造：

   | 服务 | `payload_scoped` | `required_security` | `guards`（Phase 1 即完整） |
   |------|------------------|---------------------|---------------------------|
   | **`0x22`** | `True` | `None` | **完整真实 guard**：表达 `format → 逐 DID 循环 → 未知 DID=31 → 权限不足=33 → 首个失败 DID 决定 NRC → all-or-nothing`（复刻 `:552-580` 的语义） |
   | **`0x2E`** | `True` | `SECURITY_LEVEL1` | **完整六级链**：`13 → 31 → 7F → 33 → 22 → 13`（与 `:745-780` 的 inline 顺序逐级对应） |
   | 其余 **12** 个 | `False` | `LOCKED`（或按真实声明） | **`()`** —— 它们**没有**授权 guard（其 NRC 全部为格式/范围/序列类，见 §8.2） |

   > 这与 OD-2 冻结规则**一致**：`payload_scoped=True → guards 非空`（`0x22`/`0x2E` 均满足）；
   > 其余 12 个 `payload_scoped=False → guards 允许为空`（§18.0 OD-2）。
   > **"`guards` 暂为空"的旧写法已废弃** —— 它与该规则冲突。

4. **`0x22` / `0x2E` 的 guards 在 Phase 1 就构造完整、真实**，描述**当前已有**的
   inline authorization semantics；**但 Phase 1 的 dispatcher 不执行它们**。
   它们的作用仅限于：**模型校验（model validation）** 与 **矩阵 / 审计**。

5. 建立并执行 **Phase 1 范围的静态 / conformance 检查**（§15.1、§18.0 OD-2）：
   结构有效性、`payload_scoped → guards != ()`、`0x22`/`0x2E` 的 guard 存在性、
   声明一致性。**不要求 dispatcher 使用 guards。**

6. **退出条件**：模型内部有效 + Phase 1 检查通过 + **907 用例全绿 + 零字节变化**。

### Phase 2 — dispatcher 切换 + 逐服务字节对拍迁移

7. **切换 dispatcher**：改为 `resolve ServiceSpec(sid) → 求值 spec.guards →（全部通过后）调 spec.handler`（OD-1 冻结的形态）。
8. **原子迁移 `0x22`**：**先**记录迁移前的完整响应矩阵（golden），**再**切换，**后**逐字节对拍；不符即回滚该服务。
9. **原子迁移 `0x2E`**：同上（六级链逐级对拍）。
10. **本阶段新增检查**：`legacy handler 结果` vs `spec.guards + spec.handler 结果` **逐请求逐字节对拍**
    （这是 Phase 1 **没有**的一层，见 §15.1）。
11. **退出条件**：两个服务对拍全绿 + 907 全绿。

### Phase 3 — 收敛与清理（**可选，且需单独评审**）

12. `SERVICES` 与 `SERVICE_SPECS` 合并为单一结构（`SERVICES` 变为 spec 的派生视图或只读别名）。
13. "新服务必须声明授权语义"（G4）强制化：新增 SID 若无 spec 则拒绝加载 / 测试失败（消除 P5）。
14. 删除过渡代码。**不删除** `required_security_level(did)` —— 它是 `0x22` guard 的实现。

**关于 `SERVICES → dispatcher → handler` 如何平滑迁移**：

```
Phase 1:   dispatcher 仍走   SERVICES[sid] → legacy handler
           同时存在         SERVICE_SPECS[sid]（完整模型，含真实 guards，但不执行）

Phase 2:   dispatcher 改走   spec.guards → spec.handler
                            （0x22 / 0x2E 逐服务原子迁移 + 字节对拍）

Phase 3:   SERVICES 与 SERVICE_SPECS 收敛为单一结构（可选，单独评审）
```

**关键约束**：`handle_uds_request(..., services=...)` 的**注入契约必须保持**
（`ecu.py:386-389` 依赖它注入 0x27）。Phase 3 的合并**不得**破坏该参数。
若最终 `services=` 的形态需要改变，属**接口变更**，须单独评审（见 §18.1 OD-4）。

---

## 15. Test Strategy

> **阶段划分（本文档跨越两个 PR，措辞必须区分）**：
>
> | 阶段 | 内容 | 是否改动测试 |
> |------|------|--------------|
> | **PR #25** = C1 Design Freeze | 设计冻结（本文档初版） | **否** —— 只设计测试矩阵 |
> | **PR #26** = C1 Implementation Preflight / Contract Lock | 冻结 OD-1/OD-2 + **实际新增 29 个 contract pins** | **是** —— 新增 `tests/test_c1_contract_pins.py` 与 `run_tests.sh` 接入（+8 行） |
> | **Phase 1**（未来）= 完整模型 + 结构检查 | 15 个服务全部构造完整 spec（含 `0x22`/`0x2E` 的真实 guards），**但 dispatcher 仍走 legacy handler** | 将新增防漂移机制与 Phase 1 结构/conformance 测试 |
> | **Phase 2**（未来）= dispatcher 切换 | `spec.guards` 生效；`0x22`/`0x2E` 逐服务原子迁移 | 将新增 `legacy ↔ 新路径` 的逐字节对拍 |
>
> 即：**PR #25 的设计阶段不改测试；PR #26 的 preflight 已按本文档所述新增了
> contract-pin 测试**。下文 §15.1/§15.2 描述的是**测试设计**（PR #25 起即有），
> 其**已落地的部分**见 §15.7 的 pin 表；尚未落地的部分按 §14 归属 Phase 1 或 Phase 2。

### 15.1 测试矩阵（按主题）

> **落地状态**：★ = **PR #26 已落地**（见 §15.7 的 29 例 pin 表）；
> 其余条目按 §14 的阶段归属 **Phase 1**（完整模型 + 结构检查）或 **Phase 2**（切换 + 字节对拍）。
> 本表是**测试设计**，不是"尚未开始"的意思。
>
> **Phase 1 与 Phase 2 的检查不是同一层**：Phase 1 只验证**模型的内部有效性**
> （结构规则、guard 存在性、声明一致性），**不要求 dispatcher 执行 guards**；
> Phase 2 才新增 `legacy handler` ↔ `spec.guards + handler` 的**逐字节对拍**。
> 二者**不得混成一个执行路径**（§14）。

| 主题 | 用例 | 状态 |
|------|------|------|
| **Session** | 三个会话各自 × {允许的服务、拒绝的服务}；`0x10` 切换后 `security_level` 归 LOCKED（★ F-a）；复位后回 DEFAULT；per-ECU 隔离（多 ECU 各自会话） | 部分 ★ |
| **Security** | `required level satisfied` / `insufficient`；`security_level` 提升后原被拒请求转为允许；切会话重新锁定后再次被拒（★ F-a） | 部分 ★ |
| **Permission** | service allowed / denied；subfunction denied；**DID denied**（per-DID 粒度，★ F-b/F-c）；`payload_scoped` 服务的 payload 相关分支 | 部分 ★ |
| **NRC priority** | 格式+会话、未知 DID+会话（★ N1）、安全+会话、未知服务+授权、`0x33` 先于 `0x22`（★ N4）、`0x22` DID 顺序（★ N2） —— **每组都必须断言"先发生的是哪一个"** | 大部分 ★ |
| **Matrix conformance（Phase 1）** | **结构有效性**：`payload_scoped → guards != ()`；`0x22`/`0x2E` 的 guard 存在；声明一致性（按服务类别分别判据，见 §18.0 OD-2）。**不要求 dispatcher 使用 guards** | Phase 1 |
| **Byte-for-byte migration（Phase 2）** | `legacy handler 结果` vs `spec.guards + spec.handler 结果` **逐请求逐字节对拍**（按服务类别分别判据） | Phase 2 |

### 15.2 NRC 优先级专项（**最关键的回归网**）

必须固化为可执行断言（当前**无任何测试**覆盖）：

```
2E FF FF            → 7f 2e 31     ← 未知 DID 先于会话   [当前 NOT COVERED]
2E F1 02            → 7f 2e 7f
22 F1 87 FF FF      → 7f 22 33     ← DID 顺序敏感         [当前 NOT COVERED]
22 FF FF F1 87      → 7f 22 31
22 F1               → 7f 22 13
```

### 15.3 回归保护（**不修改任何既有测试**）

| 层 | 数量 | 作用 |
|----|------|------|
| unit | **673** | 迁移后必须全绿（其中 `test_ecu_unit.py` 116、`test_services_extra_unit.py` 17 直接覆盖 0x22/0x2E 门控） |
| e2e（vcan） | **234** | 迁移后必须全绿（真实链路逐字节） |
| gateway unit | **13 + 53** | 证明 C1 **未**影响路由/运行时 |
| gateway runtime e2e | **21** | 证明 C1 **未**影响 OQ7 的 vcan0 行为 |
| Fast CI（`qa_fast.sh`） | ruff + mypy + unit + can-tools offline | 每次提交把关（e2e 不在 CI 内，需本地跑） |

**"不发生非预期行为变化"的判据（FROZEN）**：
> 迁移前后，对**同一组 (请求, 初始 state)** 的响应必须**逐字节相同**。
> 该判据由 Phase 2 的 golden 对拍覆盖，而不是靠"测试全绿" —— 因为
> §15.2 已证明**当前测试无法发现 NRC 优先级的变化**。

### 15.4 已知的覆盖缺口（**必须在 C1 中补上**）

> 全部经独立测试审计确认（逐文件 grep + AST 复核）。

| # | 缺口 | 证据 |
|---|------|------|
| N1 | **"默认会话 + 未知 DID" on `0x2E`（`31` vs `7F`）** | 唯一的 0x2E-未知 DID 用例是 `test_services_extra_unit.py:149`，但它**先在 `:150` 调 `unlock_extended`** → 只在 extended+LEVEL1 下固化 `31`。所有默认会话的 0x2E 用例都用**已知** DID |
| N2 | **`0x22` 的 DID 顺序敏感性（`31` vs `33`）** | **没有任何测试把未知 DID 与受保护 DID 放进同一请求**。唯一的多 DID 混合用例是（公开+未知）`test_ecu_unit.py:698` 与（公开+受保护）`:1612/:1630`、`test_e2e_vcan.py:1598` —— 均**不含未知+受保护**的组合 |
| N3 | **`0x14` / `0x19` / `0x85` 从未在非默认会话下被测试过** | `test_dtc_unit.py` / `test_dtc_clear_unit.py` / `test_dtc_vcan.py` / `test_dtc_clear_vcan.py` 中**没有任何会话切换**（vcan 文件里唯一的 `0x10` 是 `_wait_for_ecu_ready` 的就绪探测）→ 这三个服务**只在 DEFAULT 会话下被验证过**。**若将来把会话门控加到 dispatcher 而非 handler，它们会被静默回归，且现有测试抓不到** |
| N4 | **`0x2E` 的 `0x33` 先于 `0x22`（只读 DID + LOCKED）** | 两个只读 DID 用例都在 `unlock_extended()` **之后**执行（`test_services_extra_unit.py:157`、`test_write_did_vcan.py:185`）→ 该顺序未被固化 |
| N5 | **seed 过期 / 锁定到期无 e2e 覆盖** | 仅 `test_ecu_unit.py:1142/1157/1252/1259` 用 `mock.patch.object(ecu, "_now", …)` 覆盖；`test_e2e_vcan.py` 中检索 `expiry/expired/lockout_seconds/seed_expiry` **零命中**，e2e 的锁定用例从不让锁定自然到期 |
| N6 | **`services=` 注入缝零测试覆盖** | 全仓库 `handle_uds_request(..., services=...)` 的**唯一使用者是 `ecu.py:386-389`**；**没有任何测试直接使用它**（仅 `test_gateway_runtime_vcan.py:648` 有一句 docstring 提及）→ **C1 若复用它（OD-4），该缝本身当前无回归网** |
| N7 | **无任何测试断言 `SERVICES` 的键集或内容** | 全仓库对 `SERVICES` 的引用仅 `uds_server.py` 内部、`ecu.py:380/387`、`gateway.py:65` → 注册表重命名/删除只能被**间接**发现 |

**N3 是本次审计中最重要的新发现**：它把 §7.3/§9.3 的结论从"设计偏好"提升为
**可验证的风险** —— 一个"看起来更干净"的 dispatcher 全局会话门控，会**静默改变**
`0x14`/`0x19`/`0x85` 的行为，而**现有 907 个用例不会发现**。

### 15.5 必须声明为"冻结"的高价值既有 pin（设计不得回归）

以下 7 组是当前**最强的既有保护**，C1 必须逐项保持不变：

| # | 冻结项 | pin 位置 |
|---|--------|----------|
| F-a | **切会话把 `security_level` 归 LOCKED**（三种会话类型全覆盖） | `test_ecu_unit.py:1398/1409/1421`；e2e `test_e2e_vcan.py:1472` |
| F-b | **受保护 DID 在 default / extended / programming 三种会话下均被拒** | `test_ecu_unit.py:1543/1551/1561`；e2e `test_e2e_vcan.py:1568/1576` |
| F-c | **DID 级（而非服务级）权限** —— 同一状态下公开 DID 可读、受保护 DID 被拒 | `test_ecu_unit.py:877`（遍历全部非受保护 DID）+ `:1537` + `:1543` |
| F-d | **`0x22` 多 DID all-or-nothing 且不泄漏部分数据** | `test_ecu_unit.py:1612/1630/1642`；e2e `test_e2e_vcan.py:1598/1611` |
| F-e | **`0x2E` 的三级门控顺序 `7F` → `33` → `22`** | `test_services_extra_unit.py:124/130/156`；e2e `test_write_did_vcan.py:158/163/184` |
| F-f | **`0x27` 的 35/35/36 序列、锁定阻断所有 0x27、且 `0x12` 优先级高于 `0x37`** | `test_ecu_unit.py:1175/1201/1227`；e2e `test_e2e_vcan.py:1649/1756` |
| F-g | **切会话不得重置非门控服务的状态**（routine / download / upload / file 传输） | `test_routine_unit.py:350`、`test_transfer_unit.py:585`、`test_upload_unit.py:659`、`test_file_transfer_unit.py:1819/1831/2148` + 对应 e2e |

### 15.6 回归保护的实际边界（如实说明）

| 层 | 数量 | 在 GitHub Fast CI 内？ |
|----|------|----------------------|
| unit（11 文件） | **673** | **是**（`qa_fast.sh` → `run_tests.sh unit`） |
| e2e / vcan（11 文件） | **234** | **否**（仅 `qa_extended.sh`；CI 里是 manual `workflow_dispatch` 的 extended job） |

→ **迁移验证不能只依赖 CI**：Phase 2 的字节对拍**必须本地跑 `run_tests.sh e2e`**，
因为 234 个在网用例不在自动门禁内。

### 15.7 冻结 pin 表（**已实现**于 `tests/test_c1_contract_pins.py`，29 例）

所有 `Expected` 均为**对当前源码的实测值**（非推断）。`Context` 指发起请求前的 `EcuState` 设置。

| Pin | Request | Context | Expected | Reason |
|-----|---------|---------|----------|--------|
| **N1** | `2E FF FF` + `X` | DEFAULT | `7F 2E 31` | 未知 DID 判定**先于**会话门控；防"授权统一前置"把 `31` 变 `7F` |
| **N1** | `2E F1 02` + `X` | DEFAULT | `7F 2E 7F` | 越过 DID 判定后才撞会话门控（同状态对照） |
| **N2** | `22 F1 87 FF FF` | LOCKED | `7F 22 33` | 受保护 DID 在前 → 安全拒绝（**顺序敏感**） |
| **N2** | `22 FF FF F1 87` | LOCKED | `7F 22 31` | 未知 DID 在前 → 范围错误（**仅顺序不同**） |
| **N2** | `22 F1 90 FF FF` | LOCKED | `7F 22 31` | 公开 DID 在前也因后续未知 DID 整体失败 |
| **N3** | `14 FF FF FF` | DEFAULT / EXTENDED / PROGRAMMING | `54`（三者相同） | 冻结"**无会话门控**"；防 dispatcher 全局门控静默改变 |
| **N3** | `19 0A` | 同上三者 | `59 FF 10 00 01 01 10 00 02 02 10 00 03 04` | 同上 |
| **N3** | `85 01` | 同上三者 | `C5 01` | 同上 |
| **N4** | `2E F1 90` + `X` | EXTENDED + LOCKED | `7F 2E 33` | 安全门控**先于**只读 DID 判定（既有测试只在 unlocked 下断言过） |
| **N4** | `2E F1 90` + `X` | EXTENDED + LEVEL1 | `7F 2E 22` | 越过安全门控后才撞只读判定 |
| **N5** | — | — | （**不加重复用例**） | seed 过期 / 锁定到期**已由 `test_ecu_unit.py:1142/1157/1242/1268` 覆盖**；仅保留"这些用例仍存在"的结构性断言 |
| **N6** | 任意 4 条请求 | `services=dict(SERVICES)` | 与默认路径**逐字节相同** | 冻结 `services=` 缝的等价语义 |
| **N6** | `22 F1 90` | `services={0x3E: …}` | `7F 22 11` | 覆盖表**取代**而非合并 |
| **N6** | `3E 00` | `services={0x3E: custom}` | `7E 00` 且 handler 收到请求 | 冻结"覆盖可安装自定义 handler"（C1 迁移依赖它） |
| **N7** | — | — | `SERVICES` key set = **恰好 14 个** | 防迁移时无意增删注册项 |
| **N7** | — | — | `0x27 ∉ SERVICES` | 冻结"0x27 由入口层注入"的边界（C1/C3 都不得挪动） |
| **N7** | — | — | `SERVICES` 的每个值都是 **callable** | 记录**迁移前**的表形状；改变形状必须是显式评审事件 |
| **F-a** | `10 01/02/03` | 已解锁 | `security_level → LOCKED` | 切会话即重新锁定（三种会话全覆盖） |
| **F-b/F-c** | `22 F1 90` / `22 F1 87` | 三种会话（各自同状态） | `62 …` / `7F 22 33` | **DID 级**（非服务级）权限；同状态对照 |
| **F-c** | `22 F1 87` | EXTENDED + LEVEL1 | `62` + 出厂值 | 解锁后可读 |
| **F-d** | `22 F1 90 F1 87` | LOCKED | `7F 22 33` 且**不含** F190 数据 | all-or-nothing 且**不泄漏部分数据** |
| **F-d** | `22 F1 90 F1 87` | LEVEL1 | `62` + 两个 payload，且顺序保持 | 成功路径的载荷与顺序 |
| **F-e** | `2E F1 02` / `2E F1 90` | 逐级越过 | `7F` → `33` → `22` → `6E` | `0x2E` 完整门控链顺序 |
| **F-f** | `27 01` | 静态协议层 | `7F 27 11` | 冻结 0x27 的**注入边界**（状态机本身已由 unit 覆盖，不重复） |
| **F-g** | `85 02` → `10 03` | — | `dtc_setting_enabled` 仍 `False` | 切会话**不得**重置非门控状态 |
| **F-g** | `2E F1 02` → `10 01` | EXTENDED+LEVEL1 | DID override 仍在 | 同上（只有复位才清） |

**数量核对**：本文件 29 例 = 上表 26 行 + 3 条结构性/对照断言
（N1 的同状态对照、N2 的顺序对照、N5 的"既有用例仍存在"）。

---

## 16. Cross-OQ Dependencies

| 候选 | 与 C1 的关系 | 代码依据 |
|------|--------------|----------|
| **C2** Gateway Security Policy | **independent**（可读 state 不需 C1）；但**语义一致性上 benefit from C1** | `gateway.py:43`/`:85` 已**持有** `EcuState`（对象在手，无需新增管线）；措辞精确化：**今天没有任何 gateway 模块读取任何 state 字段**（策略关键字在两个 gateway 模块中 0 匹配）。C1 不为其前置 |
| **C3** 0x27 经 Gateway | **independent**（与 C1 正交） | C3 改的是 gateway 侧的 handler 装配（也可完全不改 `gateway.py`，见下）；C1 改的是协议层授权判定。**C2 的"基于安全等级的"授权维度依赖 C3**（精确化：仅安全等级维度被阻塞 —— 会话维度的授权**今天就能经网关端到端行使**，因为 `0x10` 在静态表内且能改变 per-ECU `state.session`，并由 `test_gateway_unit.py:107-113` 与 `test_gateway_runtime_vcan.py:583-621` 固化） |
| **C4** DTC Fault Injection | **条件依赖 C1** | 若做成**协议能力**，"谁有权注入"是授权问题；若只做进程内 seam，则不需要 |
| **C5** suppress 统一化 | **independent**（但**争用 dispatcher 同一修改点**） | 两者都改 `handle_uds_request` 区域 → 文件级冲突，非语义依赖 |
| **C6** Firmware transfer security | **independent** | ResumeFile/integrity 都在 `0x38`/`0x36`/`0x37` 内；当前无授权门控 |
| **C7** Stateful UDS Fuzz | **benefits from C1** | 跨请求序列 fuzz 的价值随"会话/安全链"（`10 03 → 27 01 → 27 02 → 2E`）增加而增加；C1 使该链的语义显式化 |
| **C10** 0x28 CommunicationControl | **条件依赖 C1 的"新服务声明"要求** | 若 C1 的 G4（新服务必须声明授权）先落地，C10 必须声明；否则 C10 只需 `guards=()` |
| **C11** 0x19 reportType | **independent** | 纯报告类扩展；当前 `0x19` 无授权门控 |

**分类汇总**：

```
depends on C1   : （无硬依赖；C4(协议形态) 与 C10(视 G4) 为条件依赖）
benefits from C1: C2（语义一致性）、C4（"谁可注入"这一设计问题）
independent     : C3、C5、C6、C7、C10、C11
must remain separate: C2（gateway policy ≠ ECU authorization）
```

**§16 的三处精确化（经独立代码复核修正）**：

1. **C2 的依赖边**：只有**基于安全等级的**授权维度被 C3 阻塞；**会话维度**的授权
   今天就能经网关端到端行使（`0x10` 在静态表内、能改 per-ECU `state.session`，
   且 `0x2E` 的会话门控（`:758`）会读它）→ **不得**笼统写成"C2 的授权维度依赖 C3"。
2. **C3 对 `gateway.py` 的改动不是必然**：`VirtualEcu.handler` 已是公开注入缝
   （`gateway.py:86`），而装配点 `gateway_runtime.py:508` 已经 import `ecu`（`:68`）
   → 可**完全不改 `gateway.py`**（该方案同时避免让纯协议层获得时钟/配置依赖）。
3. **C1 的 方案 B（`SERVICES` 值类型变更）会外溢到 C3**：`ecu.py:386-389` 的
   `{**uds_server.SERVICES, **_security_services()}` 是唯一的组合先例，
   而 `_security_services()` 返回裸 callable（`ecu.py:366-371`）；若采用方案 B，
   C3 的组合点也需同步适配 —— 这也是 OD-4 必须单独评审的原因。

---

## 17. Risks

| # | 风险 | 缓解 |
|---|------|------|
| R1 | **静默改变 NRC 优先级**（当前无测试保护，见 §15.2） | Phase 2 的 golden 对拍 + 新增 §15.2 断言 |
| R2 | 把 `0x22` 的 per-DID 语义退化成 SID 级 | §7.2 已证明 SID 级不可行；平移测试覆盖该粒度（既有的 e2e 已保护 per-DID） |
| R3 | 让 `SERVICES` 的类型变化破坏 `ecu.py` 的注入契约 | §14 明确 `services=` 契约必须保持；Phase 3 若需变更则单独评审（OD-4） |
| R4 | 借 C1 之名给 12 个服务**新增**限制（行为变更） | §8.3 显式禁止；新增必须标 `NEW POLICY` 并走行为变更流程 |
| R5 | 矩阵声明与执行逻辑**漂移**（两处真相） | §7.4 的 `guards` 是执行真相、声明字段仅用于矩阵；用 §15.1 的矩阵对拍测试锁死一致性 |
| R6 | 迁移期"两套路径"（spec 与 SERVICES）长期并存 | §14 Phase 3 明确收敛为单一结构 |
| R7 | 对 OQ7 的 gateway 分层造成间接侵蚀 | §13 明确 `gateway.py` 零改动；gateway 的 13+53+21 用例作为回归网 |
| R8 | 为"看起来完整"而新增会话 | §6.2 明确不新增 |

---

## 18. Open Decisions

> 本节记录**仍未决**的问题。OD-1 与 OD-2 已于 **2026-09-24 Preflight** 冻结，
> 其完整讨论（Options considered / Decision / Rationale / Regression protection）
> 见 §18.0，**历史讨论保留不删**。

### 18.0 OD-1 与 OD-2 —— **DESIGN FROZEN**（2026-09-24 Preflight）

#### OD-1 — `guards` 与 handler 的调用关系

**Options considered**

| 方案 | 形态 |
|------|------|
| **A（选定）** | dispatcher 解析出当前 service 的 `ServiceSpec`，**按声明顺序**求值该服务的 `guards`；**全部返回 `None` 后**才调用 handler |
| **B** | handler 自己调用 guard 链 |
| **C（明确拒绝）** | **全局** dispatcher 授权前置（对所有服务共享一条策略） |

**Decision：选 A** —— 即

```
request → resolve ServiceSpec(当前 SID) → guard[0] → guard[1] → … → handler
```

**必须严格区分（本设计的核心边界）**：

| | 全局 dispatcher precheck | per-service ordered guard evaluation |
|---|---|---|
| 策略条数 | 1 条，所有服务共享 | 每个 SID **各自一条有序链** |
| 对 `2E FF FF` 的影响 | 由 `31` 变 `7F`（**行为变更**） | 不变（DID guard 仍在 session guard 之前） |
| 对 `0x14/0x19/0x85` | 会被顺带门控（**行为变更**） | 不受影响（其 `guards = ()`） |
| 本设计 | **禁止** | **即本方案** |

**Rationale（逐项回答比较维度）**

1. **NRC precedence 稳定表达**：A 把顺序放进**数据**（tuple）→ 可枚举、可断言、可对拍；B 把顺序留在 handler 代码里 → 与今天一样"只能靠读代码"。
2. **`0x22` payload/DID 顺序**：A 用一个 guard **逐字复刻现有循环**（`uds_server.py:552-580`），顺序敏感性与 all-or-nothing 都保留；B 同样可行，但顺序不可枚举。
3. **`0x2E` unknown-DID → session 顺序**：A 把 `31` guard 排在 `7F` guard **之前**，顺序由数据保证；B 同样可行。
4. **guard 是否重复执行**：A 中每个 guard **恰好执行一次**，且**只在 handler 之前**；B 中 handler 可能调用多次或跳过 → A 更安全。
5. **handler 仍能独立处理 malformed request**：A 不把 handler 变成"信任输入"——格式/长度 guard 先行，handler **保留自己的防御性解析**（如 `0x38` 的 per-mode 结构检查）。行为与今天一致。
6. **测试如何锁定执行顺序**：A 可断言 **guard tuple 的顺序**与**由此产生的 NRC 序列**（本 Preflight 已建 pin）；B 只能做行为测试。
7. **将来 C2/C3 是否复用**：A 的 spec/guard 词汇是**协议层资产**；C3 可复用"装配点"（注入 spec），C2 不参与（见 §13）。
8. **metadata 与真实执行逻辑双写**：与 OD-2 联合解决 —— OD-2 选定"**只有 `guards` 执行**"，声明字段由防漂移测试校验（见下），因此**不存在双写执行**。
9. **是否破坏现有 907 cases**：A 与 B 在"顺序保持"的前提下**都不破坏**；A 的价值在于**让"顺序是否保持"可被验证**（今天的 907 用例**无法**发现 NRC 优先级变化）。
10. **是否破坏 `services=` override**：A 需要一个显式的**归一化规则**（见下），以保持今天的语义。

**`services=` override 的归一化规则（FROZEN）**

```
services=None                     → 静态 SERVICES（各自 spec）
services={sid: Callable}          → 归一化为 ServiceSpec(sid, handler=callable, guards=())
```

即：**覆盖一个服务 = 整体替换该服务（含其 guards）**。这与今天的语义**一致**
（今天覆盖即替换整个 handler），因此 `ecu.py:386-389` 的 0x27 注入契约不变、
既有调用方式不变 → **不构成接口变更**（OD-4 仍保持开放，见 §18.1）。

**Regression protection（本次已建立）**

`labs/vehicle/uds-basic/tests/test_c1_contract_pins.py`（29 例）：
N1（`0x2E` DID 先于会话）、N2（`0x22` DID 顺序敏感性）、N4（`0x33` 先于 `0x22`）、
N6（`services=` 缝的三条契约，含归一化相关语义）、N7（静态注册表 key set + 形状）、
F-a..F-g。**迁移前后必须逐字节一致**，由这些 pin + Phase 2 的 golden 对拍共同保证。

---

#### OD-2 — 声明字段是否参与执行

**Options considered**

| 模式 | 形态 | 结论 |
|------|------|------|
| **A** | 声明字段**就是执行源**（框架由 `allowed_sessions`/`required_security` 自动生成 guard） | **拒绝** |
| **B（选定）** | **`guards` 是唯一执行源**；`allowed_sessions`/`required_security`/`payload_scoped` 仅用于 capability matrix / audit / documentation / **静态一致性检查** | **选定** |
| **C** | 声明字段 **+** custom guards **双重执行** | **拒绝** |

**Decision：选 Mode B。**

**Rationale**

- **Mode A 无法表达现有真实行为**（这是拒绝它的硬理由）：
  - `0x22` 的判定是 **per-DID**（同一状态下 `22 F1 87 → 33` 而 `22 F1 90 → 正响应`），
    一个 SID 级 `required_security` **无法表达**；
  - `0x2E` 的顺序是 `31`（业务）**先于** `7F`（授权），由 SID 级声明自动生成的
    guard 只能给出"先会话后安全"，会**改变** `2E FF FF` 的 NRC；
  - 若为迁就声明式而把 `0x22` 的 per-DID 语义降级为服务级，则**牺牲 0x22 的语义** ——
    本设计明确不做此交换。
- **Mode C 被拒绝**：双重执行源会产生 drift，且可能造成同一判定被执行两次
  （顺序与副作用都难以保证）。
- **Mode B 的代价**是"声明可能与 guard 漂移"，因此**必须配防漂移机制**（下）。

**`payload_scoped` 的精确定义（FROZEN）**

> `payload_scoped = True` 表示：**该服务的完整授权行为不能仅由单一 SID-level
> 声明字段完整描述** —— 至少存在 payload / DID / request-specific 的授权语义。

**它并不意味着 `required_security` 必须为 `None`。** 两个字段回答的是**不同的问题**：

| 字段 | 回答的问题 |
|------|-----------|
| `required_security` | 该服务**是否整体要求**某个安全等级？（若整体要求，就是一个合法声明） |
| `payload_scoped` | 单靠声明字段**能否完整描述**该服务的授权行为？ |

因此二者可以**同时成立**（`0x2E` 即如此）。

**防漂移机制（FROZEN，必须实现）**

> 原设计中"`payload_scoped ⟺ required_security is None`"的写法**已废弃** ——
> 它与 `0x2E` 的实际情况冲突（`0x2E` 既是 payload-scoped、又确实整体要求 LEVEL1）。
> 下列规则取代它，且**不引入任何与实际服务冲突的等价关系**。

1. **静态结构规则（可测试，无虚假等价）**：

   ```
   payload_scoped is True   →   guards 必须非空
   ```

   理由（由 OD-2 直接推出）：`guards` 是**唯一执行源**；若一个服务被标记为
   "声明字段不足以完整描述其授权行为"，那么这些行为**只能**存在于 `guards` 中
   → `guards` 不可能为空。反之**不要求**：`guards` 非空不蕴含 payload_scoped
   （非 payload-scoped 的服务同样可以有 guards，只要其行为可由声明完整概括）。

2. **矩阵一致性测试（conformance test）**：**按服务类别分别验证**，
   且**按阶段分层**（Phase 1 / Phase 2 的检查不是同一层，不得混成一个执行路径）：

   **Phase 1 —— 只验证"模型内部有效"（不要求 dispatcher 执行 guards）**

   - **结构规则**：`payload_scoped=True → guards != ()`。
     其余 12 个（`payload_scoped=False`）**允许** `guards=()`。
   - **guard 存在性**：`0x22` / `0x2E` 必须各自拥有**非空**且**与其 inline 语义对应**的 guards。
   - **声明一致性**（按类别不同判据）：
     - **非 payload-scoped 的服务**：声明字段**应当**能完整描述其授权状态 →
       遍历 `(session × security_level)` **全组合**，执行 guard 链，
       把实际 allow/deny 与 `allowed_sessions`/`required_security` **逐格对比**；不一致即失败。
     - **payload-scoped 的服务**：**不得**把 guards 强行压成一张 `session/security` 矩阵
       （那正是 Mode A 的错误）。只验证：① `payload_scoped is True`；② `guards` **非空**
       （承接结构规则）；③ 声明**没有**声称自己完整描述 —— 即 `0x22` 这类服务不得把
       `required_security` 写成某个具体等级来"假装完整"（`None` 是它唯一诚实的取值）。

   **Phase 2 —— 新增"旧实现 ↔ 新路径"的逐字节对拍**

   - 对每个被切换的服务：`legacy handler 结果` vs `spec.guards + spec.handler 结果`
     **逐请求逐字节相同**；任何一格不符即回滚该服务（§14 Phase 2）。
   - 这一层在 **Phase 1 不存在**（Phase 1 的 dispatcher 仍走 legacy handler）。

3. **`0x22` / `0x2E` 如何表达（FROZEN）**：

   | 服务 | `guards` | `allowed_sessions` | `required_security` | `payload_scoped` |
   |------|----------|--------------------|---------------------|------------------|
   | `0x22` | 1 个 guard：**逐 DID 循环 + 短路**（复刻 `:552-580`） | `None`（无会话约束） | **`None`** —— 权限是 per-DID（`F187 → LEVEL1` 而 `F190 → LOCKED`），**无法用单一值概括** | **`True`** |
   | `0x2E` | **6 个有序 guards**（`13→31→7F→33→22→13` 逐级） | `None`（真实约束是"非 DEFAULT"，由 guard 表达；见 OD-5） | **`SECURITY_LEVEL1`** —— 它**确实整体要求**该等级，是合法声明 | **`True`** —— 完整授权链还依赖 DID 存在性 / 可写性 / 长度 / **请求顺序** |

   **`0x2E` 为何同时是 `LEVEL1` 与 payload-scoped**（本设计的关键区分）：

   ```
   required_security = LEVEL1   ：它对**所有 0x2E 请求**都成立（是一个真陈述）
   payload_scoped    = True     ：但单靠它**不足以**描述完整行为 ——
                                  例如 2E FF FF → 7F 2E 31（未知 DID 先于会话）
                                       2E F1 02 → 7F 2E 7F
                                  该差异由 payload（DID 是否已知）与**顺序**决定
   ```

   → 因此 `0x2E` 的声明是"**正确但不完整**"，而这正是 `payload_scoped=True` 要表达的意思。

**Regression protection**：防漂移机制本身即是回归保护
（一致性测试会在声明与 guard 分叉时失败）；叠加 §18.0 OD-1 的 29 个 pin。

---

### 18.1 仍未决（OPEN DECISION）

| ID | 问题 | 为何未决 | 影响 |
|----|------|----------|------|
| **OD-3** | 是否要**强制**"新服务必须有 spec"（G4）？以何种机制？（拒绝加载 / 测试失败 / lint 规则） | 需要先确认对既有 14 个服务与 `0x27` 注入路径的兼容性 | 决定 G4 的落地方式 —— 属 **Phase 3**（收敛与清理） |
| **OD-4** | `handle_uds_request(..., services=...)` 的**最终契约**：保留 `dict[int, Callable]`，还是改为 `dict[int, ServiceSpec]`？ | OD-1 已冻结**归一化规则**（覆盖 = 整体替换，`guards=()`），因此**当前契约可原样保留**；是否需要最终改为 spec 形态，取决于 Phase 3 的收敛方式 | 决定 Phase 3 的收敛方式 |
| **OD-5** | `0x2E` 的会话判定**写法**：保留 `== DEFAULT`，还是改为 `allowed = {EXTENDED, PROGRAMMING}`？ | 当前在三个会话下二者等价；OD-2 已确定"声明不执行"，故改写**只影响 guard 内部实现**，仍需对拍证明等价 | 决定 §11 的实现形式 —— 属 **Phase 2**（`0x2E` 切换时，有逐字节对拍兜底） |
| **OD-6** | 是否把 `0x27` 也纳入 `ServiceSpec`（作为"动态注入的服务如何声明策略"）？ | `0x27` 不在静态表，其注入路径特殊；纳入方式需先解决 OD-4 | 决定矩阵的完整性（当前是**策略盲区**） |

> **OD-1/OD-2 冻结后的连带结论**：OD-4 的**紧迫性下降**（当前契约可保留），
> OD-5 的**影响面缩小**（只影响 guard 内部实现，不影响声明语义）。

---

## 19. Design Freeze

> 以下为**冻结的架构决定**。凡标 `OPEN DECISION` 者**不在冻结范围内**。

| # | 决定 | 状态 |
|---|------|------|
| 1 | **policy 放在哪里** | **协议层（`uds_server.py`）**。**不是** dispatcher 全局前置层（§7.3 两条实测证据），**不是** gateway（那是 C2） | **DESIGN FROZEN** |
| 2 | **policy 数据结构** | `ServiceSpec(sid, handler, guards: tuple[Guard,...], allowed_sessions, required_security, payload_scoped)`；`Guard = (request, state) -> Nrc \| None`；**顺序即语义** | **DESIGN FROZEN** |
| 3 | **session 怎么表示** | 维持 `int` + `SUPPORTED_SESSIONS` frozenset；**三个会话，不新增**；`EcuState.session` 仍是唯一事实源、仍是可直写的公有字段、**不加 property** | **DESIGN FROZEN** |
| 4 | **permission 粒度** | **SID 注册 + payload 相关的有序 guard**（A/B 单独不足，C 必需） | **DESIGN FROZEN** |
| 5 | **security 与 permission 如何组合** | `security_level` 是**唯一安全事实源**；`0x27` 是**唯一写入口**；permission 只**读取**并比较（`>=`）；C1 **不新增** `security_level` 写入口 | **DESIGN FROZEN** |
| 6 | **NRC precedence** | 冻结为**每服务、有序**，且 `0x22` **payload 顺序相关**（§9.2 全部实测锚点）；**不统一前置** | **DESIGN FROZEN** |
| 7 | **0x22 如何兼容** | `required_security_level(did)` **保留**；`0x22` 迁移为**单个 guard 内含逐 DID 循环**；all-or-nothing 与顺序敏感性逐字节保持 | **DESIGN FROZEN** |
| 8 | **0x2E 如何兼容** | 6 个 rung → **6 个有序 guards**；`13→31→7F→33→22→13` 逐字节保持；**写法（OD-5）未决** | **DESIGN FROZEN**（写法除外） |
| 9 | **0x27 如何接入** | 保持 `ecu.py` 注入边界不变；C1 只消费 `security_level`，**不搬动注入、不改状态机**；0x27 经网关可达性 = **C3 的范围** | **DESIGN FROZEN** |
| 10 | **gateway 如何与 ECU authorization 分层** | **C1 = ECU service authorization**；`gateway.py` **零改动**；gateway policy = **C2**；功能寻址下各 ECU **独立**判定 | **DESIGN FROZEN** |
| 11 | **migration strategy** | **Phase 1 = 建立完整 ServiceSpec 模型（15 个服务全构造，`0x22`/`0x2E` 含**完整真实 guards**）+ 静态/conformance 检查，但 **dispatcher 仍走 legacy `SERVICES → handler`**（零可观测行为变更）；**Phase 2 = dispatcher 切换 → `spec.guards` 生效 → `0x22`/`0x2E` 逐服务原子迁移 + 逐字节对拍**；Phase 3 = 收敛与清理（可选，单独评审）。每阶段退出条件均含 **907 全绿 + 字节等价** | **DESIGN FROZEN** |
| 12 | **test strategy** | 六主题新增测试 + NRC 优先级专项（§15.2 的 5 条锚点）+ 矩阵对拍 + 既有 907 作为回归网；**不修改任何既有测试** | **DESIGN FROZEN** |
| 13 | **留给 C2 / C3 的问题** | **C2**：网关策略的求值位置/默认值/拒绝的总线表现/是否读 ECU 状态；**C3**：0x27 经网关的注入装配与"注入语义只定义一处" | **DESIGN FROZEN（边界）** |
| 14 | **OD-1：guards 与 handler 的调用关系** | **per-service ordered guards → dispatcher 对当前已解析 service 求值 → 全部通过后才调 handler**。**明确 NOT a global authorization precheck**（§18.0 OD-1） | **DESIGN FROZEN** |
| 15 | **OD-2：声明字段是否参与执行** | **`guards` 是唯一执行源**；声明字段仅用于 capability matrix / audit / 静态一致性检查；配**防漂移机制**（结构规则 `payload_scoped → guards 非空` + **分类别** conformance test；**不使用** `payload_scoped ⟺ required_security is None` 这类与实际服务冲突的等价式）（§18.0 OD-2） | **DESIGN FROZEN** |
| 16 | **`services=` 覆盖的归一化** | `services=None` → 静态 spec；`services={sid: Callable}` → `ServiceSpec(sid, handler, guards=())`（**覆盖 = 整体替换**，与今天语义一致） | **DESIGN FROZEN** |

**未冻结项（OPEN DECISION）**：**仅 OD-3 / OD-4 / OD-5 / OD-6**（§18.1）。
（OD-1 与 OD-2 已于 2026-09-24 Preflight 冻结。）

### 19.1 C1 实施边界（**再次冻结**，防止范围蔓延）

```
C1 implementation  =  Service Authorization  =  session / security evaluation（per-service，协议层）

明确**不属于** C1（不得由 C1 顺手实现）：
    Gateway routing / filtering / rate limiting / authorization policy   → C2
    0x27 through Gateway（注入装配与"注入语义只定义一处"）              → C3
```

**边界理由**：C1 = "**ECU 是否允许这台 Tester 调用这个服务/子功能/DID**"（取决于 `EcuState`）；
C2 = "**网关作为网络中间设备**的报文级策略"（与任何 ECU 的会话状态无关）。
两者是**不同的决策点**，`gateway.py` 在 C1 中**零改动**（§13）。

### 19.2 实施前置条件（Preflight 后的更新）

**C1 框架本身仍未实现任何代码**（`ServiceSpec` / `guards` / 任何生产逻辑均未落地）。
就本文档跨越的两个 PR 而言，**PR #26 已落地的只有测试**；实施前需：

1. ~~解决 OD-1/OD-2~~ → **已于 2026-09-24 Preflight 冻结**（§18.0）；
2. ~~建立 §15.2 的 NRC 优先级断言~~ → **PR #26 已建立**
   （`tests/test_c1_contract_pins.py`，29 例，覆盖 N1/N2/N4/N6/N7 + F-a..F-g）；
3. **仍需**：解决 **OD-4**（`services=` 的最终契约形态）与 **OD-3**（G4 的落地机制）；
4. **仍需**：实现 OD-2 要求的**防漂移机制**（结构规则 `payload_scoped → guards 非空`
   + 分类别 conformance test）—— 该机制**属于 C1 Phase 1**（§14），
   不在 PR #26 的 preflight 范围内。

> **Preflight 结论**：C1 的**执行模型已可实施**（OD-1/OD-2 冻结 + 契约 pin 就绪）。
> 剩余开放项的阶段归属：**OD-5**（`0x2E` 会话判定写法）属 **Phase 2**（有字节对拍兜底）；
> **OD-3**（是否强制新服务声明 spec）与 **OD-4**（`services=` 最终契约形态）
> 属 **Phase 3**（收敛与清理，§14）。**三者均不阻塞 Phase 1 的启动。**
