# OQ C3 —— 0x27 SecurityAccess through Gateway（设计评审）

> 状态：**DESIGN REVIEW = PASS —— 待评审冻结**
> `BLOCKING = 0` ／ `DEFERRED = 3` ／ `RESOLVED = 2`（C3-B1、C3-OQ-12）／ `READY_FOR_REVIEW = YES`
> 日期：2026-09-26
> 分支：`audit/oq-c3-security-access-gateway-design-20260926`
> 基线：`main = 8ed53fa430bcdac81167b4632f60ad98a3b70318`（= C2-A5 merge commit，PR #49）
> C2 状态：**C2_EXIT = PASS**（设计冻结见 `OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md` §16.1）

**本阶段只做：现状盘点 + 架构设计 + Open Questions + Design Freeze。**
**不实现 0x27 through Gateway、不启动 C3-A0、不修改任何 production / tests / Golden / CI。**

## 阅读约定

全文每条结论标注四类之一：

```
FACT            以当前 main 实际源码 / 实测为准的事实陈述
DESIGN DECISION 本设计冻结的决定（评审通过后即为约束）
OPEN QUESTION   尚未决定，需评审或后续阶段裁决
NON-GOAL        明确不做（防止范围蔓延）
```

---

## 1. Scope

```
目标：让 **Gateway 路径**能够执行**已有的** 0x27 SecurityAccess 状态机，
      并保持每 ECU 独立的安全状态与既有的运行时依赖注入语义。

本阶段范围：设计评审（本文档）
不在本阶段：任何代码实现、Golden 捕获、C3-A0 启动
```

**NON-GOAL**

```
NON-GOAL  重写 service_security_access() / keygen / NRC 序列
NON-GOAL  重新实现 SecurityAccess 状态机（必须复用，见 §6）
NON-GOAL  把 0x27 塞进 uds_server.SERVICES 或 SERVICE_SPECS（见 §8 候选 A / §15 C3-OQ-4）
NON-GOAL  给 C2 policy 增加 session / security_level / seed / key / source 任何新维度
NON-GOAL  修改 C2-OD-1 … OD-9 任何一条
NON-GOAL  0x27 的多 Tester / 差异化授权（属 C2-OQ-1 deferred）
NON-GOAL  真实 CAN / 真实 ECU 实验
```

---

## 2. Baseline

| 项 | 值 |
|---|---|
| `main` SHA | `8ed53fa430bcdac81167b4632f60ad98a3b70318` |
| C2 累计 production diff | `+193 / −6`，仅 `ecu/gateway.py`（来源 C2-A1） |
| C3 设计分支 | `audit/oq-c3-security-access-gateway-design-20260926` |
| 本阶段产物 | 仅本文档 |

---

## 3. Current 0x27 Architecture（FACT）

### 3.1 三个模块的职责

```
keygen.py        纯函数：seed 生成 / key 派生 / key 校验。SEED_LENGTH = KEY_LENGTH = 4。
                 无时间、无随机、无 I/O、无模块级可变状态。        【FACT，§3.2】

uds_server.py    协议层：0x27 状态机（service_security_access）+ 依赖绑定工厂
                 （_make_security_access_handler）+ 静态注册表 SERVICES + 授权模型
                 SERVICE_SPECS。                                    【FACT，§3.3】

ecu.py           单 ECU 入口：持有时钟（_now）与安全配置（SECURITY_CONFIG），
                 在**每次请求**时把这两项绑定进 services 表后交给协议层。  【FACT，§3.4】
```

### 3.2 keygen.py（FACT）

```
generate_seed(counter: int = 0, level: int = 0x01) -> bytes   # 4 字节
seed_is_valid(seed: bytes) -> bool
derive_key(seed: bytes, secret: int = SECRET) -> bytes        # 4 字节
verify_key(seed: bytes, key: bytes, secret: int = SECRET) -> bool

SEED_LENGTH = 4     KEY_LENGTH = 4     SECRET = 0x5A17C0DE（教学用公开常数）
```

* **纯函数**：seed = f(counter, level)，key = f(seed, SECRET)，无随机源。
* **推论（重要）**：seed 只取决于 `(counter, level)`，**不含 ECU 身份** ⇒
  两台 state 相同的 ECU 在同一 counter 下产生**相同 seed**。这是既有性质，
  不是缺陷，但会影响 §10 的 functional 分析。

### 3.3 uds_server.py（FACT）

**状态**（`EcuState`，第 157–200 行）—— 0x27 相关 8 个字段：

```
security_level   当前安全等级（LOCKED / LEVEL1）
pending_seed     已下发未消费的 seed
seed_issued_at   seed 下发时刻（monotonic 基准）
seed_consumed    seed 是否已被成功消费（重放检测）
failed_attempts  连续失败计数
lockout_until    锁定解除时刻
seed_counter     seed 序号（每次 RequestSeed 递增）
```

**状态机**（`service_security_access(request, state, *, now, security_config)`）：
**纯函数** —— 不读模块级状态、不调时钟、不读全局配置；两项依赖由调用方显式传入
（`now` 必须是**已求值的 float**，`security_config` 必须是**原对象引用**）。
NRC 顺序（严格）：`13 → 12 → 13 → 37 → 22 → 24 → 35/36 → 67`。

**依赖绑定工厂**（`_make_security_access_handler(now, security_config)`）：
把两项依赖绑成标准形态 `(request, state) -> bytes`，使其可放进 registry。

**静态注册表**：

```
SERVICES       = 14 个 SID（0x10/0x11/0x14/0x19/0x22/0x2E/0x31/0x34/0x35/
                 0x36/0x37/0x38/0x3E/0x85）—— **不含 0x27**
SERVICE_SPECS  授权规格模型；SPECS_EXCLUDED[0x27] 显式记录"为什么 0x27 不在表内"
               模块级守卫：0x27 不在 SPECS_EXCLUDED 中 → 抛 ValueError
_SPEC_EXECUTION = frozenset({0x22, 0x2E})
```

**关键解析语义**（`resolve_service_spec(sid, services)`，FACT）：

```
services is None          → SERVICE_SPECS.get(sid)         # 静态层
services 非空：
    sid 不在 SERVICE_SPECS → ServiceSpec(guards=())        # ← 0x27 走这里
    sid 在  SERVICE_SPECS → replace(base, handler=override)# handler 可换，guards 保留
```

⇒ **0x27 的注入通道今天就已经存在且可用**；C3 不需要改 `resolve_service_spec`。
（`handle_uds_request(payload, state, services={0x27: bound})` 即刻可用 —— 实测见 §7.3。）

