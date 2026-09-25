# OQ C2-A4 —— Override / Bypass 不变量审计（证据报告）

> 状态：**C2-A4 = PASS**
> 日期：2026-09-26（文件名沿用阶段编号 2026-09-25）
> 分支：`audit/oq-c2-a4-override-bypass-20260925`
> 基线：`main = b24b81694feb4e5952bdd658469e3ab614bfaa9e`（PR #47 merge = A3 merge commit）
> 设计出处：`docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md`
> §9.2.11（bypass 审计方式）/ §12（Override Paths）/ C2-OD-3 / OD-4 / OD-5 / OD-6 / OD-9

**本阶段只回答一个问题**：

> **已 DENY 的 Gateway 请求，能否通过既有 override / extension seam
> 绕过 policy 并最终进入 target ECU？**

**不是**"policy API 能不能工作"，**不是**"A1/A2 是否测过 deny"。

---

## 1. 核心证据模型（四层，缺一不可）

```
                 policy evaluate
                       │
                       ▼
              PolicyDecision.allowed == False
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
      trace         process          wire
   allowed=False   call_count=0    results == []
                       │
                       ▼
                 state unchanged
```

**为什么必须四层**：以下四种情形都会得到 `results == []`

```
policy deny │ unknown physical target │ functional broadcast │ suppressed response
```

只看 wire **无法区分**它们。A4 因此对每个正式 deny case 同时断言
**trace / process / state / wire**。

其中 ``process`` 一层**刻意 spy 在 ``target.process`` 本身，而不是 handler** ——
证明"请求根本没有到达目标 ECU"，而非"到达了但 handler 恰好没做事"（§9.2.11 明文要求）。

---

## 2. Baseline

| 项 | 值 |
|---|---|
| `main` SHA（同步后真实远程） | `b24b81694feb4e5952bdd658469e3ab614bfaa9e` |
| A3 merge commit | `b24b816`，parents = `9909288`（A2 merge）+ `10ba17b`（A3 commit） |
| A4 分支 | `audit/oq-c2-a4-override-bypass-20260925`（从 `b24b816` 建立） |
| working tree（开始） | clean |

基线同步走**单次命令级** proxy + gh 一次性凭据（origin 为 SSH 且本机私钥不可用）；
**未修改全局 git config / 系统代理，未输出任何凭据**。

---

## 3. 四个 override / extension seam 的真实代码路径

矩阵从**当前 main 的实际代码**建立（非推测）。

| # | Override | 实际入口 | 是否调用 `route()` | policy 是否在前 | deny 可否绕过 | 当前证据 |
|---|---|---|---|---|---|---|
| 1 | handler injection | `VirtualEcu.handler`（`gateway.py:173`） | 是 —— 仅经 `VirtualEcu.process()`（`:179-184`） | **是** —— `process()` 的两个调用点都在 `_route_core` 内且受 `_allow` 守卫 | **否** | 本文件 §5.A |
| 2 | custom topology | `DiagnosticGateway(ecus=[…], functional_address=…)`（`gateway.py:204-230`） | 是 —— `_route_core`（`:280-326`） | **是** —— 按**实际 target** 逐 ECU 判定 | **否** | §5.B |
| 3 | fake Transport | `Transport` Protocol（`gateway_runtime.py:118-133`）+ `GatewayRuntime(gateway, transport)`（`:339-352`） | 是 —— `handle_request()` → `self.gateway.route()`（`:454`） | **是** —— 策略在协议层，transport 无关 | **否** | §5.C |
| 4 | `build_runtime()` | `build_runtime()`（`gateway_runtime.py:491-522`） | 是 —— 返回的 runtime 仍只能经 `gateway.route()` | **是** —— 与构造路径无关 | **否** | §5.D |

### 3.1 关键执行路径

```
handler injection
    VirtualEcu.process()  ← 全包**只有** 2 个调用点：
                            gateway.py:305（functional，在 if _allow 体内）
                            gateway.py:320（physical，在 if not _allow: return 之后）

custom topology
    _route_core：
        functional（先判定）：for ecu in self.ecus: if _allow(ecu,…): ecu.process(payload)
        physical           ：target = _by_request.get(can_id)
                             if not _allow(target,…): return []
                             response = target.process(payload)

runtime / transport
    serve_once() → transport.receive() → handle_request() → gateway.route() → _send()
                                              ↑ 唯一生产 route() 调用点（:454）

build_runtime()
    build_runtime() → build_default_gateway() + IsotpTransport() + GatewayRuntime()
                      ↑ 缺少 policy 概念：策略只能来自被注入的 gateway 对象
```

