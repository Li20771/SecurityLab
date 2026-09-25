# OQ C1 Phase 2 — Final Exit Review

> 状态：**C1 Phase 2 = COMPLETE**
> 日期：2026-09-25
> 基线：`main = 6684cbc467c9e2a1320884a721f4e475e2ab6a29`（PR #41 merge commit）
> 设计文档：`docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md`（§17 Exit Criteria）
> 本文档性质：**证据索引 + exit-criteria reconciliation**。不引入新设计、不定义新测试体系。

---

## 1. Scope

C1 Service Authorization —— **Migration A + Migration B**。

```
C1 = ECU 服务授权            ← 本 Phase 2 范围
C2 = Gateway security policy ← NOT STARTED
C3 = 0x27 through Gateway    ← NOT STARTED
```

---

## 2. Final Runtime State

```
_SPEC_EXECUTION = frozenset({0x22, 0x2E})      （uds_server.py:2397；运行时实测 {34, 46}）

0x22 = ON   （_guards_for() 返回 1 个 guard，实际被求值）
0x2E = ON   （_guards_for() 返回 6 个 guard，实际被求值）
0x27 = unchanged
        0x27 ∉ SERVICE_SPECS          ✓
        0x27 ∈ SPECS_EXCLUDED         ✓
        resolve_service_spec(0x27, None) is None   ✓
```

已建模 SID 共 **14** 个；其中 **2** 个已迁移（0x22 / 0x2E），**12** 个仍走 legacy 逐字路径。

---

## 3. Migration A（0x22）

| 阶段 | 内容 | PR / commit | 证据 |
|---|---|---|---|
| **A0** | 捕获 0x22 legacy golden（**92 条**） | PR #31 · `e699803` | `tests/golden/c1_phase2_0x22_legacy.json`、`capture_c1_phase2_a0_golden.py`、`c1_golden_lib.py` |
| **A1** | 启用 0x22 的 ServiceSpec execution | PR #32 · `5d4f7eb` | `test_c1_phase2_framework_unit.py::TestA1_0x22GuardActivation` |
| **A2** | 92 条逐字节对拍门禁 | PR #33 · `dfee964` / `73ecd00` | `test_c1_phase2_a0_golden.py::TestGoldenReplay` / `TestA2ReplayGateIsMeaningful` |
| **A3** | Full regression | PR #35 · `973b4e2` | `docs/OQ_C1_PHASE2_A3_FULL_REGRESSION_2026-09-25.md` |
| **A4** | Inline defensive-check 审计 | PR #36 · `140b950` | `docs/OQ_C1_PHASE2_A4_INLINE_CHECKS_2026-09-25.md` |

**A0 / A1 / A2 没有独立的报告文档** —— 其证据载体是**代码 + 常驻测试 + PR**
（如上表），本表即为其索引。此处如实说明，不虚报文档存在。

```
A0 = PASS   A1 = PASS   A2 = PASS   A3 = PASS   A4 = PASS
```

---

## 4. Migration B（0x2E）

| 阶段 | 内容 | PR / commit | 证据 |
|---|---|---|---|
| **B0** | 捕获 0x2E legacy golden（**70 条**） | PR #37 · `6173539` | `docs/OQ_C1_PHASE2_B0_0X2E_GOLDEN_2026-09-25.md`、`c1_golden_2e_lib.py`、`golden/c1_phase2_0x2e_legacy.json` |
| **B1** | 启用 0x2E 的 ServiceSpec execution | PR #38 · `338655f` | `docs/OQ_C1_PHASE2_B1_0X2E_SPEC_EXECUTION_2026-09-25.md`、`TestB1_0x2EGuardActivation` |
| **B2** | 70 条 legacy ↔ spec 逐字节对拍 | PR #39 · `ccb2cf4` / `1ead01d` / `d730a10` | `docs/OQ_C1_PHASE2_B2_0X2E_GOLDEN_REPLAY_2026-09-25.md`、`test_c1_phase2_b2_0x2e_golden.py` |
| **B3** | Full regression | PR #40 · `3568452` | `docs/OQ_C1_PHASE2_B3_FULL_REGRESSION_2026-09-25.md` |
| **B4** | Inline defensive-check 审计 | PR #41 · `a7c3afc` | `docs/OQ_C1_PHASE2_B4_INLINE_CHECKS_2026-09-25.md`、`test_c1_phase2_b4_inline_checks.py` |