### 3.4 ecu.py（FACT）

```
SECURITY_CONFIG = {"max_attempts": 3, "lockout_seconds": _env_int(...), "seed_expiry_seconds": 5}
_now()                  → time.monotonic()；抽成函数**专为** mock.patch("ecu._now")
_security_services()    → {0x27: _make_security_access_handler(now=_now(), security_config=SECURITY_CONFIG)}
handle_uds_request(req) → handle_uds_request(req, STATE, services={**SERVICES, **_security_services()})
reset_all_security_state()  → 测试专用
```

注入约束（源码注释明确标注"请勿优化"）：

* `_now()` 必须**每次调用现场求值**，且必须在 **`ecu` 命名空间内按名字解析**
  —— 测试通过 `mock.patch.object(ecu, "_now", ...)` 注入时钟；
* `SECURITY_CONFIG` 必须**原对象引用**，不得 copy/重建（配置改动即时可见）。

**既有测试耦合（FACT）**：

```
mock.patch.object(ecu_module, "_now", ...)   出现 3 处（test_ecu_unit.py）
SECURITY_CONFIG                              在 test_ecu_unit.py 中被引用 8 次
```

### 3.5 既有 0x27 测试资产（FACT）

| 文件 | 用例数 | 0x27 相关性 |
|---|---|---|
| `test_ecu_unit.py` | 116 | 0x27 主体：8 个 `TestSecurityAccess*` / `TestSecurityStateTransitions` / `TestProtectedDidPermission` / `TestSessionTiming` 类 |
| `test_e2e_vcan.py` | 69 | vcan0 + `udsoncan` 客户端；`TestSecurityAccessHappyPath` / `Failures` / `Errors` / `TestSecurityStateInteraction` / `LockoutTrigger` / `Lockout` |
| `test_keygen_unit.py` | 34 | keygen 纯函数性 + 派生/校验 |
| `test_c1_contract_pins.py` | 29 | **冻结边界**（见下） |
| `test_services_extra_unit.py` | 17 | — |
| `test_write_did_vcan.py` | 8 | 解锁后写 DID |

**三条与本设计直接相关的冻结断言（FACT，必须逐条对待）**：

| 位置 | 断言 | 对 C3 的含义 |
|---|---|---|
| `test_c1_contract_pins.py::TestFfSecurityAccessInjectionBoundary` | `services=None` 时 `27 01` → `7F 27 11` | **C3 不改变**：C3 只注入，不改静态层 ✓ |
| `test_c1_contract_pins.py::TestN7StaticRegistryKeySet` | `SERVICES` 的 key set **恰好 14 个**，且 `0x27 not in SERVICES` | **直接否决候选 A**（见 §8） |
| `test_gateway_runtime_vcan.py::test_security_access_is_not_reachable_through_the_gateway_layer` | 经 gateway（vcan）`27 01` → `7F 27 11` | **C2 历史边界契约；C3 有意改变它** → **C3-B1 = RESOLVED**（§16.2 / C3-OD-15） |

第三条的 docstring 原文：

> "本用例把它**显式固化**，避免被误读成漏接。**若后续 OQ 决定让网关透传 0x27
> （需同时解决"每 ECU 独立注入"与"不重复定义注入语义"），本用例应随之更新。**"

⇒ 该 pin 由既有测试**主动预告**了 C3，并指明了它的两个技术难点
（每 ECU 独立注入 / 不重复定义注入语义）—— 正是本设计 §9 要回答的。

---

## 4. Current Gateway Architecture（FACT）

```
VirtualEcu
    name / request_id / response_id / state / handler
    __post_init__：handler is None → _default_handler_for(state)
    process(payload) → handler(payload)

_default_handler_for(state)
    def handle(payload):
        return uds_server.handle_uds_request(payload, state)     # ← **不带 services=**
    ⇒ 走静态 SERVICE_SPECS ⇒ **0x27 不可达**（返回 7F 27 11）

DiagnosticGateway(ecus, functional_address=0x7DF, policy=None)
    policy 默认 = DEFAULT_POLICY（allow-all，C2-OD-2）
    _by_request: request_id → ECU（构造期建立）

_route_core(can_id, payload, *, collect_trace)
    functional（先判）：for ecu in self.ecus: if _allow(...): ecu.process(payload); return [], trace
    physical          ：target = _by_request.get(can_id)
                        if not _allow(...): return [], trace
                        response = target.process(payload)

route()            = _route_core(collect_trace=False)
route_with_trace() = _route_core(collect_trace=True)
```

**gateway.py 中不存在任何把运行时依赖注入 ECU 的现成 seam（FACT）**：
全文件无 `services` / `_now` / `SECURITY_CONFIG` / `keygen` 引用。

```
gateway_runtime.py
    GatewayRuntime(gateway, transport, *, wait_timeout, log)
    serve_once() → transport.receive() → handle_request() → **self.gateway.route()**
    build_runtime(interface=…) → build_default_gateway() + IsotpTransport() + GatewayRuntime()
    全文件**不感知 0x27**（FACT：无 0x27 / security / services / _now 引用）
```

---

## 5. C1 / C2 / C3 Boundary

| 层 | 授权对象 | 输入 | 拒绝语义 | 归属 |
|---|---|---|---|---|
| **C1** | ECU **内部**服务授权 | `EcuState` 内部字段（session / security_level）+ DID 级权限 | 各服务自己的 NRC（如 `7F 2E 33`） | 已冻结，C3 不碰 |
| **C2** | Gateway **路由**授权 | `(target ECU, addressing_mode, SID)` | **静默丢弃**（无帧、目标不可见） | 已冻结（OD-1…OD-9） |
| **C3** | 0x27 **经 Gateway 可达性** | 复用 C1 的 0x27 状态机 | 0x27 自己的 NRC（35/36/37/24/22/13/12） | **本设计** |

**DESIGN DECISION — C3 的位置**：

```
C3 = 让 Gateway 路径**能够**执行既有 0x27 状态机。
C3 不新增授权层；C2 policy 仍在 `process()` 之前决定"是否放行到该 ECU"，
   然后由 ECU 内部的 0x27 状态机决定"key 对不对"。
两层序关系（FACT，已在 C2 冻结）：

    route() → _allow(target, mode, 0x27) → [通过] → target.process("27 01") → 67 01 <seed>
                                        → [拒绝] → 静默丢弃，target 完全不可见
```

**DESIGN DECISION — C3 必须复用而非复制**：

