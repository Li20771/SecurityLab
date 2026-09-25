# OQ C1 Phase 2 — B4 Inline Defensive-Check Audit（证据报告）

> 状态：**B4 = PASS** → **Migration B = COMPLETE**
> 日期：2026-09-25
> 分支：`audit/oq-c1-phase2-b4-inline-checks-20260925`
> 基线：`main = a175ec24e9136aedd65d4c9b4324dab86d62899f`（PR #40 merge commit）
> 设计出处：`docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md` §10.4
> A4 证据：`docs/OQ_C1_PHASE2_A4_INLINE_CHECKS_2026-09-25.md`

**本报告只记录审计事实。B4 是审计阶段，不是功能开发阶段。**

---

## 1. Scope

Migration B 的**最终 inline defensive-check 审计**。

本阶段回答的唯一问题：

> 当 dispatcher / ServiceSpec guard 被**绕过**、直接调用 handler 时，
> handler 自己是否仍然保持原有的 defensive authorization / capability checks？

即 §10.4 冻结的两层分工：

```
    Dispatcher → ServiceSpec guards → handler → handler inline checks

正常请求   ： guard → handler（两道都在）
直接调用   ： guard 被绕过 → **inline defense 仍必须阻止非法行为**（本报告证明）
```

**B4 不重新设计授权**，也不重新证明 dispatcher guard 的顺序
（那已由 B1 / B2 / B3 建立）。

---

## 2. Baseline

| 项 | 值 |
|---|---|
| `main` commit | `a175ec24e9136aedd65d4c9b4324dab86d62899f` |
| B4 分支 | `audit/oq-c1-phase2-b4-inline-checks-20260925` |
| B4 分支起点 | `a175ec24e9136aedd65d4c9b4324dab86d62899f`（= main） |
| `working tree`（开始前） | clean |

```
_SPEC_EXECUTION = frozenset({0x22, 0x2E})      （运行时实测）
0x22 = ON（guards = 1）    0x2E = ON（guards = 6）
```

`TestBypassMechanismIsReal.test_migration_context` 把上述时点固化进测试 ——
**两个服务都已迁移**，即 inline check 已不再是唯一防线，但仍必须存在。

---

## 3. 0x22

### A4 证据引用

A4 已对 `service_read_data_by_identifier()` 做过完整独立审计，结论：
**9 项 inline checks 全部保留**（length / DID 解析 / 逐 DID 有序循环 /
DID 存在性 / `0x31` / security / `0x33` / all-or-nothing / first-failure-wins）。
A4 报告同时记录 `production diff = 0`。

### 当前源码与 A4 audited 源码一致

```
uds_server.py  与 A4 时点逐字节相同（B4 的 production diff = 0，见 §8）
```

因此 A4 的 source-level 结论对当前 main 仍然成立。

### B4 的补充：把 A4 结论**固化为常驻测试**

A4 使用**一次性 audit script**（未进入测试体系）。B4 把同类断言写成**常驻测试**
（`TestSourceEvidence0x22` / `Test0x22DirectHandlerBypass`），
使该不变量从此进入常规回归网。

| 类别 | 内容 | 结果 |
|---|---|---|
| source-level | 9 项 pattern 在 handler 源码段内存在 | **PASS** |
| behavior-level | 直接调用 handler：畸形长度 `13` / 未知 DID `31` / 受保护 DID 锁定 `33` / 公开 DID 可读 / 解锁后受保护 DID 可读 / 两个顺序锚点 / 只读无副作用 | **PASS** |

---

## 4. 0x2E

Migration B 新迁移的服务，本阶段做**完整独立审计**。

| 类别 | 内容 | 结果 |
|---|---|---|
| source-level | 六道 inline rung 的 pattern 在 handler 源码段内存在 | **PASS** |
| behavior-level | 直接调用 handler 的 A–G case matrix | **PASS** |
| successful write | 直接调用下合法写入仍正常工作 | **PASS** |

---

## 5. Source Evidence

以 AST 精确定位两个 `FunctionDef` 的**源码段**，在其中断言各检查 pattern 存在
（`handler_segment()`）。**不是**全文件 grep。

### 0x22 —— `service_read_data_by_identifier()`

| 检查 | pattern | 结果 |
|---|---|---|
| request length | `len(request) < 3` | **YES** |
| DID 列表奇偶 | `(len(request) - 1) % 2` | **YES** |
| 逐 DID 有序处理 | `for offset in range(1, len(request), 2):` | **YES** |
| DID 解析 | `did = (request[offset] << 8) \| request[offset + 1]` | **YES** |
| DID 存在性（override 优先） | `did in state.did_overrides` / `DID_DATABASE.get(did)` | **YES** |
| 未知 DID → NRC | `if data is None:` → `NRC_REQUEST_OUT_OF_RANGE` | **YES** |
| security 检查 | `if state.security_level < required_security_level(did):` | **YES** |
| 权限不足 → NRC | `NRC_SECURITY_ACCESS_DENIED` | **YES** |
| all-or-nothing | `payload = bytearray()` + 单一正响应返回 | **YES** |

