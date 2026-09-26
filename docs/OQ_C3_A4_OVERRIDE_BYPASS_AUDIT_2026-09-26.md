# OQ C3-A4 —— Override / Bypass Audit（C3 `services_provider` seam 扩展）

> 状态：**C3-A4 = BLOCKED**
> 原因：**未发现任何 policy / C1 guard bypass**，但在审计 `services_provider` 与既有
> `handler` seam 的交互时发现 **1 个真实生产缺陷**；按 A4 规则**不在本阶段修 production**。
> 日期：2026-09-26
> 分支：`audit/oq-c3-a4-override-bypass-20260926`
> 基线：`main = 5ebfaf349b42663a0a8df49fac93777d12fd9e9b`（= C3-A3 merge commit，PR #54）
> 设计出处：`docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md`
> （**C3-OQ-8 = DECIDED：必须进行新的 override audit**；C3-OD-14 的 10 条限制）

---

## 1. BASELINE

```
BASE SHA = 5ebfaf349b42663a0a8df49fac93777d12fd9e9b
```

| 检查 | 结果 |
|---|---|
| `HEAD` | `5ebfaf349b42663a0a8df49fac93777d12fd9e9b` ✓ |
| `working tree`（开始前） | clean |
| `git diff --stat 5ebfaf3 -- labs/vehicle/uds-basic/ecu/` | **空**（production 0 diff） |

## 2. SCOPE

```
本阶段：验证 C3 新增的 services_provider 与 C2 既有 override / injection seam，
        在 C3 之后**仍不能绕过 C2 policy 与 C1 guard**。

允许：新增 A4 测试、新增本报告、必要测试辅助代码
禁止：修改任何 production / Golden / C2·C3 Design / C2-A2 differential /
      C3-A0·A1·A2 测试 / CI workflow；改变 policy 语义；改变 process() API
```

**A4 不是 Exit Review** —— 本报告**不**得出 C3 EXIT 结论。

## 3. 证据模型（沿用 C2-A4，未改写其历史报告）

```
Layer 1  policy / decision
Layer 2a provider / handler
Layer 2b process()
Layer 3  ECU state
Layer 4  wire / response
```

**只测 `route() == []` 不够** —— `[]` 无法证明 ECU 内部没有被执行。
因此每个 DENY 场景都同时断言四层。

**装置复用**：`ProcessSpy` / `CountingHandler` / `RecordingTransport` /
`install_allow_observer` / 策略工厂 / `state_projection` 直接
`import test_c2_a4_override_bypass as c2a4` 复用 —— 这是
"C2-A4 既有证据 = 历史基线，C3-A4 = 最小新增"的落地方式。

## 4. 实测证据矩阵（**数值全部来自实跑**，非按示例硬填）

| Seam | Scenario | Decision | Provider calls | process | State | Wire |
|---|---|---|---|---|---|---|
| provider | physical deny | `False` | **0** | **0** | unchanged | `[]` |
| provider | physical allow | `True` | 1 | 1 | advanced | `[(0x7E8, 67 01 …)]` |
| provider/functional | deny all（3 ECU） | `3×False` | **0** | **0** | unchanged | `[]` |
| provider/functional | mixed（只拒 A） | `[False,True,True]` | 2 | 2 | A=unchanged, B/C=changed | `[]` |
| provider/functional | allow all（3 ECU） | `3×True` | 3 | 3 | all advanced | `[]` |
| same-SID `0x27` | deny | `False` | **0** | **0** | marker hits **0** | `[]` |
| **C1 guarded `0x2E`** | guard reject | C1 guard 拒绝（policy 仍 allow） | 1※ | 1 | marker hits **0** | `[(0x7E8, 7F 2E 7F)]` |
| handler | deny | `False` | n/a | **0** | unchanged | `[]` |
| topology | deny | `False` | n/a | **0** | unchanged | `[]` |
| transport | deny | `allowed=False` | **0** | **0** | unchanged | `[]` |
| build_runtime | deny | `allowed=False` | **0** | **0** | unchanged | `[]` |
| build_runtime | allow | `allowed=True` | 1 | 1 | advanced | `[(0x7E8, 67 01 …)]` |

