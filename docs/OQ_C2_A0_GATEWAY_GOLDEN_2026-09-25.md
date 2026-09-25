# OQ C2-A0 —— Legacy Gateway Golden Capture（证据报告）

> 状态：**C2-A0 = PASS**
> 日期：2026-09-25
> 分支：`audit/oq-c2-a0-gateway-golden-20260925`
> 基线：`main = 83d1f52d9b8fcc45ef6f325595c950159d360414`（PR #43 merge commit）
> 设计出处：`docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md`
> §3（当前 Gateway 行为）/ §13（迁移策略：**C2-A0 无 policy trace 要求**）

**本报告只记录捕获事实。A0 在 C2 policy 机制引入之前运行。**

---

## 1. Scope

**C2-A0 —— 把当前 Gateway 的 legacy 行为冻结成 Golden oracle。**

```
C2-A0  capture legacy Gateway golden      ← 本文档（已完成）
C2-A1  引入空策略 + trace mechanism       ← NOT STARTED
C2-A2  legacy ↔ policy 逐字节对拍          ← NOT STARTED
C2-A3  full regression                    ← NOT STARTED
C2-A4  override / bypass 不变量审计        ← NOT STARTED
C2-A5  exit review                        ← NOT STARTED
```

**本阶段未实现任何 policy** —— 包括设计文档已冻结的 `route_with_trace()`。

---

## 2. Baseline

| 项 | 值 |
|---|---|
| `main` commit | `83d1f52d9b8fcc45ef6f325595c950159d360414` |
| A0 分支 | `audit/oq-c2-a0-gateway-golden-20260925` |
| A0 分支起点 | `83d1f52d9b8fcc45ef6f325595c950159d360414`（= main） |
| `working tree`（开始前） | clean |
| C1 Phase 2 | COMPLETE（`_SPEC_EXECUTION = frozenset({0x22, 0x2E})`，本阶段未改） |

---

## 3. "Legacy Gateway" 的定义（本 Golden 的语义前提）

```
legacy Gateway =
    DiagnosticGateway.route(can_id, payload) -> list[RouteResult]
    在 **C2 policy 机制尚不存在** 时的真实可观测行为
```

具体地：`gateway.py` 中**没有** `PolicyTrace` / `route_with_trace` /
`policy_evaluate` / `GatewayPolicy` 任何符号。

该前提由**两层护栏**保证：

1. **捕获脚本 fail-closed**（`capture_c2_a0_gateway_golden.py`）：
   若检测到上述符号，**拒绝写入并 `exit 2`**；
2. **结构测试常驻断言**（`TestCaptureProvenance::test_policy_mechanism_still_absent_in_production`）：
   一旦生产代码引入 policy 符号，该用例立刻失败，
   使"A0 的 legacy 前提已改变"**不可能被悄悄忽略**。

---

## 4. Golden Schema

```
schema_version        = 1
description           = "C2-A0 —— legacy Gateway golden corpus"
captured_at_commit    = 83d1f52d9b8fcc45ef6f325595c950159d360414
gateway_mode          = "legacy"
policy_execution      = "absent"              ← A0 的前提
functional_address    = 2015 (0x7DF)
state_projection_fields = ["name", "session", "security_level", "did_overrides"]

cases: [
  {
    case_id, description,
    can_id, payload(hex),
    ecus: [ {name, request_id, response_id, handler, state{…}} ],   ← **完整初始配置**
    expected_response:      [[response_id, payload_hex], …],
    expected_gateway_state: { ECU_NAME: {session, security_level, did_overrides}, … }
  }, …
]
```

每条 case **自带完整 ECU 配置** ⇒ 可**独立 replay**，不依赖前一个 case 的执行结果。

### 与 C1 Golden 的关系

**独立 corpus、独立 schema version**，不复用 C1 的 loader
（C1 的 corpus 描述 ECU **内部**服务授权；C2 描述 Gateway **路由**行为，
两者的 case 形状不同）。Golden 目录结构沿用既有约定。

---

## 5. Case Matrix（20 条）

