# OQ C3-A0 —— 0x27 SecurityAccess 实现前基线（证据报告）

> 状态：**C3-A0 = PASS**
> 日期：2026-09-26
> 分支：`audit/oq-c3-security-access-gateway-a0-baseline-20260926`
> 基线：`main = 4cb85274b35ab52fa7df06a346f7afe495ab6714`（= C3 Design merge commit，PR #50）
> 设计出处：`docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md`

**本阶段只做"实现前基线冻结"，**不实现 C3、不创建新的运行时 seam、不更新 C3-B1 旧 pin。**

---

## 1. Scope

```
目标：把 **C3 接线之前** 的 0x27 行为冻结为可长期重放的 baseline，
      为 C3-A1（实现与接线）与 C3-A2（Direct ECU ↔ Gateway 差分）提供"实现前"参照。

本阶段做     捕获 baseline / 结构测试 / 长期重放 / fail-closed 护栏 / 本报告
本阶段不做   任何 production 改动；任何既有测试改动；C3-B1 pin 更新；Golden 之外的生成物
```

---

## 2. Baseline

| 项 | 值 |
|---|---|
| `main` SHA | `4cb85274b35ab52fa7df06a346f7afe495ab6714` |
| A0 分支 | `audit/oq-c3-security-access-gateway-a0-baseline-20260926` |
| 捕获时 commit | `4cb85274b35ab52fa7df06a346f7afe495ab6714`（写入 corpus 头 `captured_at_commit`） |

---

## 3. 两条基线路径

```
direct_ecu   `ecu.handle_uds_request(request)`          —— 单 ECU 入口（0x27 今天**已可用**）
gateway      `DiagnosticGateway.route_with_trace(...)`  —— 网关路径（0x27 今天**不可达**）
```

两条路径都已存在、都是**生产入口**，A0 只是把它们**当前的**可观测行为记录下来。

**与 C2-A0 的关键差异（刻意）**：

```
C2-A0 的不变式 = 一个 case **恰好一次** route()
C3-A0 的不变式 = 一个 case = 一段**脚本化步骤序列**

原因：以下类别天然需要序列才能表达
    第三次失败 → lockout
    seed → key → 重放 key（消费后再发）
    解锁 → 读受保护 DID
    session 切换 / ECU reset 后的重锁

每个 case 仍**自带完整初始状态** ⇒ 仍可独立 replay，不依赖其它 case。
```

---

## 4. Golden Schema 与冻结哈希

```
文件        labs/vehicle/uds-basic/tests/golden/c3_security_access_baseline.json
schema_version           = 1
captured_at_commit       = 4cb85274b35ab52fa7df06a346f7afe495ab6714
c2_design_baseline       = 4cb85274b35ab52fa7df06a346f7afe495ab6714
c3_implementation_present = false        ← A0 的语义前提
gateway_0x27_reachable    = false        ← A0 记录的**核心事实**
base_clock                = 1000.0       （注入 ecu._now 的固定时钟）
security_config           = {max_attempts: 3, lockout_seconds: 10, seed_expiry_seconds: 5}
state_projection_fields          = [session, security_level, did_overrides]
security_state_projection_fields = [pending_seed, seed_consumed, failed_attempts,
                                    lockout_until, seed_counter]
cases                     = 29（direct_ecu = 15 / gateway = 14）

LF SHA256 = f93b42f695d7ea2c91c730404eb02a0d7d27f4e65f09e4889d71cd02a4f8846c
blob      = d211f53adfa74e59ae54f3609c83f86665b23efb
规模      = 2850 行 / 68369 字节
```

**每个 case 记录**：`path` / `addressing` / `target` / `policy` / `clock` / `frozen_boundary` /
`ecus`（完整初始配置）/ `steps`，以及 `expected`：

```
expected.steps[].request           实际请求字节（含按序列求值的 key 展开）
expected.steps[].responses         [[response_id, payload_hex], …]（direct 用 null response_id）
expected.steps[].policy_decisions  [(target, addressing_mode, sid, allowed), …]（gateway）
expected.state                     逐 ECU 三字段常规状态
expected.security_state            逐 ECU 0x27 安全状态（5 字段）
```

