# OQ C2 — Gateway Policy Design Review

> 状态：**DESIGN REVIEW（待评审冻结）**
> 日期：2026-09-25
> 分支：`audit/oq-c2-gateway-policy-design-20260925`
> 基线：`main = f4ae37d4d8936ca47a263acd0e7f04492eae9245`（PR #42 merge commit）
> 上游：`docs/OQ_C1_PHASE2_FINAL_EXIT_REVIEW_2026-09-25.md`（C1 Phase 2 = COMPLETE）
> **范围：本文档只做设计冻结。不修改任何生产代码、不创建 Golden、不实现 policy。**

---

## 1. Scope

**C2 = Gateway Policy** —— 判断一条诊断请求**是否允许**沿
`Tester → Gateway → Target ECU` 这条路径传递。

```
C1 = ECU service authorization        ← COMPLETE（Phase 2 已收口）
C2 = Gateway security policy          ← 本设计
C3 = 0x27 through Gateway             ← NOT STARTED
```

---

## 2. Baseline

| 项 | 值 |
|---|---|
| `main` commit | `f4ae37d4d8936ca47a263acd0e7f04492eae9245` |
| C2 设计分支 | `audit/oq-c2-gateway-policy-design-20260925` |
| C1 Phase 2 | COMPLETE |
| C1 最终运行时状态 | `_SPEC_EXECUTION = frozenset({0x22, 0x2E})`；0x22/0x2E = ServiceSpec execution；0x27 = unchanged |

**C2 不触碰 C1 的任何冻结面**：`_SPEC_EXECUTION`、`SERVICE_SPECS`、guard、handler
在本阶段全部保持不动。

---

## 3. Current Gateway Behavior（**事实记录**，不做安全评价）

以当前 `main` 源码为准（`ecu/gateway.py` 190 行、`ecu/gateway_runtime.py` 631 行）。

### 3.1 两层架构（OQ7 硬约束）

```
vcan0 ──ISO-TP 重组──► GatewayRuntime ──► DiagnosticGateway.route()
                                                  │
                      ◄──ISO-TP 分帧── [(response_id, payload)]
```

| 层 | 文件 | 职责 | 约束 |
|---|---|---|---|
| 协议层 | `gateway.py` | `CAN ID + UDS payload → 路由决策` | **不** import isotp/socket，可离线单测 |
| 运行时 | `gateway_runtime.py` | socket 生命周期、select 循环、信号、错误遏制 | **不重新实现任何寻址规则**（`gateway_runtime.py:20-22`） |

### 3.2 输入

```
DiagnosticGateway.route(can_id: int, payload: bytes) -> list[RouteResult]
    RouteResult = tuple[int, bytes]        # (response_id, response_payload)
```

* `can_id`：**物理请求 ID** 或 **功能地址**。
* `payload`：ISO-TP **重组后**的完整 UDS payload（PCI 已由内核剥离）。
* **输入中没有"来源"** —— 11-bit CAN 帧的 ID 表示**目的地**，不携带发送方身份。

### 3.3 目标识别与路由（`gateway.py:152-179`）

```
route(can_id, payload):
    ① if can_id == self.functional_address:      # 功能寻址：**先判**，优先
           for ecu in self.ecus: ecu.process(payload)   # 全量扇出
           return []                                    # **不收集响应**
    ② target = self._by_request.get(can_id)      # 物理寻址：O(1) 精确匹配
       if target is None: return []              # **静默丢弃**
    ③ response = target.process(payload)
       if not response: return []                # 抑制正响应 → 不发帧
    ④ return [(target.response_id, response)]
```

| 维度 | 当前事实 |
|---|---|
| 物理寻址 | `request_id` 精确匹配（字典 O(1)），响应发往该 ECU 的 `response_id` |
| 功能寻址 | `can_id == functional_address`；**先于**物理判定 |
| `0x7DF` | `FUNCTIONAL_ADDRESS = 0x7DF`（`gateway.py:46`），**每实例可覆盖**（`__init__` 参数） |
| 一个请求如何路由 | 先判功能、再判物理；两者都不匹配 → `[]` |
| 多 ECU 隔离 | 每个 `VirtualEcu` 持有**独立** `EcuState`（`gateway.py:85`） |
| 响应如何返回 | 仅物理寻址返回 `[(response_id, payload)]`；功能寻址**恒返回 `[]`** |
| 未知 target | `return []` —— **不回应、不报错、不留痕** |
| 畸形请求 | **不做判定**，原样交给 handler；由 handler 返回 NRC |
| 空响应 | `b""`（如 `3E 80` suppress）→ 不发任何帧 |

### 3.4 默认 handler（`gateway.py:63-75`）

```python
def handle(payload: bytes) -> bytes:
    return uds_server.handle_uds_request(payload, state)   # services=None
```

**重要事实**：`services=None` ⇒ 使用 `SERVICE_SPECS`。因此

* **0x27 SecurityAccess 在网关路径上不可用**（静态表不含它）—— 这是既有的、
  有意的边界（`gateway.py:66-69`），**C3 的议题**；
* 由于 C1 Phase 2 已把 `_SPEC_EXECUTION = {0x22, 0x2E}`，**网关路径上的 0x22 / 0x2E
  现在会执行 ServiceSpec guards**（C1 生效的直接后果，非 C2 引入）。

### 3.5 运行时层（`gateway_runtime.py`）

```
GatewayRuntime.handle_request(can_id, payload):        # :446
    results = self.gateway.route(can_id, payload)      # 全部寻址判断在协议层
    if not results: return []                          # 三种"不回帧"情形合流
    for response_id, payload in results: self._send(...)
    return results
```

`gateway_runtime.py:456-461` 明确列出三类"不回帧"**且都不是错误**：

1. 未知物理 ID —— 本网关不认识的地址，留给链路上其它设备
2. 功能寻址 `0x7DF` —— 刻意不收集响应（多 ECU 同时驱动总线会仲裁冲突）
3. 服务抑制正响应（如 `3E 80`）

### 3.6 现有测试所冻结的行为事实

