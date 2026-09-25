# C1 Phase 2 Migration A4 —— legacy inline defensive checks 保留确认（审计报告）

> 状态：**A4 = PASS** → **Migration A = COMPLETE**
> 日期：2026-09-25
> 分支：`audit/oq-c1-phase2-a4-inline-checks-20260925`
> 基线：`main = be45d0240ce1f701c001a58876189f51817159db`（PR #35 merge commit）
> 上游设计：`docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md` §10.4 / §11.1（A4）

**本报告只记录审计事实，不引入新的设计决策。**

A4 是 Migration A 的**最后一个阶段**，也是**确认步骤而非变更步骤**：
按 §10.4，`service_read_data_by_identifier()` 与 `service_write_data_by_identifier()`
内的 inline 防御性检查**必须继续保留**。因此 A4 的正确结果**不是**"删除重复代码"，
而是**证明这些检查仍然存在且仍然有效**。

---

## 12.1 基线

| 项 | 值 |
|---|---|
| `main` commit | `be45d0240ce1f701c001a58876189f51817159db` |
| A3 PR #35 merge commit | `be45d0240ce1f701c001a58876189f51817159db` |
| A4 分支 | `audit/oq-c1-phase2-a4-inline-checks-20260925` |
| A4 分支 HEAD（审计时） | `be45d0240ce1f701c001a58876189f51817159db`（= main，无生产改动） |
| `working tree` | clean |

### C1 迁移状态（运行时实测）

```
_SPEC_EXECUTION = frozenset({0x22})
0x22 = ON        （_guards_for() 返回其 1 个 guard）
0x2E = OFF       （_guards_for() 返回 ()）
0x27 = unchanged （∉ SERVICE_SPECS、∈ SPECS_EXCLUDED）
```

---

## 12.2 0x22 handler —— `service_read_data_by_identifier()`（def @ `uds_server.py:527`）

| 检查 | inline 是否保留 | 证据（源码行） |
|---|---|---|
| request length（`len<3` 或 DID 列表奇数） | **YES** | `:545` `if len(request) < 3 or (len(request) - 1) % 2 != 0:` |
| DID 解析 | **YES** | `:555` `did = (request[offset] << 8) \| request[offset + 1]` |
| 逐 DID 有序处理 | **YES** | `:554` `for offset in range(1, len(request), 2):` |
| DID 存在性（override 优先，其次出厂库） | **YES** | `:559` `if did in state.did_overrides:` / `:562` `data = DID_DATABASE.get(did)` |
| 未知 DID → NRC | **YES** | `:564` `if data is None:` → `:566` `NRC_REQUEST_OUT_OF_RANGE`（`0x31`） |
| security 检查 | **YES** | `:569` `if state.security_level < required_security_level(did):` |
| 权限不足 → NRC | **YES** | `:575` `NRC_SECURITY_ACCESS_DENIED`（`0x33`） |
| all-or-nothing（独立缓冲区累积，不返回部分数据） | **YES** | `:552` `payload = bytearray()`；仅 `:581` 一处正响应返回 |
| first-failure-wins（首个失败 DID 决定 NRC） | **YES** | 循环内 `return` 短路（`:564` / `:569`） |

**handler 未承担任何"guard 已检查过"的假设**：所有判定条件在函数体内独立求值。

---

## 12.3 0x2E handler —— `service_write_data_by_identifier()`（def @ `uds_server.py:726`）

| rung | 检查 | inline 是否保留 | 证据（源码行） |
|---|---|---|---|
| ① `0x13` | 长度 `len(request) < 4` | **YES** | `:747` |
| ② `0x31` | DID 未知 `did not in DID_DATABASE` | **YES** | `:755` |
| ③ `0x7F` | DEFAULT 会话 `state.session == SESSION_DEFAULT` | **YES** | `:760` |
| ④ `0x33` | 安全等级 `< SECURITY_LEVEL1` | **YES** | `:765` |
| ⑤ `0x22` | 不可写 `did not in WRITABLE_DIDS` | **YES** | `:770` |
| ⑥ `0x13` | 数据长度越界 `WRITABLE_DID_LENGTHS[did]` 闭区间 | **YES** | `:776` + `:777` |

