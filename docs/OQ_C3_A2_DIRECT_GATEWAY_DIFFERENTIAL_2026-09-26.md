# OQ C3-A2 —— Direct ECU ↔ Gateway 差分（证据报告）

> 状态：**C3-A2 = PASS**
> 日期：2026-09-26
> 分支：`audit/oq-c3-security-access-gateway-a2-differential-20260926`
> 基线：`main = 2733de7f51ceced41b87ad560faa04de2c4b987d`（= C3-A1 merge commit，PR #52）
> 设计出处：`docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md`
> §13（Direct ECU ↔ Gateway 等价性策略）/ §11（多 ECU 不变量）/ §10.3（functional）

---

## 1. Scope

```
目标：证明 **同一输入 / 同一初始 security state / 同一时间与配置** 下，
      Direct ECU 路径与 Gateway 路径**等价**，且多 ECU 隔离正确。

本阶段做   差分 harness + 23 条 case + 反向自证 + 本报告
本阶段不做 full regression closure（A3）/ provider bypass 完整审计（A4）/ exit review（A5）
```

**production diff = 0** —— 本阶段只新增测试与文档。

---

## 2. oracle 的定义（**核心**）

```
oracle  = Direct ECU   `ecu.handle_uds_request(request)`
          —— C3 **没有**修改它的行为（C3-A1 只把绑定改为委托，语义不变）

subject = Gateway      `DiagnosticGateway.route()`，经 C3-A1 接线的 0x27
```

**明确不是 oracle 的东西**（已由反向自证用例锁定）：

```
✗ 手写 expected
✗ C3-A0 的 Gateway golden —— 那只是"C3 接线之前"的**历史事实 / 迁移参照**
✗ 当前 Gateway 自己的 expected
✗ 任何"重新生成的正确答案"
```

对应用例：`TestA2DoesNotUseTheA0GoldenAsOracle`
（断言 `c3_differential_lib` **不读取** A0 corpus、oracle 是**活着的**直连入口）。

---

## 3. 差分模型

```
        same input（同一条请求序列）
        same initial security state
        same clock / SECURITY_CONFIG
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   Direct ECU             Gateway（0x27 接线后）
   （全新 ecu.STATE）      （全新 gateway + 全新三 ECU）
        │                       │
   responses / state       responses / state
   / security_state        / security_state（**逐 ECU**）
        └───────────┬───────────┘
                    ▼
            DifferentialResult
              ├── response_diffs        逐字节，按步定位
              ├── state_diffs           ECU → field → direct/gateway 值
              └── metadata              case_id / steps / 两侧终态
```

**比较映射（physical 与 functional 刻意不同）**：

```
physical    Direct（单 ECU）  vs  Gateway 的**目标 ECU**
functional  Direct（单 ECU）  vs  Gateway 的**每一个** ECU
            —— C2-OD-4 冻结"功能寻址逐 ECU 判定"，
               故每个 ECU 都应各自表现得像"直连 ECU 收到同一请求"
```

### 3.1 两类期望语义（**刻意区分，不混为一谈**）

```
equivalent   两侧必须等价（默认）—— 覆盖 22 条 case
policy_deny  Gateway 因 **C2 policy deny** 静默丢弃 ⇒ 两侧**必然不同**
             （直连路径**没有** policy 层），故改判"丢弃由策略引起"：
                 ① Gateway 不产生任何帧
                 ② Gateway 目标 ECU 的状态保持**初始值**（未被推进）
                 ③ **oracle 侧确实会产生正响应** —— 证明丢弃是策略造成的，
                    而不是 0x27 路径坏了
                 ④ policy decision 记录 allowed=False 且 target 正确
```

> 若把 `policy_deny` 也当成"要求两侧相等"，会得出一个**错误结论**
> （"policy 让两侧不等价"）—— 这是模型问题，不是实现问题。

---

## 4. Case 矩阵（23 条）

| case | 覆盖项 | 实测（两侧一致） |
|---|---|---|
| `D01-physical-request-seed` | 1 physical 27 01 | `67 01 <seed>` |
| `D02-physical-send-key` | 2 physical 27 02 | `67 02`，两侧均 LEVEL1 |
| `D03-wrong-key` | 3 wrong key | `7F 27 35` |
| `D04-third-failure-lockout` | 4 third failure / lockout | `35 / 35 / 36` → `37` |
| `D05-lockout-expiry` | 5 lockout expiry | 到期后恢复 `67 01` |
| `D06-seed-expiry` | 6 seed expiry | `7F 27 24` |
| `D07-consumed-seed-replay` | 7 replay | `7F 27 24` |
| `D08-seed-while-unlocked` | 8 unlocked request seed | `7F 27 22` |
| `D09-session-switch-relock` | 9 session switch | 两侧均归 LOCKED |
| `D10-ecu-reset-relock` | 10 ECU reset | 两侧均归 LOCKED |
| `D11-protected-did-locked` | 11 protected DID | `7F 22 33` |
| `D12-protected-did-unlocked` | 11 protected DID | `62 F1 87 …` |
| `D13a/b/c-target-ecu-{a,b,c}` | 12 response_id mapping | `0x7E8 / 0x7E9 / 0x7EA` |
| `D14-isolation-unlock-a` | 13 / 14 target isolation | A 解锁，B/C 保持初始 |
| `D15-isolation-lockout-a` | 13 / 15 lockout isolation | A 锁定，B/C 正常 |
| `D16-a-request-seed-b-request-seed` | 16 / 17 per-ECU seed | 逐 ECU 独立 |
| `D17-per-ecu-seed-counter` | 18 per-ECU failed_attempts / counter | A=2，B/C=0 |
| `D18-policy-allow` | 19 policy allow | 两侧等价 |
| `D19-policy-deny` | 19 policy deny | **policy_deny 模型**（见 §3.1） |
| `D20-functional-request-seed` | 20 functional | `[]` + 逐 ECU 与 oracle 一致 |
| `D21-functional-send-key` | 20 functional | 同上（2 步） |