| 测试 | 冻结的行为 |
|---|---|
| `test_gateway_unit.py::test_unknown_physical_address_has_no_route` | `route(0x7E5, …) == []` |
| `test_gateway_unit.py::test_functional_broadcast_changes_all_sessions_but_returns_nothing` | 广播改变**全部** ECU 状态，返回 `[]` |
| `test_gateway_unit.py::test_functional_broadcast_does_not_leak_responses` | 即使广播 `22 F1 90`（会产生正响应）也不回帧 |
| `test_gateway_unit.py::test_write_to_one_ecu_does_not_affect_others` | 物理写入不波及其它 ECU |
| `test_gateway_unit.py::test_custom_handler_is_used` | **`VirtualEcu.handler` 可注入**，网关按 ECU 分发到该 handler |
| `test_multi_ecu_vcan.py` | 四个 e2e：各自 response_id、per-ECU TesterPresent、会话状态独立、错误地址无响应 |

### 3.7 现有"隐式策略"清点（§3 问 11、12）

| # | 现有行为 | 是 authorization policy 吗？ |
|---|---|---|
| 1 | 未知物理 ID → 静默丢弃 | **否** —— 是**路由归属**判定（"这个地址不是我的"） |
| 2 | 功能寻址不收集响应 | **否** —— 是**总线仲裁**约束的工程选择 |
| 3 | 空响应不发帧 | **否** —— 是 suppress 位语义 |
| 4 | `ALLOWED_INTERFACES = ("vcan0",)` fail-closed（`gateway_runtime.py:86`，仅 `main()` 强制） | **否** —— 是**实验安全边界**（环境级），与诊断授权无关 |

> **结论：当前网关对"某条请求是否应该被允许"没有任何 authorization 判定。**
> 所有现有行为都是**路由 / 总线 / 环境**层面的结构性事实，不是策略。

---

## 4. C1 / C2 Boundary（职责分离）

```
C1 = ECU Service Authorization
     问题：ECU **内部**某个 UDS service 是否允许执行？
     位置：ecu/uds_server.py 的 ServiceSpec guards（`_SPEC_EXECUTION` 控制）
     粒度：per-SID（+ per-DID payload）
     例子：0x22 读受保护 DID 在 LOCKED 下 → 7F 22 33

C2 = Gateway Policy
     问题：一条请求是否允许沿 Tester → Gateway → Target ECU 传递？
     位置：**待定**（见 §9，候选为 ecu/gateway.py 的 route()）
     粒度：**待定**（见 §7）
     例子：（设计目标）禁止某 Tester 向 ECU_B 发送 0x2E
```

**明确禁止**：把 C2 做成"另一套 ECU service authorization"。

判据：C2 的决策**必须**能够在不了解任何 service 内部语义（session / security /
DID 权限）的前提下做出。C2 只能基于**路由层可见的信息**（目标、寻址模式、SID）。

若一条规则的成立条件需要读取 `EcuState` 的内部字段（session、security_level、
did_overrides），**那条规则属于 C1，不属于 C2**。

---

## 5. Policy Model Candidates

根据 §3 的**真实数据流**评估三个候选。

### 候选 A —— 在 handler 内部加策略（ECU 层）

```
target.process(payload) → handler:  if policy_denies(...): ...
```

**评估：拒绝。**

1. 违反 C1/C2 边界：handler 是 ECU 内部实现，在此加策略等于把 C2 做进 C1；
2. `VirtualEcu.handler` 可注入（§3.6）—— 策略会被 override 顺带绕过；
3. 功能寻址下每个 ECU 各自判一次，**网关无法表达"这条广播整体被拒"**；
4. 网关层将失去对该请求是否发出的可见性。

### 候选 B —— 在运行时层加策略（`GatewayRuntime.handle_request`）

```
results = gateway.route(...)     # 先路由
policy_check(...)                # 再判
```

**评估：拒绝。**

1. **位置太晚**：`route()` 已经调用 `ecu.process(payload)`，**目标 ECU 已经看到请求**
   —— 违反"被拒请求不得到达目标 ECU"（§4 的核心目标）；
2. 只覆盖 runtime 路径：离线单测（`test_gateway_unit.py`）无法覆盖策略，
   与 OQ7 "协议层可离线单测" 的硬约束相悖；
3. 与 C1 的做法不一致（C1 把模型放在**协议层**，离线可测）。

### 候选 C —— 在协议层路由内部、目标解析之后、ECU 执行之前

```
route(can_id, payload):
    解析 target（物理 or 功能）
    → policy 判定（按 target 逐个）
    → 允许者才 target.process(payload)
    → 收集响应
```

**评估：选定。** 见 §6。

---

## 6. Decision

> **C2-OD-1 —— 策略在协议层 `DiagnosticGateway.route()` 内求值，
> 位置是"目标解析之后、`target.process()` 之前"。**

**理由：**

1. **满足 §4 的核心目标**：被拒请求**不会**到达目标 ECU（其 `EcuState` 不被改动）。
2. **离线可测**：`gateway.py` 不 import isotp/socket，策略因此可在无 vcan0 的
   机器上完整单测 —— 与 OQ7 硬约束一致。
3. **无法被 handler override 绕过**：策略在 `route()` 内、`target.process()` 之前，
   与 `VirtualEcu.handler` 的注入无关（见 §12）。
4. **与 C1 的做法一致**：C1 把授权模型放在协议层并离线可测；C2 沿用同一模式。

---

## 7. Policy Dimensions

**只纳入对 Gateway authorization 有明确语义、且当前数据流中**确实存在**的字段。**