六道 rung 的**顺序**与冻结语义 `13 → 31 → 7F → 33 → 22 → 13` 一致，未被重排。

> 注：0x2E 的 handler 在 A4 时点**尚未**进入 spec 执行路径（`0x2E ∉ _SPEC_EXECUTION`），
> 因此它当前**就是**唯一生效的实现；A4 确认其未被任何人提前清理。

---

## 12.4 Git 历史证据（§7 方法 A）

```
git diff --stat main...HEAD   ->  空（0 改动）
git diff main...HEAD | wc -l  ->  0
```

A4 分支在撰写本报告前**没有任何 commit**，即相对 main **零差异**。
这是"生产代码 / 测试 / golden 均未改动"的最强证据。

### 冻结 blob（A4 分支 vs `origin/main`，逐文件比对）

| 文件 | blob（前 12 位） | 状态 |
|---|---|---|
| `ecu/uds_server.py` | `66e249714f12` | IDENTICAL |
| `ecu/ecu.py` | `710d6515e1e5` | IDENTICAL |
| `ecu/gateway.py` | `e64cd088fc86` | IDENTICAL |
| `ecu/gateway_runtime.py` | `85604271db06` | IDENTICAL |
| `ecu/service_spec.py` | `128f8e5c2875` | IDENTICAL |
| `tests/test_c1_contract_pins.py` | `46f14b13d94c` | IDENTICAL |
| `tests/golden/c1_phase2_0x22_legacy.json` | `85c28dbdd3dd` | IDENTICAL |

---

## 12.5 源码级 + 行为级审计（§7 方法 B）

一次性审计脚本（**仅用于 A4 验证，未进入仓库测试体系**）对
`uds_server.py` 做两件事：

1. **源码级**：用 AST 定位两个 `FunctionDef`，在**其源码段内**检查 24 个
   与上述检查一一对应的 pattern；
2. **行为级**：**直接调用** `SERVICES[0x22]` / `SERVICES[0x2E]` 的 handler
   （**完全绕过 dispatcher 与 guards**），对 12 条非法请求断言 NRC。

### 源码级结果

```
0x22 handler (line 527):  12 / 12 pattern  YES
0x2E handler (line 726):  12 / 12 pattern  YES
```

（逐项对应见 12.2 / 12.3 表格的"证据"列。）

### 行为级结果 —— 直接调用 handler，绕过 dispatcher / guards

| 请求 | 状态 | 直接调用结果 |
|---|---|---|
| `22`（len<3） | EXTENDED+LOCKED | `7F 22 13` |
| `22 F1 90 F1`（DID 列表奇数） | EXTENDED+LOCKED | `7F 22 13` |
| `22 FF FF`（未知 DID） | EXTENDED+LEVEL1 | `7F 22 31` |
| `22 F1 87`（受保护） | EXTENDED+LOCKED | `7F 22 33` |
| `22 F1 87 FF FF`（受保护在前） | EXTENDED+LOCKED | `7F 22 33` |
| `22 FF FF F1 87`（未知在前） | EXTENDED+LOCKED | `7F 22 31` |
| `2E F1`（len<4） | EXTENDED+LOCKED | `7F 2E 13` |
| `2E FF FF 58`（未知 DID） | DEFAULT+LOCKED | `7F 2E 31` |
| `2E F1 02 58`（DEFAULT 会话） | DEFAULT+LOCKED | `7F 2E 7F` |
| `2E F1 02 58`（未解锁） | EXTENDED+LOCKED | `7F 2E 33` |
| `2E F1 90 58`（只读 DID） | EXTENDED+LEVEL1 | `7F 2E 22` |
| `2E F1 02` + 17 字节（数据过长） | EXTENDED+LEVEL1 | `7F 2E 13` |

**12 / 12 与期望一致。**

---

## 12.6 §10 关键审计问题的回答

