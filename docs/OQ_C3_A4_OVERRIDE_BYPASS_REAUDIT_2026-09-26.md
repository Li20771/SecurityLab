# OQ C3-A4 Re-Audit —— Override / Bypass Closure

> 状态：**C3-A4 Re-Audit = PASS**
> 日期：2026-09-26
> 分支：`audit/oq-c3-a4-r1-override-bypass-20260926`
> 基线：`main = 5559db0326b58d60bfbaba9cf9d6351f185bf0d4`（= PR #56 merge commit）
> 前序：`docs/OQ_C3_A4_OVERRIDE_BYPASS_AUDIT_2026-09-26.md`（**原 A4 = BLOCKED**，本文件不改写它）

---

## 1. BASELINE

```
NEW_MAIN_SHA = 5559db0326b58d60bfbaba9cf9d6351f185bf0d4
               = PR #56 merge commit（C3-DEFECT-1 修复）
parents      = 5ebfaf349b42663a0a8df49fac93777d12fd9e9b（原 A4 基线 / C3-A3 merge）
             + 02cf405c73a03f545a0e259e55382ce1fb075f3c（fix commit）
```

| 检查 | 结果 |
|---|---|
| `main` HEAD == `origin/main` | ✓ 均为 `5559db03…`（`git pull --ff-only` 后） |
| `main` post-merge CI | **success**（run `36234296601`，35s） |
| Re-Audit 分支 | `audit/oq-c3-a4-r1-override-bypass-20260926`，base = `5559db03…` |
| working tree（开始前） | clean |

## 2. REASON FOR RE-AUDIT

```
原 C3-A4（PR #55）  = **BLOCKED**
    10 项授权边界全部成立（无任何 policy / C1 guard bypass），
    但发现 **1 个真实生产缺陷 DEFECT-1**；
    A4 规则禁止本阶段修 production ⇒ 无法闭环。

C3-DEFECT-1 Remediation（PR #56） = 修复完成并已合并
    修复方式：VirtualEcu 记录 framework 自动创建的 default_handler 引用，
    has_custom_handler 用 **identity**（handler is not default_handler）判定来源；
    Gateway 只重接 framework default handler，不覆盖 custom handler。

本次 Re-Audit = 在**修复后的生产基线**上重新建立与原 A4 同等的完整矩阵，
    并确认 DEFECT-1 已消失且未引入新的 defect。
```

## 3. DEFECT-1 BEFORE / AFTER

### 3.1 决定性证据（**同一份断言，修复前后**）

```
原 A4 的 45 例审计文件在两条基线上运行：

    修复前（5ebfaf3）: Ran 45 tests … **FAILED (failures=1)**
                       （唯一失败 = TestDefectReproduction 的正确行为断言）
    修复后（5559db0）: Ran **46** tests … **OK**
                       （45 例转绿 + 本阶段新增 1 例「构造后重赋」）
```

> **这不是"把失败用例改绿"** —— 断言的行为一字未变，变的是 production。
> 该用例已从 `TestDefectReproduction`（缺陷可见性）转为
> `TestDefectRegressionGuard`（**回归护栏**：行为一旦回归立刻失败）。

### 3.2 三态（＋ 本阶段新增的第四态）实测

| 形式 | 构造 | 修复前 | 修复后 |
|---|---|---|---|
| **A** 构造期同时给出 | `VirtualEcu(handler=custom, services_provider=prov)` | handler 保留 ✓ | **保留** ✓ |
| **B** 网关级 provider | `VirtualEcu(handler=custom)` + `gw(services_provider=prov)` | **被覆盖** ✗ | **保留** ✓（custom 被调用 1 次） |
| **C** 默认 handler | `VirtualEcu(默认)` + `gw(services_provider=prov)` | 正确重绑 ✓ | **正确重绑** ✓（`0x27 → 67 01`） |
| **D** 构造后重赋（**本阶段新增**） | `ecu = VirtualEcu(…)` → `ecu.handler = custom` → `gw(services_provider=prov)` | —— | **保留** ✓（custom 被调用 1 次） |

> **D 是本修复采用 identity 而非 boolean 的直接理由**：boolean 会在
> 事后赋值后**过期**，把 custom handler 误判为 framework handler 而覆盖；
> identity 天然跟随真实来源。用例
> `TestProviderAndCustomHandler::test_post_construction_handler_reassignment_is_preserved`
> 常驻锁定该性质。