| 候选维度 | 当前模型是否可见 | 是否纳入 | 理由 |
|---|---|---|---|
| **target ECU** | ✓ `_by_request[can_id]` / 功能扇出列表 | **纳入** | 这是 C2 的中心问题："这条请求是否允许到达**这个** ECU" |
| **addressing mode**（physical / functional） | ✓ `can_id == functional_address` | **纳入** | 二者授权语义本质不同（见 §10）；单播与广播不能共用一条规则 |
| **UDS SID** | ✓ `payload[0]`（payload 非空时） | **纳入** | 网关能看到的**唯一**服务粒度信息；是"哪类操作"的最小表达 |
| **payload 内容（DID / 数据）** | ✓ 字节可见 | **不纳入** | 解析 DID = 复制 ECU 的 payload 语义 → 越界进 C1；网关只应看"哪个服务" |
| **session / security_level** | ✗ 在 `EcuState` 内，网关不持有语义 | **不纳入** | 属 C1；需要它的规则属 C1 |
| **direction（request / response）** | ✓ 结构性（`route` 只吃 request） | **不纳入（本轮）** | 响应侧过滤是独立议题，见 §15 |
| **source / Tester identity** | **✗ 当前模型完全没有** | **不纳入（本轮）** | 11-bit CAN 帧 ID 表示目的地、不携带发送方；引入 source 需要**变更输入契约**，见 §15 |
| **broadcast 计数 / 频率** | ✗ | **不纳入** | 属速率限制，与授权正交 |

> **刻意不因为"字段存在"而纳入**：payload 内容字节可见，但解析 DID 会把
> ECU 的语义搬进网关，模糊 C1/C2 边界。

### 7.1 由此得到的最小 policy 输入

```
PolicyInput = (target: VirtualEcu, addressing_mode: PHYSICAL | FUNCTIONAL, sid: int | None)
```

* `sid is None` ⇔ payload 为空（结构上畸形）—— 当前实现会把它交给 handler 处理
  （handler 返回 `7F 00 13` 或类似），**策略不介入**（见 §8）。
* `target` 在功能寻址下是**扇出列表中的单个 ECU**（见 §10）。

---

## 8. Default Decision

### 8.1 候选与评估

| 候选 | 引入时的行为变化 | 与 C1 迁移模式兼容性 |
|---|---|---|
| **default-allow**（空策略 ≡ 无条件放行） | **零** | ✓ 可复制 C1 的 "框架先行、零行为变更 + 全量回归" |
| **default-deny** | **巨大** —— 未显式允许的一切（含全部既有实验请求）立即被拒 | ✗ 引入即破坏全部既有 gateway 行为 |

### 8.2 冻结决定

> **C2-OD-2 —— default-allow。空策略是 no-op，等价于当前行为。**

**理由：**

1. **迁移可行性**：C1 Phase 2 的关键经验是"**先引入机制并证明零行为变更**，
   再逐项迁移"。default-deny 在引入瞬间就会改变全部 gateway 行为，
   使"等价性对拍"这一步**不可能存在**（没有可比基线）。
2. **default-allow 不等于弱安全**：它决定的是**机制**的缺省，不是**部署**的姿态。
   "拒绝一切未显式允许"可以表达为一条**显式 allowlist 策略**（数据），
   而不需要机制层 default-deny。
3. **可测**：空策略必须与 legacy 逐字节等价 —— 这本身就是一个强断言。

### 8.3 未匹配规则 / 未知项的逐项决定

| 情形 | 决定 | 理由 |
|---|---|---|
| **未匹配任何规则** | **放行** | C2-OD-2（default-allow） |
| **未知物理 target**（`_by_request` 无此 ID） | **不进入策略**，维持 `route()` 现有 `return []` | 这是**路由归属**判定（"不是我的地址"），不是授权；若策略在此返回"拒绝"，会与"不是本网关的地址"混淆语义。**行为完全不变。** |
| **未知 SID**（ECU 未实现） | **不进入策略**，交给 handler → `7F <sid> 11` | 响应形态由 ECU 决定，网关不代答 |
| **payload 为空** | **不进入策略**，交给 handler | 畸形请求的 NRC 语义属 ECU |
| **功能寻址（广播）** | **逐 ECU 进入策略**（见 §10） | 扇出目标不同 ⇒ 授权结论可以不同 |

---

## 9. Enforcement Point

```
DiagnosticGateway.route(can_id, payload)                     # gateway.py:152
    │
    ├─ ① 功能寻址？ ──► 对**每个** ecu：policy(ecu, FUNCTIONAL, sid) ──► 放行者 process()
    │                                                                  拒绝者**不 process**
    │                   return []（仍不收集响应 —— 行为不变）
    │
    └─ ② 物理寻址？  target = _by_request.get(can_id)
            ├─ target is None → return []                    （**不进入策略**，见 §8.3）
            └─ policy(target, PHYSICAL, sid)
                    ├─ deny  → return []（静默丢弃）
                    └─ allow → response = target.process(payload)
                               …（后续与现状完全一致）
```

**精确插入位置**：目标解析完成之后、`target.process(payload)` 之前。

### 9.1 拒绝时的语义

| # | 问题 | 决定 |
|---|---|---|
| 1 | Gateway 是否向 Tester 回响应？ | **否** |
| 2 | 返回什么？ | 什么都不返回（`route()` 返回 `[]`，总线上无帧） |
| 3 | 是否静默丢弃？ | **是** |
| 4 | 是否允许目标 ECU 看见请求？ | **否** —— `target.process()` 不被调用，`EcuState` 不变 |
| 5 | 是否允许 response 回传？ | 不适用（无请求即无响应） |
| 6 | **物理寻址**被拒时，**谁**收到 response？ | **无人** —— 不产生任何帧，Tester 收不到回应 |
| 7 | **功能寻址**下某个 target 被**单独**拒绝时，response 如何表示？ | **无额外表示** —— 功能寻址恒返回 `[]`；被拒者只是**不被 process**，其 `EcuState` 不变 |
| 8 | **多个 target** 中允许 A、拒绝 B，Gateway 最终返回什么？ | **仍返回 `[]`** —— 功能寻址"不收集响应"的既有语义不变 |
| 9 | Gateway 是否 **synthesize** `7F` response？ | **否** —— 网关不代 ECU 作答 |
| 10 | 使用什么 **NRC**？ | **无 NRC** —— 不产生任何否定响应 |

> **C2-OD-3 —— 策略拒绝 = 静默丢弃（无响应、目标 ECU 不可见、不合成 NRC）。**

