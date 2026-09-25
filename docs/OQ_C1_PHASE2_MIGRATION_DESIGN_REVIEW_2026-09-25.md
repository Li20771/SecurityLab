# C1 Phase 2 —— Migration Design Review / Design Freeze

> 状态：**DESIGN REVIEW（待评审冻结）**
> 日期：2026-09-25
> 基线：`main = f04b0b28b674f701a4dae6cba516db95b8b8202c`
> 上游文档：`docs/OQ_C1_PERMISSION_SESSION_DESIGN_REVIEW_2026-09-24.md`（Phase 1 设计冻结，已合并）
> 范围：**本文档只做设计冻结，不含任何 production code 或测试改动。**

---

## 1. Baseline

### 1.1 代码基线

| 项 | 值 |
|---|---|
| `main` HEAD | `f04b0b28b674f701a4dae6cba516db95b8b8202c` |
| PR #25（C1 设计冻结） | merged，`a1de775b` |
| PR #26（C1 preflight / contract lock） | merged，`5337cccf` |
| PR #27（C1 Phase 1 实现） | merged，`f04b0b28` |
| Phase 1 原始 commit | `470ba69` → `becee88` → `66d2d73`（未 squash） |

### 1.2 测试基线

| 项 | 值 |
|---|---|
| unit | 730 |
| e2e | 234 |
| total | 964 |
| contract pins | 29/29 |
| service-spec tests | 28/28 |
| Fast QA | PASS（ruff 0 / mypy 0） |

### 1.3 关键代码位置（本设计所引用的真实位置）

| 符号 | 位置 |
|---|---|
| `SERVICES` 静态注册表（14 项） | `ecu/uds_server.py:2131` |
| `SERVICE_SPECS`（14 项 spec） | `ecu/uds_server.py:2311` |
| `SPECS_EXCLUDED`（0x27） | `ecu/uds_server.py:2343` |
| `_build_service_specs()` | `ecu/uds_server.py:2255` |
| `_guard_read_did()`（0x22 guard） | `ecu/uds_server.py:2165` |
| `service_read_data_by_identifier()`（0x22 legacy handler） | `ecu/uds_server.py:526` |
| `service_write_data_by_identifier()`（0x2E legacy handler） | `ecu/uds_server.py:725` |
| `required_security_level()` | `ecu/uds_server.py:425` |
| `EcuState.session: int = SESSION_DEFAULT` | `ecu/uds_server.py:178` |
| `_make_security_access_handler()`（0x27 工厂） | `ecu/uds_server.py:2092` |
| `handle_uds_request()` 协议层 dispatcher | `ecu/uds_server.py:2364` |
| `_security_services()`（0x27 注入） | `ecu/ecu.py:352` |
| `ecu.handle_uds_request()` 入口包装 | `ecu/ecu.py:374` |
| `handle_uds_request_protocol` 别名 | `ecu/ecu.py:403` |
| 网关默认工厂直调协议层 | `ecu/gateway.py:73` |

---

## 2. Phase 1 Review（已交付能力）

Phase 1 已完成并合并，本文档以其为**不可回退的前提**：

```
ServiceSpec / Guard model            → service_spec.py（零依赖叶子模块）
15-service coverage model            → 14 executable spec + 0x27 SPECS_EXCLUDED
0x22 guard                           → 1 个 guard，复刻逐 DID 循环 + 短路
0x2E six-rung guard chain            → 13 → 31 → 7F → 33 → 22 → 13
anti-drift validation                → validate_specs + 集合一致性 + 排除清单护栏
model-vs-legacy tests                → 28 例（含全组合矩阵与冻结锚点）
legacy dispatcher unchanged          → handle_uds_request 仍走 SERVICES → handler
```

### 2.1 Phase 1 的既有结论（本文档继承，不重新讨论）

- **OD-1 FROZEN**：per-service ordered guards；`resolve service → guard[0..n] → handler`。
  **禁止** global dispatcher authorization precheck（会改变既有 NRC 顺序）。
- **OD-2 FROZEN**：`guards` 是**唯一执行源**；`allowed_sessions` / `required_security` /
  `payload_scoped` 仅供 capability matrix / audit / 静态一致性检查。
  **禁止**声明字段与 guards 双重执行。
- `SERVICE_SPECS` 在 Phase 1 **不被执行**，且仅被 `uds_server.py` 与
  `tests/test_c1_service_spec_unit.py` 引用。

### 2.2 Phase 1 遗留的四个 OD

`OD-3 / OD-4 / OD-5 / OD-6` 均 **OPEN**（见 §5–§8）。本文档的目标之一即逐项冻结。

---

## 3. Phase 2 Goals

### 3.1 唯一目标（Single Objective）

> **迁移后 observable response bytes 必须与迁移前逐请求完全一致。**

不是"让新架构工作"，而是"**在不改变任何可观测行为的前提下**，把 0x22 / 0x2E 的执行路径
从 legacy inline 逻辑切到 `ServiceSpec.guards → handler`"。

### 3.2 具体目标

| # | 目标 |
|---|---|
| G1 | dispatcher 引入 `resolve_service_spec()`，统一进入 spec 执行路径 |
| G2 | `0x22` 原子迁移 + 逐字节对拍 |
| G3 | `0x2E` 原子迁移 + 逐字节对拍（**必须在 G2 通过之后**） |
| G4 | 建立 legacy response corpus（golden oracle），比 29 pins 更广 |
| G5 | 定义 per-SID 可回滚机制 |
| G6 | 冻结 OD-3 / OD-4 / OD-5 / OD-6 |

### 3.3 执行模型（Phase 2 完成态）

```
handle_uds_request(request, state, *, services=None)
    │
    ├─ 空请求            → 7F 00 13           （不变）
    ├─ resolve_service_spec(sid, services)
    │      ├─ services is None  → SERVICE_SPECS[sid]
    │      └─ services is not None → normalize（见 §6）
    ├─ spec is None      → 7F <sid> 11        （不变）
    ├─ spec.guards 按序求值
    │      └─ 任一返回 NRC → 7F <sid> <nrc>   （短路）
    └─ spec.handler(request, state)
```

**注意**：`guards` 只判定、不产生响应；拒绝时**只**返回 NRC 值，由 dispatcher 组装
`7F <sid> <nrc>`。这一分工必须在 Phase 2 保持，否则 0x22/0x2E 的 NRC 组装会分叉。

---

## 4. Non-Goals（Phase 2 明确不做）