※ **重要区分**：C1 guarded 行中 `Provider calls = 1` 是**正确**的 ——
provider 只负责**依赖绑定**（供给 services 表），它在 `process()` 内、
guard **之前**被调用；而 marker **handler** 的调用次数为 **0**
（guard 先拒绝）。即：

```
provider 被调用  ≠  handler 被执行
```

---

## 5. `services_provider` audit（§6 / §7 / §8）

### 5.1 physical deny（A4-P1）

```
decision : target=0x7E0 / addressing_mode="physical" / sid=0x27 / allowed=**False**
provider : **0** 次      ← DENY 必须发生在 provider 生效之前
process  : **0** 次
state    : unchanged（pending_seed=None / seed_counter=0 / failed_attempts=0 /
           seed_consumed=False / lockout_until=None / security_level=LOCKED）
wire     : []
```

反向对照（同装置、同 provider、allow）：

```
decision : allowed=True ；provider=1 ；process=1 ；wire=[(0x7E8, 67 01 <seed>)]
```

### 5.2 functional deny（A4-P2）

```
全 deny 广播 ：decision = 3×False ；provider = **0** ；process = **0** ；state unchanged ；wire=[]
mixed（只拒 A）：decision = [False, True, True]
                process = A:0 / B:1 / C:1（**按 ECU 归因**）
                provider = 2（只有被放行的 B/C 各自调用一次）← 这是**正确**行为
                A 的 security state **不变**，B/C 正常推进
                wire = []
```

> **"无部分绕过"的正确判据是按 ECU 归因**，而不是 provider 总数：
> 混合策略下总数不为 0 恰恰说明逐 ECU 授权在正常工作。
> 全 deny 广播的 `provider == 0` 由独立用例覆盖。

### 5.3 顺序没有反过来（§8）

```
physical allow  ：policy=1 ／ provider=1 ／ process=1
physical deny   ：policy=1 ／ provider=0 ／ process=0
functional allow：policy=3 ／ provider=3 ／ process=3
functional deny ：policy=3 ／ provider=0 ／ process=0
decision 顺序与字段逐条核对 == self.ecus 遍历顺序，addressing_mode / sid / allowed 均正确
```

顺序证明：**provider 只在 `process()` 之内被调用**，而 `process()` 只在
policy 允许之后被调用 ⇒ `policy → provider → process` 不可能反过来。

## 6. handler + provider 交互（§9）

**关键区分**（两件**完全不同**的事）：

```
① "custom handler 能绕过 provider"  → 既有 handler override 语义，**允许**
② "custom handler 能绕过 policy"    → **必须 FAIL**（实测确实 FAIL，见下）
```

| 场景 | 结果 |
|---|---|
| 构造期同时给出 `handler=custom` + `services_provider` + **allow** | custom 被调用 1 次；provider 的 marker hits **0** ⇒ ① 成立 |
| 同上 + **deny** | `process=0`、custom **0 次**、provider **0 次**、state unchanged、wire `[]`、decision `False` ⇒ ② **正确 FAIL** |

即：**custom handler 不能绕过 policy** ✓。

## 7. same-SID override（§10）与 C1 guard（§11）

### 7.1 same-SID `0x27`

```
allow ：marker handler 执行 1 次，wire = [(0x7E8, EE 01)] ⇒ 覆盖契约正向成立
deny  ：marker hits **0** ；provider **0** ；process **0** ；wire [] ；decision False ；state unchanged
⇒ **provider 能 override service implementation ≠ 能 override policy**
```

### 7.2 C1 guard 不得被绕过（**C3-OQ-12 的核心约束**）

选用 `0x2E`：它在 `_SPEC_EXECUTION` 中（guard **实际执行**），带 **6** 个 guard
（含 session 与 security）。