**理由：**

1. **与既有行为一致**：当前"未知物理 ID"就是静默丢弃（`gateway.py:169-171`），
   总线上无法区分"不是我的地址"与"被策略拒绝"—— 这是**有意的**，
   避免网关暴露自身策略的存在与形状。
2. **不撒谎**：返回 NRC 需要选一个语义上不成立的 NRC（ECU 根本没执行该服务，
   `7F 22 33` 之类会谎称 ECU 做了什么）。静默丢弃不产生关于 ECU 状态的虚假陈述。
3. **可观测性的边界**：
   拒绝**不能**仅凭 `route()` 的返回值与其它 `[]` 情形区分 ——
   未知物理地址、功能寻址、以及"**放行但服务抑制正响应**"都会返回 `[]`。因此：

   * 物理寻址下「被拒」与「放行后抑制响应」在**返回值上不可区分**；
   * 功能寻址下「某 ECU 被拒」与「该 ECU 被执行」在返回值上**同样不可区分**；
   * 拒绝的**唯一**可观测差异是"目标 ECU 的 `EcuState` 未被改动" ——
     而对**本身不修改状态的服务**（如 `3E 80` TesterPresent+suppress）
     该差异**也不存在**。

   → 该缺口由 **C2-OD-9（§9.2）** 解决 —— 原则是
   **两条语义严格分开**：

   ```
   wire observable（CAN/UDS 对外行为）  = **不变**
   test/API observable                  = **显式的每次调用 trace**
   ```

   即：**不改变 `route()` 的返回契约，也不改变任何对外行为**；
   决策可观测性由**调用方/测试显式请求**的旁路接口提供。

### 9.2 C2-OD-9 —— Policy decision observability（**FROZEN**）

> **C2-OD-9 —— 策略决策仅通过一个显式的"每次调用"trace 接口可观测；
> `route()` 的返回契约与 wire 行为均保持不变。**

#### 9.2.1 接口

```
route(can_id, payload)                -> list[RouteResult]                    # 契约不变
route_with_trace(can_id, payload)     -> (list[RouteResult], PolicyTrace)     # 新增旁路观测
```

**为什么不能改 `route()` 的返回值**（明确排除 `GatewayResult(responses=…, decisions=…)`
这类方案）：

1. 修改现有**公共返回契约**；
2. 会扩大 C2 的迁移范围；
3. runtime 当前**直接消费** `list[RouteResult]`（`gateway_runtime.py:454`）；
4. C1 已经采用"**保持行为契约、额外建立验证证据**"的思路；
5. 决策信息属**测试/调用方观测**，不是总线 wire semantics。

```
正常生产调用：  route()
需要决策证据：  route_with_trace()
```

#### 9.2.2 trace 必须是"单次调用"的产物

```
PolicyTrace
    decisions: ordered sequence[PolicyDecision]      # 本次调用独有，不可变

PolicyDecision
    target          : int          # ECU 的 request_id
    addressing_mode : "physical" | "functional"
    sid             : int
    allowed         : bool
```

**禁止**：

```
global last_decision / global policy_counter / module-level mutable trace
把日志 grep 当作测试 oracle
```

必须满足 `call #1 trace ≠ call #2 trace`（互不污染）。

#### 9.2.3 字段冻结

| 字段 | 冻结值 | 理由 |
|---|---|---|
| `target` | **ECU 的 `request_id`**（`0x7E0` / `0x7E1` / `0x7E2`） | `request_id` 在路由层**本来就是寻址身份**，是稳定、唯一、可断言的 oracle。**禁止**用 `id(obj)` / `repr(obj)` / 对象身份 |
| `addressing_mode` | **字符串 `"physical"` / `"functional"`** | 让测试报告直接表达语义；**禁止** `True/False`、`0/1` |
| `sid` | `payload[0]` | 仅在 `payload != b""` **且**该请求按 **C2-OD-8** 进入策略求值时产生 decision |
| `allowed` | `bool` | 唯一判定结果 |

**刻意不纳入**（属 observability enhancement，非 authorization correctness 必要条件）：

```
reason / rule_id / message / timestamp / logger
```

最小 oracle `(target, addressing_mode, sid, allowed)` 已足以支撑
A2 等价性对拍、A4 bypass 审计、functional per-ECU 证明；将来需要可直接扩展。

#### 9.2.4 与 C2-OD-8 的关系（**不留歧义**）

未进入策略求值的情形**不产生任何 decision 记录** ——
**不得**把它们伪装成 `allowed = True`：

| 情形 | `trace.decisions` |
|---|---|
| 未知物理 target（`route(0x7E5, …)`） | **`[]`** —— 是"**不进入 policy**"，不是 deny |
| 空 payload（`route(0x7E0, b"")`） | **`[]`** —— 交由既有 handler/protocol 语义 |
| 未知 SID | **`[]`** —— 保持 C2-OD-8；**不得**记为 `allowed=True` |

#### 9.2.5 functional 寻址必须**逐 ECU**产生 decision

```
route_with_trace(0x7DF, payload)   （默认三 ECU）

trace.decisions == [
    PolicyDecision(target=0x7E0, addressing_mode="functional", sid=…, allowed=decision_A),
    PolicyDecision(target=0x7E1, addressing_mode="functional", sid=…, allowed=decision_B),
    PolicyDecision(target=0x7E2, addressing_mode="functional", sid=…, allowed=decision_C),
]
```

**顺序必须等于 `self.ecus` 的确定性遍历顺序**（当前为 ECU_A / ECU_B / ECU_C）。
**禁止** set / dict 无序遍历 / 对象身份顺序。
测试必须能断言 `decision order == target traversal order`。

#### 9.2.6 空策略 / default-allow 时的 trace（A1/A2 的核心证据）

空策略按 **C2-OD-2** 的 `default-allow` ⇒ 对**实际进入策略求值**的 target：

```
decision.allowed == True
```

这使 C2-A1/A2 可以**同时**证明：

```
response unchanged  ∧  state unchanged  ∧  policy **确实被求值过**  ∧  decision = allow
```