| # | 非目标 | 归属 |
|---|---|---|
| N1 | 迁移其余 12 个静态服务（0x10/0x11/0x14/0x19/0x31/0x34–0x38/0x3E/0x85） | Phase 3 |
| N2 | `SERVICES` 与 `SERVICE_SPECS` 合并/去重 | Phase 3 |
| N3 | 删除 legacy handler 中的 inline 检查（见 §10 决策为"保留"） | 不计划 |
| N4 | 把 `0x27` 纳入 `ServiceSpec` | OD-6 决定为**不纳入** |
| N5 | 修改 `services=` 的**对外**签名 | 见 §6 冻结 |
| N6 | 修改 `gateway.py` / `gateway_runtime.py` | C2 边界 |
| N7 | 新增 session / 新增授权策略 | 永久禁止 |
| N8 | 清理 Issue #28（CRLF）与设计文档"15 个服务"措辞 | 独立 follow-up |

---

## 5. OD-3 —— 新服务是否必须有 ServiceSpec

### 5.1 现状（实测，非推测）

`_build_service_specs()`（`uds_server.py:2255`）的实现是：

```python
for sid, handler in SERVICES.items():
    specs[sid] = service_spec.ServiceSpec[EcuState](sid=sid, handler=handler)
```

即 **spec 由 `SERVICES` 自动派生**，而非逐个手写。因此：

- 向 `SERVICES` 新增一个服务 → **自动**获得 `guards=()` 的 spec；
- `set(SERVICE_SPECS) == set(SERVICES)` 的导入期护栏**恒成立**（同源派生，不可能不等）；
- `validate_specs()` 的 R1（`payload_scoped=True → guards 非空`）对自动派生项
  **恒不触发**（`payload_scoped` 默认 `False`、`guards` 默认 `()`）。

**结论：当前机制无法强制"新服务必须被认真声明授权语义"。** 它只保证了
"每个静态服务都有一条 spec 记录"，不保证"这条记录是经过考虑的"。

### 5.2 四个候选机制

| 方案 | 机制 | 时机 | 对 Phase 2 的影响 | 对 0x27 注入的影响 |
|---|---|---|---|---|
| **A** runtime fail-fast | dispatcher 每请求检查 spec 存在性 | 运行时 | 引入 per-request 开销；错误在生产路径才暴露 | 0x27 不在静态表 → 需额外豁免分支 |
| **B** import-time validation | 导入期校验（**当前已有**） | 导入期 | 无（已存在） | 无（0x27 走 `services=`，不经过该表） |
| **C** test / lint enforcement | 单测或 ruff 自定义规则强制"新增 SID 必须显式声明" | CI | 无（不影响 dispatcher） | 无 |
| **D** 仅文档约束 | 无强制 | — | 无 | 无 |

### 5.3 冻结决定

> **OD-3 = FROZEN：不 BLOCK Phase 2。**
> Phase 2 **不引入**任何新的"新服务必须有 spec"强制机制；
> 该强制属 **Phase 3**，且**选定落地形态为 C（test/lint enforcement）**，
> 排除 A（runtime fail-fast）与 D（仅文档）。

**理由：**

1. **不阻塞 Phase 2**：Phase 2 只迁移 0x22 / 0x2E，两者**都已有**完整 spec 与 guards。
   新服务的声明问题与本次迁移正交。
2. **排除 A（runtime fail-fast）**：它与 OD-1 的"禁止 global dispatcher precheck"
   同属"在 dispatcher 上叠加全局判定"的模式，且会在生产热路径引入每请求分支；
   更重要的是——**它无法表达"声明是否被认真考虑过"**，只能表达"记录是否存在"，
   而后者当前已由同源派生保证，属于**零信息量**的检查。
3. **排除 D**：Phase 1 已建立 `SPECS_EXCLUDED` 这类"显式记录"模式，
   回退到纯文档约束与既有工程水位不符。
4. **选定 C**：真正的缺口是"新增 `SERVICES` 条目时缺少一次**显式授权语义判断**"。
   这本质是**代码审查/一致性检查**问题，天然属于测试或 lint 层，
   可在不触碰 dispatcher 的前提下实现（例如：断言每个 SID 要么在显式白名单中、
   要么其 spec 的 `note` 非空）。

**对 Phase 2 的具体约束**：Phase 2 **不得**顺手修改 `_build_service_specs()` 的派生方式，
也不得引入任何 per-request 的 spec 存在性检查。

---

## 6. OD-4 —— `services=` 最终契约（Phase 2 的关键决策）

### 6.1 当前真实契约（N6 pins 实测冻结）

```
services=None             → 使用静态 SERVICES
services=自定义 mapping   → 完全取代静态表（不做合并）
自定义表缺某 SID          → 该 SID 返回 7F <sid> 11
```

由 `tests/test_c1_contract_pins.py` 的 `TestN6ServicesOverrideContract` 三条用例锁定：
`test_explicit_static_table_is_equivalent_to_default`、
`test_override_replaces_the_table_and_missing_sid_is_unsupported`、
`test_override_can_install_a_custom_handler`。

### 6.2 真实使用方（全仓库实测）

| 使用方 | 位置 | 形态 | 是否含 0x27 |
|---|---|---|---|
| `ecu.py` 入口包装 | `ecu/ecu.py:374-392` | `{**uds_server.SERVICES, **_security_services()}` —— **完整表 + 追加 0x27** | **是** |
| 网关默认工厂 | `ecu/gateway.py:73` | `uds_server.handle_uds_request(payload, state)` —— **不传 `services=`** | 否（静态表） |
| 测试 | 仅 `test_c1_contract_pins.py` N6 | 自定义 mapping | 否 |

`_security_services()`（`ecu/ecu.py:352`）只返回一个条目：

```python
{0x27: uds_server._make_security_access_handler(now=_now(), security_config=SECURITY_CONFIG)}
```

### 6.3 ⚠ 关键发现：朴素归一化会让 0x22 / 0x2E 的 guards 在生产主路径上失效

上游设计文档 §18.1 对 OD-4 的记载是：

> OD-1 已冻结**归一化规则**（覆盖 = 整体替换，`guards=()`）

若**字面**执行该规则——把所有 override 条目归一化为 `ServiceSpec(sid, handler, guards=())`——
则会产生如下后果：

```
ecu.py 路径（生产主路径）
    services = {**SERVICES, 0x27: ...}      ← 完整表
        ↓ 逐条归一化
    0x22 → ServiceSpec(handler=SERVICES[0x22], guards=())   ← guards 被清空
    0x2E → ServiceSpec(handler=SERVICES[0x2E], guards=())   ← guards 被清空
        ↓
    spec.guards 为空 → 授权链在**生产主路径上完全不执行**
```

**这是一个真实且危险的分叉**：

- **短期（Phase 2 完成时）**：由于 §10 决策保留 handler 内的 inline 检查，
  **可观测字节仍然正确**，测试全绿 —— 缺陷被"防御性检查"完全掩盖。