```
B0 = PASS   B1 = PASS   B2 = PASS   B3 = PASS   B4 = PASS
```

---

## 5. Golden Integrity

**LF 规范化后的文件内容 SHA-256（平台无关）**：

```
A0（0x22）  604d906e21906e2e6148977b5d5c162bdfb4ca41020fc51e610e42d3c8d07f8d
B0（0x2E）  5b7d7e43f79590c8282937631764d2f4824f9c47c8a9134dc29a8a2b1d19f642
```

> 这是"文件内容（CRLF→LF 规范化）的 SHA-256"，**不是 Git 对象哈希**（后者的对象 ID
> 在对象头 + 内容上另算，是另一个量）。

整个 Phase 2 期间，两个 golden **只在 B0/A0 捕获时被创建，此后从未被修改**：

```
git log --oneline 80ed246..HEAD -- labs/vehicle/uds-basic/tests/golden/
    6173539 test(c1): capture 0x2e legacy golden corpus
    e699803 test(c1): capture legacy golden corpus for 0x22
```

两次均为**新增**，**无任何后续修改**。

---

## 6. Regression（引用 B3，未重跑）

B3 已按 A3 建立的入口逐项重跑，覆盖面未减少。

| Gate | A3 历史 | B3 当时 | 当前（含 B4） |
|---|---|---|---|
| UDS unit | 797（15 文件） | 862（17 文件） | **901**（18 文件） |
| UDS e2e | 234（12 项） | 234（12 项） | **234**（12 项） |
| CAN-tools all | 358（7 项） | 358（7 项） | **358**（7 项） |
| qa_fast | 1100（23 项） | 1165（25 项） | **1204**（26 项） |
| qa_extended | PASS | PASS | PASS |
| healthcheck --extended | 78 / 2 / 0 | 78 / 2 / 0 | 78 / 2 / 0 |

```
code/test regression FAIL = 0
```

### 增量说明（**不得**误读为原始 regression 的增删）

```
A3 历史 → B3：+65，全部为 C1 Phase 2 新增测试
    framework  41 → 56   +15   （B1 TestB1_0x2EGuardActivation）
    B0 测试       — → 30  +30
    B2 测试       — → 20  +20
    验证：797 + 15 + 30 + 20 = 862 ✓

B3 → 当前：+39，为 B4 新增
    B4 测试       — → 39  +39
    验证：862 + 39 = 901 ✓
```

**e2e 与 CAN-tools 全程 Δ = 0**；**未删除任何既有测试**；
即：**原始基线用例全部通过，增量只来自新阶段测试**。

---

## 7. Contract / ServiceSpec

```
test_c1_contract_pins.py          29/29 PASS
test_c1_service_spec_unit.py      28/28 PASS
```

（两者在整个 Phase 2 期间**数量与断言均未变**。）

---

## 8. Architecture Constraints（OD-1 ~ OD-6）

| ID | 冻结决定 | 落地证据 |
|---|---|---|
| **OD-1** | per-service ordered guards；**禁止** global dispatcher authorization precheck | `_guards_for(spec)` 只返回**该 spec** 的 guard 链；dispatcher 无任何全局前置判定 |
| **OD-2** | `guards` 是**唯一执行源**；声明字段不参与执行 | `allowed_sessions` / `required_security` / `payload_scoped` 无任何执行点 |
| **OD-3** | 属 Phase 3；本 Phase 不引入新强制机制 | 未新增 runtime 检查 |
| **OD-4** | **方案 A′**：对外 API 不变；**实现可覆盖、授权不可覆盖** | B2 / B4 锁定：override 下 guards 保留（0x22 保 1、0x2E 保 6） |
| **OD-5** | 保持 `== SESSION_DEFAULT`，不得改写 | B4 `test_rung3_session_check_keeps_frozen_writing` 显式锁定 |
| **OD-6** | `0x27 ∉ SERVICE_SPECS`、`∈ SPECS_EXCLUDED` | 运行时实测 + 导入期护栏 |

