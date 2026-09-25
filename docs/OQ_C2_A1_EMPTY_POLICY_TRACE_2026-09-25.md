# OQ C2-A1 —— 空策略 + per-call PolicyTrace 机制（证据报告）

> 状态：**C2-A1 = PASS**
> 日期：2026-09-25
> 分支：`audit/oq-c2-a1-empty-policy-trace-20260925`
> 基线：`main = 1b43b12763beb02bfeb0969193fe2c683eb6cf6b`（PR #44 merge commit）
> 设计出处：`docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md`
> （C2-OD-1 … C2-OD-9 均已 **DESIGN FROZEN**）

**本阶段的唯一主张**：

> 引入"**空策略 + per-call PolicyTrace**"机制，并在空策略 / default-allow 下
> 证明 Gateway 的 **wire behavior 与 state behavior 不发生变化**。

**不做** C2-A2 的完整 Golden 对拍。

---

## 1. Baseline

| 项 | 值 |
|---|---|
| `main` commit | `1b43b12763beb02bfeb0969193fe2c683eb6cf6b` |
| A1 分支 | `audit/oq-c2-a1-empty-policy-trace-20260925` |
| `working tree`（开始前） | clean |
| A0 Golden（开始前） | blob `541395b8…`，LF SHA256 `d1d35892…` |

---

## 2. Production Diff

```
仅 1 个文件：labs/vehicle/uds-basic/ecu/gateway.py    +187 / -6
```

**未修改**：

```
gateway_runtime.py    UNCHANGED（寻址实现一字未动）
uds_server.py         UNCHANGED（C1 面未触碰，_SPEC_EXECUTION 未动）
ecu.py / service_spec.py / did_database.py / uds_types.py   全 UNCHANGED
```

---

## 3. `route()` API —— **保持不变**（C2-OD-9）

```python
def route(self, can_id: int, payload: bytes) -> list[RouteResult]:
    results, _trace = self._route_core(can_id, payload, collect_trace=False)
    return results
```

* **签名与返回类型一字未改**（仍为 `list[tuple[int, bytes]]`）；
* 策略决策**不**出现在返回值里；
* 既有调用方（`gateway_runtime.py:454` 等）**无需改动**。

> 明确**拒绝**了 `GatewayResult(responses=…, decisions=…)` 方案
> （设计文档 §9.2.1 列出五条理由）。

---

## 4. `route_with_trace()` API（新增）

```python
def route_with_trace(
    self, can_id: int, payload: bytes
) -> tuple[list[RouteResult], PolicyTrace]:
    return self._route_core(can_id, payload, collect_trace=True)
```

**与 `route()` 共用同一个 `_route_core`** —— 不是第二条实现路径，
因此"生产路径"与"测试路径"的语义**不可能分叉**（§9.2.9）。
唯一差别是**是否记录**决策；策略本身在两条路径上都会被求值并生效。

---

## 5. PolicyTrace / PolicyDecision Schema

```python
@dataclass(frozen=True)
class PolicyDecision:
    target: int              # ECU 的 request_id —— 稳定寻址身份（非 id()/repr()）
    addressing_mode: str     # "physical" | "functional"（字符串，非 bool/int）
    sid: int                 # payload[0]
    allowed: bool

@dataclass(frozen=True)
class PolicyTrace:
    decisions: tuple[PolicyDecision, ...] = ()
```

**刻意不纳入**（属 observability enhancement，非 correctness 必要条件）：
`reason` / `rule_id` / `message` / `timestamp` / `logger`。

### 策略载体（C2-OQ-3 的实现形态选择）

```python
GatewayPolicy = Callable[["VirtualEcu", str, int], bool]

def allow_all_policy(ecu, addressing_mode, sid) -> bool:
    return True                                        # 空策略（C2-OD-2）

DEFAULT_POLICY: GatewayPolicy = allow_all_policy
```

`DiagnosticGateway.__init__(..., policy: GatewayPolicy | None = None)` ——
`None` ⇒ `DEFAULT_POLICY`。**既有调用方无需改动**，
行为与机制引入前**逐字节相同**。

> C2-OQ-3（源码常量 vs 数据配置）本身仍**未冻结** —— 本阶段只引入**机制**，
> 不规定策略**内容**的存储形式。

---

## 6. 策略求值位置（C2-OD-1）

```
目标解析
    ↓
policy(ecu, addressing_mode, sid)        ← _allow()
    ↓
if allowed: target.process(payload)      ← 仅在放行时
```

因此：

* 被拒请求**不会到达目标 ECU**（其 `EcuState` 不变）；
* 策略在 `VirtualEcu.handler` **之外** ⇒ **handler 注入不能绕过它**（C2-OD-6）。

---