- **长期**：一旦有人依据"guard 是唯一执行源（OD-2）"而清理 handler 内 inline 逻辑，
  **ecu.py 路径将静默丢失 0x22 / 0x2E 的授权**，且**现有任何测试都不会失败**
  （pins 测的是 legacy 行为，service-spec 测试测的是模型自身，都不覆盖
  "ecu.py 路径下 guards 是否真的执行"）。

**并且该分叉是路径相关的**：`gateway.py:73` 不传 `services=`，
会走 `SERVICE_SPECS`，guards **会**执行。于是同一个 0x22 请求，
经 ECU 入口与经网关默认工厂，**执行机制不同**。

> 这正是 Phase 1 设计文档把该缝标为"C1 最可能触碰的迁移点"且"此前零测试覆盖"的原因。

### 6.4 四个方案

| 方案 | 对外 API | dispatcher 内部 | 评价 |
|---|---|---|---|
| **A** 朴素归一化 | `Mapping[int, Callable]` | 覆盖条目一律 `guards=()` | 忠实于 OD-1 字面；**但生产主路径 guards 失效**（§6.3） |
| **B** 改为 spec API | `Mapping[int, ServiceSpec]` | 直接使用 | 迫使 `ecu.py` 构造 spec → **接口变更**；N6 三条 pin 全部需重写；且 `ecu.py` 需 import `service_spec`，注入层被迫了解 spec 形态 |
| **C** 兼容二者 | 二者皆可 | 类型分派 + 优先级规则 | 引入双形态长期共存与隐式类型判定；复杂度最高，收益不明确 |
| **A′** handler 可覆盖、**授权不可覆盖** | `Mapping[int, Callable]`（**不变**） | sid 先在 override 中解析**存在性**；`guards` 一律取自 `SERVICE_SPECS`；`handler` 取自 override（若存在） | 见 §6.5 |

### 6.5 A′ 的精确定义

```
resolve_service_spec(sid, services) -> ServiceSpec | None:

    if services is None:
        return SERVICE_SPECS.get(sid)

    # 覆盖表决定"哪些 SID 存在"（保持 N6 的"取代而非合并"）
    handler = services.get(sid)
    if handler is None:
        return None                      # → 7F <sid> 11

    base = SERVICE_SPECS.get(sid)
    if base is None:
        # 静态表未建模的 SID（典型：0x27 注入、测试自定义 SID）
        return ServiceSpec(sid=sid, handler=handler, guards=())

    # 已建模 SID：保留授权链，仅替换 handler
    return replace(base, handler=handler)
```

**语义**：`services=` 覆盖的是**实现**（handler），**不覆盖授权**（guards）。

### 6.6 为什么选 A′

1. **对外契约零变化**：`ecu.py`（`ecu/ecu.py:374`）与 `gateway.py:73` 一行不改；
   N6 三条 pin **全部原样通过**（逐条验证见 §6.7）。
2. **消除 §6.3 的路径分叉**：无论经 ECU 入口还是网关，0x22/0x2E 的 guards 都会执行。
3. **安全默认（fail-closed）**：授权不可被调用方静默移除。
   显式替换 handler 仍然允许（N6 的 `test_override_can_install_a_custom_handler` 需要），
   但**不能顺带取消授权**。
4. **与 OD-2 自洽**：`guards` 仍是唯一执行源，且其来源是**服务身份（SID）**，
   而不是调用方传入的实现对象。
5. **不引入类型分派**：避免方案 C 的双形态长期共存。

### 6.7 A′ 对既有契约的逐条验证（设计期推演）

| 既有测试 | 请求 | A′ 下的结果 | 是否保持 |
|---|---|---|---|
| `test_explicit_static_table_is_equivalent_to_default` | `dict(SERVICES)` vs `None` | 两者 SID 集合相同、guards 同源、handler 同对象 → 逐字节相同 | ✅ |
| `test_override_replaces_the_table_and_missing_sid_is_unsupported` | override 只含 0x3E，请求 0x22 | 0x22 不在 override → `None` → `7F 22 11` | ✅ |
| 同上 | override 只含 0x3E，请求 0x3E | `SERVICE_SPECS[0x3E].guards == ()` → handler 返回 `7E 00` | ✅ |
| `test_override_can_install_a_custom_handler` | override 装自定义 0x3E handler | guards=() → 自定义 handler 被调用 → `7E 00` | ✅ |
| `ecu.py` 0x27 注入 | `{**SERVICES, 0x27: ...}`，请求 0x27 | 0x27 ∉ `SERVICE_SPECS` → `ServiceSpec(guards=())` → handler 正常执行 | ✅ |
| `ecu.py` 0x22 / 0x2E | 同上，请求 0x22 | guards 取自 `SERVICE_SPECS[0x22]` → **授权链生效** | ✅（修复 §6.3） |

### 6.8 对 OD-1 冻结规则的关系（必须显式说明）

A′ **偏离**了"覆盖 = 整体替换，`guards=()`"的**字面**表述，但**符合其意图**：

- OD-1 的冻结对象是**执行模型**（per-service ordered guards，禁止全局前置检查），
  以及**覆盖的归一化方向**（把任意 override 归一进同一执行模型，而不是让 override
  绕过模型）。
- A′ 正是这一意图的**严格执行**：override 无法绕过模型，只能替换模型中的 handler。
- 若评审认为必须**字面**保留 `guards=()`，则等价于选择方案 A，
  并**必须同时**接受 §6.3 的路径分叉，且**必须**在 Phase 2 增加一条
  "ecu.py 路径下 guards 确实执行"的专项测试作为补偿控制。

### 6.9 冻结决定

> **OD-4 = FROZEN：方案 A′。**
> `handle_uds_request(..., services=...)` 的**对外签名与语义保持不变**
> （`Mapping[int, Callable]`；取代而非合并；缺 SID → `11`）。
> dispatcher 内部按 §6.5 归一化：**存在性取自 override，guards 取自 `SERVICE_SPECS`，
> handler 取自 override**。
> **`services=` 不可覆盖授权。**

**对 Phase 2 的约束**：`ecu.py`、`gateway.py` 不得修改；
N6 三条 pin 不得修改、不得弱化。

---

## 7. OD-5 —— 0x2E session guard 的写法

### 7.1 现状

Phase 1 已实现（`uds_server.py:2214`，`_guard_write_did_session`）：

```python
if state.session == uds_types.SESSION_DEFAULT:
    return uds_types.NRC_SERVICE_NOT_SUPPORTED_IN_SESSION
return None
```

与 legacy inline（`uds_server.py:759-762`）逐字一致。

### 7.2 两种写法在何种输入下不等价

```
SUPPORTED_SESSIONS = {SESSION_DEFAULT(0x01), SESSION_PROGRAMMING(0x02), SESSION_EXTENDED(0x03)}
```

`EcuState.session` 是 **public int**（`uds_server.py:178`，默认 `SESSION_DEFAULT`），
**可以直接赋值**，不经过 `0x10` 的 `SUPPORTED_SESSIONS` 校验。