---

## 9. Exit Criteria（§17 逐项）

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| **1** | `0x22` migration **byte-for-byte identical** | **PASS** | **92/92 response + 92/92 state**（A2 gate，当前复跑仍成立） |
| **2** | `0x2E` migration **byte-for-byte identical** | **PASS** | **70/70 response + 70/70 state**（B2 gate，当前复跑仍成立） |
| **3** | 29 contract pins **PASS** | **PASS** | 29/29 |
| **4** | Phase 1 service-spec tests（28）**PASS** | **PASS** | 28/28 |
| **5** | baseline regression **964 PASS** | **PASS** | 见下方说明 |
| **6** | Gateway behavior **unchanged** | **PASS** | `gateway.py` / `gateway_runtime.py` 在整个 Phase 2 **UNCHANGED**；相关 unit（multi-ECU routing、gateway runtime）+ e2e（gateway runtime daemon）全 OK |
| **7** | `0x27` injection behavior **unchanged** | **PASS** | `ecu.py` UNCHANGED；`_security_services()` / `_now()` 现场求值 / `SECURITY_CONFIG` 原对象引用 均未改动 |
| **8** | **no new session** | **PASS** | Phase 2 生产 diff 中 **无任何** session / `SUPPORTED_SESSIONS` / `session_type` 改动（逐行核对） |
| **9** | **no unrelated authorization policy** | **PASS** | Phase 2 生产 diff 仅涉及 `_guards_for` / `resolve_service_spec` / `_SPEC_EXECUTION` / dispatcher 接入；未新增无关授权政策、未改 session policy、未扩展 gateway policy |
| **10** | **no unresolved migration path** | **PASS** | 见下方两组成 |

### 判据 5 的准确表述

设计文档 §17 写的是 `baseline regression 964 PASS` —— 该数字是 **Phase 1 时期的历史基线**
（unit 730 + e2e 234）。Phase 2 期间新增了测试，因此**不得**把本轮结果记作 "964"：

```
historical design baseline   = 964    （unit 730 + e2e 234）
current executed baseline    = 1135   （unit 901 + e2e 234）
```

判据的实质是"**历史基线用例全部仍然通过**"，而非"数字必须仍是 964"。
验证：`901 − 56（framework）− 26（A0 golden）− 30（B0）− 20（B2）− 39（B4） = 730` ✓
（回到 Phase 1 unit 基线），且 e2e 全程稳定 234。**原始用例无一删除、无一失败。**

### 判据 10 的两个组成

**第一组成 —— `sid ∈ _SPEC_EXECUTION` ⟺ guards 在 dispatcher 中被求值**

对全部 14 个已建模 SID 逐项实测：

```
0x22  in_white=True   guards=1  evaluated=True    OK
0x2E  in_white=True   guards=6  evaluated=True    OK
其余 12 个  in_white=False  guards=0  evaluated=False  OK
→ 全部一致
```

**第二组成 —— `services=` 覆盖路径下 guards 求值必须仍发生（§6.5.1 收紧语义）**

```
0x22  override → handler 被替换、guards 保留 1 个   OK
0x2E  override → handler 被替换、guards 保留 6 个   OK
0x27  override（未建模 SID）→ guards = ()           OK
```

该语义由 **A1 / B1 / B4 的可执行测试**实际锁定（非仅声明）：

* A1 / B1：`test_override_does_not_disable_guard_for_enabled_service`、
  `TestB1_0x2EGuardActivation::test_override_cannot_bypass_guard_*`
* B4：`TestInlineDefenseIsIndependentOfSpecModel`

---

## 10. Known Warnings / Historical Findings（未抹去）

### 环境级（非代码回归）