---

## 5. 结果

```
cases                = 23
equivalent cases     = 22
policy_deny cases    =  1

response matches     = 22 / 22      （equivalent 集合）
state    matches     = 22 / 22      （含逐 ECU 的 3 字段 + 5 个 0x27 字段）
mismatches           =  0
```

**核心判据**：`response mismatch = 0` ∧ `state mismatch = 0` ✓

> 汇总数字由 `test_aggregate_counts_are_locked` 常驻锁定 —— 任何漂移
> （少比一条、多一条 case）立刻失败。

---

## 6. 逐 ECU 隔离证据（覆盖项 13–18）

```
D14（只对 A 解锁）
    ECU_A: seed_counter=1, security_level=LEVEL1（与 oracle 一致）
    ECU_B / ECU_C: 安全状态**逐字段等于初始值**，security_level 仍 LOCKED

D15（只让 A 进入 lockout）
    ECU_A: lockout_until 非空, failed_attempts=3
    ECU_B / ECU_C: lockout_until=None, failed_attempts=0, seed_counter=0

D17（对 A 连续两次请求 seed）
    ECU_A.seed_counter=2 ；ECU_B / ECU_C 的 counter / pending_seed / failed_attempts 全为 0

正面证据：目标 ECU 的 security_state **等于 oracle**（不只是"其它 ECU 未变"）
```

---

## 7. functional 27 证据（覆盖项 20 —— 冻结的 known limitation）

设计 §10.3 已冻结 **functional 0x27 = ALLOW**。**不能因为"没有 response"
就把它从差分中删除** —— 本阶段如实验证：

```
Gateway 侧  ：**每一步**都恒不产生帧（`responses` 全为空）—— C2-OD-4
比较映射    ：Direct 单 ECU  vs  Gateway 的**每一个** ECU（3 个）
逐 ECU 结果 ：D20 / D21 中，三个 ECU 的
              `state`          == oracle
              `security_state` == oracle
```

known limitation 的**正面记录**（D20）：

```
ECU_A / ECU_B / ECU_C 的 pending_seed 均非空、seed_counter 均为 1
    —— Tester 拿不到任何 seed，却推进了三个 ECU 的 seed 状态
但 security_level 均仍为 LOCKED、failed_attempts 均为 0
    —— 副作用范围限于 seed 生命周期，不影响授权与惩罚计数
```

**反向自证**：production 中**不存在** functional 0x27 的专门分支
（`gateway.py` / `gateway_runtime.py` 中无 `is_functional` 等标记）。

---

## 8. policy 交互证据（覆盖项 19）

```
D18 policy allow ：两侧 response 逐字节相同（`67 01 <seed>`），state 一致
D19 policy deny  ：Gateway responses == []（静默丢弃）
                   目标 ECU 的 security_state **等于初始值**（未被推进）
                   oracle 侧确实产生 `67 01 <seed>`（证明丢弃由策略引起）
                   policy decision：target=0x7E0 / sid=0x27 / allowed=False
```

---

## 9. 独立性与反伪造

### 9.1 路径独立性（设计 §13 硬约束）

```
✓ Direct 与 Gateway **各自新建**执行对象与状态投影（`assertIsNot` 三重断言）
✓ 结构差异：Direct 是**单 ECU 的 flat 三字段投影**；
            Gateway 是**按 ECU 名索引**的多 ECU 投影
✓ Direct 执行后 `ecu.STATE` 被**恢复**（不留副作用）
✓ 两次 `run_case` 的 Gateway 与状态投影互不相同
✓ 时间输入：两侧各持 Clock 实例，起始值与推进计划相同
            ⇒ 两侧产生的 **seed 逐字节相同**（间接佐证 seed_counter 与时钟一致）
✗ 不存在"Direct 执行后拿同一批 ECU 再跑 Gateway"
```

### 9.2 Comparator 反伪造（negative control）

```
未扰动                  → matched == True
response 单侧扰动        → 检出，且**不污染** state 判定
state 单侧扰动           → 检出，且 response 判定仍为 True
security_state 单侧扰动  → 检出
失败报告定位             → 含 case_id / ECU.field / 两侧值
```

⇒ `mismatch == 0` **不是**"比较器永远通过"。