| `state.session` | `== SESSION_DEFAULT` | `not in {EXTENDED, PROGRAMMING}` |
|---|---|---|
| `0x01` DEFAULT | 拒绝 `7F` | 拒绝 `7F` |
| `0x02` PROGRAMMING | 放行 | 放行 |
| `0x03` EXTENDED | 放行 | 放行 |
| **`0x04` 及任意未支持值** | **放行** | **拒绝 `7F`** |

即：**二者在协议可达域内等价，在协议不可达域（直接赋值）内不等价**。

### 7.3 冻结决定

> **OD-5 = FROZEN：保持 `== SESSION_DEFAULT`。**

**理由：**

1. **最小行为变化原则**：Phase 2 的唯一目标是"可观测字节不变"。
   改为 `not in {EXTENDED, PROGRAMMING}` 会**改变**未知 session 值下的行为，
   属于**授权语义变更**，需要独立的 justification 与新增 regression coverage——
   两者都不在 Phase 2 范围内。
2. **协议不可达**：进入 `EcuState.session` 的唯一协议路径是 `0x10`
   （`service_diagnostic_session_control`，`uds_server.py:438`），
   它校验 `session_type not in uds_types.SUPPORTED_SESSIONS → 0x12`。
   因此**不存在**能让未知值到达该 guard 的 UDS 请求。
3. **收紧不是免费的**：若未来引入新 session，`not in` 写法会**默认拒绝**新 session，
   而 `== DEFAULT` 默认放行 —— 二者对"语义变更的可见性"取向不同。
   当前编码风格（`== DEFAULT`）与 legacy 一致，且变更成本最低。
4. **一致性**：保持与 legacy inline 逐字一致，使 Phase 2 的逐字节对拍
   不因"顺手改进写法"而产生噪声。

**对 Phase 2 的约束**：`_guard_write_did_session` 的实现**不得修改**。
若未来需要改变，必须在**独立**的 design review 中进行，并提供
"未知 session 值"的专项 regression coverage。

---

## 8. OD-6 —— 0x27 是否纳入 ServiceSpec

### 8.1 0x27 与静态服务的真实差异（实测）

| 维度 | 静态服务（如 0x22） | 0x27 SecurityAccess |
|---|---|---|
| 注册位置 | `SERVICES`（`uds_server.py:2131`） | **不在**静态表内 |
| 构造时机 | 模块导入时 | **每次请求**现场构造（`ecu/ecu.py:352-371`） |
| 依赖 | 无 | `_now()` 时间快照 + `SECURITY_CONFIG` 原对象引用 |
| 注入点 | — | `ecu.handle_uds_request` 包装（`ecu/ecu.py:374`） |
| 行为性质 | **服务被授权门控**（session / security / DID） | **自身 SecurityAccess 状态机**（seed / key / lockout / 过期） |
| 跨请求状态 | 由 `EcuState` 承载 | 经 `SECURITY_CONFIG` 与 `EcuState` 中的 seed/lockout 字段 |
| 网关路径可达 | 是（`gateway.py:73` → 静态表） | **否**（网关默认工厂不含 0x27） |

关键约束（`ecu.py:358-365` 明确要求"请勿优化"）：

- `_now()` **必须每次调用现场求值**，不得缓存 —— 测试通过
  `mock.patch.object(ecu, "_now", ...)` 注入时钟，只有按名字在 `ecu` 命名空间
  现场解析才能让 patch 生效。
- `SECURITY_CONFIG` **必须原对象引用**传入，不得 copy —— 保持"配置改动即时可见"。

### 8.2 三个方案

| 方案 | 内容 | 评价 |
|---|---|---|
| **A** 维持现状 | `0x27 ∉ SERVICE_SPECS`、`0x27 ∈ SPECS_EXCLUDED` | 见 §8.3 |
| **B** 注入时构造 ServiceSpec | `_security_services()` 改为返回 `ServiceSpec` | **迫使 `services=` 契约变化**（`ecu.py` 变成 spec 生产者）→ 直接冲突于 §6 冻结的 OD-4；且让注入层 import `service_spec`，把 `now`/`SECURITY_CONFIG` 的捕获时机问题带进 spec 构造 |
| **C** 统一动态 spec registry | 引入可注册的动态 spec 表 | 相当于**重做 dispatcher 的注册机制**；且"动态服务如何声明策略"与网关策略（C2/C3）耦合，会把 C1 与 C2/C3 边界打穿 |

### 8.3 冻结决定

> **OD-6 = FROZEN：方案 A（维持现状）。**
> `0x27` 继续 `∉ SERVICE_SPECS`、`∈ SPECS_EXCLUDED`；
> Phase 2 **不**改动注入路径、**不**改 `SecurityAccess` 状态机、
> **不**改 `services=` 契约。

**理由：**

1. **方案 B 与 §6 的 OD-4 冻结直接冲突**：OD-4 已冻结"对外 API 保持
   `Mapping[int, Callable]`"。若 0x27 注入改为传 spec，则契约必然改变，
   或必须走 §6.4 的方案 C（双重形态），两处决策互相打架。
2. **方案 B 还会破坏一个已验证的时序不变式**：`_now()` 必须**每请求现场求值**。
   若 spec 在 `_build_service_specs()` 之类的导入期位置构造，
   `now` 会被冻结在导入时刻；即使在请求期构造，也是把
   "spec 构造"与"时钟注入"两个关注点耦合。
3. **方案 C 超出 C1 边界**：统一动态 registry 本质是 C2/C3 的话题
   （网关策略与 0x27 的集成），属 §18 明确禁止的越界。
4. **不因"15 个服务完整覆盖"而强行纳入**：`SPECS_EXCLUDED` 已经在
   **capability description 层面**完整记录了 0x27 的存在与排除原因，
   矩阵的"策略盲区"是**显式声明**的，而不是静默漏掉的。
   这比把它塞进一个语义不匹配的 registry 更诚实。
5. **Phase 2 的实际需求为零**：Phase 2 只迁移 0x22 / 0x2E。

**对 Phase 2 的约束**：`_security_services()`、`_make_security_access_handler()`、
`handle_uds_request` 包装（`ecu/ecu.py:374`）、`SECURITY_CONFIG`、`_now()`
**全部不得修改**。

---

## 9. Dispatcher Migration Architecture

### 9.1 `resolve_service_spec()`

新增一个模块级纯函数（**Phase 2 实现时才写**，此处冻结其契约）：

```python
def resolve_service_spec(
    sid: int,
    services: Mapping[int, Callable[[bytes, EcuState], bytes]] | None,
) -> service_spec.ServiceSpec[EcuState] | None
```

语义见 §6.5。**契约要点：**