### 3.2 `production` 侧无第二套执行路径（结构性事实）

```
ecu/ 包内 .process(...) 调用点   = 2 处，均在 gateway.py::_route_core 内且受 _allow 守卫
ecu/ 包内 .route(...)   调用点   = 1 处（gateway_runtime.py:454 `self.gateway.route`）
gateway_runtime.py               = 不含 policy / _allow / _by_request / PolicyDecision
                                   （因此它不可能自行裁决放行与否）
```

---

## 4. DENY 策略来源（测试侧）

A4 的 DENY 策略在**测试内**定义：

```python
deny_all(ecu, addressing_mode, sid)            # 拒绝一切
deny_target(request_id)                        # 拒指定 target，其余放行
allow_only(request_id)                         # 只放行指定 target（B4 用）
deny_target_sid(request_id, sid)               # 只拒 (target, SID) 组合
```

**未修改** `DEFAULT_POLICY`（仍为 `allow_all_policy`）、未修改生产 policy 语义、
未修改 Golden、未修改任何 A2 fixture。由 `TestA4TestPolicySeam` 与
`TestStructuralInvariants::test_f3_default_policy_object_is_not_rebound` 常驻锁定。

---

## 5. 测试矩阵与结果

```
命令：python3 labs/vehicle/uds-basic/tests/test_c2_a4_override_bypass.py
结果：Ran 54 tests … OK        （skipped = 0）
```

| 测试类 | 用例数 | 覆盖 |
|---|---|---|
| `TestA4TestPolicySeam` | 4 | DENY 来自测试侧 seam；`DEFAULT_POLICY` 未变 |
| `TestHandlerInjectionBypass` | 9 | A1–A4（seam 1） |
| `TestCustomTopologyBypass` | 11 | B1–B6（seam 2） |
| `TestFakeTransportAndRuntime` | 8 | C1–C5（seam 3） |
| `TestBuildRuntimePath` | 5 | D1–D3（seam 4） |
| `TestRouteCoreParityUnderDeny` | 5 | E1–E3（§9 一致性） |
| `TestStructuralInvariants` | 6 | F1–F3（源码级不变量） |
| `TestNegativeControls` | 4 | DENY=0 / ALLOW=1 反向对照 |
| `TestScopeBoundary` | 2 | 边界显式记录（§8） |

### A. handler injection（seam 1）

| 用例 | 断言 |
|---|---|
| A1 | DENY + 自定义 handler ⇒ **四层同时**：`trace.decisions == (PolicyDecision(target=0x700, "physical", 0x22, False),)` / `process spy == 0` / state 逐 ECU 不变 / `results == []`；且 handler 计数器 == 0 |
| A2 | 自定义 handler 副作用计数器：4 种 payload（0x22 / 0x2E / 0x10 / 0x99）全部 DENY ⇒ `counter == 0` |
| A3 | **ALLOW 对照** ⇒ `counter == 1`、payload 记录正确、`results == [(0x708, 0x77…)]` |
| A4 | spy 确实装在 `target.process` **本身**（实例属性遮蔽类方法），且 ALLOW 下读数 1 ⇒ 证明 spy 在真实路径上（不是"永远为 0"的装饰）；反向同一装置 DENY 读数 0 |
| — | 默认协议层 handler 同样不可绕过 |
| — | `route()` 路径同样不得触达 target |

### B. custom topology（seam 2）

| 用例 | 断言 |
|---|---|
| B1 | 自定义 request/response ID（0x600/0x608）+ physical DENY ⇒ 四层 |
| B1b | 自定义 ID + ALLOW ⇒ 响应使用**自定义 response_id** |
| B2 | 自定义 functional address（0x600）+ functional DENY ⇒ 3 条 decision 全 `allowed=False`、process 0、state 不变、`results == []` |
| B2b | 自定义 functional address 后，原 0x7DF 成为**未知物理 target** ⇒ 0 条 decision（C2-OD-8） |
| B3 | **functional mixed**（A allow / B deny / C allow）⇒ trace `[(0x7E0,True),(0x7E1,False),(0x7E2,True)]`、process **`[1,0,1]`**、state A/C 变化 ∧ B 不变、wire `[]` |
| B3b | functional 无论放行几个 ECU，wire **恒为 `[]`**（总线约束，不是策略） |
| B4 | **"允许 A" 不自动授权 B/C**：策略只放行 0x7E0 ⇒ 广播下 process `[1,0,0]`、trace `[(A,T),(B,F),(C,F)]` |
| B5 | 边界：`functional_address` 与某 ECU `request_id` **重合** ⇒ functional 分支优先（既有语义，**未修改 production**），策略仍逐 ECU 生效（process `[1,0]`） |
| B5b | 重合拓扑下**不**重合的 request_id 仍走物理寻址 |
| B6 | 同一次广播：deny ECU state 逐字段不变 ∧ allow ECU session 由 1→3 |
| B6b | 广播写入：deny ECU 的 `did_overrides` 保持 `{}` ∧ allow ECU 已被写入 |