```
provider 对 0x2E 提供 marker handler
默认会话 + LOCKED 下调用 2E F1 02 XY
    → wire  = [(0x7E8, 7F 2E 7F)]   ← 会话 guard 仍生效
    → marker hits = **0**            ← handler 未被绕过
    → did_overrides 不变
    → decision.allowed = True        ← C1 guard 属 C1 层；policy 仍放行（两件事）
```

结构性证据：`resolve_service_spec(0x2E, {0x2E: marker})` 返回的 spec
**guards 来自静态 `SERVICE_SPECS`**，覆盖表只替换 handler：

```
spec.guards == uds_server.SERVICE_SPECS[0x2E].guards   ✓
```

反向对照（条件满足时 marker 才执行）：extended + unlocked 下同一 provider
→ marker hits = 1、wire = `[(0x7E8, EE 02)]`
⇒ 前一条不是"恒拒绝"。

> 这一项直接对应 C3-OQ-12 的冻结约束：**`services=` 覆盖的是实现，不是授权。**

## 8. C2 四个既有 seam 复验（§13）

| Seam | 结果 |
|---|---|
| handler injection | deny ⇒ decision False ／ process 0 ／ state unchanged ／ wire `[]` ／ custom handler 0 次 ✓ |
| custom topology | deny ⇒ 同上（自定义 request_id / functional_address 拓扑）✓ |
| fake transport | deny ⇒ `transport.sent == []` ／ process 0 ／ state unchanged ／ provider 0 ✓ |
| **build_runtime** | **C3 明确变化过的 seam**，见下 ✓ |

### 8.1 `build_runtime` —— A4 的重点

```
C2：build_runtime() → gateway
C3：build_runtime() → gateway + services_provider
```

实测（patch 网关工厂 + fake transport，**未绑定任何真实接口**）：

```
deny ：wire(sent) = [] ／ process = 0 ／ provider = **0** ／ state unchanged
        ⇒ 接线 provider **没有**制造第二条 bypass path
allow：provider = 1 ／ sent = [(0x7E8, 67 01 <seed>)]
        ⇒ 按 C3-A1 接线**正常工作**
```

## 9. Source-level invariants（AST 级，非仅 grep）

| 检查 | 结果 |
|---|---|
| 全包 `.process(...)` 调用点 | `gateway.py` **恰好 2 处**；`gateway_runtime` / `uds_server` / `ecu` / `keygen` 均 **0** 处 ✓（C2 冻结不变量保持） |
| `_allow(...)` 调用位置 | 全部落在 `_route_core` 函数体内（AST 行号区间断言）✓ |
| `gateway_runtime` 无第二套授权 | 不含 `policy` / `_allow` / `_by_request` / `PolicyDecision` / `allow_all` ✓ |
| provider 的调用点 | production 中仅一处：`services={**uds_server.SERVICES, **services_provider()}` ✓ |
| `gateway.py` 不感知 clock/config/keygen | 不含 `_now` / `SECURITY_CONFIG` / `keygen` ✓ |
| 无 functional 0x27 特例 | `gateway` / `gateway_runtime` 中无 `is_functional` / `functional_0x27` / `SID == 0x27` ✓ |
| 无第二套 0x27 状态机 | `gateway.py` 中无 `seed_counter` / `failed_attempts` / `lockout_until` / `verify_key` / `derive_key` / `generate_seed` ✓ |

**provider 契约**（C3-OD-14 限制 1）：provider 只把返回值合并进 `services=`，
不接触 policy、不接触 `EcuState`、不产生 `PolicyDecision` ✓

## 10. Negative controls（§16）