| 输入 | 输出 |
|---|---|
| `services=None`，sid 已建模 | `SERVICE_SPECS[sid]` |
| `services=None`，sid 未建模 | `None` |
| `services` 非空，sid **不在** override | `None`（取代而非合并，N6） |
| `services` 非空，sid 在 override 且 **∉** `SERVICE_SPECS` | `ServiceSpec(sid, handler, guards=())` |
| `services` 非空，sid 在 override 且 **∈** `SERVICE_SPECS` | `replace(base, handler=override[sid])` |

**纯函数要求**：不得读全局可变状态、不得有副作用、**不得调用任何 guard**。
这使其可以被独立单测，也保证"解析"与"执行"分离。

### 9.2 dispatcher 的最终形态

```python
def handle_uds_request(request, state, *, services=None) -> bytes:
    # ① 空请求（必须在查表之前）—— 语义完全不变
    if not request:
        return uds_types.negative_response(0x00, NRC_INCORRECT_MESSAGE_LENGTH)

    service = request[0]

    # ② 解析 spec（唯一新增的步骤）
    spec = resolve_service_spec(service, services)

    # ③ 未注册 SID —— 语义完全不变
    if spec is None:
        return uds_types.negative_response(service, NRC_SERVICE_NOT_SUPPORTED)

    # ④ 按序求值 guards（Phase 2 仅对已启用的 SID 生效，见 §15）
    for guard in _guards_for(spec):
        nrc = guard(request, state)
        if nrc is not None:
            return uds_types.negative_response(service, nrc)

    # ⑤ handler
    return spec.handler(request, state)
```

### 9.3 必须保持不变的 dispatcher semantics

| 行为 | 当前 | Phase 2 后 |
|---|---|---|
| 空请求 | `7F 00 13`，**先于**任何查表 | **不变** |
| 未注册 SID | `7F <sid> 11`，不额外做长度检查，尾随负载一并忽略 | **不变** |
| `services=None` | 静态 `SERVICES` | `SERVICE_SPECS`（同源派生，handler 同对象） |
| override 取代语义 | 取代而非合并 | **不变**（§6.5） |
| guard 拒绝 | — | `7F <sid> <nrc>`，**只**由 dispatcher 组装 |

**明确禁止**：任何形式的 global / 前置授权判定（OD-1）。
guards **只**能来自被解析出的单个 `spec`，**不得**在任何服务之前统一执行。

---

## 10. guards / handler 关系（重复校验问题）

### 10.1 问题

Phase 1 的 guards 是从 handler inline 逻辑**复制**出的模型。Phase 2 之后会出现：

```
guard   : 检查长度 / DID / session / security
handler : 仍然检查长度 / DID / session / security
```

### 10.2 三个方案

| 方案 | 内容 | 行为风险 | 维护成本 | NRC precedence | 复杂度 | 测试成本 |
|---|---|---|---|---|---|---|
| **1** 保留 handler 防御检查 | guard = 外部强制门；handler = 防御性不变式 | **最低** | 中（两处同步） | **零风险** | 低 | 低（golden 已覆盖） |
| **2** 删除 handler 重复检查 | 单一职责 | **高**：§6.3 类问题会直接变成安全漏洞；handler 被直接调用时失去保护 | 低 | 有风险（顺序依赖 guard 链） | 中 | 高（需新增 handler 单测） |
| **3** 分离 validation / authorization / business | 三层重构 | 中 | **最高**（触及全部 14 个服务的结构） | 需重新冻结 | 高 | 很高 |

### 10.3 补充约束（决定性）

- `SERVICES` 是**公开的**模块级常量，`handle_uds_request_protocol`
  （`ecu/ecu.py:403`）也是公开别名 —— handler **可以被绕过 guard 直接调用**。
  删除 handler 内检查等于**移除**这层保护。
- `test_ecu_unit.py`（116 例）与既有 e2e 直接调用 handler / dispatcher，
  删除 inline 检查会改变它们的可观测前提。
- Phase 2 的唯一目标是"字节不变"，方案 2/3 都**不产生任何字节收益**。

### 10.4 冻结决定

> **方案 1 —— 保留 handler 内的防御性检查。**

冻结其不变式表述：

```
guard   = externally enforced authorization / validation gate
          （唯一执行源，决定 NRC 与短路顺序）
handler = defensive invariant
          （不假设调用方已通过 guard；自身仍可独立正确拒绝）
```

**必须确认**：保留不改变 NRC。因为 guard 链**先于** handler 执行且**短路**，
handler 内的同序检查在 guard 通过后**必然**也会通过（两者判定条件逐字节等价，
已由 Phase 1 的 `TestModelAgreesWithLegacy` 28 例证明），
因此 handler 不会产生**不同的** NRC。

**对 Phase 2 的约束**：`service_read_data_by_identifier`（`uds_server.py:526`）
与 `service_write_data_by_identifier`（`uds_server.py:725`）的函数体
**不得删除任何检查**。

> 备注：Phase 1 review 阶段曾记录 `WRITABLE_DID_LENGTHS[did]` 的 KeyError 潜在风险
> （guard ⑤ 担保 ⑥ 可取值）。该风险**继承自 legacy**，Phase 2 不引入也不修复；
> 若未来要收紧，应作为独立设计项并在 `validate_specs` 增加**导入期**不变量
> （`WRITABLE_DIDS ⊆ keys(WRITABLE_DID_LENGTHS)`），而不是在 guard 里加分支。

---

## 11. Migration A —— 0x22

### 11.1 阶段

```
A0  capture legacy golden（迁移前，dispatcher 仍为 legacy）
        ↓
A1  enable spec execution for 0x22 only
        ↓
A2  byte-for-byte compare（golden replay）
        ↓
A3  full regression（964 + 29 pins + 28 service-spec）
        ↓
A4  决定 legacy inline 的去留（按 §10.4 = 保留）
```

**A4 已预先冻结**：按 §10.4，`service_read_data_by_identifier` 的 inline 检查**保留**。
A4 因此**不是**代码变更步骤，而是"确认无变更"步骤。

### 11.2 启用方式

per-SID 白名单（详见 §15）：

```
_SPEC_EXECUTION: frozenset[int]    A 阶段 = {0x22}      B 阶段 = {0x22, 0x2E}
```

### 11.3 Golden corpus 覆盖矩阵（0x22）