### C. fake Transport / GatewayRuntime（seam 3）

| 用例 | 断言 |
|---|---|
| C1 | `receive → handle_request → gateway.route → DENY` ⇒ `transport.sent == []`，且 `serve_once()` 返回 True（确实处理了请求，不是空闲轮） |
| C2 | 同场景四层：`sent == []` / process 0 / state 不变 / trace `allowed=False` |
| C3 | fake transport 的公开可调用面**恰好** `{open, close, receive, send}` |
| C3b | fake transport **不持有** ECU / Gateway 实例，且无 `policy` / `ecus` / `gateway` / `targets` 属性 |
| C4 | `runtime.handle_request()` 必须调用 `gateway.route()`（route spy 记录 == 请求本身） |
| C4b | 同一输入下 `runtime.handle_request()` 与直接 `route()` 结果一致（3 组输入） |
| C5 | **ALLOW 对照** ⇒ process 1 次、`transport.sent` 出现 1 帧且 response_id 正确 |
| C5b | 同一 runtime 上：先被拒的 target 无帧，换成放行的 target 后正常回帧 |

### D. `build_runtime()`（seam 4）

| 用例 | 断言 |
|---|---|
| D1 | patch 网关工厂 + `IsotpTransport` 后，`build_runtime()` 返回的 runtime 仍经 `gateway.route()`（route spy `call_count == 1`） |
| D2 | DENY ⇒ `sent == []` / process 0 / state 不变 / trace `allowed=False` |
| D2b | 全程**不调用 `open()`** ⇒ 不绑定任何接口；并被注入的网关对象就是 runtime 实际使用的那个 |
| D3 | **接口取值与 policy 正交**：`interface ∈ {"vcan0","can0","vcan1",""}` 四种取值下策略均生效、均不发帧、均未 open |
| D3b | `build_runtime()` 签名中**没有** `policy` 参数 ⇒ 策略只能来自被注入的网关对象 |

> D3 全程使用 **fake transport + patch**，**未绑定、未触碰任何真实或非 vcan0 接口**。

### E. `route()` / `route_with_trace()` 一致性（§9）

| 用例 | 断言 |
|---|---|
| E1 | 4 组输入下 `route()` 与 `route_with_trace()` 的 wire 结果**逐字节相同**，且两侧 state 投影相同 |
| E2 | core spy：`route()` ⇒ `collect_trace == [False]`；`route_with_trace()` ⇒ `[False, True]` —— **同一 core，仅记录与否不同** |
| E3 | **反向验证**：普通 `route()` 路径同样执行 deny（process 0、后续 state 不变） |
| E3b | 普通 `route()` 的 deny 不改变 state |
| E3c | `route()` 返回类型仍为 `list`（C2-OD-9 不变） |

### F. 结构性不变量（AST 级，防"第二套执行路径"）

| 用例 | 断言 |
|---|---|
| F1 | `ecu/` 包内 `.process(...)` 调用点**恰好 2 处**，且都落在 `gateway.py::_route_core` 函数体内（AST 定位，不受注释/文档字符串干扰） |
| F1b | 两处调用均受 `_allow(...)` 守卫 —— 同时承认两种既有形态：**正分支守卫**（functional，调用在 If 体内）与**早返回守卫**（physical，`if not _allow: return` 之后）。守卫位置由 AST 推导，**不硬编码行号**；移除守卫必然失败 |
| F2 | `gateway_runtime.py` 不含 `policy` / `_by_request` / `_allow` / `allow_all` / `PolicyDecision` |
| F2b | `gateway_runtime.py` 内 `.route(...)` 调用点**恰好 1 处**，且形如 `self.gateway.route(` |
| F2c | `gateway_runtime.py` **不调用** `process()` |
| F3 | `DEFAULT_POLICY` 仍 `is allow_all_policy`；`build_default_gateway().policy` 仍是它 |