```
MUST  复用 uds_server.service_security_access()  （唯一状态机定义）
MUST  复用 uds_server._make_security_access_handler()（唯一依赖绑定工厂）
MUST  复用 keygen 的 seed/key 派生             （唯一密码学定义）
MUST  复用 EcuState 的 8 个 0x27 字段与 reset 语义（唯一状态定义）
MUST NOT 在 gateway.py / gateway_runtime.py 中出现第二份 0x27 分支逻辑
```

---

## 6. Existing 0x27 State Machine（复用边界，FACT）

C3 的**全部** 0x27 行为必须来自既有实现；下表是 C3 承诺的最小行为契约
（**每条都已在 §7.3 实测确认可由既有代码产生**）：

| 输入（physical 寻址） | 既有状态机输出 | 实测 |
|---|---|---|
| `27 01` | `67 01 <seed:4>` | ✅ |
| `27 01`（已解锁） | `7F 27 22`（conditionsNotCorrect） | 既有 unit 覆盖 |
| `27 01 <多余字节>` | `7F 27 13` | ✅ |
| `27 03`（未支持子功能） | `7F 27 12` | ✅ |
| `27 02 <正确 key>` | `67 02`，`security_level = LEVEL1` | ✅ |
| `27 02 <错误 key>` | `7F 27 35`，`failed_attempts += 1` | ✅ |
| `27 02 <错误 key> 第 3 次` | `7F 27 36`，进入 lockout | ✅ |
| lockout 期间任何 `27` | `7F 27 37` | ✅ |
| `27 02` 无 seed / seed 已消费（重放） | `7F 27 24` | 既有 unit 覆盖 |
| seed 过期（> `seed_expiry_seconds`） | `7F 27 24` | 既有 unit 覆盖 |
| `27 02 <长度不符>` | `7F 27 13` | 既有 unit 覆盖 |
| 切 session（`10 xx`） | `security_level` 归 LOCKED（`failed_attempts`/`lockout_until`/`seed_counter` **保留**） | ✅ |
| ECUReset（`11 xx`） | `_reset_volatile_state` → 同上 + 清 `did_overrides` 等；**保留**惩罚状态 | 既有 unit 覆盖 |
| 解锁后读受保护 DID | 通过（`required_security_level`） | 既有 unit/vcan 覆盖 |

**DESIGN DECISION — Gateway 不重新实现上表任何一行。**

---

## 7. Runtime Dependency Injection Analysis

### 7.1 依赖清单（FACT）

0x27 需要且**仅**需要两项请求时依赖：

```
now              float（已求值快照，不是 callable）
security_config  原对象引用（不得 copy）
```

`EcuState` **不是**"依赖"，而是**每个 ECU 自己的状态**，与依赖正交 ——
这正是 C3 可以"一次绑定依赖、多 ECU 各自持有状态"的结构基础（FACT：`_security_services()`
返回的 handler 不捕获任何 `EcuState`；state 由 `handle_uds_request(…, state)` 参数传入）。

### 7.2 注入点对比（FACT）

| 路径 | 现状 | 备注 |
|---|---|---|
| 直连 ECU（`ecu.handle_uds_request`） | **已注入** `{**SERVICES, 0x27: bound}` | 每次请求现场求值 `_now()` |
| Gateway（`VirtualEcu` 默认 handler） | **未注入** → `7F 27 11` | gateway.py 无 services 引用 |
| Gateway（自定义 `handler=`） | 由注入方自行决定 | C2-A4 已审计该 seam |

### 7.3 注入可行性实测（FACT —— 本设计探针，只读，未修改仓库）

在**当前 main** 上用既有 `VirtualEcu.handler` seam 模拟候选 C，实测结果：

```
FACT 1  静态层（services=None）27 01            → 7f 27 11      ← C1 pin 不变
FACT 2  physical 27 01（注入后）                → [(0x7E8, 67 01 85ebda6b)]  ← 响应 id 正确
FACT 3  physical 只影响目标 ECU                 → A: seed set/counter=1；B/C: 无变化
FACT 4  functional 27 01                        → []（恒空）
        但 A/B/C 的 pending_seed 与 seed_counter **全部被推进**
        且三个 ECU 得到**相同 seed**（keygen 只取决于 (counter, level)，不含 ECU 身份）
FACT 5  解锁严格 per-ECU                        → A security_level=1；B/C=0
FACT 6  NRC 复用既有状态机                      → 错误 key 35 / 无 seed 24 / 超长 13 / 子功能 12
FACT 7  lockout 只锁目标 ECU                    → 3 次错误后 A 得 36、再请求得 37；B 仍 67 01
FACT 8  切 session 重新上锁                     → security_level 1 → 0
FACT 9  **C2 policy 输入未被改变**              → 策略只看到 (0x7E0,"physical",0x27) /
                                                 (0x7E0/0x7E1/0x7E2,"functional",0x27)
```

⇒ 候选 C 在**不改任何 production 文件**的前提下即可达成 C3 的全部行为目标，
说明 C3 是一个**接线（wiring）问题**，不是状态机问题。

**FACT 9 直接回答 C3-OQ-5：C2 policy 的输入仍是三元组，0x27 只是 `sid = 0x27` 的取值。**

---

## 8. Architecture Candidates

### 候选 A —— 把 0x27 放进 `uds_server.SERVICES` / `SERVICE_SPECS`

**REJECTED（被既有冻结断言直接否决，FACT）**

```
1. `test_c1_contract_pins.py::TestN7StaticRegistryKeySet` 精确断言：
       set(SERVICES) == EXPECTED_STATIC_SERVICES 且 len == 14
       0x27 not in SERVICES
   ⇒ 候选 A 会**立即**使该 pin 失败，而 C3 不允许修改测试。

2. 静态表 entry 形态是**纯 callable**，无法承载 per-request 的 now。
   若把 now 变成模块级/全局状态：
       - 破坏 `mock.patch.object(ecu, "_now", ...)` 这一既有测试 seam（3 处）；
       - 使"时间依赖"成为全局可变量，与 `_make_security_access_handler` 的
         既有约束（"now 必须是已求值的 float，不是 callable/时钟"）直接冲突。

3. 与既有分层边界冲突：`SERVICE_SPECS`/`SPECS_EXCLUDED` 的模块级守卫明确要求
   0x27 **必须**出现在 SPECS_EXCLUDED 中（否则抛 ValueError）。
```

### 候选 B —— Gateway 自己实现 0x27

**REJECTED（重复实现，必然漂移）**