| # | 类别 | 用例 |
|---|---|---|
| 1 | 单个公开 DID | `22 F1 90` |
| 2 | 单个受保护 DID | `22 F1 87` |
| 3 | 未知 DID | `22 FF FF` |
| 4 | 受保护 → 未知 | `22 F1 87 FF FF` |
| 5 | 未知 → 受保护 | `22 FF FF F1 87` |
| 6 | 公开 → 未知 | `22 F1 90 FF FF` |
| 7 | 多个公开 | `22 F1 90 F1 80` |
| 8 | 多个受保护 | `22 F1 87 F1 87` |
| 9 | 混合成功 | `22 F1 80 F1 87`（解锁态） |
| 10 | 畸形长度 | `22`、`22 F1`、`22 F1 90 F1` |
| 11 | 三种会话 | DEFAULT / EXTENDED / PROGRAMMING |
| 12 | 安全态 | LOCKED / LEVEL1 |
| 13 | DID override | 先 `2E F1 02` 写入，再 `22 F1 02` 读回 overridden 值 |
| 14 | 边界 | `22 F1 87 FF FF`（受保护在前，**必须** `7F 22 33`）<br>`22 FF FF F1 87`（未知在前，**必须** `7F 22 31`） |

第 14 项是**冻结锚点**，同时被 29 pins 中的 N2 与 Phase 1 的 28 例覆盖。

### 11.4 必须显式验证的语义

- **all-or-nothing**：任一 DID 失败 → 整个请求失败，**不返回部分数据**。
- **first-failure-wins**：**首个**失败的 DID 决定 NRC，因此 NRC 依赖 DID 的**请求顺序**。
- **短路方向**：逐 DID 循环内，未知（`31`）判定**先于**安全（`33`）判定。

### 11.5 通过判据

```
∀ case ∈ corpus:  legacy_response(case) == spec_path_response(case)     （逐字节）
              ∧  state_projection_after(case) 相等                       （§14）
```

---

## 12. Migration B —— 0x2E

### 12.1 前置条件

> **Migration A 的 A2 / A3 全部通过后，方可设计/实施 B。**
> B 不得与 A 并行，不得合并为一次切换。

### 12.2 阶段

```
B0  capture legacy golden
        ↓
B1  enable spec execution for 0x2E only（A 保持启用）
        ↓
B2  byte-for-byte compare
        ↓
B3  full regression
        ↓
B4  确认 legacy inline 保留（§10.4）
```

### 12.3 Golden corpus 覆盖矩阵（0x2E）

| # | 类别 | 用例 |
|---|---|---|
| 1 | 畸形长度 | `2E`、`2E F1`、`2E F1 02`（数据为空） |
| 2 | 未知 DID | `2E FF FF 58` |
| 3 | DEFAULT 会话 | `2E F1 02 58` |
| 4 | EXTENDED + LOCKED | `2E F1 02 58` |
| 5 | EXTENDED + LEVEL1 | `2E F1 02 58`（合法写入） |
| 6 | PROGRAMMING + LOCKED | `2E F1 02 58` |
| 7 | PROGRAMMING + LEVEL1 | `2E F1 02 58` |
| 8 | 只读 DID | `2E F1 90 58`（EXTENDED+LEVEL1） |
| 9 | 可写 DID | `2E F1 02 58` |
| 10 | 数据长度非法 | 长度 0（同上 #1）、长度 17（`WRITABLE_DID_LENGTHS[0xF102] = (1,16)`） |
| 11 | 合法写入 | 长度 1 与长度 16（闭区间边界） |
| 12 | 写后读 | 写入后 `22 F1 02` 读回新值 |

### 12.4 必须冻结的 rung 顺序

```
13 → 31 → 7F → 33 → 22 → 13
```

逐级语义：

| rung | 条件 | NRC |
|---|---|---|
| ① | `len(request) < 4` | `0x13` |
| ② | DID 未知（∉ `DID_DATABASE`） | `0x31` |
| ③ | `state.session == SESSION_DEFAULT` | `0x7F` |
| ④ | `state.security_level < SECURITY_LEVEL1` | `0x33` |
| ⑤ | DID ∉ `WRITABLE_DIDS`（已知但只读） | `0x22` |
| ⑥ | 数据长度 ∉ `WRITABLE_DID_LENGTHS[did]` 闭区间 | `0x13` |

**冻结锚点**（必须同时被 corpus 与 pins 覆盖）：

```
2E FF FF 58 → 7F 2E 31      （② 先于 ③ —— 未知 DID 优先于会话门控）
2E F1 02 58 → 7F 2E 7F      （③ DEFAULT 会话）
```

**特别验证的优先关系**：`session`（③）先于 `security`（④）、
`security`（④）先于 `writability`（⑤）、
`writability`（⑤）先于 `data length`（⑥）。

---

## 13. Golden Corpus（实现方式）

### 13.1 原则

> **不手写 expected 值。**
> 手写期望值只能证明"我同意我自己的假设"，无法证明"迁移前后一致"。

必须设计 **legacy oracle**：在迁移**前**捕获，在迁移**后**重放。

### 13.2 数据结构

```
GoldenCase:
    case_id      : str
    sid          : int
    request      : bytes
    initial_state: StateSnapshot          # 见 §14
    legacy_response : bytes               # A0/B0 阶段由 legacy 路径捕获
    legacy_final_state : StateSnapshot    # 同上（捕获副作用）

GoldenCorpus:
    schema_version : int
    captured_at_commit : str              # 捕获时的 git commit
    dispatcher_mode : "legacy"            # 捕获时 dispatcher 必须仍是 legacy
    cases : list[GoldenCase]
```

### 13.3 存储与生命周期

| 项 | 决定 |
|---|---|
| 位置 | `labs/vehicle/uds-basic/tests/golden/`（Phase 2 实现时创建） |
| 格式 | JSON（可 diff、可人工审计、可 code review） |
| 生成 | 由脚本从 legacy 路径生成并提交 —— 生成脚本本身属测试代码 |
| 重放 | 对同一 `(request, initial_state)` 走 spec 路径，逐字节比较 |
| 保留 | **迁移完成后长期保留**（作为 §16 的 migration contract test） |

### 13.4 生成时机约束

- golden **必须**在 `dispatcher_mode == "legacy"` 时捕获；
- 捕获 commit 必须记录，且该 commit 必须**早于** A1 / B1；
- golden 一旦捕获，**不得**为"让测试通过"而重新生成 —— 任何重新生成
  都必须视为**行为变更**并单独评审。

---

## 14. State Equality

### 14.1 为什么不能只比较 response bytes

- **0x22**：本身**不应**改变 state（只读）。若某次迁移意外引入状态副作用，
  只比 response 会**漏掉**。
- **0x2E**：成功路径会写 `state.did_overrides[did] = data`
  （`uds_server.py:781`）。若迁移后写入的值/时机不同，
  只比 response（`6E <DID:2>`）**完全看不出来**。

### 14.2 StateSnapshot 定义

比较 **response equality + relevant final-state equality**。
对 0x22 / 0x2E，其可达状态面为：

| 字段 | 0x22 | 0x2E | 说明 |
|---|---|---|---|
| `session` | 读 | 读 | guard ③ 依赖 |
| `security_level` | 读 | 读 | guard ④ / `required_security_level` 依赖 |
| `did_overrides` | **读** | **写**（成功路径） | 0x22 读 override 优先；0x2E 写入 |