## 7. Decision 数量与顺序

| 情形 | decision 数 | 顺序 |
|---|---|---|
| physical + 非空 payload | **1** | — |
| functional + 非空 payload | **N（= `len(self.ecus)`，默认 3）** | **== `self.ecus` 遍历顺序** |
| unknown physical target | **0** | — |
| empty payload（含 functional） | **0** | — |

`functional` 的 decision 顺序**可重复性**已断言（3 次调用顺序集合大小为 1）。

---

## 8. Deny 语义（C2-OD-3）

```
wire : results == []        不产生帧 / 不合成 7F / 不生成 NRC
trace: PolicyDecision(..., allowed=False)
target.process()  **不被调用**
EcuState          **不变**
```

**未被 deny 的 ECU 照常执行** —— functional 逐 ECU 判定（C2-OD-4/OD-5）：
只拒绝 `0x7E1` 时，实测 `calls == ["A", "C"]`，decision 为
`[(0x7E0, True), (0x7E1, False), (0x7E2, True)]`。

---

## 9. `route()` 与 `route_with_trace()` 一致性

对 **13 组** `(can_id, payload)` 覆盖（物理正/负/suppress/写/空/未知 target、
功能会话/读/suppress、三个 ECU 地址）：

```
route() 的 results  ==  route_with_trace() 的 results   （逐字节）
route() 的 state    ==  route_with_trace() 的 state     （逐 ECU 三字段）
```

另有一条反向用例证明 **`route()` 路径同样求值策略**（拒绝策略下自定义
handler 同样不被调用）—— 排除"只有 trace 路径才跑策略"的两套语义。

---

## 10. A0 Golden —— 完全 untouched

```
LF 规范化 SHA256 = d1d358927a4eb86e750fda13a961d935b1afccd3e01e50c26a696dcc7f43c208  （MATCH）
blob             = 541395b82e74ce915830fea23369e21c65fe7b27                          （未变）
git status       = golden/ 0 处改动
```

### ⚠ A0 的一条 live 断言按设计**失败**，并在 A1 **阶段迁移**

A0 结构测试中的 `test_policy_mechanism_still_absent_in_production`
断言"生产代码中不存在 policy 符号" —— A1 **必然**使它失败。

**这正是该断言的设计意图**（A0 报告原文："若本用例失败 ⇒ A1 必须现在处理，
而不是让 A0 的 corpus 被悄悄重新解读"）。A1 的处理方式：

| 层面 | 处理 |
|---|---|
| **捕获脚本的 fail-closed 护栏** | **保留不变**。实测：A1 后运行捕获脚本 → `[FAIL] 检测到 Gateway policy 机制已存在` → **`EXIT=2`**，拒绝写入。**legacy oracle 从此不可再生成。** |
| **A0 结构测试的 live 断言** | 迁移为 `test_capture_guard_refuses_to_regenerate_the_legacy_oracle`：A0 时断言"机制不存在"，**A1 起**断言"机制存在 ⇒ 捕获被拒绝"。两者锁定同一件事的两面。 |
| **corpus 头部的历史事实** | `policy_execution = "absent"` **保持不变**（仍由 `test_policy_execution_was_absent_at_capture` 断言）—— 它记录的是捕获时的代码状态。 |
| **新增 A1 实时不变量** | `test_default_gateway_ships_with_the_empty_policy`：机制已存在，但默认策略是空策略。 |

A0 结构测试现为 **28/28 PASS**（原 27 + 迁移后新增 1）。

---

## 11. 测试

| 测试 | 结果 |
|---|---|
| `test_c2_a1_empty_policy_trace.py` | **34/34 PASS**（新增） |
| `test_c2_a0_gateway_golden.py` | **28/28 PASS** |
| `test_gateway_unit.py` | **13/13 PASS** |
| `test_gateway_runtime_unit.py` | **53/53 PASS** |
| UDS unit | **963**（928 + 34 + 1；20/20 文件，FAIL 0） |
| UDS e2e | **234**（12 项，FAIL 0） |
| `qa_fast.sh` | **PASS**（28 项 OK / 0 FAIL，1266 cases；ruff 0 / mypy 0） |

### A1 单元测试覆盖点（对照需求清单）

```
 1  empty policy 下 physical positive 仍正常            ✓
 2  physical negative 仍转发                            ✓
 3  suppress 仍 []                                      ✓
 4  unknown target 仍 [] 且 trace 无 decision           ✓（含 deny 策略下的对照）
 5  empty payload 仍交给 handler 且 trace 无 decision   ✓（含 functional）
 6  physical 非空 payload ⇒ 1 条 decision               ✓
 7  functional 非空 payload ⇒ 每个 ECU 1 条 decision    ✓
 8  functional trace 顺序稳定                           ✓（== self.ecus）
 9  default-allow ⇒ 所有 decision.allowed == True       ✓
10  route() 与 route_with_trace() 的 response/state 一致 ✓（13 组）
11  deny 时 target.process() 不被调用                  ✓
12  custom handler 不能绕过 policy enforcement point   ✓
```