```
若在 gateway 侧实现，将出现第二份：
    SecurityAccess 状态机（13/12/13/37/22/24/35/36 顺序）
    keygen 调用与 seed 生命周期
    lockout / failed_attempts / seed_expiry 判定

后果：
    1. 直连 ECU 与 Gateway 行为**可能漂移**（两份实现各自演进）；
    2. 违反 §5 的 MUST NOT；
    3. vcan pin 的 docstring 已点明要避免的问题之一正是"重复定义注入语义"。
```

### 候选 C —— VirtualEcu / 默认 handler 增加动态 services provider ★

**CHOSEN**（见 §9）

```
核心：`_default_handler_for()` 在**每次请求**时向一个 provider 取
      `{**SERVICES, **provider()}`，provider 由**入口层**注入。
      gateway.py 本身不持有时钟/配置，保持"纯协议层"。
```

### 候选 D —— 新建独立的 runtime dependency carrier / service-provider 层

**REJECTED（当前项目规模下是过度抽象）**

```
评估：
    + 表面上有"统一抽象"的吸引力（ecu.py 与 gateway.py 共用一个 carrier）
    − 需要把 `_now` / `SECURITY_CONFIG` 移出新位置，而它们被既有测试强耦合
      （`mock.patch.object(ecu, "_now")` 3 处；`SECURITY_CONFIG` 8 处引用）
      ⇒ 会破坏既有测试契约，违反"不得为了架构漂亮而破坏既有测试契约"
    − 现有 `uds_server._make_security_access_handler` 已经是"依赖 → handler"的绑定器，
      再包一层 carrier 没有新的表达力
    − 每多一层抽象就多一个 C3-A4 需要审计的 override 面

结论：候选 C 用**最小新增面**达成同一目标；D 留作未来 OQ（若出现第二个依赖消费者再评估）。
```

### 对比表

| 维度 | A 静态表 | B Gateway 自实现 | **C provider 注入** | D 独立 carrier 层 |
|---|---|---|---|---|
| 复用既有状态机 | — | ✗ 复制 | **✓** | ✓ |
| 破坏既有冻结 pin | ✗ 直接失败 | — | **无** | 无 |
| per-request `now` | ✗ 需全局化 | ✓ | **✓** | ✓ |
| 保持 ecu `_now` patch seam | ✗ | — | **✓** | ✗ 需迁移 |
| 保持 `SECURITY_CONFIG` 引用语义 | — | — | **✓** | ✗ 可能需迁移 |
| 新增 production 面 | 中 | 大 | **小** | 大 |
| 新增 override 面（C3-A4 需审计） | 中 | 大 | **1 个（provider）** | 2 个 |
| 与既有分层一致 | ✗ | ✗ | **✓** | 部分 |

---

## 9. Chosen Design / Rationale（DESIGN DECISION）

### 9.1 结构

```
uds_server.py   + security_access_services(*, now, security_config) -> dict[int, Callable]
                  ★ 0x27 依赖绑定的**唯一定义**（`_make_security_access_handler` 之上的薄封装）
                  纯函数；不读时钟、不读全局状态。

gateway.py      VirtualEcu 新增可选字段：
                    services_provider: Callable[[], Mapping[int, Callable]] | None = None
                _default_handler_for(state, services_provider=None)
                    provider is None → **保持现状**（`handle_uds_request(payload, state)`）
                    provider 非 None → 每请求
                        handle_uds_request(payload, state,
                                           services={**uds_server.SERVICES, **provider()})
                DiagnosticGateway(…, services_provider=None)
                    将其应用到**未自带** provider 的 ECU（ECU 级优先，保持 override 语义）
                ★ gateway.py **仍不感知时钟与配置**（纯协议层不变）

gateway_runtime.py
                build_runtime(…, services_provider=…) —— 由**入口层**构造并注入
                ★ runtime 仍只做搬运 + 生命周期；不引入第二套授权（A4 F2 保持成立）

ecu.py          _security_services() 改为委托 uds_server.security_access_services(…)
                ★ `_now()` / `SECURITY_CONFIG` **仍在 ecu 命名空间解析** ⇒ patch seam 不变
```

### 9.2 「不重复定义注入语义」如何被满足

```
FACT：vcan pin 的 docstring 要求同时解决
      ① 每 ECU 独立注入   ② 不重复定义注入语义

本设计：
    ① 每个 ECU 持有自己的 EcuState；provider 只提供**依赖**，不提供状态
       （FACT：handler 不捕获 EcuState）⇒ 天然每 ECU 独立
    ② 「0x27 依赖 → services 表」这一步只在 uds_server.security_access_services() 定义一次；
       直连 ECU 与 Gateway 两条路径都调用它 ⇒ 注入语义单一定义

    唯一仍然"两处"的是**依赖的取值来源**（ecu._now / ecu.SECURITY_CONFIG）——
    这是刻意的：见 C3-OQ-2 / C3-OQ-3，C3 不移动它们。
```

### 9.3 关键约束（C3-A1 必须遵守）