### 0x2E —— `service_write_data_by_identifier()`

| rung | 检查 | pattern | 结果 |
|---|---|---|---|
| ① `13` | 长度 | `len(request) < 4` | **YES** |
| ② `31` | DID 存在性 | `if did not in DID_DATABASE:` | **YES** |
| ③ `7F` | 会话 | `if state.session == uds_types.SESSION_DEFAULT:` | **YES** |
| ④ `33` | 安全等级 | `if state.security_level < uds_types.SECURITY_LEVEL1:` | **YES** |
| ⑤ `22` | 可写性 | `if did not in WRITABLE_DIDS:` | **YES** |
| ⑥ `13` | 数据长度 | `WRITABLE_DID_LENGTHS[did]` + `min_len <= len(data) <= max_len` | **YES** |

> rung③ 的写法仍是 OD-5 冻结的 `== SESSION_DEFAULT`，**未**被改写成 `not in {...}` ——
> 由 `test_rung3_session_check_keeps_frozen_writing` 显式锁定。

### handler 不承担"guard 已检查过"的假设

两个 handler 的源码段内均**不含** `_guards_for` / `_guard_*` / `resolve_service_spec`
（由 `test_handler_has_no_assumption_that_guards_already_ran` 断言）。
即所有判定条件在函数体内**独立求值**。

### 与 dispatcher guard 的关系

```
guard   = externally enforced authorization / validation gate   （§10.4）
handler = defensive invariant
```

B4 **未**修改 guard、**未**新增 guard、**未**重排 guard、**未**改 handler 语义。

---

## 6. Behavioral Evidence

**关键前提**：`direct_handler()` 直接取 `SERVICES[sid]` 并调用，
**不经过** `handle_uds_request` / `resolve_service_spec` / `_guards_for`。

### 绕过是真的（`TestBypassMechanismIsReal`）

把 `resolve_service_spec` / `_guards_for` / `handle_uds_request` 全部替换为
**抛异常的 mock** 之后，直接调用仍正常工作（合法请求返回 `6E F1 02`，
非法请求返回 `7F 2E 31`）。这排除了"所谓的 direct 测试其实又走了一遍 dispatcher"。

### 0x2E case matrix

| Case | 状态 | 请求 | 期望 NRC | 实际 | 副作用 |
|---|---|---|---|---|---|
| A | DEFAULT 会话 | `2E F1 02 58` | `7F` | **`7F 2E 7F`** | `did_overrides` 不变 |
| B | security 不足 | `2E F1 02 58` | `33` | **`7F 2E 33`** | 不变 |
| C | 只读 DID（`F190`/`F180`/`F187`） | `2E F1 90 58` 等 | `22` | **`7F 2E 22`** | 不变 |
| D | 未知 DID（DEFAULT+LOCKED） | `2E FF FF 58` | `31` | **`7F 2E 31`** | 不变 |
| E | 畸形长度（`2E` / `2E F1` / `2E F1 02`） | — | `13` | **`7F 2E 13`** | 不变 |
| F1 | 数据长度 0（线上长度 3） | `2E F1 02` | `13`（由 rung① 拦下） | **`7F 2E 13`** | 不变 |
| F2 | 数据长度 > 16 | `2E F1 02` + 17/24 字节 | `13`（rung⑥） | **`7F 2E 13`** | 不变 |
| **G** | **合法写入** | `2E F1 02 4F 4B` | 正响应 | **`6E F1 02`** | **`did_overrides[F102] = b"OK"`** |

补充锁定：

* 闭区间端点长度 1 与 16 均为合法写入，且写入值与 payload 一致；
* `did_overrides` 预置**不**使只读 DID（`22`）或未知 DID（`31`）变得可写
  —— inline 判定只看 `DID_DATABASE` / `WRITABLE_DIDS`，与 override 无关；
* inline rung 顺序在 direct 调用下同样保持 `13 → 31 → 7F → 33 → 22 → 13`。

### 0x22 case matrix（最小 smoke）

| 请求 | 状态 | 期望 | 实际 |
|---|---|---|---|
| `22` / `22 F1` / `22 F1 90 F1` | EXTENDED+LOCKED | `13` | **`7F 22 13`** |
| `22 FF FF` | EXTENDED+LOCKED | `31` | **`7F 22 31`** |
| `22 F1 87` | EXTENDED+LOCKED | `33` | **`7F 22 33`** |
| `22 F1 90` | EXTENDED+LOCKED | 正响应 | **`62 …`** |
| `22 F1 87` | EXTENDED+LEVEL1 | 正响应 | **`62 …`** |
| `22 F1 87 FF FF` / `22 FF FF F1 87` | EXTENDED+LOCKED | `33` / `31` | **`7F 22 33` / `7F 22 31`** |
| 只读请求 | — | 无副作用 | `did_overrides` / `session` / `security_level` 均不变 |