| 对照 | 结果 |
|---|---|
| baseline 是干净的 deny | decision False ／ provider 0 ／ process 0 ／ wire `[]` ✓ |
| allow 路径 provider 计数 = 1 | 证明计数断言**非恒零** ✓ |
| allow 路径 wire 非空 | 证明 wire 断言**非恒空** ✓ |
| **三字段投影对 0x27 不敏感** | `0x27` 不改变 `(session, security_level, did_overrides)` ⇒ 三字段断言**非恒真**；变化由 `security_state` 投影检出 ✓ |

> 第 4 项是审计过程中**修正的一处自己的假断言**：初版用 `27 01` 做
> "state 投影能检出变化"的对照，而该请求根本不改那三个字段 —— 那是个**恒真**的
> 假断言。已改为用 `10 03`（会话切换）做对照，并**单独**加一条
> "0x27 不改三字段但改 security_state" 的用例，把这一区分固化下来。

## 11. 测试结果

```
python3 labs/vehicle/uds-basic/tests/test_c3_a4_override_bypass.py
    Ran 45 tests … FAILED (failures=1)

唯一失败 = §12 的生产缺陷 reproduction（**预期失败**，见下）
```

| Gate | 结果 |
|---|---|
| **C3-A4 专项** | 45 例，**1 失败**（= 缺陷 reproduction） |
| **C2-A4 回归** | **58 / 58 OK** ✓ 既有 seam 未被破坏 |
| C3-A2 | 42 / 42 OK ✓ |
| C3-A1 | 48 / 48 OK ✓ |
| C3-A0 | 35 / 35 OK ✓ |
| C2-A2 | 46 / 46 OK ✓ |
| `run_tests.sh unit` | PASS 25 / WARN 0 / **FAIL 1**（唯一失败即缺陷） |
| ruff | All checks passed |

## 12. BLOCKER / DEFECT

```
BLOCKER = 1
```

### DEFECT-1 —— 网关级 `services_provider` 静默覆盖 ECU 自带的 custom handler

**授权影响：无**（不是 bypass；policy 与 C1 guard 均仍生效）。
**性质**：**覆盖（override）语义回归** —— 破坏 C2-A4 已审计的 seam-1
（"handler 注入**可替换实现**"）。

#### 触发条件（三种形式对比，实测）

| 形式 | 构造 | handler 是否保留 | custom 调用 |
|---|---|---|---|
| **A** | `VirtualEcu(handler=custom, services_provider=prov)` | **True** ✓ | 1 次 ✓ |
| **B** | `VirtualEcu(handler=custom)` + `DiagnosticGateway(..., services_provider=prov)` | **False** ✗ | **0 次** ✗ |
| **C** | `VirtualEcu(默认)` + `DiagnosticGateway(..., services_provider=prov)` | False（**预期**） | 走默认 handler ✓ |

> **形式 A 按设计工作**（这是 C3 Design 明确描述的组合）；
> **缺陷只出现在形式 B**。

#### Reproduction

```python
import gateway
from gateway import DiagnosticGateway, VirtualEcu

calls = []
def custom(payload):
    calls.append(payload)
    return b"\x99" + payload

provider = ...          # 任一合法 services_provider
ecu = VirtualEcu("X", 0x700, 0x708, handler=custom)
gw = DiagnosticGateway([ecu], services_provider=provider)

assert gw.ecus[0].handler is custom     # ← **失败**：已被替换为默认 provider-bound handler
gw.route(0x700, bytes([0x27, 0x01]))
assert calls == [bytes([0x27, 0x01])]   # ← **失败**：custom 调用 0 次
```

对应用例：`TestDefectReproduction::test_gateway_level_provider_must_not_replace_a_custom_handler`
（断言**正确行为**，当前失败；另有 `test_construct_time_provider_form_is_unaffected`
作为反向对照证明形式 A 正常）。

#### 根因

`gateway.py::DiagnosticGateway.__init__`（C3-A1 新增）：

```python
if services_provider is not None:
    for ecu in self.ecus:
        if ecu.services_provider is None:
            ecu.services_provider = services_provider
            if ecu.handler is not None:
                ecu.handler = _default_handler_for(ecu.state, services_provider)
```