```
CONSTRAINT-1  services_provider 默认 None ⇒ 既有调用方行为**逐字节不变**
              （A1/A2/A4 的 gateway 构造路径都不传 provider ⇒ 基线不受影响）
CONSTRAINT-2  provider 必须**每请求**调用（不得缓存结果或缓存 callable）
              —— 否则 now 会陈旧
CONSTRAINT-3  provider **不得**返回值以外的状态；不得捕获/修改 EcuState
CONSTRAINT-4  provider 不得引入新的 policy 维度；C2 policy 输入仍是 (target, mode, SID)
CONSTRAINT-5  `VirtualEcu.process()` **API 不变**（否则 C2-A4 seam-1 证据失效）
CONSTRAINT-6  gateway.py / gateway_runtime.py 中不得出现
              `policy` / `_allow` / `_by_request` / `PolicyDecision` 字样
              （否则 C2-A4 的 F2 源码级不变量失败）
CONSTRAINT-7  不得新增 `.process(` 调用点（C2-A4 F1 要求恰好 2 处）
CONSTRAINT-8  `SERVICES` / `SERVICE_SPECS` / `SPECS_EXCLUDED` / `_SPEC_EXECUTION` 均不改
CONSTRAINT-9  `build_default_gateway()` 默认**不**接线 provider（见 9.4）
```

### 9.4 默认是否接线？（DESIGN DECISION）

```
DECISION：`build_default_gateway()` 默认不注入 provider；由入口层（`build_runtime`）
          或测试显式传入。

理由：
    a) gateway.py 是**纯协议层**，不应拥有"当前时间"与"安全配置"的所有权；
    b) 若默认接线，离线 `build_default_gateway()` 的行为会改变，
       使 C2-A1/A2/A4 的既有基线在语义上漂移（虽然 20 条 golden case 不含 0x27，
       但"默认网关能力变了"本身是需要独立评审的事件）；
    c) 与 `ecu.py` 的分层一致：依赖绑定发生在入口层。

代价：离线 `DiagnosticGateway` 默认仍不提供 0x27 —— 这是**机制 / 接线分离**的
      有意结果，由 C3-A1 的测试显式接线后再验证。
```

---

## 10. Functional Addressing Analysis（C3-OQ-1）

### 10.1 实测事实（§7.3 FACT 4）

```
route(0x7DF, "27 01") → []          （C2-OD-4/§10.2 冻结：功能寻址恒不收集响应）
但 ECU_A / ECU_B / ECU_C 的：
    pending_seed  = 85ebda6b  ← **三个 ECU 得到相同 seed**
    seed_counter  = 1
全部被推进 ⇒ Tester 拿不到任何 seed，却改变了三个 ECU 的 seed 状态
```

补充事实：由于 `generate_seed` 只取决于 `(counter, level)`、**不含 ECU 身份**，
"每个 ECU 独立生成 seed"在**状态**意义上是独立的，在**取值**意义上是相同的。

### 10.2 分析

```
与既有冻结模型的关系：
    C2-OD-4 冻结"功能寻址逐 ECU 判定"；A2 golden 的 F4 用例（functional 0x2E 写入）
    **本身就会改变全部三个 ECU 的 did_overrides** —— 即"广播扇出导致逐 ECU 状态变化"
    是 C2 **已冻结并已归档**的行为，不是 C3 引入的新类别。

差别：
    对 0x2E，状态变化正是 Tester 的意图；
    对 0x27 01，状态变化**没有可用结果**（无响应 ⇒ 拿不到 seed），属"空转副作用"。

若要"抑制 functional 0x27"，只有两条路，均被本设计排除：
    (i)  在 C2 policy 中加 0x27 特例  → 违反"不得增加 0x27 特殊 policy"（§1 NON-GOAL）
    (ii) 给 `VirtualEcu.process()` 增加寻址参数 → 违反 CONSTRAINT-5，
         并使 C2-A4 的 seam-1 证据失效（需整体重做 A4）
```

### 10.3 DESIGN DECISION（C3-OQ-1）

```
DECISION：**ALLOW —— C3 不为 functional 0x27 引入任何专门机制。**
          功能寻址下的 0x27 完全沿用 C2-OD-4 的既有语义（逐 ECU 求值、恒不收集响应）。

理由：与 C2 已冻结模型一致；两条抑制路径都违反更高的既有约束。

记录为 KNOWN LIMITATION（C3 明示、不掩盖）：
    - functional `27 01` 不返回 seed（沿用 C2 响应语义）；
    - 每个 ECU 的 pending_seed / seed_counter 会被推进（空转副作用）；
    - 因 keygen 不含 ECU 身份，各 ECU 该次 seed 取值相同；
    - 不影响任何 ECU 的 security_level，也不消耗 failed_attempts。

后续若需要"functional 0x27 静默丢弃"，必须作为**独立机制**另行设计
（见 §15 C3-OQ-10，DEFERRED），不得搭车进 C3。
```

---

## 11. Multi-ECU Isolation（C3-OQ-7）

### 11.1 设计级不变量

```
INV-1  安全状态严格 per ECU：
           A 解锁 ⇏ B 解锁 ⇏ C 解锁
INV-2  惩罚状态严格 per ECU：
           A 的 failed_attempts / lockout_until 不影响 B、C
INV-3  seed 生命周期严格 per ECU：
           A 的 pending_seed / seed_issued_at / seed_consumed / seed_counter 独立
INV-4  依赖是**共享**的（同一个 now 快照语义 + 同一个 SECURITY_CONFIG 对象），
       状态是**独立**的 —— 二者不得混淆
INV-5  每个 ECU 的 handler 必须绑定到**它自己的** EcuState
       （不得出现两个 ECU 共享一个 state 对象）
```

### 11.2 实测（§7.3）

```
FACT 3  physical 只影响目标 ECU（B/C 无变化）           → INV-1/3 ✓
FACT 5  A 解锁后 B/C 仍 LOCKED                         → INV-1 ✓
FACT 7  A 进入 lockout 后 B 仍能正常 27 01              → INV-2 ✓
FACT 8  切 session 只重锁目标 ECU                       → INV-1 ✓
```

### 11.3 DESIGN DECISION

```
DECISION：C3 保持 INV-1…5；不引入 per-ECU 配置（SECURITY_CONFIG 仍是单一共享对象，
          与今天直连 ECU 的语义一致）。
          若未来需要 per-ECU 安全参数，属**新维度**，须独立设计（DEFERRED）。
```

---

## 12. Override / Bypass Implications（C3-OQ-8）

### 12.1 C2-A4 已证明什么（FACT，C2 阶段结论）

```
4 个 seam（handler / topology / transport / build_runtime）在 **C2 policy** 下
均不能绕过 deny（四层证据：trace / process / state / wire）。
process() 全包仅 2 个调用点且均受 _allow 守卫（AST 级不变量）。
gateway_runtime 无第二套授权路径。
```

### 12.2 C3 新增的 override 面与缺口

```
NEW SEAM  services_provider（本设计新增）
    问题：provider 能否绕过 C2 policy？
    预期：**不能** —— policy 在 `_route_core` 内、`process()` **之前**求值，
          provider 只在 `process()` → handler 内部生效，层级更靠后。
    但这必须**被证明**（沿用 A4 的四层证据模型），不能靠推理默认成立。

既有 seam 在 C3 后是否仍成立？
    handler 注入      ：仍成立（C3 不使 handler 前移）—— 但需复验"自定义 handler
                        + provider 同时存在"时的优先关系
    custom topology   ：仍成立（provider 是 per-ECU 可覆盖的）
    fake transport    ：仍成立（C3 不改 runtime 的搬运语义）
    build_runtime     ：**变化** —— build_runtime 将注入 provider，
                        故 C3-A4 必须重新审计该 seam（这是 C2-A4 未覆盖的形态）
```

### 12.3 DESIGN DECISION

```
DECISION：C3-A4 必须重跑 A4 的四层证据矩阵，并**新增** provider seam 的行：
    - provider 存在时，DENY 仍然 process()==0 / state 不变 / wire==[] / decision False
    - provider 不能改变 policy 的求值次数与顺序
    - provider 返回的表**不能**覆盖 0x27 以外的既有服务语义（`{**SERVICES, **provider()}`
      的顺序保证 SERVICES 可被显式覆盖 —— 这是既有 override 语义，需显式固化）
```

---

## 13. Direct ECU ↔ Gateway Equivalence Strategy（C3-OQ-9）

### 13.1 为什么需要一个对拍

```
C3 之后，0x27 有**两条**执行路径：

    直连 ECU  ：ecu.handle_uds_request(req)  → STATE（单 ECU）
    Gateway   ：gw.route(0x7E0, req) → _allow → VirtualEcu.process → handler → 同一状态机

两条路径的**协议层是同一个**（都调用 uds_server.handle_uds_request + 同一个状态机），
但**接线不同**（依赖来源、state 归属、响应封装层级）。
⇒ 需要一个**真实执行的双路径差分**来证明"接线没有改变行为"。
```

### 13.2 对拍模型（DESIGN DECISION）

```
ORACLE  = 直连 ECU 路径（`ecu.py` 入口，**C3 前就已存在 0x27**）
SUBJECT = Gateway 路径（C3-A1 之后接线 provider）

    same input (request bytes + 相同初始 EcuState + 相同依赖快照)
        ├── Direct ECU  → response(D) + security_state(D)
        └── Gateway     → response(G) + security_state(G) + policy decision
                                    ↓
                       差分比较器（沿用 C2-A2 的四维：response / state /
                       security state / policy decision）

比较维度（缺一不可）：
    response bytes        逐字节（含 NRC）
    EcuState 三字段投影    session / security_level / did_overrides
    0x27 专用状态投影      pending_seed(是否存在) / seed_consumed / failed_attempts /
                          lockout_until(相对) / seed_counter
    policy decision       (target, addressing_mode, sid, allowed)
```

**与 C2-A2 的区别（DESIGN DECISION）**：

```
C2-A2 的对拍是 "冻结的 legacy 实现 ↔ 当前实现"（时间轴上的两个版本）。
C3-A2 的对拍是 "两条**并存**的入口路径"（同一版本的两种接线）。
两者共用四维证据模型，但 oracle 的来源不同 —— C3 **不需要** vendor 冻结实现，
因为直连 ECU 路径本身就是活着的、未修改的 oracle。
```

**注意（FACT）**：`seed` 的**具体取值**取决于 `seed_counter`，两条路径若初始 counter 相同
则取值相同；因此对拍应比较"seed 存在性与长度 + 用同一 seed 走完整个序列的**后续响应**"，
而不是把 seed 字节本身当作跨路径常量（否则会把 keygen 的确定性误当成等价性证据）。

---

## 14. Golden / Test Strategy

### 14.1 六段式迁移（沿用 C2，**本阶段不执行**）

| 阶段 | 证明什么（单一职责） | 明确**不**证明 |
|---|---|---|
| **C3-Design** | 本文件：现状 / 边界 / 候选 / 冻结 | 任何实现正确性 |
| **C3-A0** | 冻结 **baseline 证据**：直连 ECU 的 0x27 行为快照（C3 改动**之前**） | 不证明 Gateway 可用 |
| **C3-A1** | 实现"Gateway 可执行 0x27"的**机制 + 接线**，且**零行为漂移**（直连路径不变、既有 golden 不变） | 不证明两路径等价 |
| **C3-A2** | **Direct ↔ Gateway 差分**（§13） | 不证明全系统未回归 |
| **C3-A3** | **Full regression**（UDS / CAN / QA / healthcheck / repro） | 不证明 override 不可绕过 |
| **C3-A4** | **Override / bypass 不变量**（含新 provider seam） | 不做退出判定 |
| **C3-A5** | **Exit review**（冻结项逐项核验） | —— |

**DESIGN DECISION**：禁止一个测试承担多个证据职责（沿用 C2 的分工纪律）。

### 14.2 C3-A0 应冻结的类别（设计建议，**本阶段不创建 Golden**）

| # | 类别 | 备注 |
|---|---|---|
| 1 | `27 01` RequestSeed（物理） | 含 seed 长度/非零断言 |
| 2 | `27 02` 无 seed | `24` |
| 3 | `27 02` key 长度错误 | `13` |
| 4 | `27 02` 错误 key | `35` + `failed_attempts` |
| 5 | 第 3 次失败 → lockout | `36`，随后 `37` |
| 6 | seed 过期 | `24`（需时钟注入） |
| 7 | seed 重放（成功消费后再发 key） | `24` |
| 8 | 已解锁时再 `27 01` | `22` |
| 9 | 切 session 重锁 | `security_level → LOCKED`，惩罚状态保留 |
| 10 | ECUReset 重锁 | 同上 + `did_overrides` 清除 |
| 11 | 解锁后读受保护 DID | `62 F1 87 …` |
| 12 | 多 ECU 隔离 | A 解锁/lockout 不影响 B/C |
| 13 | Gateway 物理寻址 | 响应 id 正确 |
| 14 | Gateway 功能寻址 | **决策见 §10.3**：记录"恒 [] + 逐 ECU 状态推进" |
| 15 | 未知物理 target | `[]`，不进入策略（C2-OD-8） |
| 16 | **与 C2 deny 的交互** | policy DENY 时 `process()==0`、无 `67/7F`、state 不变 |

**证据维度（每一类都要）**：

```
response bytes       逐字节
state projection     逐 ECU 三字段
security state       0x27 专用字段投影
policy decision      (target, addressing_mode, sid, allowed)
```

**DESIGN DECISION**：C3-A0 的 Golden 应同时覆盖**直连 ECU** 与 **Gateway** 两侧的输入集，
前者作为 A2 的 oracle、后者作为 A2 的 subject。

---

## 15. Open Questions

### C3-OQ-1 —— functional addressing

**DECIDED**（§10.3）：ALLOW，无专门机制；记录 KNOWN LIMITATION。
残余项见 C3-OQ-10（DEFERRED）。

### C3-OQ-2 —— runtime dependency 的单一 source of truth

**DECIDED**：

```
clock        ：`ecu._now()` —— 唯一时钟源。Gateway 侧由入口层的 provider
               **每请求**在 `ecu` 命名空间解析（保持既有 patch seam）。
SECURITY_CONFIG：`ecu.SECURITY_CONFIG` 这个**同一个 dict 对象**，按引用传入（不 copy）。
per-ECU config ：**不引入**（保持单一共享配置，与直连 ECU 语义一致）。
测试注入      ：不变 —— `mock.patch.object(ecu, "_now", …)` 与直接改写
               `ecu.SECURITY_CONFIG` 继续对两条路径同时生效
               （因为它们指向同一对象 / 同一名字解析）。
```

### C3-OQ-3 —— 是否移动 / 提取 SECURITY_CONFIG

**DECIDED：不移动。**

```
理由（FACT）：`SECURITY_CONFIG` 在 test_ecu_unit.py 中被引用 8 次；
`_now` 被 `mock.patch.object(ecu, "_now")` patch 3 次。
移动它们会破坏既有测试契约 —— 违反本节约束。
Gateway 侧通过入口层引用同一对象即可，无需搬迁。
```

### C3-OQ-4 —— 0x27 是否进入 SERVICE_SPECS

**DECIDED：不进入。**

```
理由：0x27 的"授权"本身就是 SecurityAccess 状态机（seed/key 交换），
      它不是"被 guard 门控的普通服务"，而是**授权机制的提供者**。
      `SPECS_EXCLUDED[0x27]` + 模块级守卫已冻结该判断；`_SPEC_EXECUTION`
      亦不含 0x27。C3 保持"注入型服务"定位。
```

### C3-OQ-5 —— C2 policy 输入是否保持完全不变

**DECIDED：完全不变。**

```
FACT 9 实测：策略只观察到 (target, addressing_mode, sid)，
             0x27 仅作为 `sid = 0x27` 的取值出现。
DESIGN DECISION：C3 **不**为 policy 增加 session / security_level / seed / key /
                 source 任何维度。
```

### C3-OQ-6 —— reset / session transition 是否完全复用既有 EcuState 语义

**DECIDED：完全复用，不新增任何 reset 分支。**

```
FACT 8：切 session → security_level 归 LOCKED。
FACT（源码）：ECUReset → `_reset_volatile_state` → 同样的 relock，
              且**保留** failed_attempts / lockout_until / seed_counter（防惩罚刷新）。
DESIGN DECISION：C3 不定义任何新的 reset 语义；上表行为由既有代码产生。
```

### C3-OQ-7 —— 多 ECU security state 是否严格 per ECU

**DECIDED：是。** 不变量 INV-1…5（§11），实测 FACT 3/5/7/8。

### C3-OQ-8 —— 是否需要新的 override audit

**DECIDED：需要。** C2-A4 覆盖 4 个 seam；C3 新增 provider seam 且改变了
`build_runtime` 的接线形态 ⇒ C3-A4 必须重跑矩阵并新增 provider 行（§12.3）。

### C3-OQ-9 —— Direct ECU ↔ Gateway 等价性证明

**DECIDED**：采用 §13 的双路径差分（oracle = 直连 ECU）。
**不**采用"再 vendor 一份冻结实现"（不需要：直连路径本身未被 C3 修改）。

### C3-OQ-10 —— functional 0x27 的空转副作用是否需要专门机制

**DEFERRED**（不阻塞 C3 实现）

```
问题：§10.3 记录的空转副作用（无响应 + 逐 ECU seed 状态推进）。
为何暂缓：抑制它需要新机制（policy 特例 或 process() 寻址参数），
          两者都触碰既有冻结约束；且不影响安全等级、不消耗惩罚计数，
          实际影响面小。
归属：未来架构（若引入"服务级寻址约束"机制时一并考虑）。
```

### C3-OQ-11 —— 是否需要 per-ECU SECURITY_CONFIG

**DEFERRED**：当前语义是单一共享配置；per-ECU 配置是**新维度**，
与 C2-OQ-3（策略载体）同属"配置模型"议题，不阻塞 C3。

### C3-OQ-12 —— provider 的返回值是否允许覆盖 0x27 以外的服务

**RESOLVED —— 允许，并与既有 `services=` override contract 同构。**

```
FACT：`{**uds_server.SERVICES, **provider()}` —— provider 的键**会覆盖**静态表同名 SID。

DECISION：允许。语义与既有 `resolve_service_spec()` 的覆盖契约**同构**：

    同名 SID       由 provider **覆盖**
    未同名 SID     保留静态 service
    对已有 ServiceSpec 的 SID：**handler 可以被替换，但 C1 guards 语义不得被绕过**
                    （既有契约原文："services= 覆盖的是**实现**，**不是授权**"）
```

**冻结的 10 条限制（C3-A1 必须遵守，C3-A4 必须显式审计）**：

```
 1  provider **仅**负责 runtime dependency binding
 2  不得改变 C1 ServiceSpec / guard 语义
 3  不得改变 C2 policy 维度
 4  C2 policy 仍严格为 (target, addressing_mode, SID)
 5  0x27 状态机仍**只有** uds_server.service_security_access()
 6  keygen 仍**只有** keygen.generate_seed / derive_key / verify_key
 7  now 必须**每次请求重新求值**
 8  不得缓存旧 now
 9  SECURITY_CONFIG 继续保持现有**对象引用**语义
10  C3-A4 必须显式审计：provider override / same-SID override
    **不得**造成第二套 0x27 状态机，
    **不得**绕过 C2 policy 或 C1 guard
```

### C3-OQ-13 —— 是否还需要独立的 runtime dependency carrier 层（候选 D）

**DEFERRED**（不阻塞 C3 实现）

```
问题：是否把"运行时依赖"抽象成独立 carrier 层，供 ecu.py 与 gateway.py 共用？

为何暂缓（§8 候选 D 的评估结论）：
    · 现有 `uds_server._make_security_access_handler` + 本设计的
      `security_access_services()` 已构成"依赖 → handler"的绑定器，再包一层无新增表达力；
    · 需要迁移 `_now` / `SECURITY_CONFIG`，而它们被既有测试强耦合
      （patch 3 处 / 引用 8 次）⇒ 会破坏既有测试契约；
    · 每多一层抽象就多一个 C3-A4 需要审计的 override 面。

归属：未来架构 —— **若出现第二个依赖消费者**（例如 0x29/0x28 等同样需要
      请求时依赖的服务）再重新评估。
```

---

## 16. Blocking vs Deferred

### 16.1 BLOCKING（0 项）

```
当前无 BLOCKING 项。
```

### 16.2 RESOLVED（2 项）

| # | 项 | 冻结表述 |
|---|---|---|
| **C3-B1** | vcan pin `test_security_access_is_not_reachable_through_the_gateway_layer` | **RESOLVED**（见下） |
| **C3-OQ-12** | provider 覆盖 0x27 以外服务的语义 | **RESOLVED**（§15，含 10 条限制） |

#### C3-B1 = RESOLVED

```
C2 阶段该测试断言"Gateway 当前不提供 0x27"，
属于 **C2 历史边界契约**。

进入 C3 后，该行为将被**有意改变**。

C3-A1 implementation 必须：
    · 更新该旧 pin
    · 改为 Gateway 0x27 **正常可达**契约
    · 增加必要的 0x27 Gateway 覆盖
    · 本 Design 阶段**不修改测试文件**

因此：C3-B1 = RESOLVED
      不是 implementation 缺失造成的设计 blocker。
```

> 该 pin 的 docstring 原文已预告此更新：
> "若后续 OQ 决定让网关透传 0x27（需同时解决"每 ECU 独立注入"与"不重复定义注入语义"），
> 本用例应随之更新。"
> 本设计正是"后续 OQ 的决定"，且 §9.2 已给出两个技术难点的解法。
>
> **归属澄清（避免误读）**：C3-B1 从来不是"设计缺口"，而是**跨阶段契约交接**
> —— 该 pin 冻结的是 C2 时期的可达性事实，C3 有意改变它，
> 因此更新动作属于 **C3-A1 的实现范围**，由本节显式授权并限定范围。

### 16.3 DEFERRED（3 项，不阻塞）

```
C3-OQ-10  functional 0x27 空转副作用的专门抑制机制
C3-OQ-11  per-ECU SECURITY_CONFIG
C3-OQ-13  provider 之外是否还需要独立的 runtime dependency carrier 层
          （候选 D；当前判定为过度抽象，若出现第二个依赖消费者再评估）
```

### 16.3 NON-GOAL（回顾）

```
见 §1 NON-GOAL 列表（不改 C2 语义 / 不重写状态机 / 不塞静态表 / 不碰 0x27 以外服务
/ 不做多 Tester / 不碰真实 CAN）
```

---

## 17. Design Freeze

以下各项在评审通过后即为 **FROZEN**（C3-A1…A5 不得违反）：

| ID | 决定 | 状态 |
|---|---|---|
| **C3-OD-1** | 0x27 经 Gateway 的执行**必须复用** `uds_server.service_security_access()`；不得在 gateway / runtime 出现第二份状态机 | 待冻结 |
| **C3-OD-2** | 注入机制 = **per-request services provider**（候选 C）；`_default_handler_for` 在 provider 为 None 时行为**逐字节不变** | 待冻结 |
| **C3-OD-3** | 依赖的单一来源：clock = `ecu._now()`（每请求现场求值）、config = `ecu.SECURITY_CONFIG`（**原对象引用**）；**不移动**这两者 | 待冻结 |
| **C3-OD-4** | `uds_server.security_access_services(now, security_config)` 作为**唯一**的 0x27 依赖绑定定义；`ecu._security_services()` 委托它 | 待冻结 |
| **C3-OD-5** | C2 policy 输入**保持不变**：`(target, addressing_mode, sid)`；C3 **不**新增 policy 维度 | 待冻结 |
| **C3-OD-6** | `VirtualEcu.process()` **API 不变**；`SERVICES` / `SERVICE_SPECS` / `SPECS_EXCLUDED` / `_SPEC_EXECUTION` **均不改** | 待冻结 |
| **C3-OD-7** | `build_default_gateway()` **默认不**接线 provider；由入口层（`build_runtime`）或测试显式注入 | 待冻结 |
| **C3-OD-8** | functional 0x27 = **ALLOW**（无专门机制），并按 §10.3 记录 KNOWN LIMITATION | 待冻结 |
| **C3-OD-9** | 多 ECU 不变量 INV-1…5（安全/惩罚/seed 生命周期严格 per ECU；依赖共享、状态独立） | 待冻结 |
| **C3-OD-10** | C3-A4 必须重跑 A4 四层证据矩阵并新增 provider seam 行 | 待冻结 |
| **C3-OD-11** | C3-A2 采用 **Direct ECU ↔ Gateway 双路径差分**（oracle = 直连 ECU 路径） | 待冻结 |
| **C3-OD-12** | 迁移沿用六段式；每个阶段只承担单一证据职责 | 待冻结 |
| **C3-OD-13** | C2-OD-1 … C2-OD-9 **一条也不修改**；若 C3 必须改 C2 冻结内容，须作为 BLOCKING OQ 提出而非直接实现 | 待冻结 |
| **C3-OD-14** | provider override 语义：`{**SERVICES, **provider()}` 允许同名覆盖，与既有 `services=` 契约同构；但 provider **仅**做 runtime dependency binding，**不得**改变 C1 guard 语义 / C2 policy 维度，**不得**产生第二套 0x27 状态机（§15 C3-OQ-12 的 10 条限制即为本条内容） | 待冻结 |
| **C3-OD-15** | C3-B1 的处置：该 vcan pin 属 **C2 历史边界契约**，C3 **有意改变**该行为；更新动作属 **C3-A1 实现范围**，须改为"Gateway 0x27 正常可达"契约并补充必要覆盖；**Design 阶段不修改该测试文件** | 待冻结 |

---

## 18. Next Stage

```
C3-Design（本文档）            ← 待评审冻结
      ↓
C3-A0  baseline evidence       （冻结直连 ECU 的 0x27 行为；**不创建 Golden 于本阶段**）
      ↓
C3-A1  implementation          （机制 + 接线；含按 C3-OD-15 更新 C3-B1 的旧 pin）
      ↓
C3-A2  Direct ECU ↔ Gateway differential
      ↓
C3-A3  Full regression
      ↓
C3-A4  Override / bypass audit（含 provider seam）
      ↓
C3-A5  Exit Review
```

**本阶段到本文档为止。不启动 C3-A0，不创建 Golden，不实现任何代码。**

---

## 附表：分类汇总

| 分类 | 数量 | 明细 |
|---|---|---|
| **FACT** | §3 / §4 / §6 的源码事实 + §7.3 的 **9 组实测** + §3.5 的测试资产与 **3 条冻结断言** | 均以 `main` 实际源码或只读实测为准 |
| **DESIGN DECISION** | **15 条** | C3-OD-1 … C3-OD-15（§17） |
| **OPEN QUESTION** | **BLOCKING = 0**；**RESOLVED = 2**（C3-B1、C3-OQ-12）；**DEFERRED = 3**（C3-OQ-10/11/13） | §16 |
| **NON-GOAL** | **7 条** | §1 |

```
BLOCKING       = 0
DEFERRED       = 3
RESOLVED       = 2      （C3-B1、C3-OQ-12）
DECIDED（OQ）   = 8      （C3-OQ-1/2/3/4/5/6/7/8/9 —— OQ-1 为 ALLOW + KNOWN LIMITATION）
DESIGN DECISION = 15    （C3-OD-1 … OD-15）

C3-B1     = RESOLVED
C3-OQ-12  = RESOLVED
当前 BLOCKING = 0
READY_FOR_REVIEW = YES
```