> `pending_seed` 只记录**是否存在**（hex）。原因：`generate_seed` 只取决于
> `(counter, level)`、**不含 ECU 身份**（设计 §3.2），把它当跨路径常量会
> 把 keygen 的确定性误当成等价性证据。

---

## 5. Case 矩阵（29 条）

### 5.1 直连 ECU 0x27 baseline（15 条，`direct_ecu`）

| case | 覆盖 | 实测响应 |
|---|---|---|
| `D1-request-seed` | `27 01` | `67 01 85ebda6b` |
| `D2-send-key-without-seed` | `27 02` 无 seed | `7F 27 24` |
| `D3-malformed-length-seed` | `27 01` 带多余字节 | `7F 27 13` |
| `D4-malformed-length-key` | `27 02` key 长度不足 | `7F 27 13` |
| `D5-unsupported-subfunction` | `27 03` | `7F 27 12` |
| `D6-wrong-key` | 错误 key | `7F 27 35`（`failed_attempts=1`） |
| `D7-third-failure-lockout` | 连续三次错误 | `35 / 35 / 36` → 随后 `27 01` → `37` |
| `D8-lockout-expires` | 锁定到期 | 推进时钟 11s 后 `27 01` → 恢复正响应 |
| `D9-seed-expiry` | seed 过期 | `7F 27 24` |
| `D10-replay-consumed-seed` | 成功解锁后重放 key | `67 02` → `7F 27 24` |
| `D11-seed-while-unlocked` | 已解锁再 `27 01` | `7F 27 22` |
| `D12-session-switch-relocks` | 切 extended | `50 03 00 64 03 E8`，`security_level` 归 LOCKED |
| `D13-ecu-reset-relocks` | `11 01` | `51 01`，`security_level` 归 LOCKED（惩罚状态保留） |
| `D14-protected-did-while-locked` | 未解锁读受保护 DID | `7F 22 33` |
| `D15-protected-did-after-unlock` | 解锁后读受保护 DID | `62 F1 87 53 50 41 52 2D 50 41 52 54 2D 30 30 30 31` |

> `D7` 的 seed 每次不同（`seed_counter` 递增）—— 已如实记录，未做任何归一化。

### 5.2 当前 Gateway baseline（14 条，`gateway`）

| case | 覆盖 | 实测响应 |
|---|---|---|
| `G1-physical-27-on-ecu-a` | 物理 `0x7E0` `27 01` | **`0x7E8: 7F 27 11`** ← C2 时代边界 |
| `G2-response-id-mapping` | 物理 `0x7E1` 读公开 DID | `0x7E9: 62 F1 90 …`（响应 ID 映射现状） |
| `G3-old-pin-explicit` | **C3-B1 旧 pin 所断言的事实** | **`0x7E8: 7F 27 11`** |
| `G4-unknown-physical-target` | 未知 target `0x7E5` | `[]`（路由归属失败，不进入策略） |
| `G5-functional-27` | 功能寻址 `27 01` | `[]` |
| `M1-physical-27-each-ecu` | 逐个物理寻址三 ECU | `0x7E8/0x7E9/0x7EA: 7F 27 11` |
| `M2-security-state-untouched-by-gateway-27` | 连续两次 `27 01` | 两次均 `7F 27 11`，安全状态不变 |
| `M3-functional-27-state-effect` | 功能寻址对三 ECU 的影响 | `[]`，安全状态不变 |
| `M4-per-ecu-isolation-preserved` | `0x7E0` 后查 `0x7E1` | `7F 27 11` 后 `62 F1 90 …` |
| `C1-policy-allow-27` | policy allow 下 `27 01` | `0x7E8: 7F 27 11` |
| `C2-policy-deny-27` | deny(`0x7E1`) 对 `0x7E1` | `[]` |
| `C3-policy-deny-is-silent-drop` | deny 语义（两请求） | 均 `[]`，decision 全 `allowed=False`，状态不变 |
| `C4-policy-dimensions` | 策略输入维度 | decision 字段恰为 `{target, addressing_mode, sid, allowed}` |
| `X1-same-request-both-paths` | Direct↔Gateway 对照输入 | `0x7E8: 7F 27 11`（**不要求与直连相等**） |

