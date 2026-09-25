# C1 Phase 2 Migration B1 —— 0x2E ServiceSpec execution activation（证据报告）

> 状态：**B1 = PASS**
> 日期：2026-09-25
> 分支：`audit/oq-c1-phase2-b1-0x2e-spec-execution-20260925`
> 基线：`main = 3fdd06e30fb1d69b2c1e16ec4415647fede9ba84`（PR #37 merge commit）
> 上游设计：`docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md`
> §12（Migration B）/ §12.4（rung 顺序）/ §15（白名单）

**本报告只记录事实。B1 只回答一个问题：**

> 0x2E 的 ServiceSpec guards 是否真的接管了 dispatcher，同时 0x22 与 0x27 的边界是否保持？

**B1 不做 legacy ↔ spec 对拍** —— 那属 **B2**。

---

## 1. 基线

| 项 | 值 |
|---|---|
| `main` commit | `3fdd06e30fb1d69b2c1e16ec4415647fede9ba84` |
| B1 分支 | `audit/oq-c1-phase2-b1-0x2e-spec-execution-20260925` |
| B1 分支起点 | `3fdd06e30fb1d69b2c1e16ec4415647fede9ba84`（= main） |
| `working tree`（修改前） | clean |
| Migration A | COMPLETE |
| B0 | PASS（0x2E legacy golden 已冻结，70 条） |

---

## 2. Activation

```
before:  _SPEC_EXECUTION = frozenset({0x22})
after:   _SPEC_EXECUTION = frozenset({0x22, 0x2E})
```

即：

```
0x22 = ON     （_guards_for() 返回 1 个 guard）
0x2E = ON     （_guards_for() 返回 6 个 guard）
0x27 = unchanged
```

**production 改动只有一个常量**（外加同处注释）：
`uds_server.py:2397` `_SPEC_EXECUTION: frozenset[int] = frozenset({0x22, 0x2E})`。

未修改 `resolve_service_spec()` / `_guards_for()` / `handle_uds_request()` /
`service_write_data_by_identifier()` / `service_read_data_by_identifier()`。

---

## 3. 0x2E guard 证据

```
SERVICE_SPECS[0x2E].guards          = 6 个
_guards_for(SERVICE_SPECS[0x2E])    == SERVICE_SPECS[0x2E].guards    ✓
```

### 六道 rung 逐条由 spec path 接管

冻结顺序 `13 → 31 → 7F → 33 → 22 → 13`：

| rung | 请求 | 状态 | 实际响应 |
|---|---|---|---|
| ① `13` | `2E` | EXTENDED + LEVEL1 | `7F 2E 13` |
| ① `13` | `2E F1` | EXTENDED + LEVEL1 | `7F 2E 13` |
| ② `31` | `2E FF FF 58` | DEFAULT + LOCKED | `7F 2E 31` |
| ③ `7F` | `2E F1 02 58` | DEFAULT + LOCKED | `7F 2E 7F` |
| ④ `33` | `2E F1 02 58` | EXTENDED + LOCKED | `7F 2E 33` |
| ⑤ `22` | `2E F1 90 58` | EXTENDED + LEVEL1 | `7F 2E 22` |
| ⑥ `13` | `2E F1 02` + 17 字节 | EXTENDED + LEVEL1 | `7F 2E 13` |

### 优先关系（spec path 实测）

```
2E FF FF 58 + DEFAULT + LOCKED  →  7F 2E 31     ② 先于 ③
2E F1 02 58 + DEFAULT + LOCKED  →  7F 2E 7F     ③ 先于 ④
2E F1 02 58 + EXTENDED + LOCKED →  7F 2E 33     ④ 先于 ⑤
2E F1 90 58 + EXTENDED + LEVEL1 →  7F 2E 22     ⑤ 先于 ⑥
```

### guards 全过 → handler 执行 → **真实写入副作用**

```
2E F1 02 58 + EXTENDED + LEVEL1  →  6E F1 02
                                     state.did_overrides[0xF102] == b"X"
```

这一条把 `guard chain pass` 与 `handler side effect` 连起来 ——
**仅断言响应字节不足够**（§14：0x2E 是写服务，成功路径的副作用只体现在 final state）。

### 0x2E handler 的 inline checks 未改动

按 §10.4 / A4 结论，`handler = defensive invariant`。B1 **未删除**任何
inline check，`service_write_data_by_identifier()` 与 main 逐字节相同
（其所在的 `uds_server.py` 只有白名单常量一行不同）。