## 4. services_provider matrix（实测，数值全部来自实跑）

| Seam | Scenario | Decision | Provider | process | State | Wire |
|---|---|---|---|---|---|---|
| provider | physical **allow** | `True` | 1 | 1 | advanced | `[(0x7E8, 67 01 …)]` |
| provider | physical **deny** | `False` | **0** | **0** | unchanged | `[]` |
| provider/functional | **allow** all（3 ECU） | `3×True` | 3 | 3 | all advanced | `[]` |
| provider/functional | **deny all**（3 ECU） | `3×False` | **0** | **0** | unchanged | `[]` |
| provider/functional | **mixed** deny A | `[False,True,True]` | 2 | A=**0** / B=1 / C=1 | A=unchanged / B,C=advanced | `[]` |
| same-SID `0x27` | **allow** | `True` | 1 | 1 | marker=1 | `[(0x7E8, EE 01)]` |
| same-SID `0x27` | **deny** | `False` | **0** | **0** | marker=**0** | `[]` |
| **C1 guarded `0x2E`** | guard reject（默认会话） | policy allow=True | 1※ | 1 | marker=**0** | `[(0x7E8, 7F 2E 7F)]` |
| handler | deny | `False` | n/a | **0** | unchanged | `[]` |
| topology | deny | `False` | n/a | **0** | unchanged | `[]` |
| transport | deny | `allowed=False` | **0** | **0** | unchanged | `[]` |
| build_runtime | **deny** | `allowed=False` | **0** | **0** | unchanged | `[]` |
| build_runtime | **allow** | `allowed=True` | 1 | 1 | advanced | `[(0x7E8, 67 01 …)]` |

※ **`provider = 1` 是正确的**：provider 只做依赖绑定，在 `process()` 内、
guard **之前**被调用；marker **handler** 为 **0**（guard 先拒绝）。
⇒ **`provider 被调用 ≠ handler 被执行`**。

### 4.1 functional **按 ECU 归因**（不只比总数）

```
deny all ：3 个 ECU 的 process 均为 0（不是"总数 0"）
mixed    ：A=0 / B=1 / C=1 ；A 的 security_state 不变、B/C 正常推进
            ⇒ 无部分绕过
```

## 5. handler / provider precedence

```
framework default handler  → provider **可以**重绑（否则 provider 永不生效）
custom handler（构造期）    → **不替换**
custom handler（构造后重赋）→ **不替换**
```

Test 2 同时断言 `has_custom_handler is False` —— 反向排除
"把 framework handler 误判为 custom"从而使 provider 静默失效。

## 6. same-SID override（C3-OQ-12）

```
allow ：marker handler 执行 1 次，wire = [(0x7E8, EE 01)] ⇒ 覆盖契约正向成立
deny  ：marker=0 ／ provider=0 ／ process=0 ／ state unchanged ／ wire=[] ／ decision=False

⇒ **provider 能 override implementation ≠ 能 override policy**
```

## 7. C1 guard 保留（C3-OQ-12 的核心约束）

选用 `0x2E`（仍在 `_SPEC_EXECUTION` 中、带 6 个 guard，源码结构未变）：

```
默认会话 + LOCKED，provider 把 0x2E 覆盖为 marker handler：
    wire          = [(0x7E8, 7F 2E 7F)]   ← 会话 guard 仍生效
    marker hits   = **0**                  ← handler 未被绕过
    did_overrides = 不变
    policy        = allowed=True           ← C1 guard 属 **C1 层**（两件事）

结构性证据：resolve_service_spec(0x2E, {0x2E: marker}).guards
            == SERVICE_SPECS[0x2E].guards    ✓
反向对照：extended + unlocked ⇒ marker hits = 1（证明前一条非恒拒绝）
```

⇒ **`services=` 覆盖的是实现，不是授权。**

## 8. C2 四个既有 seam 复验

| Seam | DENY 四层证据 |
|---|---|
| handler injection | decision `False` ／ process **0** ／ state unchanged ／ wire `[]` ／ custom 0 次 ✓ |
| custom topology | 同上（自定义 `request_id` / `functional_address` 拓扑）✓ |
| fake transport | `transport.sent == []` ／ process **0** ／ state unchanged ／ provider **0** ✓ |
| **build_runtime** | 见 §9 ✓ |