---

## 6. 冻结的核心事实

### 6.1 C2 时代分层边界（10 条带标记的 case）

```
Gateway 路径 27 01 → 7F 27 11（serviceNotSupported）

标记：frozen_boundary = "c2_era_gateway_0x27_unreachable"
```

该标记**只描述这是 C2 时代边界**，**不预测** C3-A1 之后的新取值 ——
新取值属 C3-A1 的实现与 pin 更新范围（C3-OD-15）。

**这是 C3-B1 的基线侧证据**：A0 把"实现前的样子"固定下来，
使 C3-A1 改变它时**有据可依**、且该改变是**显式评审事件**而非静默漂移。

### 6.2 实现前的整体事实

```
Gateway 路径下：
    · 不产生任何 0x27 肯定响应（67 01 / 67 02）
    · 没有任何 ECU 的 0x27 安全状态被触碰
      （pending_seed=None / failed_attempts=0 / lockout_until=None /
        seed_counter=0 / seed_consumed=False，全部保持初始值）
```

对应用例：`test_boundary_cases_never_return_a_seed_pre_c3`、
`test_gateway_security_state_is_pristine_pre_c3`。

### 6.3 关于 Multi-ECU 隔离的诚实说明

**实现前，"多 ECU 安全状态独立性"这个问题是空洞的** —— 因为 Gateway 路径
根本触及不到任何 ECU 的 0x27 状态，所以"互不影响"自动成立。

因此 A0 **只冻结"什么都没发生"这一事实**；**真正的 per-ECU 隔离证据是
C3-A2 的义务**（设计 §11 的 INV-1…5）。本文档不把"空洞成立"包装成"已验证隔离"。

---

## 7. 捕获护栏（fail-closed，已实测）

```
护栏：c3_implementation_present()  —— 检查 gateway.py 是否含 C3 接线标记
      C3_IMPLEMENTATION_MARKERS = ("services_provider", "security_access_services")

捕获脚本在护栏为真时 **拒绝写入并 exit 2**。
```

**实测（不改仓库的模拟）**：

```
当前护栏（C3 未接线）      : False
把 marker 换成 gateway.py 中确实存在的符号（模拟 C3-A1 已接线）: True
捕获脚本返回码             : 2      ← fail-closed
corpus 未被改写            : True
```

⇒ C3-A1 落地后，**A0 基线不可能被静默重新生成**。

---

## 8. 测试结果

```
命令：python3 labs/vehicle/uds-basic/tests/test_c3_a0_baseline.py
结果：Ran 33 tests … OK        （skipped = 0）
```

| 测试类 | 用例数 | 覆盖 |
|---|---|---|
| `TestCorpusIsFrozen` | 4 | SHA256 / 规模 / 路径划分 / case_id 唯一 |
| `TestCaptureProvenance` | 8 | schema / `c3_implementation_present=False` / `gateway_0x27_reachable=False` / commit / 投影字段 / 护栏 |
| `TestCoverageMatrix` | 2 | 类别齐备 + 无未登记 case |
| `TestC2EraBoundaryIsRecorded` | 7 | 边界标记集合 / 旧 pin 事实 / 响应 ID 映射 / 未知 target / deny 静默 / 策略三维 |
| `TestDirectEcuBaselineReplay` | 4 | 响应逐字节 / 常规状态 / 安全状态 / 汇总计数 |
| `TestGatewayBaselineReplay` | 4 | 非边界行重放 / 边界行=实现前事实 / 无肯定响应 / 安全状态 pristine |
| `TestBaselineHasNoA1Expectations` | 3 | 反向自证：无任何 A1 预期字段 |

**重放覆盖**：29/29 case 的 response 逐字节重放；direct_ecu 的常规状态与安全状态
**逐字段**重放；汇总计数常驻锁定（正响应 / 24 / 13 / 12 / 22 / 33 / 35 / 36 / 37 各 ≥1）。

---

## 9. C3-A1 的迁移点（**显式登记**）