> 如果未来有人把 `_guard_read_did()` 和 0x2E guards 当作唯一保护，
> 然后**直接调用 legacy handler**，handler 本身是否仍然能够拒绝非法请求？

```
direct handler call  →  长度 / DID 存在性 / session / security / 可写性 / 数据长度
                        全部仍有独立防御

答案 = YES
```

依据：12.5 的**行为级直接调用**结果（12/12 正确拒绝），
且该路径**完全绕过** `resolve_service_spec()` / `_guards_for()` / guard 链。
§10.4 冻结的
`guard = externally enforced gate` / `handler = defensive invariant`
分工**成立**。

---

## 12.7 测试

| 测试 | 结果 |
|---|---|
| `scripts/qa_fast.sh` | **PASS**（23 项 OK / 0 FAIL，1100 用例） |
| `test_ecu_unit.py` | **116/116 PASS** |
| `test_services_extra_unit.py` | **17/17 PASS** |

**未重复执行 A3 的五层全量回归** —— A3 已完成（UDS 1031 / CAN 358 /
qa_fast / qa_extended / healthcheck），且 A4 相对 main **零改动**，
无重复执行的依据。

### ⚠ 覆盖性说明（诚实记录，不虚构覆盖）

**上述两个 unit 测试均经 dispatcher，而非直接调用 handler**：

* `test_ecu_unit.py` 从 `ecu` import `handle_uds_request`（入口包装）；
* `test_services_extra_unit.py` 从 `uds_server` import `handle_uds_request`。

由于 `_SPEC_EXECUTION = {0x22}`，**经 dispatcher 的 0x22 请求会先经过 guards**
（`resolve_service_spec(0x22, None)` 的 guard 链长度为 1）。因此：

> **"handler 自身的独立防御能力"这一静态属性，当前没有被任何常驻测试覆盖。**
> 它由本次 A4 的一次性审计脚本（12.5 行为级）验证。

这是 **A4 的审计发现**，不是缺陷：按 §10.4，该属性本就属于"防御性不变式"，
其价值恰恰在于**不依赖** guard 路径。若未来需要常驻覆盖，
应作为独立设计项另开（**A4 不做**，见 §11 禁止事项）。

---

## 12.8 最终判定

| 判定项 | 结果 |
|---|---|
| 0x22 inline checks preserved | **YES**（12/12 源码级 + 直接调用行为验证） |
| 0x2E inline checks preserved | **YES**（6/6 rung，顺序未变） |
| production code unchanged | **YES**（`diff main...HEAD` = 0） |
| tests unchanged | **YES** |
| golden unchanged | **YES**（`85c28dbdd3dd`） |
| `_SPEC_EXECUTION` unchanged | **YES**（`frozenset({0x22})`） |
| `qa_fast` | **PASS** |

```
A4 = PASS
Migration A = COMPLETE
```

### 本阶段范围

```
production files changed = 0
test files changed       = 0
golden changed           = NO
_SPEC_EXECUTION changed  = NO
```

**未做**：删除两个 handler 的 inline checks / 把 handler 改成"只相信 guard" /
合并 guard 与 handler / 重构两个 handler / 修改 29 pins / 修改 golden /
修改 `SERVICE_SPECS` / 修改 `_SPEC_EXECUTION` / 开启 0x2E / 修改正式测试体系。

---

## 12.9 Migration A 收束

```
A0 = PASS   捕获 0x22 legacy golden（92 条）
A1 = PASS   _SPEC_EXECUTION = {0x22}，0x22 切到 ServiceSpec execution
A2 = PASS   92/92 response + 92/92 state 逐字节对拍
A3 = PASS   全量回归闸门（UDS 1031 / CAN 358 / qa_fast / qa_extended / healthcheck）
A4 = PASS   legacy inline defensive checks 保留确认

Migration A = COMPLETE

0x22 = ServiceSpec execution
0x2E = legacy execution
0x27 = unchanged
```

**下一步**：Migration B（0x2E）**尚未开始**，且需先完成其 B0 golden capture。
本阶段结束后**未**进入 B0 / Migration B / C2 / C3。