### `ServiceSpec` guards **不是**唯一边界

`TestInlineDefenseIsIndependentOfSpecModel` 在不引用 `SERVICE_SPECS` /
`_guards_for` 的前提下，仅凭 handler 自身即拒绝全部 6 类非法 `0x2E` 请求；
并断言 `SERVICES[sid] is SERVICE_SPECS[sid].handler` ——
即被审计的就是**生产注册表里的那个 handler**，不是测试替身。

---

## 7. Golden Integrity

LF 规范化后的文件内容 SHA-256：

```
A0  604d906e21906e2e6148977b5d5c162bdfb4ca41020fc51e610e42d3c8d07f8d   MATCH（B4 前后一致）
B0  5b7d7e43f79590c8282937631764d2f4824f9c47c8a9134dc29a8a2b1d19f642   MATCH（B4 前后一致）

git diff -- labs/vehicle/uds-basic/tests/golden/   → 无 diff
```

**未修改任何 golden。**

---

## 8. Production Integrity

```
production diff = 0
    uds_server.py / ecu.py / gateway.py / gateway_runtime.py / service_spec.py  全 UNCHANGED
```

**未修改**：handler / guard / `SERVICE_SPECS` / `_SPEC_EXECUTION` / 任何 runtime 逻辑。
**未新增** guard，**未重排** guard。

---

## 9. Test Results

| 测试 | 结果 |
|---|---|
| `test_c1_phase2_b4_inline_checks.py` | **39/39 PASS**（新增） |
| UDS unit | **901**（862 + 39；18/18 文件，FAIL 0） |
| UDS e2e | **234**（12 项，FAIL 0；含 `0x2E/0x3E write-DID`） |
| `qa_fast.sh` | **PASS**（26 项 OK / 0 FAIL，1204 cases；ruff 0 / mypy 0） |

> 按设计 §18，**未**在 B4 重复 B3 的 full regression —— B3 已建立该证据。

### 变异验证（/tmp 副本，未触碰仓库）—— 证明审计非空断言

```
baseline green                            = True
M1 删除 0x2E inline session 检查          -> DETECTED
M2 删除 0x2E inline writability 检查      -> DETECTED
M3 删除 0x22 inline security 检查         -> DETECTED
```

即：**source 层的存在性断言**与 **behavior 层的绕过断言**都会在防御被移除时失败。

### 一次自身测试缺陷（如实记录）

首次运行时 `test_read_has_no_side_effect` 失败：helper `make_state()` 默认
`SECURITY_LEVEL1`，而该用例断言 `SECURITY_LOCKED`。属**测试自身缺陷**
（B3 §12 分类中的 Type B），**非代码回归**。已修正 helper 调用并复跑通过。

---

## 10. CI

PR 上运行 Fast QA（ruff + mypy + offline tests）；结果记录于 PR。

---

## 11. Conclusion

| # | 门槛 | 结果 |
|---|---|---|
| 1 | 0x22 inline checks remain present | ✓ |
| 2 | 0x22 direct-handler defense remains valid | ✓ |
| 3 | 0x2E inline checks remain present | ✓ |
| 4 | 0x2E direct-handler bypass cases all pass | ✓ |
| 5 | 0x2E valid write still works | ✓ |
| 6 | no unexpected side effects | ✓ |
| 7 | production diff = 0 | ✓ |
| 8 | golden diff = 0 | ✓ |
| 9 | A0 / B0 hashes unchanged | ✓ |
| 10 | relevant unit tests pass | ✓ |
| 11 | CI pass | ✓ |

```
B4 = PASS
```

**结论**：`ServiceSpec` guards **不是** handler 的唯一安全边界。
即使在 dispatcher / resolver / guards 被完全绕过的情况下，
两个 handler 仍凭自身 inline checks 拒绝全部非法请求，且不产生不应有的副作用。

### A4 历史 finding 保留（未抹去）

A4 曾记录：**handler 独立 capability 覆盖不是长期既有 unit test**，
A4 使用**一次性 audit script** 验证。

**B4 保留该历史事实**，并**在此之上**把同类断言写成**常驻测试**
（`test_c1_phase2_b4_inline_checks.py`，39 例），使之成为 migration-final 的
长期回归网的一部分。**不**把 A4 的一次性 audit 伪装成"长期成熟测试体系"。

---

## 12. Phase 2 Final Status

```
Migration A = COMPLETE
    A0 = PASS   A1 = PASS   A2 = PASS   A3 = PASS   A4 = PASS

Migration B = COMPLETE
    B0 = PASS   B1 = PASS   B2 = PASS   B3 = PASS   B4 = PASS

0x22 = ServiceSpec execution
0x2E = ServiceSpec execution
0x27 = unchanged

C2 = NOT STARTED
C3 = NOT STARTED
```

**B4 结束后不进入 C2 / C3。**