| 组 | case | can_id | payload | 期望响应 | 状态变化 | 该 case 冻结的事实 |
|---|---|---|---|---|---|---|
| **P** | `P1-physical-ecu-a` | `0x7E0` | `22 F1 90` | `[(0x7E8, 62…)]` | — | ECU_A 的 response_id |
| | `P2-physical-ecu-b` | `0x7E1` | `22 F1 90` | `[(0x7E9, 62…)]` | — | ECU_B 的 response_id |
| | `P3-physical-ecu-c` | `0x7E2` | `22 F1 90` | `[(0x7EA, 62…)]` | — | ECU_C 的 response_id |
| **N** | `N1-unknown-did` | `0x7E0` | `22 FF FF` | `[(0x7E8, 7F 22 31)]` | — | **负响应被转发**（ECU 拒绝，非 Gateway deny） |
| | `N2-protected-did-locked` | `0x7E0` | `22 F1 87` | `[(0x7E8, 7F 22 33)]` | — | 同上（C1 语义透过 Gateway 可见） |
| **S** | `S1-physical-suppress` | `0x7E0` | `3E 80` | `[]` | — | 抑制正响应 → 不发帧 |
| **U** | `U1-unknown-target-7e5` | `0x7E5` | `3E 00` | `[]` | — | 未知物理 target：**路由归属失败** |
| | `U2-unknown-target-7e3` | `0x7E3` | `22 F1 90` | `[]` | — | 同上（会产生正响应的服务也不例外） |
| **F** | `F1-functional-session` | `0x7DF` | `10 03` | `[]` | **A/B/C 会话** | 广播扇出 |
| | `F2-functional-read` | `0x7DF` | `22 F1 90` | `[]` | — | 广播**不收集响应**（即使会正响应） |
| | `F3-functional-suppress` | `0x7DF` | `3E 80` | `[]` | — | 广播 + 抑制 |
| | `F4-functional-write` | `0x7DF` | `2E F1 02 BC` | `[]` | **A/B/C 的 did_overrides** | **写入扇出到全部 ECU** |
| **W** | `W1-physical-write` | `0x7E0` | `2E F1 02 OK` | `[(0x7E8, 6E F1 02)]` | **仅 A** | 物理写入只影响目标 |
| **I** | `I1-override-scoped-to-ecu-a` | `0x7E1` | `22 F1 02` | `[(0x7E9, 62 F1 02 "DEFAULT_CONFIG")]` | — | **隔离**：A 的 override 不被 B 读到 |
| | `I2-write-scoped-to-ecu-a` | `0x7E0` | `2E F1 02 NEW` | `[(0x7E8, 6E F1 02)]` | **仅 A** | **隔离**：B/C 各自的 override 不被改动 |
| **M** | `M1-empty-payload` | `0x7E0` | `` | `[(0x7E8, 7F 00 13)]` | — | 空 payload **不被 Gateway 预分类** |
| | `M2-unregistered-sid` | `0x7E0` | `00` | `[(0x7E8, 7F 00 11)]` | — | 未注册 SID 由协议层处理 |
| **C** | `C1-custom-handler-in-default-topology` | `0x7E1` | `01 02` | `[(0x7E9, 77 01 02)]` | — | **override 基线**（只影响被注入的 ECU） |
| | `C2-custom-topology-single-ecu` | `0x700` | `01 02` | `[(0x708, 77 01 02)]` | — | 自定义拓扑 + 自定义 handler |
| **D** | `D1-tester-present` | `0x7E0` | `3E 00` | `[(0x7E8, 7E 00)]` | — | 正响应且无副作用 |

### 覆盖取舍说明

* **只覆盖 Gateway 路由语义**，未复制整个 UDS suite。
  C2 policy 的输入粒度是 **SID**，不是完整 service semantics（设计文档 §7）。
* **自定义 functional address 未纳入**：当前系统**没有**依赖该行为稳定性的既有测试
  （`DiagnosticGateway` 支持该参数，但 `build_default_gateway()` / `build_runtime()`
  均使用默认值）。按"不为凑覆盖率新增场景"的原则取舍并记录于此。
* **`N*` 明确不是 policy deny** —— 它是"**Gateway 放行、ECU 拒绝**"，
  是 C1/ECU 语义透过 Gateway 的可见形态。A0 只记录事实，不附加 policy 术语。

---

## 6. Case Count

```
20
```

---

## 7. GatewayStateSnapshot 定义

对**每一个 ECU** 记录：

```
(name, session, security_level, frozenset(did_overrides.items()))
```

序列化为按 DID **排序**的 `{hex: hex}` —— **不依赖 dict 插入顺序**，
保证"同逻辑状态 ⇒ 同 Golden 字节"。

**为什么不能只比较 response**：只比较 `route()` 返回值**无法**发现
**错误扇出 / 漏扇出 / 状态泄漏 / ECU 间状态污染**。
`F4`（广播写入改变全部 3 个 ECU）与 `I1`/`I2`（隔离）都只有靠状态投影才能验证。

---

## 8. Provenance

```
captured_at_commit  = 83d1f52d9b8fcc45ef6f325595c950159d360414
gateway_mode        = legacy
policy_execution    = absent
functional_address  = 0x7DF
schema_version      = 1
```

**Git commit 是核心 provenance** —— 不使用 "captured_at = now" 作为唯一来源。

---

## 9. Golden SHA256

**LF 规范化后的文件内容 SHA-256（平台无关）**：

```
d1d358927a4eb86e750fda13a961d935b1afccd3e01e50c26a696dcc7f43c208
```

> **这不是 Git 对象哈希** —— 后者的对象 ID 在对象头 + 内容上另算。
> 之所以规范化行尾：Windows 的 `core.autocrlf=true` 会把仓库中的 LF 内容以 CRLF
> 写到工作区；C1/B2 阶段曾因直接哈希工作区字节而在 Linux CI 上失败。
> 本 corpus 的 LF 规范化哈希已**硬编码进结构测试**，使"corpus 被改写"立刻失败。

---

## 10. Capture Command