```
必须保持绿色（C3 不得改变）：
    · 全部 15 条 direct_ecu case            （ecu.py 的 0x27 行为不变）
    · 非边界 gateway case：G2 / G4 / C2 / C3 （响应 ID 映射 / 未知 target / deny）

需要迁移（C3-OD-15）：
    · 10 条 frozen_boundary case
      → C3-A1 将其迁移为"Gateway 0x27 正常可达"契约，
        并同步更新 vcan 旧 pin（test_gateway_runtime_vcan.py，Design 阶段未修改）
      → **迁移边界行**，不是重新生成 corpus
```

对应测试：`TestGatewayBaselineReplay::test_c2_era_boundary_cases_are_still_the_pre_c3_fact`
—— C3-A1 落地后本用例**预期失败**，那是**迁移信号**而非回归。

---

## 10. Scope

```
production diff = 0     （ecu/ 全目录 UNCHANGED）
既有测试 diff    = 0     （未修改 A0/A1/A2/A3/A4/C1 任何测试；C3-B1 旧 pin **未更新**）
C2 design diff  = 0     （C2-OD-1…OD-9 及 C2 全部报告未改）
Golden diff     = 0     （C1/C2 的 golden 未改；本阶段**新增** C3 自己的 baseline）
CI diff         = 0
```

本阶段新增文件：

```
新增  labs/vehicle/uds-basic/tests/golden/c3_security_access_baseline.json
新增  labs/vehicle/uds-basic/tests/c3_baseline_lib.py
新增  labs/vehicle/uds-basic/tests/capture_c3_a0_baseline.py
新增  labs/vehicle/uds-basic/tests/test_c3_a0_baseline.py
新增  docs/OQ_C3_A0_SECURITY_ACCESS_BASELINE_2026-09-26.md
修改  labs/vehicle/uds-basic/tests/run_tests.sh   （+8 行接入）
```

---

## 11. Known Limitations

```
1  实现前 "Multi-ECU 安全状态独立性" 空洞成立（§6.3）——
   真正的隔离证据是 C3-A2 的义务。
2  seed 取值不含 ECU 身份（keygen 只取决于 (counter, level)）——
   已记录但**不**作为等价性证据；A2 的比较维度是 "seed 存在性 + 长度 +
   用同一 seed 走完后续序列的响应"，而非 seed 字节本身（设计 §13.2）。
3  vcan 层的 C3-B1 旧 pin **本阶段未修改**（属 C3-A1，C3-OD-15）。
4  `qa_extended.sh --fuzz` 等可选长测未纳入本阶段。
5  本 baseline 只覆盖**离线**路径；vcan e2e 层的行为不在 A0 冻结范围。
```

---

## 12. 结论

| # | 判据 | 结果 |
|---|---|---|
| 1 | 直连 ECU 0x27 baseline 已冻结（15 条，含 11 类 NRC / 时序） | ✓ |
| 2 | Gateway baseline 已冻结（14 条，含 C2 时代边界） | ✓ |
| 3 | Multi-ECU baseline 已冻结（4 条，并如实说明其空洞性） | ✓ |
| 4 | C2 interaction baseline 已冻结（4 条，含 deny 静默 + 策略三维） | ✓ |
| 5 | Direct ↔ Gateway 对照输入已备（1 条，不要求相等） | ✓ |
| 6 | `Gateway 27 01 = 7F 27 11` 这一实现前事实已固定 | ✓ |
| 7 | 未混入任何 A1 预期行为（反向自证 3 例） | ✓ |
| 8 | 捕获护栏 fail-closed 且已实测 | ✓ |
| 9 | production / 既有测试 / C2 / CI diff = 0 | ✓ |
| 10 | 未更新 C3-B1 旧 pin、未实现 C3 | ✓ |

```
C3-A0 = PASS
```

---

## 13. Next Stage

```
C3-A0（本文档）           ← PASS
      ↓
C3-A1  implementation     （机制 + 接线；含按 C3-OD-15 迁移 10 条边界行 +
                           更新 vcan 旧 pin）
      ↓
C3-A2  Direct ECU ↔ Gateway differential
      ↓
C3-A3  Full regression
      ↓
C3-A4  Override / bypass audit（含 provider seam）
      ↓
C3-A5  Exit Review
```

**A0 完成后不自动进入 A1。**