而不只是看到 `route(...) == []`。

#### 9.2.7 deny 时的 trace（两个维度严格分开）

```
trace :  PolicyDecision(..., allowed=False)
wire  :  results == []
         target.process() **不被调用**
```

#### 9.2.8 trace 必须是**真实求值**的产物

`PolicyDecision` **只允许**在真正执行策略求值之后产生。

**禁止**：

```
先执行策略、事后再"补写"一个 trace
根据 results / state **事后推断** decision
```

正确结构（概念上）：

```
target resolved
    ↓
policy.evaluate(...)          ← 唯一产生 decision 的地方
    ↓
PolicyDecision
    ↓
trace.append(decision)
    ↓
if allowed:
    target.process(...)
```

#### 9.2.9 `route()` 与 `route_with_trace()` 必须共用**同一个 route core**

```
route()              = route core，仅返回 results
route_with_trace()   = **同一个** route core，返回 results + trace
```

**禁止**两套 routing 实现 —— 否则会出现"普通运行路径一套语义、测试路径另一套语义"，
直接摧毁 C2-A2 的证明价值。

#### 9.2.10 契约表（冻结）

| 情形 | policy evaluated | trace record | wire result |
|---|---|---|---|
| known physical + valid SID | yes | **1** | existing behavior |
| functional + valid SID | **yes, per ECU** | **N** | `[]` |
| unknown physical target | **no** | **0** | `[]` |
| empty payload | **no** | **0** | existing handler/protocol result |
| unknown SID | **no** | **0** | existing handler result |
| policy allow | yes | `allowed=true` | target may execute |
| policy deny | yes | `allowed=false` | **no response** |

> `N` = 参与广播的 ECU 数量（默认网段为 3）。

#### 9.2.11 C2-A4 bypass 审计如何使用 trace

```
制造 policy deny
    → route_with_trace(...)
        → 断言 trace.decisions == [PolicyDecision(..., allowed=False)]
        → **spy/monkeypatch `target.process()`，断言 call_count == 0**
```

这比单看 `results == []` 强得多 —— 它同时排除了
"看起来被拒但其实 handler 被调用了"（§6.3 类问题的同型防线）。

---

## 10. Functional Addressing

### 10.1 现状（§3.3）

```
0x7DF → for ecu in self.ecus: ecu.process(payload)   → 全量扇出
      → return []                                     → 不收集响应
```

功能寻址是**一对多**：一条请求影响 N 个 ECU 的状态。

### 10.2 关键设计决定

> **C2-OD-4 —— 功能寻址的策略判定**逐 ECU**进行，而非对请求整体判定一次。**

即：广播不是"一次授权、N 次执行"，而是**N 次独立授权**。

```
route(0x7DF, payload):
    for ecu in self.ecus:
        if policy(ecu, FUNCTIONAL, sid).allow:
            ecu.process(payload)      # 仅放行的 ECU 执行
        # 未放行的 ECU **不被 process**，其 EcuState 不变
    return []                          # 仍不收集响应（行为不变）
```

**理由：**

1. **授权结论本来就可以逐 ECU 不同**：策略的维度包含 target（§7），
   因此"允许 ECU_A 收到该广播、不允许 ECU_B"是模型内可表达的合法结论。
2. **若整体判一次再全量扇出**，则"允许 A 的规则"会**顺带**授权 B ——
   这正是 §12 要求排除的"request intended for ECU-A ≠ automatically authorized
   for ECU-B"。
3. **保持响应语义不变**：无论放行多少 ECU，功能寻址**仍然 `[]`**。

### 10.3 明确不做的事

* **不取消功能寻址**。广播是既有能力，且有 e2e 覆盖（`test_multi_ecu_vcan.py`）。
* **不改变"不收集响应"**：多 ECU 同时驱动总线会仲裁冲突，这是总线约束，不是策略。
* **不引入"广播白名单之外一律拒绝"**：那是策略**内容**（数据），不是机制缺省。

---

## 11. Multi-ECU Isolation

### 11.1 现状（事实）

* 每个 `VirtualEcu` 持有独立 `EcuState`（`gateway.py:85`）→ 会话 / 安全 / DID 覆盖互不影响；
* `test_gateway_unit.py::test_write_to_one_ecu_does_not_affect_others` 已冻结该行为；
* `test_multi_ecu_vcan.py::test_session_state_is_independent_between_ecus` 在 vcan0 上冻结同一事实。

**C2 不重新实现 ECU 状态隔离。**

### 11.2 冻结的不变量

> **C2-OD-5 —— 策略判定 MUST 以 (target ECU, addressing_mode, SID) 为单位；
> 任意两个 ECU 的授权结论互相独立。**

该不变量由 §10.2 的逐 ECU 判定**结构性保证**，并由 C2 的测试显式断言
（同一广播下 A 放行、B 拒绝，且 B 的 `EcuState` 不变）。

---

## 12. Override Paths

### 12.1 现有 override / extension 面（§3 实测）

| # | 接缝 | 位置 | 性质 |
|---|---|---|---|
| 1 | `VirtualEcu.handler` 注入 | `gateway.py:86`（公开字段，`__post_init__` 缺省绑定） | **实现替换** —— 已有测试使用（`test_custom_handler_is_used`） |
| 2 | `DiagnosticGateway(ecus=[...], functional_address=…)` | `gateway.py:117-133` | **拓扑重构** —— 任意 ECU 集合与地址 |
| 3 | `Transport` Protocol | `gateway_runtime.py:118-133` | 运行时离线测试接缝（非路由 override） |
| 4 | `build_runtime()` 直接调用 | `gateway_runtime.py:491` | **绕过 `main()` 的接口白名单**（文档明示为调用方责任，见 §17） |

### 12.2 决定

> **C2-OD-6 —— 策略由 `route()` 在 `target.process()` 之前求值，
> 因此 `handler` 注入**不能**绕过策略。**
>
> 即：**override 可替换实现，不可替换授权** —— 与 C1 的 OD-4 A′ 同构。