## 9. build_runtime（C3 明确变化过的 seam）

```
deny ：sent = [] ／ process = 0 ／ provider = **0** ／ state unchanged ／ decision=False
        ⇒ 接线 provider **没有**制造第二条 bypass path
allow：provider = 1 ／ process = 1 ／ sent = [(0x7E8, 67 01 …)]
        ⇒ 按 C3-A1 接线**正常工作**
```

（patch 网关工厂 + fake transport，**未绑定任何真实接口**。）

## 10. policy order / count

```
physical   allow : policy=1  provider=1  process=1      （1-1-1）
physical   deny  : policy=1  provider=0  process=0      （1-0-0）
functional allow : policy=3  provider=3  process=3      （3-3-3）
functional deny  : policy=3  provider=0  process=0      （3-0-0）
```

**结构性论证**：provider 只在 `process()` **之内**被调用，而 `process()` 只在
policy **允许之后**被调用 ⇒ `policy → provider → process` 顺序不可能反过来。

`decision` 的**顺序与字段**（`target` / `addressing_mode` / `sid` / `allowed`）
逐条核对等于 `self.ecus` 遍历顺序。

## 11. AST 级源码不变量

| 检查 | 结果 |
|---|---|
| `.process(...)` 调用点 | `gateway.py` **恰好 2 处**；`gateway_runtime` / `uds_server` / `ecu` / `keygen` 均 **0** ✓ |
| `_allow(...)` 调用位置 | 全部落在 `_route_core` 函数体内 ✓ |
| `gateway_runtime` 无第二套授权 | 不含 `policy` / `_allow` / `_by_request` / `PolicyDecision` / `allow_all` ✓ |
| `gateway.py` 不感知 clock / config | 不含 `_now` / `SECURITY_CONFIG` / `keygen` ✓ |
| 无第二套 0x27 状态机 | `gateway.py` 不含 `seed_counter` / `failed_attempts` / `lockout_until` / `verify_key` / `derive_key` / `generate_seed` ✓ |
| 无 functional 0x27 特例 | 无 `is_functional` / `functional_0x27` / `SID == 0x27` ✓ |

> 其中 `.process(...) = 恰好 2 处` 在本次修复后仍成立 ——
> **修复只改了 handler 接线判据，未新增任何执行路径**。

## 12. Negative controls

| 对照 | 证明 |
|---|---|
| baseline 是干净 deny | decision False ／ provider 0 ／ process 0 ／ wire `[]` ✓ |
| allow 路径 provider = 1 | provider 计数断言**非恒零** ✓ |
| allow 路径 wire 非空 | wire 断言**非恒空** ✓ |
| **三字段投影对 `0x27` 不敏感** | `0x27` 不改变 `(session, security_level, did_overrides)` ⇒ 用 `27 01` 做 state 对照是**恒真**的假断言；已改用 `10 03`，并**单独**用 `security_state` 投影检出 `0x27` 的变化 ✓ |

> 第 4 项是原 A4 阶段**修正过的一处自己的假断言**，本次 Re-Audit **保留该修正**
> 并重新验证 —— 不重新引入该错误。

## 13. Regression gates

| Gate | 结果 | 要求 | 符合 |
|---|---|---|---|
| **C3-A4 Re-Audit 专项** | **46 / 46 OK** | —— | ✓ |
| `test_gateway_unit.py` | **20 / 20 OK** | —— | ✓ |
| `test_c2_a4_override_bypass.py` | **58 / 58 OK** | C2 handler seam 未破坏 | ✓ |
| **C3-A0** | **35 / 35 OK** | 35/35 | ✓ |
| **C3-A1** | **48 / 48 OK** | 48/48 | ✓ |
| **C3-A2** | **42 / 42 OK** | 42/42 | ✓ |
| **C2-A2** | **46 / 46 OK** | 46/46 | ✓ |
| `test_c2_a1_empty_policy_trace.py` | 42 / 42 OK | —— | ✓ |
| `run_tests.sh unit` | **PASS 26 / WARN 0 / FAIL 0** | FAIL 0 | ✓（26 文件，新增 A4 Re-Audit） |
| `qa_fast.sh` | **PASS**（exit 0） | PASS | ✓ |
| ruff | All checks passed | —— | ✓ |