---

## 4. OD-4 A′（override 不绕过 0x2E guards）

`services = {0x2E: custom_handler}`：

| 状态 | 请求 | 实际响应 | custom_handler |
|---|---|---|---|
| DEFAULT + LOCKED | `2E F1 02 58` | `7F 2E 7F` | **未调用** |
| EXTENDED + LOCKED | `2E F1 02 58` | `7F 2E 33` | **未调用** |
| EXTENDED + LEVEL1 | `2E F1 02 58` | `AA BB`（sentinel） | **被调用** |

复刻 `ecu.py:374` 的 `{**SERVICES, ...}` 全表注入形态，guards 仍生效。

即：`services=` 替换**实现**，不替换**授权** —— 与 0x22 在 A1 建立的行为一致。

---

## 5. 0x22 边界

```
0x22 仍 ON（_guards_for() 返回 1 个 guard）
```

A0 的 92 条 legacy oracle 在 spec 路径下仍逐字节成立：

```
response = 92/92     state = 92/92
```

```
A0 golden sha256 = d0d83244c85d7916924227c09dc3565ce33e5534e485650aa1f32efbd520cf79
```

**与 B1 开始前完全一致。**

---

## 6. 0x27 边界

```
0x27 ∉ SERVICE_SPECS      ✓
0x27 ∈ SPECS_EXCLUDED     ✓
resolve_service_spec(0x27, None) is None   ✓
```

`_security_services()` / `_make_security_access_handler()` / `_now()` 现场求值机制 /
`SECURITY_CONFIG` 原对象引用语义 —— **均未改动**。

---

## 7. B0 oracle

```
file      = labs/vehicle/uds-basic/tests/golden/c1_phase2_0x2e_legacy.json
cases     = 70
schema    = v2
target_sid = 0x2E
target_execution_mode = legacy          （捕获时的事实）
spec_execution_at_capture = [0x22]      （捕获时的白名单）
captured_at_commit = 19d146bf931b78e143d72af22dd10f05f2708f9a

sha256 = 0ad204331c390a00f0906d294f6a8024a048082fb2266a69ff07e428b966f974
```

**与 B1 开始前完全一致。**

> **B0 golden 继续作为冻结的 legacy oracle。** B1 **未**重新捕获、未重新生成、
> 未修改任何字节。B2 才消费该 corpus 做 spec ↔ legacy 对拍。

### B0 测试的阶段性调整（§11 / §12）

B0 测试中原有的 `test_fresh_capture_equals_stored_corpus` 会**重新执行当前
dispatcher** 再与磁盘 corpus 比较。B0 时点 `0x2E ∉ _SPEC_EXECUTION`，两侧都是
legacy，该检查是合理的自洽验证。

**B1 之后 0x2E 已切到 spec 路径** —— 再执行当前 dispatcher 得到的是 spec 输出，
把它与 legacy golden 直接比较，性质上已经是 **B2 的对拍**，不应在 B1 里以
"自洽检查"的名义发生。

因此该用例被替换为**不执行**当前 0x2E dispatcher 的结构性冻结检查
（`TestFrozenCaseDefinitions`）：

* `build_cases()` 的 **case 定义**（case_id / request / initial_state / description）
  与冻结 corpus 逐条一致 —— **不**重新生成 expected 值；
* corpus 必须自带 `legacy_response` / `legacy_final_state`；
* 显式断言"`build_corpus()` 不再是 legacy oracle"（0x2E 现在确在 `_SPEC_EXECUTION` 中）。

同时，B0 测试中原有的 `test_0x2e_is_still_not_migrated_now`（断言当前白名单不含 0x2E）
在 B1 后已不成立，被替换为 **`test_capture_time_whitelist_is_frozen`** ——
断言的是 corpus 头部记录的**历史事实** `[0x22]`，与当前阶段无关。
`target_execution_mode = "legacy"` 同理，表示**捕获时** target SID 走 legacy，
**不是**说当前 0x2E 仍走 legacy。

---

## 8. 测试