```
StateSnapshot = (
    session,
    security_level,
    frozenset(did_overrides.items()),     # 有序无关，用 frozenset 规范化
)
```

**要求**：

```
∀ case:  snapshot_after(legacy) == snapshot_after(spec_path)
```

### 14.3 关于"全状态比较"的取舍

`EcuState` 尚有 seed / lockout / failed_attempts / active_routines /
download_buffer / dtc 相关字段等。对 0x22 / 0x2E 而言这些**不可达**。

**决定**：Phase 2 的 **强制**判据是 §14.2 的三字段投影（**可达面**）；
**额外建议**（非阻塞）在 A2/B2 期间同时记录**全字段**快照差异，
用于发现非预期的间接副作用 —— 但**不**将其作为失败判据，
以免因无关字段的正常抖动产生假阳性。

### 14.4 时间相关性

- 0x22 / 0x2E **均不依赖时钟**（时钟只被 0x27 SecurityAccess 使用）。
- 因此 0x22 / 0x2E 的 golden **无需** mock 时间；重放是确定性的。
- 这是选择 0x22 / 0x2E 作为 Phase 2 首批迁移对象的**额外理由**
  （0x27 的 seed 过期/锁定行为与时间耦合，不适合作为首批迁移对象）。

---

## 15. Rollback Strategy

### 15.1 目标

```
0x22 migration failed
    → disable only 0x22 spec execution
    → 0x2E untouched
    → other services untouched
```

**明确拒绝**："所有服务一次性切换"的设计。

### 15.2 机制：per-SID 显式白名单

```
#: 已切换到 ServiceSpec 执行路径的 SID；其余仍走 legacy 逐字路径。
_SPEC_EXECUTION: frozenset[int] = frozenset()
```

dispatcher 中（`_guards_for` 的实现）：

```python
def _guards_for(spec):
    return spec.guards if spec.sid in _SPEC_EXECUTION else ()
```

| 阶段 | `_SPEC_EXECUTION` | 语义 |
|---|---|---|
| Phase 1（当前） | `frozenset()` | 全部走 legacy（guards 均不执行） |
| A1 后 | `{0x22}` | 仅 0x22 走 spec |
| B1 后 | `{0x22, 0x2E}` | 两者走 spec |
| 回滚 A | `frozenset()` 或 `{0x2E}` | 仅回退 0x22 |

### 15.3 为什么不用 feature flag（环境变量）

| 候选 | 评价 |
|---|---|
| 环境变量 feature flag | 引入**运行时可变**的执行路径 → 同一 commit 在不同环境行为不同；CI 与本地可能分叉；且违反"行为变更必须显式、必须有证据"的原则 |
| **per-SID dispatch map（选定）** | 状态是**源码常量**，随 commit 一起被评审、被测试、被 review；回滚 = 一次明确的可 review 的代码改动 |
| 临时 adapter | 需要额外抽象层，Phase 2 结束后还要拆除，净收益为负 |

> 关键理由：Phase 2 的"回滚"应该是一次**可审计的代码变更**，
> 而不是一次**环境配置变更**。前者留下 git 历史与 review 记录，后者不会。

### 15.4 双重路径的关闭（Phase 2 完成时）

当 A2/A3 与 B2/B3 全部通过并稳定后：

- `_SPEC_EXECUTION` 收敛为 `{0x22, 0x2E}` 并**保留**（它同时也是 §16 的
  "哪些服务已迁移"的显式记录）；
- `() else ()` 的 legacy 分支**不删除** —— 其余 12 个服务仍走 legacy，
  这是 Phase 3 的入口；
- **禁止**在 Phase 2 顺手把 `_SPEC_EXECUTION` 扩到全部 14 个服务（§4 N1）。

---

## 16. Test Impact

### 16.1 现有测试的分类

| 测试 | 用例数 | Phase 2 后的定位 |
|---|---|---|
| `test_c1_contract_pins.py` | 29 | **长期 contract test**（不变、不弱化） |
| `test_c1_service_spec_unit.py` | 28 | **长期 contract test**（不变） |
| `test_ecu_unit.py` | 116 | **长期**（legacy 行为基线） |
| `test_e2e_vcan.py` | 69 | **长期**（端到端字节基线） |
| gateway 相关（unit + vcan） | 13 + 21 | **长期**（C2 边界证据） |
| 其余 unit / vcan | — | **长期**（回归网） |

### 16.2 新增测试（Phase 2 实现时才写）

| 新增 | 类型 | 迁移后是否保留 |
|---|---|---|
| golden 生成脚本 | 工具 | 保留（可审计性） |
| golden replay 测试 | **migration contract test** | **保留** —— 它是 Phase 3 逐服务迁移的可复用夹具 |
| `resolve_service_spec()` 单测 | 长期 contract test | 保留（§6.5 五条契约） |
| `_SPEC_EXECUTION` 回滚测试 | 长期 | 保留（证明 per-SID 隔离有效） |

### 16.3 明确禁止

- **不得删除 29 个 pins**；
- **不得**为了让迁移通过而弱化任何既有断言（改写为更宽松的预期）；
- **不得**删除 golden（迁移完成后它仍然是回归网的一部分）。

### 16.4 用例数量预期

| 阶段 | unit | e2e | total |
|---|---|---|---|
| Phase 2 前（当前） | 730 | 234 | 964 |
| Phase 2 后 | 730 + 新增 | 234（不变） | > 964 |

**e2e 不应变化**；unit 只增不减。

---

## 17. Exit Criteria（Phase 2 完成判据）

> **"tests pass" 本身不能作为唯一完成标准。**

必须**同时**满足：

| # | 判据 |
|---|---|
| 1 | `0x22` migration **byte-for-byte identical**（golden corpus 全通过） |
| 2 | `0x2E` migration **byte-for-byte identical**（golden corpus 全通过） |
| 3 | 29 contract pins **PASS** |
| 4 | Phase 1 service-spec tests（28）**PASS** |
| 5 | baseline regression **964 PASS** |
| 6 | Gateway behavior **unchanged** |
| 7 | `0x27` injection behavior **unchanged** |
| 8 | **no new session** |
| 9 | **no unrelated authorization policy** |
| 10 | **no unresolved migration path**（`_SPEC_EXECUTION` 与实际执行路径逐 SID 一致） |

**判据 10 的检验方式**：对每个 SID，断言
"`sid ∈ _SPEC_EXECUTION` ⟺ 该 SID 的 guards 在 dispatcher 中被求值"。
避免出现"代码看起来迁移了、实际没生效"（§6.3 类问题的通用防线）。

---

## 18. C2 / C3 Boundary

再次冻结：

```
C1 = ECU service authorization        ← 本文档范围
C2 = Gateway security policy          ← NOT STARTED
C3 = 0x27 through Gateway             ← NOT STARTED
```