| 接缝 | 策略是否仍然生效 |
|---|---|
| 1 `handler` 注入 | **生效** —— 策略在 handler 之外（`route()` 内） |
| 2 自定义拓扑 | **生效** —— 策略按实际 `target` 判定 |
| 3 `Transport` fake | **生效** —— 策略在协议层，transport 无关 |
| 4 `build_runtime()` | **生效** —— 策略在协议层，与构造路径无关 |

**本阶段不修改任何 override 实现**，仅冻结上述不变量，供后续 C2 迁移测试断言。

---

## 13. Migration Strategy

沿用 **C1 已验证的六段式**，不发明新流程：

```
C2-A0  capture legacy Gateway golden
       （在**策略机制尚未引入**时捕获：route() 对 (can_id, payload) 的
         [(response_id, payload)] 与各 ECU 的 final state projection）
       —— **无 policy trace 要求**（此时 trace 机制尚不存在）

C2-A1  引入**空策略 + trace 机制**
       —— 必须证明"框架引入本身"零行为变更：
          ① 空策略下 golden **逐字节不变**；
          ② `route()` 的 **wire 行为不变**；
          ③ 对**实际进入策略求值**的 target，`route_with_trace()` 产生
             `allowed=True` 的 decision 记录（§9.2.6）—— 用于证明
             "策略**确实被求值过**"，而不只是"什么都没发生"

C2-A2  legacy ↔ policy 逐字节对拍（空策略）
       —— 比较三个维度：**response** + **state projection** + **PolicyTrace 断言**

C2-A3  full regression

C2-A4  override / bypass 不变量审计（§12）
       —— 使用**显式 DENY trace** + spy `target.process()` 断言 `call_count == 0`
          （§9.2.11）；这比单看 `results == []` 强得多

C2-A5  exit review
```

**Golden 内容必须覆盖**（供 A0 设计参考，本阶段**不捕获**）：

| 类别 | 例 |
|---|---|
| 物理单播成功 | `route(0x7E0, 22 F1 90)` → `[(0x7E8, 62 …)]` |
| 物理单播负响应 | `route(0x7E0, 22 FF FF)` → `[(0x7E8, 7F 22 31)]` |
| 抑制正响应 | `route(0x7E0, 3E 80)` → `[]` |
| 未知物理地址 | `route(0x7E5, …)` → `[]` |
| 功能寻址 | `route(0x7DF, 10 03)` → `[]` **且全部 6 个 EcuState 会话改变** |
| 功能广播不泄露响应 | `route(0x7DF, 22 F1 90)` → `[]` |
| 多 ECU 状态隔离 | 写 ECU_A 后 B/C 的 `did_overrides` 不变 |
| 畸形 payload | `route(0x7E0, b"")` → ECU 的 `7F 00 13` |
| 自定义 handler | 注入 handler 的路由结果 |

**State projection**（沿用 C1 §14.2 的三字段 + 网关必需的第四项）：

```
GatewayStateSnapshot = 对**每个** ECU 记录：
    (name, session, security_level, frozenset(did_overrides.items()))
```

理由：网关的核心可观测副作用是"**哪些 ECU 的状态被改变了**"，
仅比较 response 无法发现"广播被错误扇出/漏扇出"。

---

## 14. C3 Boundary

```
C2 提供：
    - 策略模型（target / addressing / SID）
    - 求值点（route() 内，目标解析后、process() 前）
    - 拒绝语义（静默丢弃，目标 ECU 不可见）
    - 逐 ECU 判定（广播）
    - override 不变量

C3 将添加：
    - 0x27 SecurityAccess 在 Gateway 路径上的可用性
      （当前 `_default_handler_for` 用 services=None ⇒ 无 0x27，见 §3.4）
    - 与 0x27 状态机（seed/key/lockout）的交互策略

C2 明确不实现：
    - 0x27 的任何改动
    - SecurityAccess 状态机
    - security state / keygen / _now()
    - 任何 C1 面的改动（_SPEC_EXECUTION / SERVICE_SPECS / guard / handler）
```

> **C2-OD-7 —— C2 的设计必须天然容纳 C3**：策略模型把"服务"抽象为 **SID**，
> 因此 0x27 无需新维度即可被 C3 纳入（它只是一个 SID）。
> C2 **不得**为 0x27 预置任何特殊分支。

---

## 15. Open Questions（**已分级**）

**分级标准**：Blocking = 未决则会阻塞 C2 的**实现或迁移对拍**；
Deferred = 属于未来架构议题，当前机制不被其阻塞。

### 15.1 RESOLVED（原 BLOCKING，已由 C2-OD-9 冻结）

| # | 问题 | 状态 | 解决方式 |
|---|---|---|---|
| **C2-OQ-5** | 策略决策如何对调用方与测试可观测？ | **RESOLVED** —— 由 **C2-OD-9** 冻结 | 新增**显式、每次调用、有序**的旁路 trace 接口 `route_with_trace()`；`route()` 的返回契约与 wire 行为**均不变**。详见 **§9.2** |

> **审计历史保留**：C2-OQ-5 曾是本设计的 **BLOCKING** 缺口 —— 它源于
> 本文件初稿中"拒绝在**返回值**上可观测"这一**不准确**表述（§9.1 理由 3
> 已给出实证：`[]` 无法区分拒绝与未知地址 / 功能寻址 / 放行但抑制响应）。
>
> 该缺口现已由 **C2-OD-9** 解决，**wire 语义与 `route()` 契约均未改变**：
> 决策可观测性走**独立的显式接口**，不影响任何 CAN/UDS 对外行为。

### 15.2 BLOCKING

**当前无 BLOCKING 项。**

（C2-OQ-5 冻结后，剩余 Open Question 均不阻塞 C2 的实现与对拍。）

### 15.3 DEFERRED（不阻塞 C2，属未来架构）