## 14. New WARN / FAIL

```
new WARN = 0
FAIL     = 0
```

**未发现任何新的 production defect。**

### 14.1 本阶段新增的用例（1 例）

```
test_c3_a4_override_bypass.py::TestProviderAndCustomHandler
    ::test_post_construction_handler_reassignment_is_preserved
        —— §3.2 的"D 态"：构造后重赋 handler 仍不得被替换
           （identity 判定的直接理由）
```

### 14.2 文件来源说明（**审计完整性**）

```
本分支新增的 test_c3_a4_override_bypass.py **不是**新写的一套审计：
    取自原 A4 分支的同一文件（git show d0eb672:…，970 行 / 45 例），
    **唯一改动**为：
        ① 模块 docstring 与缺陷类 docstring：从"A4 审计（含缺陷）"
           更新为"Re-Audit（缺陷已修复，该类转为回归护栏）"
        ② 类名 TestDefectReproduction → TestDefectRegressionGuard
        ③ 新增 1 例（§14.1）
    **断言逻辑一字未改。**

理由：原 A4 的测试资产只存在于 PR #55 分支（**保持 OPEN / 不合并**），
      而 Re-Audit 必须在 main 基线上交付**可长期运行**的完整矩阵。
```

## 15. CONCLUSION

| # | 判据 | 结果 |
|---|---|---|
| 1 | **DEFECT-1 = FIXED**（三态 + 构造后重赋四态全部成立） | ✓ |
| 2 | services_provider physical allow / deny 四层证据 | ✓ |
| 3 | functional allow / deny-all / **mixed 逐 ECU 归因** | ✓ |
| 4 | same-SID override 不能扩大授权边界 | ✓ |
| 5 | **C1 guard 未被 same-SID override 绕过** | ✓ |
| 6 | C2 四个既有 seam 全部复验 | ✓ |
| 7 | `build_runtime` 接线 provider 未制造第二条 bypass path | ✓ |
| 8 | policy 求值顺序/计数（1-1-1 / 1-0-0 / 3-3-3 / 3-0-0） | ✓ |
| 9 | AST 级不变量（含 `.process()` 仍恰 2 处） | ✓ |
| 10 | negative controls 非恒真 | ✓ |
| 11 | **未发现任何 policy / C1 guard bypass** | ✓ |
| 12 | 未发现新的 production defect | ✓ |

```
C3-A4 Re-Audit = PASS
```

### 15.1 与原 A4 的关系

```
原 C3-A4（PR #55）        = **BLOCKED**（DEFECT-1；历史证据，**本文件不改写它**）
    ↓ PR #56 修复并合并
C3-A4 Re-Audit（本文档）   = **PASS**
```

### 15.2 本阶段**不**得出的结论

```
**不写 C3 EXIT = PASS** —— C3 是否最终 EXIT 由 **C3-A5 Exit Review** 判定。
本阶段只回答"C3 的 override / bypass 边界在修复后的基线上是否闭合"。
```

## 16. Scope（本阶段改动）

```
production modifications = **0**
    gateway.py 的修复来自 PR #56（已合并），**Re-Audit 未再修改 production**
    验证：`git diff 5559db03 -- labs/vehicle/uds-basic/ecu/gateway.py` = 空

Golden diff      = 0
Design diff      = 0    （C2·C3 设计文档未改）
C2-A2 diff       = 0    （differential 与其 shared pin 未动）
C3-A0/A1/A2 diff = 0    （既有测试语义未改）
CI workflow diff = 0
原 A4 报告 diff   = 0    （`OQ_C3_A4_OVERRIDE_BYPASS_AUDIT_2026-09-26.md` 未改）

本阶段新增/修改：
    新增  labs/vehicle/uds-basic/tests/test_c3_a4_override_bypass.py（46 例）
    新增  docs/OQ_C3_A4_OVERRIDE_BYPASS_REAUDIT_2026-09-26.md
    修改  labs/vehicle/uds-basic/tests/run_tests.sh（+8 行接入）
```

## 17. Next Stage

```
C3-A4 Re-Audit（本文档）  ← PASS
      ↓
等待主审确认证据闭环
      ↓
C3-A5  Exit Review
```

**不自动进入 C3-A5。PR #55 保持 OPEN / 不合并。**