### Negative controls

| 用例 | 断言 |
|---|---|
| DENY 对照 | `process call_count == 0` |
| ALLOW 对照 | `process call_count == 1` |
| ALLOW 广播对照 | 放行 2 个 ECU ⇒ `call_count == 2` |
| SID 粒度对照 | 同一 target 的 0x22 放行（+1）、0x2E 被拒（不变）⇒ 证明策略**真的按输入求值**，而非恒定拒绝 |

> 这四条确保 §5 中所有"0"都**不是**"永远不调用 process"的结果。

---

## 6. 显式记录的既有语义（C2-OD-8，**不是** bypass）

审计过程中必须把两条**设计**语义写清楚，避免被误读为"策略覆盖不全"：

```
空 payload     ⇒ 结构上取不到 SID ⇒ _allow 直接放行且**不产生 decision**
                 ⇒ DENY 策略**不会**拦住它（交由 handler 按协议层处理，如 7F 00 13）
未知物理 target ⇒ **路由归属失败**，不是授权判定 ⇒ 不产生 decision、不触达任何 ECU
```

对应用例：

* `test_empty_payload_does_not_enter_policy_by_design` —— 断言 `trace.decisions == ()`
  （**可观测地**证明策略未被求值）且 `process == 1`；
* `test_unknown_target_does_not_enter_policy_by_design` —— `trace.decisions == ()` 且 `process == 0`。

**A4 证明的是"已进入策略求值的请求不能被绕过"，而不是"一切请求都被策略覆盖"。**
这条区分是刻意的，写进本文档以免后续阶段误判。

> 附带记录：该语义是在**测试编写过程中被测试本身捕获的** ——
> 最初把 `b""` 放进"应被 DENY 拦住"的 payload 清单，测试失败后确认
> `b""` 按设计不进入策略，遂改为显式断言。

---

## 7. 边界声明（三条，显式记录）

### 7.1 Override invariant

```
override may replace implementation,
but cannot replace authorization.
```

即 **C2-OD-6**：`handler` / 拓扑 / transport / 构造路径都可以替换**实现**，
但授权结论一律由 `route()` 内、`target.process()` **之前**的策略求值决定。

### 7.2 `ecu.py` 边界

```
direct ecu.py execution is outside Gateway policy scope.
It is a separate topology, not a Gateway bypass.
```

`ecu.py` 是**单 ECU 独立守护进程**：它没有 gateway 对象，因而没有 Gateway policy 概念
（结构断言：`ecu.py` 不含 `DiagnosticGateway` / `_route_core`，且不调用 `.route(` / `.process(`）。
设计 §12 冻结的 override 面是 4 类，**不含**它；A4 **未**为其增加 bypass 测试面。

### 7.3 接口安全边界

```
ALLOWED_INTERFACES / vcan0
is an environment safety boundary,
not the Gateway authorization policy itself.
```

* 接口白名单位于 `gateway_runtime.py`（`DEFAULT_INTERFACE = "vcan0"`，由 `main()` 强制）；
* 授权策略位于 `gateway.py`（`GatewayPolicy` / `policy` 求值）；
* 二者**不同模块、不同机制**。静态断言：协议层**代码**中不含任何接口名字面量
  （用 AST 排除文档字符串，避免把注释误判成实现）。

> 因此 **A4 不把"`build_runtime()` 可传非 vcan0 interface"表述为 C2 deny policy 问题**
> —— 它属环境边界（设计 §12 #4 / §17 明示为调用方责任），A4 只验证
> "策略与 interface 取值**正交**"。

---

## 8. A4 不做的事

```
no new production policy          （未新增任何生产策略）
no policy semantics change        （DEFAULT_POLICY 保持 allow-all）
no Golden changes                 （tests/golden/ 未改）
no C1 changes                     （_SPEC_EXECUTION / SERVICE_SPECS / handler 未触碰）
no C3 / 0x27                      （SecurityAccess 未触碰）
no real CAN                       （未绑定、未触碰 can0/can1 或任何真实接口）
no GatewayRuntime / gateway.py change（production diff = 0）
```

---

## 9. 验证与回归

```bash
python3 labs/vehicle/uds-basic/tests/test_c2_a4_override_bypass.py   # 54/54 OK
bash labs/vehicle/uds-basic/tests/run_tests.sh unit                  # PASS 22 / FAIL 0
bash scripts/qa_fast.sh                                              # PASS
git diff --check                                                     # 空
```