| # | 问题 | 为何暂缓 | 归属 |
|---|---|---|---|
| **C2-OQ-1** | 是否需要 **source / Tester identity**？ | 当前输入契约中**完全没有**该信息（11-bit CAN ID 表示目的地）。引入它需要变更 `route()` 的输入签名 —— 属**接口变更**。而 C2 冻结的 dimensions 不含 source，故机制不被其阻塞 | 未来架构（多 Tester 差异化授权） |
| **C2-OQ-2** | 是否需要 **response 方向**过滤？ | **经复核，它不影响 C2 deny 的 observable behavior**：C2-OD-3 已冻结"拒绝时不产生任何响应"，因此 deny 路径上**没有可被过滤的响应**。egress 过滤是"拦截**被放行**请求的响应"，属**新增能力**而非 C2 deny 语义 | 未来架构（egress policy） |
| **C2-OQ-3** | 策略的**表达载体**：源码常量 / 数据文件 / 代码 | 这是**实现形态**选择，不改变已冻结的语义（OD-1…OD-8）。倾向与 C1 一致：**源码常量**（可 review、可回滚、随 commit 评审） | C2 实现阶段决定（**非**阻塞设计） |
| **C2-OQ-4** | 是否存在**多网关 / 中继**场景需要跨网段策略？ | 当前实验只有一个 `DiagnosticGateway` 实例，架构中**没有**该场景 | **deferred / out of scope** |

> **关于 C2-OQ-2 的判定**：它**不是** implementation blocker —— 因为
> C2-OD-3 的"静默丢弃"选择使 deny 路径上**不存在**响应，
> 故 response-direction filtering 与 C2 deny 的可观测行为**正交**。
> （若未来把 OD-3 改为"网关合成 `7F` 响应"，则该结论**需重新评估** ——
> 届时 deny 会产生响应，response 方向过滤将与其直接相关。）

---

## 16. Design Freeze

### 16.1 冻结项

| ID | 决定 | 状态 |
|---|---|---|
| **C2-OD-1** | 策略在协议层 `DiagnosticGateway.route()` 内求值；位置 = 目标解析后、`target.process()` 前 | **FROZEN** |
| **C2-OD-2** | **default-allow**；空策略 ≡ 当前行为（no-op） | **FROZEN** |
| **C2-OD-3** | 拒绝 = **静默丢弃**（无响应、目标 ECU 不可见） | **FROZEN** |
| **C2-OD-4** | 功能寻址**逐 ECU**判定，非整体判定一次 | **FROZEN** |
| **C2-OD-5** | 判定单位 = `(target ECU, addressing_mode, SID)`；ECU 之间结论独立 | **FROZEN** |
| **C2-OD-6** | `handler` / 拓扑 / transport / 构造路径的 override **不能**绕过策略 | **FROZEN** |
| **C2-OD-7** | C2 用 SID 表达服务，天然容纳 C3 的 0x27；**不得**为 0x27 预置特殊分支 | **FROZEN** |
| **C2-OD-8** | 未知物理 target / 未知 SID / 空 payload **不进入策略**，维持既有路由与 NRC 语义 | **FROZEN** |
| **C2-OD-9** | **Policy decision observability**：策略决策仅通过**显式的每次调用有序 trace** 对调用方/测试可见；`route()` 的**返回类型与 wire 行为保持不变**（§9.2） | **FROZEN** |
| Policy dimensions | `target` / `addressing_mode` / `SID`（**不含** payload 内容、session、source） | **FROZEN** |
| C1 / C2 边界 | 需要读 `EcuState` 内部字段才能成立的规则属 **C1** | **FROZEN** |
| 迁移策略 | 沿用 C1 六段式（capture → 空策略等价 → 对拍 → 回归 → override 审计 → exit） | **FROZEN** |
| C3 边界 | C2 不碰 0x27 / SecurityAccess / keygen / `_now()` / C1 任何面 | **FROZEN** |

### 16.2 本阶段不做的事

```
production code 改动      = 0
测试改动                  = 0
Golden 创建 / 修改        = 0        （C2-A0 才开始捕获）
策略实现                  = 0
0x27 改动                 = 0
C1 面改动                 = 0
C3 实现                   = 0
```

### 16.3 待评审确认项

1. **C2-OD-2 default-allow** —— 这是本设计最具争议的决定。它以**可迁移性**为
   首要理由，代价是"机制缺省不 fail-closed"。请确认接受该取舍
   （替代方案是 default-deny，但会牺牲"空策略零行为变更"这一迁移前提）。
2. **C2-OD-3 静默丢弃** —— 拒绝在总线上与"地址不属于本网关"不可区分。
3. **§7 排除 source / payload 内容** —— 若您认为 C2 必须支持"区分 Tester"，
   则需先在 C2-OQ-1 上做出接口变更决定，本设计需相应修订。
4. **§13 的 GatewayStateSnapshot 增加"逐 ECU 状态"** —— 比 C1 的三字段投影更宽，
   理由是网关的可观测副作用是"哪些 ECU 被改变了"。

---

## 17. Observed Issues（**只记录，不修复**）

阅读代码时发现的既有问题，均**不在本阶段修复**。

| # | 观察 | 严重度 | 影响 | 是否阻塞 C2 设计 |
|---|---|---|---|---|
| 1 | `ALLOWED_INTERFACES` 白名单**仅在 `main()` CLI 入口**强制；`build_runtime()` 以编程方式调用时**不检查**接口名 | 低 | 测试/实验脚本可绑定非 vcan 接口。`build_runtime` 的 docstring 已明示这是调用方责任 | **否**（与诊断授权正交；属环境安全边界） |
| 2 | `DiagnosticGateway.__init__` 不拒绝 `request_id == functional_address` 的 ECU | 低 | 这样的 ECU 将**永远无法被物理寻址**（`route()` 先判功能）。当前 `DEFAULT_ECUS` 不触发 | **否** |
| 3 | 网关默认 handler 用 `services=None` ⇒ 网关路径**没有 0x27** | — | **非缺陷**，是 `gateway.py:66-69` 明示的设计边界 | **否**（C3 的议题） |
| 4 | 以上之外未发现 gateway 路由层的正确性缺陷 | — | — | — |

---

## 18. Next Stage