另含：trace per-call 不累积 / 不可变 / 两次调用互不污染；
`target` 为 `request_id`；`addressing_mode` 为语义字符串；
**未知 SID 仍进入策略**（C2-OD-8）；**策略输入不含 `EcuState` 字段**（C2-OD-8）。

### 11.1 A1 验收门 —— **A0 Golden 逐字节复现**

设计文档 §13 对 C2-A1 的三项要求，第 ① 项（"空策略下 Golden **逐字节不变**"）
需要把 **A0 冻结的 20 条 corpus 当作完整 oracle** 跑一遍 —— 这是
`TestEmptyPolicyMatchesFrozenA0Golden` 的职责。

**这不是重新生成 Golden**：测试只**读取** `c2_gateway_legacy.json`，
expected 值**只来自磁盘上的冻结文件**；写入路径仍只属于捕获脚本，
而该脚本在 A1 之后 **fail-closed（exit 2）**，已不可能再写。

对每条 case：用**其自带的 `ecus` 初始配置**独立构造全新网关
（`policy` 缺省 ⇒ `DEFAULT_POLICY` 空策略），执行**恰好一次**
`route_with_trace()`，再把结果按 A0 口径标准化后逐字节比较。

```
Golden cases 对比数量   = 20 / 20
response match          = 20 / 20
state    match          = 20 / 20

trace skip cases        = 3        （U1/U2 未知 target + M1 空 payload）
physical decisions      = 13
functional decisions    = 12       （F1–F4 × 3 ECU）
allowed=True            = 25 / 25   （空策略下**无** allowed=False）
```

逐 case 的 decision 数：

```
P1/P2/P3 物理读              1 1 1
N1/N2    ECU 负响应          1 1
S1       suppress            1
U1/U2    未知 target         0 0     ← 路由归属失败，不进入策略
F1–F4    功能寻址            3 3 3 3 ← 逐 ECU（C2-OD-4）
W1/I1/I2 写入与隔离          1 1 1
M1       空 payload          0       ← 无 SID 可取
M2/C1/C2/D1                  1 1 1 1
```

trace 形状断言逐 case 校验（空 payload / 未解析 target ⇒ `decisions == ()`；
physical 非空 ⇒ 恰好 1；functional 非空 ⇒ `len(ecus)` 且顺序 == `self.ecus`），
并断言**每一条** decision 的 `allowed is True`。

汇总数字另有 `test_aggregate_counts_are_stable` 常驻锁定，
任何漂移（少比一条、多产生一条 decision）都会立刻失败。

---

## 12. 边界（本阶段未做）

```
未修改 A0 Golden / 未重新运行 capture 写入
未修改 C1 面（_SPEC_EXECUTION / SERVICE_SPECS / guard / handler）
未修改 0x27
未修改 GatewayRuntime 的寻址实现
未增加 source / Tester identity
未增加 response-direction policy
未增加 rate limit / frequency policy
未加入 SERVICE_SPECS / service registry / handler registry 感知
未做 C3
C2-A2 / A3 / A4 / A5 = NOT STARTED
```

---

## 13. 结论

| # | 判据 | 结果 |
|---|---|---|
| 1 | policy carrier / empty policy 已引入 | ✓ |
| 2 | PolicyTrace 数据结构（per-call / immutable / ordered） | ✓ |
| 3 | route core 已抽取（单一 `_route_core`） | ✓ |
| 4 | `route()` 与 `route_with_trace()` 共用核心 | ✓ |
| 5 | default-allow | ✓ |
| 6 | trace 单元测试 | ✓（42 例） |
| 7 | zero-wire-behavior regression | ✓（971 unit / 234 e2e / qa_fast PASS） |
| 8 | A0 Golden untouched | ✓ |
| **9** | **§13 三项要求**：① 空策略下 Golden 逐字节不变 ✓（**20/20 response + 20/20 state**）② `route()` wire 行为不变 ✓ ③ 进入策略的 target 产生 `allowed=True` ✓（**25/25**） | ✓ |

```
C2-A1 = PASS
```

---

## 14. 下一步

```
C2-A2 = NOT STARTED   （legacy golden ↔ 空策略路径的逐字节对拍）
C2-A3 = NOT STARTED
C2-A4 = NOT STARTED
C2-A5 = NOT STARTED
C3    = NOT STARTED
```

**A1 完成后不自动进入 A2。**