| Gate | 结果 | 计数 |
|---|---|---|
| A4 专项 | **PASS** | **54 / 54**（skipped 0） |
| UDS unit（`run_tests.sh unit`） | **PASS 22 / WARN 0 / FAIL 0** | 1071 cases |
| UDS all（unit + e2e） | **PASS 34 / WARN 0 / FAIL 0** | **1305 cases**，SKIP 0 |
| CAN all（offline + vcan） | **PASS 7 / WARN 0 / FAIL 0** | **358 cases**，SKIP 0 |
| `qa_fast.sh` | **PASS**（exit 0） | ruff OK / mypy OK / uds unit 22-0 / can-tools offline 4-0 |
| `qa_extended.sh` | **PASS**（exit 0） | 4/4 步骤；**1663 cases**；FAIL 0；真实 skip 0 |
| `healthcheck.sh` | **PASS 75 / WARN 2 / FAIL 0**（exit 0） | 77 项 |
| `healthcheck.sh --extended` | **PASS 78 / WARN 2 / FAIL 0**（exit 0） | 80 项 |
| A0 / A1 / A2 回归 | **28/28 · 42/42 · 46/46** | 未受影响 |

口径对照：A3 时 UDS unit = 1017；A4 新增 54 ⇒ **1071**；UDS all 合计
1251 → **1305**（+54）✓；qa_extended 内层 1609 → **1663**（+54）✓。

### 9.1 qa_extended

```
[1/4] UDS 全套（单元 + vcan0 端到端）   → [ OK ] uds all        （PASS 34 / FAIL 0）
[2/4] CAN tools 全套（离线 + 畸形 ISO-TP）→ [ OK ] can-tools all （PASS 7 / FAIL 0）
[3/4] capture → save → replay 闭环       → [ OK ] capture lab
[4/4] YARA 真阳性 / 真阴性演练            → [ OK ] yara
QA EXTENDED RESULT: PASS        exit 0
```

### 9.2 healthcheck 的 2 个 WARN（历史/环境级，非 A4 新增）

| WARN | 分类 |
|---|---|
| `Git working tree has uncommitted changes` | **本轮预期** —— A4 的改动尚未提交（lab work 期间的既定预期）；另叠加 Windows/WSL 行尾假阳性（A3 文档已记录） |
| `docker installed but engine not reachable` | 环境级 —— Docker Desktop 未运行（历史同类已记录） |

> `--extended` 比 A3（79 PASS / 1 WARN）少 1 个 PASS、多 1 个 WARN：正是上面第一条
> git 检查从 PASS 变为 WARN 所致（总项数 80 不变）。**不是回归。**

---

## 10. Scope

```text
production diff      = 0   （ecu/gateway.py 与 ecu/gateway_runtime.py 均 UNCHANGED）
Golden diff          = 0
A0/A1/A2 diff        = 0
CI diff              = 0
C1 diff              = 0
```

A4 新增物：

```
新增  labs/vehicle/uds-basic/tests/test_c2_a4_override_bypass.py
新增  docs/OQ_C2_A4_OVERRIDE_BYPASS_2026-09-25.md
修改  labs/vehicle/uds-basic/tests/run_tests.sh   （+8 行接入）
```

---

## 11. 结论

| # | 判据 | 结果 |
|---|---|---|
| 1 | seam 1 `handler` 注入不能绕过策略（四层证据 + 计数器） | ✓ |
| 2 | seam 2 自定义拓扑不能绕过策略（含 functional mixed 逐 ECU） | ✓ |
| 3 | "允许 A" 不自动授权 B / C | ✓ |
| 4 | seam 3 fake transport / runtime 不能绕过策略；runtime 无第二套裁决 | ✓ |
| 5 | seam 4 `build_runtime()` 路径仍经 `gateway.route()` | ✓ |
| 6 | 接口边界与授权策略正交（且未触碰真实接口） | ✓ |
| 7 | `route()` 与 `route_with_trace()` 共用同一 core，deny 行为一致 | ✓ |
| 8 | 结构性不变量：`process()` 仅 2 处且均受 `_allow` 守卫 | ✓ |
| 9 | Negative controls：DENY=0 / ALLOW=1（非"永远不调用"） | ✓ |
| 10 | `DEFAULT_POLICY` / Golden / production 未变 | ✓ |

```
C2-A4 = PASS
```

---

## 12. 下一步

```
C2-A5 = NOT STARTED   （exit review）
C3    = NOT STARTED
```

**A4 完成后不自动进入 A5。**
