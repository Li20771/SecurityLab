# C1 Phase 2 Migration B2 —— 0x2E Golden Replay 等价性验证（证据报告）

> 状态：**B2 = PASS**
> 日期：2026-09-25
> 分支：`audit/oq-c1-phase2-b2-0x2e-golden-20260925`
> 基线：`main = da6bb8e7248c39bf06d7046a4d85bfcc18ab0cc7`（PR #38 merge commit）
> 上游设计：`docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md`
> §12（Migration B）/ §13（Golden Corpus）/ §14.2（StateSnapshot）

**本报告只记录 replay 等价性事实。B2 只回答一个问题：**

> B0 冻结的 legacy oracle 与当前 0x2E ServiceSpec execution 是否**逐字节等价**？

---

## 1. Baseline

| 项 | 值 |
|---|---|
| `main` commit | `da6bb8e7248c39bf06d7046a4d85bfcc18ab0cc7` |
| B2 分支 | `audit/oq-c1-phase2-b2-0x2e-golden-20260925` |
| B2 分支起点 | `da6bb8e7248c39bf06d7046a4d85bfcc18ab0cc7`（= main） |
| `working tree`（开始前） | clean |

## 2. 当前 `_SPEC_EXECUTION`

```
_SPEC_EXECUTION = frozenset({0x22, 0x2E})      （uds_server.py:2397，运行时实测）
0x22 = ON      0x2E = ON
```

因此 replay **走的是 spec 路径**：

```
dispatcher → resolve_service_spec(0x2E) → ordered guards → handler
```

## 3. 冻结 corpus

```
path = labs/vehicle/uds-basic/tests/golden/c1_phase2_0x2e_legacy.json
```

## 4. Corpus SHA256

```
B0  0ad204331c390a00f0906d294f6a8024a048082fb2266a69ff07e428b966f974
A0  d0d83244c85d7916924227c09dc3565ce33e5534e485650aa1f32efbd520cf79
```

两个哈希在 B2 前后**完全一致**（见 §10–§12）。
两个 SHA256 已**硬编码进 B2 测试**，使"任何人改写 corpus"成为一个**立刻失败**的事实 ——
B2 的 expected 只有在文件逐字节未变时才有意义。

## 5. Corpus size

```
70 cases
    L malformed        18
    D unknown DID      12
    W writable F102     6
    R readonly         18
    N data length      10
    O overrides         6
```

**未增删任何 case、未改输入、未改 initial_state、未改 expected、未改 schema。**

---

## 6. 执行顺序验证

以 ``patch.object`` 对 ``c1_golden_lib.execute`` 安装**调用记录器**，
断言依据是"**真实发生过的执行**"，而非"corpus 里有什么"：

```
actual_case_order == golden_case_order      （列表相等）
```

**列表相等**（非集合相等）同时排除：漏执行、重复执行、顺序变化、跳过 case。

## 7. 恰好一次执行验证

```
expected execution count = 70
actual   execution count = 70
unique   case count      = 70      （len(set(executed)) == 70，排除重复）
visited  ==  executed              （遍历到的 ⇔ 真正执行到的）
```

## 8. Response 比较

```
response: 70 / 70 PASS
```

逐案 `actual_response == golden.legacy_response`（完整 bytes 相等）。

## 9. Final state 比较

```
state: 70 / 70 PASS
```

逐案 `actual_final_state == golden.legacy_final_state`，比较范围**仍是 B0 冻结的三字段投影**
（`session` / `security_level` / `did_overrides`）——**未自行扩大**。

成功写入用例的**副作用**单独锁定：

* 成功路径（`6E ...`）必须真的改变 `did_overrides`，且与 oracle 一致；
* `0xF102` 的写入值单独断言；
* 失败路径（`7F ...`）必须**无** `did_overrides` 副作用；
* `session` / `security_level` 从不改变。

> 只比较 response 不足以发现"写入了错误的值"或"写入了错误的 DID"。

---

## 10. Golden 未被修改

```
git diff -- labs/vehicle/uds-basic/tests/golden/   →  无 diff
```

## 11. A0 hash unchanged