`if ecu.handler is not None` **无法区分**：

```
"未接线的默认 handler"          → 应当重绑（形式 C 的意图）
"用户显式注入的 custom handler"  → **不得覆盖**
```

#### 影响面评估

```
生产路径（build_runtime → build_default_gateway(provider)） = 形式 C ⇒ **不受影响**
C3-A1 / A2 的既有用例                                        ⇒ 均不覆盖形式 B
既有任何 gate                                                ⇒ **未覆盖形式 B**
⇒ 该缺陷目前是**潜伏**的，无人发现；本报告首次记录。
```

#### A4 处置（按规则）

```
1  停止修复                 ✓ 未修改任何 production 文件
2  记录 reproduction        ✓ 本节 + 失败用例
3  报告标记 BLOCKER/DEFECT  ✓ BLOCKER = 1
4  不在 A4 分支修生产代码    ✓
```

#### 建议的修复方向（**未实施**，供主审决策）

```
判据应从 "handler is not None" 改为 "handler 是否是该 ECU 的**默认** handler"，
例如在 VirtualEcu 上记录 handler 是否由 __post_init__ 默认绑定
（如 `handler_is_default: bool`），只对默认绑定的 handler 重新接线。
```

> 该修改会触碰 `gateway.py` 的 production 语义与 `VirtualEcu` 结构，
> 属**独立决策**，不在 A4 范围内。

## 13. Scope

```
production diff = 0        （ecu/ 全目录；本阶段未改任何 production）
Golden diff     = 0
Design diff     = 0        （C2·C3 设计文档未改）
C2-A2 diff      = 0        （未触碰 differential 与其 pin）
C3-A0/A1/A2 diff = 0       （既有测试语义未改）
CI workflow diff = 0
```

本阶段新增/修改：

```
新增  labs/vehicle/uds-basic/tests/test_c3_a4_override_bypass.py
新增  docs/OQ_C3_A4_OVERRIDE_BYPASS_AUDIT_2026-09-26.md
修改  labs/vehicle/uds-basic/tests/run_tests.sh   （+8 行接入）
```

## 14. Conclusion

| # | 判据 | 结果 |
|---|---|---|
| 1 | C2-A4 四层证据矩阵**复验通过**（4 seam） | ✓ |
| 2 | 新增 `services_provider` seam 的四层证据 | ✓ |
| 3 | DENY 时 `provider == 0`（physical / functional 全 deny） | ✓ |
| 4 | DENY 时 `process() == 0` / state unchanged / wire `[]` / decision False | ✓ |
| 5 | provider 不改变 policy 的求值次数与顺序 | ✓（1-1-1 / 1-0-0 / 3-3-3 / 3-0-0） |
| 6 | same-SID override 不能扩大授权边界 | ✓ |
| 7 | **C1 guard 未被 same-SID override 绕过** | ✓ |
| 8 | `build_runtime` 接线 provider 未制造第二条 bypass path | ✓ |
| 9 | 源码级不变量保持（含 `process()` 调用点恰 2 处） | ✓ |
| 10 | **未发现任何 policy / C1 guard bypass** | ✓ |
| 11 | **`services_provider` 与 `handler` seam 的交互** | ✗ **DEFECT-1（BLOCKER = 1）** |

```
C3-A4 = BLOCKED

理由：授权边界全部成立（第 1–10 项 ✓）；
      但审计范围内发现 1 个真实生产缺陷（第 11 项），
      而 A4 规则禁止在本阶段修 production ⇒ 无法在本阶段闭环。
```

**本报告不写 `C3 EXIT = PASS`** —— 那属 C3-A5 Exit Review。

## 15. Next Stage

```
C3-A4（本文档）  ← BLOCKED（DEFECT-1 待处置）
      ↓
处置 DEFECT-1（需主审决策：是否修 production / 如何修）
      ↓
C3-A5  Exit Review
```

**A4 完成后不自动进入 A5。**