| 测试 | 结果 |
|---|---|
| `test_c1_phase2_framework_unit.py` | **56/56 PASS**（41 → 56，含 `TestB1_0x2EGuardActivation`） |
| `test_c1_phase2_b0_0x2e_golden.py` | **30/30 PASS**（27 → 30） |
| `test_c1_phase2_a0_golden.py` | **26/26 PASS** |
| `test_c1_service_spec_unit.py` | **28/28 PASS** |
| `test_c1_contract_pins.py` | **29/29 PASS** |
| UDS unit | **842**（824 + 15 + 3；16/16 文件，FAIL 0） |
| UDS e2e | **234**（12 项，FAIL 0；含 `0x2E/0x3E write-DID e2e`） |
| `qa_fast.sh` | **PASS**（24 项 OK / 0 FAIL，1145 用例） |

### 阶段常量迁移

`test_c1_phase2_framework_unit.py`：

```python
MIGRATION_STAGE = "B1"
EXPECTED_SPEC_EXECUTION = frozenset({SID_READ, SID_WRITE})   # {0x22, 0x2E}
```

保持"**显式声明**"机制（**未**改成 `EXPECTED_SPEC_EXECUTION = _SPEC_EXECUTION`）：

* `A1 → A2` 是**验证阶段**推进 → 白名单**不变**；
* `A2 → B1` 是**执行阶段**推进 → 白名单**必须新增 0x2E**。

三条属于 A1 阶段的断言被转换为 B1 契约（**未删除**）：
`test_0x2E_is_still_disabled` → `test_0x2E_is_now_enabled`；
`test_0x2E_guards_are_skipped_even_though_spec_has_guards` → `test_0x2E_guards_are_executed`；
反向对照用例改用**仍未迁移**的服务（0x3E）而非 0x2E。

> 连带修正 `test_c1_phase2_a0_golden.py` 中的
> `test_non_migrated_services_still_skip_guards` —— 它原本用 **0x2E** 作为
> "未迁移服务"的例子，B1 后已不成立，改用 0x3E。

---

## 9. 边界

| 项 | 状态 |
|---|---|
| `ecu.py` / `gateway.py` / `gateway_runtime.py` / `service_spec.py` | **unchanged** |
| `service_write_data_by_identifier()` inline checks | **unchanged** |
| 0x22 行为 | **unchanged**（仍 ON） |
| 0x27 | **unchanged** |
| A0 golden（0x22） | **unchanged**（sha256 一致） |
| B0 golden（0x2E） | **unchanged**（sha256 一致） |
| 0x2E corpus | **未重新捕获** |

### 本阶段改动文件

```
labs/vehicle/uds-basic/ecu/uds_server.py                      白名单常量 + 同处注释（+3/-3）
labs/vehicle/uds-basic/tests/test_c1_phase2_framework_unit.py  阶段常量 + B1 激活测试（+238/-…）
labs/vehicle/uds-basic/tests/test_c1_phase2_b0_0x2e_golden.py  冻结语义调整（§8）
labs/vehicle/uds-basic/tests/test_c1_phase2_a0_golden.py       "未迁移服务"例子 0x2E → 0x3E
docs/OQ_C1_PHASE2_B1_0X2E_SPEC_EXECUTION_2026-09-25.md         本报告（新增）
```

合计：`4 files changed, 291 insertions(+), 63 deletions(-)` + 本报告。

**未**修改任何 golden 文件（0 改动）。

---

## 10. 最终判定

| 判定项 | 结果 |
|---|---|
| 0x22 = ON / 0x2E = ON / 0x27 = unchanged | ✓ |
| 0x2E guards = 6，`_guards_for()` 返回完整链 | ✓ |
| `13 / 31 / 7F / 33 / 22 / 13` 全部由 spec path 正确拦截 | ✓ |
| guards 全过 → handler 执行 → `did_overrides` 真实写入 | ✓ |
| OD-4 override 不可绕过 guards | ✓ |
| A0 golden unchanged | ✓ |
| B0 golden unchanged | ✓ |
| B0 70-case corpus 仍为冻结 legacy oracle | ✓ |
| framework / B0 validation / UDS unit / UDS e2e / qa_fast | ✓ 全 PASS |

```
B1 = PASS
```

### 下一步（本阶段**未**执行）

```
B2 = NOT STARTED   （B0 70 条 golden 的 legacy ↔ spec 逐字节对拍）
B3 = NOT STARTED
B4 = NOT STARTED
C2 = NOT STARTED
C3 = NOT STARTED

0x22 = ServiceSpec execution
0x2E = ServiceSpec execution
0x27 = unchanged

Migration B = IN PROGRESS
```

**B1 结束后不自动进入 B2。**