```
C2 Design Freeze  = 本次交付（本文档）
C2 Implementation = NOT STARTED
C2 Golden         = NOT STARTED（C2-A0 才捕获）
C2-A0 … C2-A5     = NOT STARTED
C3                = NOT STARTED

C1 Phase 2 = COMPLETE（不受本阶段影响）
```

**本阶段结束后不在同一分支继续写 Gateway Policy。**
下一阶段应从设计冻结后的 main 另开 implementation / migration 分支，
继续遵循：

```
Design → Legacy Golden → Implementation → Equivalence → Regression → Audit → Exit Review
```

---

## 19. Review Focus（本次评审重点）

以下五项请评审明确确认或推翻。**本节不引入新的设计决定**，
仅把已有决定与未决缺口挑明。

### Focus A —— C2-OD-2 `default-allow`（请确认取舍）

**必须区分两件事：**

```
policy **mechanism** default   = allow      ← C2-OD-2 冻结的是这个
deployment **security posture** = ???       ← 不由 C2-OD-2 决定
```

即：`default-allow` 描述的是**机制缺省**（空策略是 no-op），
**不是**部署姿态。"拒绝一切未显式允许"完全可以用一条**显式 allowlist 策略**表达
（那是**数据**，不是机制缺省）。

**请确认接受该取舍**：代价是"机制缺省不 fail-closed"，
收益是**唯一**能复制 C1「先引入机制、证明零行为变更 → 再逐项迁移」的路径。
替代方案 default-deny 在引入瞬间改变全部 gateway 行为，
使 C2-A2 的「等价性对拍」**不可能存在**（没有可比基线）。

### Focus B —— deny 语义：**wire 不可见、trace 可见**（C2-OD-3 + C2-OD-9）

已冻结的部分（§9.1 表格 1–10 行逐条给出）：

```
physical  deny   → **无人**收到 response（不产生任何帧）
functional deny  → 无额外表示；功能寻址恒返回 []；被拒 ECU 的 EcuState 不变
多 target A 允 / B 拒 → **仍返回 []**（功能寻址不收集响应的语义不变）
Gateway 是否合成 7F   → **否**
使用什么 NRC          → **无 NRC**
```

**原 BLOCKING 缺口（C2-OQ-5）已由 C2-OD-9 解决**，形成两条**严格分开**的语义：

```
C2-OD-3  wire deny semantics        —— deny **在总线上不可见**
C2-OD-9  decision observability     —— deny **通过显式 trace API 可见**
```

即：

| 维度 | deny 时 |
|---|---|
| wire（CAN/UDS 对外） | **不可见** —— 无帧、无 `7F`、无 NRC |
| 显式 trace API（`route_with_trace`） | **可见** —— `PolicyDecision(..., allowed=False)`，且 `target.process()` 不被调用 |
| `route()` 返回契约 | **不变**（`list[RouteResult]`） |

这正好解决 §9.1 理由 3 发现的问题：迁移阶段因此**可以写出有效的判定断言**
（§9.2.11），而**不需要**改变任何对外行为。

完整契约见 **§9.2.1–9.2.11**，其中 §9.2.10 给出七种情形的
policy-evaluated / trace-record / wire-result 对照表。

### Focus C —— C2-OD-4 功能寻址逐 target 判定（请确认）

```
0x7DF
  ├─ ECU_A → policy decision A
  ├─ ECU_B → policy decision B
  └─ ECU_C → policy decision C
```

**不得**"一次整体判定 → 授权结果被所有 ECU 共用"——
否则「允许 A 的规则」会**顺带**授权 B，违反多 ECU 隔离（C2-OD-5）。

### Focus D —— C2-OD-6 override 不变量（请确认与 C1 概念一致）

```
override **may** replace:  implementation / topology / transport
override **may not** replace:  authorization
```

与 **C1 的 OD-4 A′** 同构（"可覆盖实现，不可覆盖授权"）。
判据：策略在 `route()` 内、`target.process()` 之前求值，
因此位于 `VirtualEcu.handler` 注入点**之外**。

### Focus E —— C2-OQ-3 策略载体（请给设计意见，**不要提前实现**）

```
policy carrier:
    (a) 源码常量   —— 与 C1 的 _SPEC_EXECUTION 一致：随 commit 评审、可回滚
    (b) 数据配置   —— 灵活，但引入"配置即行为"的运行时可变性
```

本设计**倾向 (a)**，理由与 C1 §15.3 相同：**回滚应是一次可审计的代码变更，
而不是一次环境配置变更**。仅需 reviewer 给出设计意见。

### 评审检查清单

| # | 检查项 | 对应 |
|---|---|---|
| 1 | C1 / C2 authorization boundary（需读 `EcuState` 的规则属 C1） | §4 |
| 2 | `default-allow` rationale（机制缺省 vs 部署姿态） | Focus A / §8.2 |
| 3 | Gateway deny response semantics（wire 不可见 / trace 可见） | Focus B / §9.1 + §9.2 |
| 4 | functional addressing per-ECU semantics | Focus C / §10.2 |
| 5 | multi-ECU isolation（判定单位与结论独立） | §11 |
| 6 | override cannot bypass policy | Focus D / §12 |
| 7 | C3 / 0x27 boundary（C2 不预置 0x27 分支） | §14 |
| 8 | implementation observability（**C2-OD-9**，原 C2-OQ-5） | §9.2 / §15.1 |
| 9 | legacy → policy migration feasibility（六段式 + 空策略等价） | §13 |
| 10 | policy carrier | Focus E / §15.2 |

### 本次评审的放行条件

```
C2-OQ-5（→ C2-OD-9）已冻结      ← **已完成**（本文件 §9.2）
Focus A / C / D 明确确认
Focus E 给出设计意见（不阻塞）
```

> **原 BLOCKING 项已解除**：C2-OQ-5 已由 **C2-OD-9** 冻结（§9.2），
> 且**未**改变 `route()` 契约或任何对外行为。
> 当前 **无 BLOCKING 项**（§15.2）。

满足上述确认后方可进入下一阶段（C2-A0 legacy Gateway golden capture）。