| 项 | 内容 | 判定 |
|---|---|---|
| **Issue #28** | Windows/WSL `*.sh` CRLF 检出；表现为 healthcheck 的 `Git working tree has uncommitted changes` WARN | 环境级；测量期间以工作区规范化规避，**未修改仓库内容**，**未加 `.gitattributes`** |
| **Docker** | `docker installed but engine not reachable` | 环境级；本轮全部门禁均不依赖 Docker 引擎 |

两条与 A3 逐项相同（78 PASS / 2 WARN / 0 FAIL），**始终与代码 FAIL 分开记录**。

### 历史 finding（保留，不美化）

| 项 | 内容 |
|---|---|
| **A4 coverage finding** | A4 记录：handler 独立 capability 覆盖**不是**长期既有 unit test，A4 用**一次性 audit script** 验证。**B4 保留该事实**，并在此之上写成常驻测试（39 例）。**未**把一次性 audit 伪装成长期成熟测试体系。 |
| **B2 平台 hash 事故** | B2 首次 CI **FAIL**（本地 WSL 全绿）：第一版 `_sha256` 哈希工作区字节，Windows autocrlf 使其成为平台相关值。**由 CI 发现**，根因定位后修正为 LF 规范化口径（`1ead01d`），并更正历史报告中的哈希口径。 |
| **B4 自身测试缺陷** | `test_read_has_no_side_effect` 首次失败：helper `make_state()` 默认 LEVEL1 而断言写 LOCKED。属**测试自身缺陷**（Type B），非代码回归，已修正。 |
| **B2 哈希措辞** | `d730a10` 更正了"git blob 哈希"这一不准确表述。 |
| **e2e 瞬时启动超时** | A3 期间出现过 1 次（后经复跑排除为环境抖动）；B1–B4 **未复现**。 |

---

## 11. Production Integrity

### 整个 C1 Phase 2 的生产 diff（`80ed246` = PR #29 merge，Phase 2 起点）

```
仅 1 个文件：labs/vehicle/uds-basic/ecu/uds_server.py     +133 / -12
```

代码级改动全部为：

```
from dataclasses import dataclass, field, replace      （新增 replace）
__all__ += "resolve_service_spec"
_SPEC_EXECUTION: frozenset[int] = frozenset({0x22, 0x2E})
def _guards_for(spec) -> tuple[...]                     （新增）
def resolve_service_spec(sid, services) -> ...          （新增，纯函数）
handle_uds_request 主体：table/handler → resolve spec + guard loop
```

其余改动均为**注释 / docstring**（含 PR #34 `87f72e0` 的注释措辞同步 ——
该 commit **只改注释**，已由其 AST 对比证明逻辑零改动）。

```
ecu.py              UNCHANGED
gateway.py          UNCHANGED
gateway_runtime.py  UNCHANGED
service_spec.py     UNCHANGED
did_database.py     UNCHANGED
uds_types.py        UNCHANGED
```

```
golden diff（内容）= 0     （两个 corpus 仅被创建，此后从未修改）
session 模型改动   = 0
新增 guard         = 0
重排 guard         = 0
handler 语义改动   = 0
0x27 改动          = 0
```

---

## 12. Final Status

```
Migration A = COMPLETE
    A0 = PASS   A1 = PASS   A2 = PASS   A3 = PASS   A4 = PASS

Migration B = COMPLETE
    B0 = PASS   B1 = PASS   B2 = PASS   B3 = PASS   B4 = PASS

Exit Criteria §17 1–10 = PASS

C1 Phase 2 = COMPLETE

0x22 = ServiceSpec execution
0x2E = ServiceSpec execution
0x27 = unchanged

C2 = NOT STARTED
C3 = NOT STARTED
```

### Evidence gap

**无。** §17 的 10 条判据均有可执行的、已合并的证据支撑（见 §9 表内"证据"列）。

> 唯一需要说明的口径差异：判据 5 的字面数字 `964` 是 Phase 1 历史基线，
> 已在 §9 中给出准确表述与验算；这属**口径**问题，不是证据缺失。

**C1 Phase 2 结束后不进入 C2 / C3。**