```
d0d83244c85d7916924227c09dc3565ce33e5534e485650aa1f32efbd520cf79   （前后一致）
```

## 12. B0 hash unchanged

```
0ad204331c390a00f0906d294f6a8024a048082fb2266a69ff07e428b966f974   （前后一致）
```

**未重新捕获、未 regenerate、未以当前 spec execution 重算 expected。**

### 反 self-vs-self

B2 测试有一处专门用例把 ``c1_golden_2e_lib.build_corpus()`` 替换为抛异常，
若 replay 路径以任何方式依赖"当前执行产生 oracle"，该用例**立刻失败**。
（B0 的 70 条 case 输入空间由 ``build_cases()`` 提供，但 **expected 只来自磁盘上的冻结文件**。）

---

## 13. Production diff

```
production files changed = 0
```

`uds_server.py` / `ecu.py` / `gateway.py` / `gateway_runtime.py` / `service_spec.py`
**全部 UNCHANGED**（blob 逐一比对）。

0x22 / 0x27 行为未被本阶段改变。

### 本阶段改动文件

```
labs/vehicle/uds-basic/tests/test_c1_phase2_b2_0x2e_golden.py   新增（20 例）
labs/vehicle/uds-basic/tests/run_tests.sh                      +8（接入）
docs/OQ_C1_PHASE2_B2_0X2E_GOLDEN_REPLAY_2026-09-25.md          本报告
```

---

## 14. 执行的测试

| 测试 | 结果 |
|---|---|
| `test_c1_phase2_b2_0x2e_golden.py` | **20/20 PASS**（新增） |
| `test_c1_phase2_b0_0x2e_golden.py` | **30/30 PASS** |
| `test_c1_phase2_a0_golden.py` | **26/26 PASS** |
| `test_c1_phase2_framework_unit.py` | **56/56 PASS** |
| `test_c1_service_spec_unit.py` | **28/28 PASS** |
| `test_c1_contract_pins.py` | **29/29 PASS** |
| UDS unit | **862**（842 + 20；17/17 文件，FAIL 0） |
| UDS e2e | **234**（12 项，FAIL 0） |
| `qa_fast.sh` | **PASS**（25 项 OK / 0 FAIL，1165 用例） |

### 职责分工保持不变

```
B0 = frozen corpus / 定义完整性（结构性，**不**执行当前 dispatcher）
B2 = legacy oracle ↔ 当前 spec execution 的等价性（本文件）
```

B0 测试**未**被改回 `fresh_capture_equals_stored_corpus`，也未与 B2 重复承担 replay oracle。

### 变异验证（/tmp 副本，未触碰仓库）—— 证明 B2 测试非空断言

```
baseline green                              = True
M1 篡改 golden legacy_response              -> DETECTED
M2 篡改 golden legacy_final_state           -> DETECTED
M3 replay 跳过第一条 case                   -> DETECTED
M4 replay 重复执行第一条 case               -> DETECTED
```

---

## 15. CI

PR 上运行 Fast QA（ruff + mypy + offline tests）；结果记录于 PR。

---

## 16. 结论

| 判定项 | 结果 |
|---|---|
| B0 70/70 全部执行 | ✓ |
| 执行顺序与 corpus 完全一致 | ✓ |
| 每 case 恰好执行一次（无重复 / 无跳过） | ✓ |
| response bytes 与 legacy oracle 完全一致 | ✓ **70/70** |
| relevant final state 与 legacy oracle 完全一致 | ✓ **70/70** |
| Golden 文件未变（A0 / B0 SHA256 一致） | ✓ |
| 未修改任何生产代码 | ✓ |
| 0x22 / 0x27 行为未被改变 | ✓ |

```
B2 = PASS
```

**0x2E 的 legacy inline 实现与 ServiceSpec guard→handler 执行路径在 70 条 case 上逐字节等价。**

## 17. 下一步

```
B3 = NOT STARTED
B4 = NOT STARTED
C2 = NOT STARTED
C3 = NOT STARTED

0x22 = ServiceSpec execution
0x2E = ServiceSpec execution
0x27 = unchanged

Migration B = IN PROGRESS
```

**B2 结束后不自动进入 B3。**