---

## 10. 与 C3-A0 Golden 的关系

```
C3-A0 Golden  = "C3 接线之前"的历史基线
                 → 只作 **历史事实 / 迁移参照**
                 → **不是** A2 的 oracle

A2 的 oracle  = Direct ECU 路径（**活着的**直连入口）

阶段分工：
    A0：freeze before implementation
    A1：implementation
    A2：Direct ECU ↔ Gateway
```

反向自证用例锁定：`c3_differential_lib` 中不出现
`c3_security_access_baseline` / `c3_baseline_lib` / `load_corpus`。

---

## 11. 验证

| Gate | 结果 | 计数 |
|---|---|---|
| **C3-A2 专项** | **42 / 42 PASS**（skipped 0） | 23 cases 差分 |
| C3-A1 专项 | 48 / 48 PASS | —— |
| C3-A0 专项 | 35 / 35 PASS | golden 未重写 |
| **C2-A2** | **46 / 46 PASS** | response 20/20、state 20/20、mismatch 0 |
| `run_tests.sh unit` | **PASS 25 / WARN 0 / FAIL 0** | 1177 cases |
| can-tools `run_tests.sh all` | **PASS 7 / WARN 0 / FAIL 0** | 358 cases |
| `qa_fast.sh` | **PASS**（exit=0） | ruff OK / mypy OK |
| `test_gateway_runtime_vcan.py`（vcan0） | **22 / 22 PASS**（23.7s） | **非 A2 范围**，仅作回归佐证 |
| ruff / mypy | **OK** | —— |

```
FAIL = 0    SKIP = 0
```

---

## 12. Scope

```
production diff = 0     （ecu/ 全目录 **0 个文件**差异 —— 两侧核对确认）
C2/C3 golden diff = 0   （C3-A0 corpus、C2 golden、legacy frozen copy 均未改）
C2 测试 diff      = 0
C3-A0/A1 测试 diff = 0
CI diff           = 0
设计文档 diff      = 0（C3 Design / C2-OD / C3-OD 均未改）
shared dependency pin = 未再次修改
```

本阶段新增：

```
新增  labs/vehicle/uds-basic/tests/c3_differential_lib.py
新增  labs/vehicle/uds-basic/tests/test_c3_a2_direct_gateway_differential.py
新增  docs/OQ_C3_A2_DIRECT_GATEWAY_DIFFERENTIAL_2026-09-26.md
修改  labs/vehicle/uds-basic/tests/run_tests.sh   （+8 行接入）
```

---

## 13. Known Limitations

```
1  A2 是**离线**差分（`ecu.handle_uds_request` vs `DiagnosticGateway.route`）：
   vcan / ISO-TP 层不在本阶段范围（22/22 vcan 通过仅作回归佐证）。
2  functional 0x27 的空转副作用按设计保留（专门抑制属 DEFERRED C3-OQ-10）；
   A2 如实记录其范围（只推进 seed 生命周期，不动 security_level / 惩罚计数）。
3  `policy_deny` 是**唯一**两侧必然不同的语义；A2 用专门判据处理，
   并有反向对照（oracle 侧确实会成功）证明该判据非空洞。
4  provider override 的**完整 bypass 审计**不在本阶段（A4）。
5  差分只覆盖 0x27 相关的输入集；未对全部 14 个 SID 做穷举差分
   （那些服务的等价性由 C3-A1 的 22 组接线前等价性实测覆盖）。
6  Windows/WSL 行尾（Issue #28 同源）在本阶段再次出现于 `run_tests.sh`
   （PR #52 merge 的 ff 重写了它）—— 处置为**原地去除 CR**、保留工作区改动；
   两侧核对确认 `ecu/` 内容差异为 **0**。
```

---

## 14. 结论

| # | 判据 | 结果 |
|---|---|---|
| 1 | oracle = 未被修改的直连 ECU 路径（非手写 / 非 A0 golden） | ✓ |
| 2 | 同一输入 · 同一初始 security state · 同一时间与配置 | ✓ |
| 3 | response 等价 | ✓ **22 / 22** |
| 4 | state 等价（3 字段 + 5 个 0x27 字段，逐 ECU） | ✓ **22 / 22** |
| 5 | **mismatches = 0** | ✓ |
| 6 | 两侧各自新建实例、不共享状态 | ✓ |
| 7 | per-ECU 隔离（解锁 / lockout / counter / pending_seed / failed_attempts） | ✓ |
| 8 | response_id 映射 | ✓ |
| 9 | policy allow / deny 交互 | ✓（deny 用专门判据 + 反向对照） |
| 10 | functional 27 的冻结 known limitation | ✓（未删除、未加专门分支） |
| 11 | comparator 反伪造 | ✓ |
| 12 | production / golden / 设计文档 diff = 0 | ✓ |

```
C3-A2 = PASS
```

---

## 15. Next Stage

```
C3-A2（本文档）  ← PASS
      ↓
C3-A3  Full regression
      ↓
C3-A4  Override / bypass audit（含 provider seam）
      ↓
C3-A5  Exit Review
```

**A2 完成后不自动进入 A3。**