```bash
python3 labs/vehicle/uds-basic/tests/capture_c2_a0_gateway_golden.py
```

输出：

```
[ OK ] 已捕获 legacy Gateway golden corpus
       path             = golden/c2_gateway_legacy.json
       gateway_mode     = legacy
       policy_execution = absent
       functional_addr  = 0x7DF
       cases            = 20
       commit           = 83d1f52d9b8fcc45ef6f325595c950159d360414
```

---

## 11. A0 Structural Test

`labs/vehicle/uds-basic/tests/test_c2_a0_gateway_golden.py` —— **27/27 PASS**

| 类 | 职责 |
|---|---|
| `TestCorpusIsFrozen` | LF 规范化 SHA256 / case 数 / case_id 唯一 |
| `TestCaptureProvenance` | schema / gateway_mode / **policy_execution=absent** / commit / functional address / 投影字段 / **policy 符号仍不存在** |
| `TestSingleRequestInvariant` | 无 sequence·steps / 每条自带完整 ECU 配置 / 逐 ECU 状态齐全 |
| `TestCoverageMatrix` | 10 类覆盖齐备 + 反向（无未登记 case）+ 关键语义锚点（广播恒 `[]`、扇出、隔离、负响应转发、override） |
| `TestCaptureIsReproducibleButNotTheOracle` | 每 case 可**独立构造**网关；handler kind 可确定性重建 |

**A0 不做 legacy-vs-current 等价性对拍** —— 那属 A2。

---

## 12. Existing Gateway Regression

| 测试 | 结果 |
|---|---|
| `test_gateway_unit.py` | **13/13 PASS** |
| `test_gateway_runtime_unit.py` | **53/53 PASS** |
| UDS unit（全量） | **928**（901 + 27；19/19 文件，FAIL 0） |
| UDS e2e | **234**（12 项，FAIL 0；含 `multi-ECU physical addressing` / `gateway runtime daemon`） |
| `qa_fast.sh` | **PASS**（27 项 OK / 0 FAIL，1231 cases；ruff 0 / mypy 0） |
| C1 全部专项测试 | 29 / 56 / 26 / 30 / 20 / 39 全 PASS |

---

## 13. Production Diff = 0

```
git diff -- labs/vehicle/uds-basic/ecu/   → 空

gateway.py            UNCHANGED
gateway_runtime.py    UNCHANGED
uds_server.py         UNCHANGED
service_spec.py       UNCHANGED
ecu.py                UNCHANGED
did_database.py       UNCHANGED
```

未实现 policy、未实现 `route_with_trace()`、未修改 `route()` /
`RouteResult` / functional addressing / response semantics。

---

## 14. C1 Unchanged

```
_SPEC_EXECUTION = frozenset({0x22, 0x2E})    （未动）
C1 Phase 2 = COMPLETE
```

---

## 15. 0x27 Unchanged

```
未触碰 0x27 / SecurityAccess / keygen / _now()
```

---

## 16–20. 后续阶段状态

```
C2-A1 = NOT STARTED   （引入空策略 + trace mechanism）
C2-A2 = NOT STARTED   （等价性对拍）
C2-A3 = NOT STARTED
C2-A4 = NOT STARTED
C3    = NOT STARTED
```

---

## 21. A0 PASS 条件逐项

| # | 条件 | 结果 |
|---|---|---|
| 1 | Golden 完全由 legacy Gateway 生成 | ✓（两层护栏保证前提） |
| 2 | policy implementation = 0 | ✓ |
| 3 | Golden schema 有效 | ✓ |
| 4 | 每条 case 单请求 | ✓ |
| 5 | initial state 显式 | ✓（每 case 自带完整 ECU 配置） |
| 6 | response 已捕获 | ✓ |
| 7 | 逐 ECU final state 已捕获 | ✓ |
| 8 | 物理路由已覆盖 | ✓（P1–P3 / W1 / I2 / M1–M2 / D1） |
| 9 | 功能路由已覆盖 | ✓（F1–F4） |
| 10 | 未知 target 已覆盖 | ✓（U1–U2） |
| 11 | suppress 已覆盖 | ✓（S1 / F3） |
| 12 | 状态隔离已覆盖 | ✓（I1 / I2 / W1 / F4） |
| 13 | override / custom handler 基线已覆盖 | ✓（C1 / C2） |
| 14 | provenance 已记录 | ✓ |
| 15 | 规范化 SHA256 已记录 | ✓ |
| 16 | A0 structural tests PASS | ✓（27/27） |
| 17 | 既有 Gateway 测试 PASS | ✓（13/13 + 53/53） |
| 18 | production diff = 0 | ✓ |
| 19 | C1 unchanged | ✓ |
| 20 | 0x27 unchanged | ✓ |

```
C2-A0 = PASS
```

---

## 22. 下一步

**A0 完成后不自动进入 A1。** 等待对 Golden 的**覆盖范围与 schema** 的审阅。

```
C2 Design  = COMPLETE
C2-A0      = COMPLETE
C2-A1 … A5 = NOT STARTED
C3         = NOT STARTED
```