**Phase 2 不得修改**：

| 文件 | 约束 |
|---|---|
| `ecu/gateway.py` | 0 改动（含 `gateway.py:73` 的直调） |
| `ecu/gateway_runtime.py` | 0 改动 |
| `ecu/ecu.py` | 0 改动（0x27 注入边界冻结） |

**已知并接受的既有不对称**（本设计**不修复**，仅登记）：
`gateway.py:73` 直调协议层且不传 `services=`，因此网关路径**不含** 0x27；
这是 C2/C3 的议题，Phase 2 保持现状。

---

## 19. Risks

| # | 风险 | 严重度 | 缓解 |
|---|---|---|---|
| R1 | **§6.3 类路径分叉**：归一化让 guards 在生产路径失效，且被 handler 防御检查掩盖 | **高** | §6.9 选定 A′（guards 取自 `SERVICE_SPECS`，不可被 override 覆盖）+ §17 判据 10 |
| R2 | 未来依据 OD-2 删除 handler inline 检查 → 失去防御纵深 | 高 | §10.4 冻结"保留"；且 R1 的 A′ 已消除主因 |
| R3 | golden 只比 response 漏掉状态副作用 | 中 | §14 强制 state projection 相等 |
| R4 | 为"通过测试"重新生成 golden，抹掉真实回归 | 中 | §13.4：重新生成 = 行为变更，须单独评审 |
| R5 | 一次性迁移 0x22 + 0x2E，失败时无法定位 | 中 | §11/§12 强制分两次；§15 per-SID 白名单 |
| R6 | 范围蔓延到"顺手迁移其余 12 个服务" | 中 | §4 N1/N2；§15.4 明文禁止 |
| R7 | 改动 `services=` 契约导致 `ecu.py` / 网关连带变更 | 中 | §6.9 冻结对外签名不变；N6 三条 pin 不得改动 |
| R8 | Phase 2 顺手修 `WRITABLE_DID_LENGTHS` KeyError 风险（§10.4 备注） | 低 | 明确列为独立设计项，不在 Phase 2 修复 |
| R9 | Issue #28（CRLF）干扰验证，产生假失败 | 中 | 独立处理；验证时使用不受 CRLF 影响的方式并在报告中说明 |
| R10 | 迁移后 `SERVICE_SPECS` 与 `SERVICES` 出现双源漂移 | 低 | 保留 Phase 1 的导入期集合一致性护栏 |

---

## 20. Final Design Freeze

### 20.1 冻结项

| ID | 决定 | 状态 |
|---|---|---|
| **OD-3** | 不 BLOCK Phase 2；强制机制属 Phase 3，选定 **C（test/lint enforcement）**，排除 A / D | **FROZEN** |
| **OD-4** | **方案 A′** —— 对外 `Mapping[int, Callable]` 不变；内部归一定义见 §6.5；**`services=` 不可覆盖授权** | **FROZEN** |
| **OD-5** | **保持 `== SESSION_DEFAULT`**，不得改写 | **FROZEN** |
| **OD-6** | **方案 A（维持现状）** —— `0x27 ∉ SERVICE_SPECS`、`∈ SPECS_EXCLUDED` | **FROZEN** |
| Migration architecture | `resolve_service_spec()` + ordered guards + handler（§9） | **FROZEN** |
| guards / handler | **方案 1 保留 handler 防御检查**（§10.4） | **FROZEN** |
| Migration A | 0x22 原子迁移，A0–A4 五阶段（§11） | **FROZEN** |
| Migration B | 0x2E 原子迁移，B0–B4 五阶段，**须在 A 通过后**（§12） | **FROZEN** |
| Golden corpus | legacy oracle 捕获 + 重放，JSON 存储，长期保留（§13） | **FROZEN** |
| State equality | response + state projection（§14.2） | **FROZEN** |
| Rollback | per-SID `_SPEC_EXECUTION` 白名单；**拒绝** feature flag（§15） | **FROZEN** |
| Exit criteria | 十条，见 §17 | **FROZEN** |
| C2 / C3 边界 | `gateway.py` / `gateway_runtime.py` / `ecu.py` 零改动（§18） | **FROZEN** |

### 20.2 Phase 2 实现阶段的前置条件

本文档通过评审并合并后，Phase 2 实现**方可开始**，且必须按以下顺序：

```
1. 实现 resolve_service_spec() + _SPEC_EXECUTION（_SPEC_EXECUTION 初始为 frozenset()）
2. 全量回归（此时行为必须完全不变 —— 因为白名单为空）
3. A0：捕获 0x22 golden
4. A1：_SPEC_EXECUTION = {0x22}
5. A2/A3：对拍 + 全量回归
6. B0–B3：同法迁移 0x2E
7. 按 §17 逐条验证 Exit Criteria
```

**第 2 步不可跳过**：它证明"引入 spec 执行框架本身"是零行为变更的，
把"框架引入"与"服务迁移"两类变更**分离**，使失败可归因。

### 20.3 本文档不做的事

| 项 | 状态 |
|---|---|
| production code 改动 | **0** |
| 测试改动 | **0** |
| 设计文档 | **1**（本文档） |
| Phase 2 实现 | **NOT STARTED** |
| C2 / C3 | **NOT STARTED** |
| Issue #28 | deferred（独立） |
| C1 设计文档"15 个服务"措辞 | deferred（独立 docs-only follow-up） |

### 20.4 待评审确认项

以下为本文档作出的**判断性**决定，请在评审中明确接受或推翻：

1. **OD-4 选择 A′ 而非字面的 `guards=()`**（§6.8 / §6.9）——
   这是本文档**最重要**的决定，因为它偏离了上游文档 §18.1 的字面表述。
2. **OD-3 选定 C 而非 A**（§5.3）。
3. **§14.3**：全状态比较作为**建议**而非强制判据。
4. **§15.3**：拒绝 feature flag，改用源码常量白名单。

---

## 附录 A —— 与上游冻结文档的关系

| 上游条目 | 本文档的关系 |
|---|---|
| OD-1（per-service ordered guards） | **继承**，不重新讨论；§6.8 说明 A′ 与它的关系 |
| OD-2（guards 唯一执行源） | **继承**；§10.4 的 guard/handler 分工即其落地 |
| §11 的 `0x22` / `0x2E` 冻结表 | **继承**，作为 §11.3 / §12.3 corpus 的锚点来源 |
| §14 Phase 1 / Phase 2 划分 | **继承**；Phase 2 只做 0x22 / 0x2E |
| §15.2 冻结锚点 | **继承**，同时进入 golden corpus 与 29 pins |
| §18.1 OD-3/4/5/6 | **本文档逐项冻结** |
| §18.1 OD-4 的"覆盖 = guards=()" | **本文档 §6.8 显式偏离并说明理由** |
