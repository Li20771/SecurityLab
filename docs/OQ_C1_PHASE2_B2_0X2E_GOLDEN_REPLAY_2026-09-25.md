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

**行尾规范化后的文件内容 SHA-256（平台无关）**：

```
B0  5b7d7e43f79590c8282937631764d2f4824f9c47c8a9134dc29a8a2b1d19f642
A0  604d906e21906e2e6148977b5d5c162bdfb4ca41020fc51e610e42d3c8d07f8d
```

> **这是"文件内容（CRLF→LF 规范化）的 SHA-256"，不是 Git 对象哈希。**
> Git 的 blob 对象 ID 是在对象头 + 内容上另算的（本 corpus 为
> `f249612b101b5e0d79f719dfee9ac411e26e8a78`），与上表不是同一个量。
> 本文所有 hash 均指**前者**。

两个哈希在 B2 前后**完全一致**（见 §10–§12）。
两个 SHA256 已**硬编码进 B2 测试**，使"任何人改写 corpus"成为一个**立刻失败**的事实 ——
B2 的 expected 只有在文件未变时才有意义。

> **⚠ 关于哈希口径的更正（由 CI 发现，见 §15）**
>
> 本文件与更早的报告曾给出另一组值
> （`0ad20433…` / `d0d83244…`）。那组值是**工作区 CRLF 字节**的 SHA-256 ——
> Windows 的 `core.autocrlf=true` 会把仓库中的 LF 内容以 CRLF 写到工作区，
> 因此它**是检出平台的产物，不是仓库内容**。
>
> 那组值在更早各阶段被用于"前后一致"的**比较**（同环境下 before/after），
> 比较结论有效；但作为**绝对值**引用时是平台相关的，故此处更正为
> 规范化口径。B2 测试现在对**LF 规范化后的文件内容**求 SHA-256，因此在
> LF（Linux/WSL 原生检出）与 CRLF（Windows autocrlf 检出）下结果一致。

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
604d906e21906e2e6148977b5d5c162bdfb4ca41020fc51e610e42d3c8d07f8d   （前后一致）
```

## 12. B0 hash unchanged

```
5b7d7e43f79590c8282937631764d2f4824f9c47c8a9134dc29a8a2b1d19f642   （前后一致）
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

## 15. CI（含一次由 CI 发现并修复的真实缺陷）

PR #39 第一次 CI 运行 **FAIL**，且**只在 CI 失败、本地 WSL 全绿**。
按 §7 的要求"不修改 Golden、不放宽断言、定位实际差异"，先查日志再结论：

```
FAIL: test_a0_corpus_sha256_unchanged
FAIL: test_b0_corpus_sha256_unchanged
AssertionError: '604d906e…' != 'd0d83244…'
AssertionError: '5b7d7e43…' != '0ad20433…'
FAILED (failures=2)        ← 20 例中仅 2 例失败
```

**根因**：第一版把 `_sha256` 直接作用在**工作区字节**上。Windows 的
`core.autocrlf=true` 把仓库中的 LF 内容以 CRLF 写到工作区，于是：

| corpus | 工作区(CRLF) | 规范化(LF) |
|---|---|---|
| A0（0x22） | `d0d83244…` | `604d906e…` |
| B0（0x2E） | `0ad20433…` | `5b7d7e43…` |

我硬编码的是**左列**（检出平台产物），而 Linux CI 算出的是**右列**（仓库内容）。
这是一个**平台相关的断言缺陷** —— 正是 Issue #28 的同一环境现象。

**修复**：`_sha256` 先把 CRLF 折成 LF 再哈希，常量改用右列值。
修复后本地验证 LF 与 CRLF 检出下哈希**完全一致**。

**关键**：CI 上**只有这 2 例失败**，B2 的其余 18 例（含
`test_replay_response_bytes_match` 70/70、`test_replay_state_projection_matches` 70/70、
`test_every_case_is_executed_exactly_once_in_order`、
`test_replay_does_not_recompute_expected_from_current_execution`）
**全部通过** —— 即 **B2 的等价性结论在 CI 上成立**，出错的只是冻结哈希的口径。

修复后的 CI 结果见 PR #39。

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
