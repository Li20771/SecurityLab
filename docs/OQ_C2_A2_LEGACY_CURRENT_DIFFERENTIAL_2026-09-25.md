# OQ C2-A2 —— Legacy ↔ Current 差分对拍审计（证据报告）

> 状态：**C2-A2 = PASS**
> 日期：2026-09-25
> 分支：`audit/oq-c2-a2-legacy-current-20260925`
> 基线：`720e3fef122af3be78368bbf2469baa83ca5b2b4`（= PR #45 merge 的第二个父提交）
> 设计出处：`docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md` §13

**本阶段的核心主张**：

> **同一个 case，Legacy 冻结实现与 Current 生产实现各自独立执行一次，
> response 与 state 逐字节一致。**

---

## 1. 本阶段**不是**什么

| 不是 | 为什么 |
|---|---|
| 不是"再跑一次 A1" | A1 是 `Current → 冻结 JSON`（重放）；A2 是 `Legacy → Current`（两条**真实执行**的差分） |
| 不是 `Current ↔ JSON` 的包装 | 冻结 Golden 在 A2 里**只作锚点**，A2 的核心 gate 由 `legacy != current` 直接决定 |
| 不是"复制一份看起来像 legacy 的代码" | Legacy 是 `gateway.py@4059cf5` 的 git blob **逐字节冻结副本**，哈希双层钉住 + git 交叉校验 |
| 不是把两条路径合并到同一 gateway 对象后比较 | 两条路径**各自实例化**（独立 gateway / 独立 ECU / 独立 `EcuState`），各自执行一次 |

```
A1 :  Current  ──→  冻结 JSON         重放（当前实现能否复现历史记录）
A2 :  Legacy   ──→  Current           差分（两条真实执行是否一致）
      +  两侧各自与冻结 JSON 的锚点一致性（辅助证据，**不是**核心证明）
```

---

## 2. Baseline

| 项 | 值 |
|---|---|
| `HEAD`（开始前） | `720e3fef122af3be78368bbf2469baa83ca5b2b4` |
| A2 分支 | `audit/oq-c2-a2-legacy-current-20260925`（从 HEAD 建立，**不从**本地 `main`） |
| `working tree`（开始前） | clean |
| A0 Golden（开始前/后） | blob `541395b8…`，LF SHA256 `d1d35892…`（**未改动**） |

> **说明（本地 main 陈旧）**：本地 `main` ref 停留在 `1b43b12`（PR #44），
> 不包含 A1；远程 `main` = `15288ae2`（PR #45 的真 merge commit）。
> 由于 `720e3fe` 是该 merge 的第二个父提交，且 `HEAD^{tree}` =
> `6e23db24dfa17356907ad108442ad43e59865a8d` = PR #45 merge 的 tree，
> 因此**从 HEAD 建分支与从远程 main 建分支在内容上等价**。

---

## 3. Legacy 来源（真实位置与可复现方式）

```python
LEGACY_SOURCE_COMMIT    = "4059cf5d3b10c6b94b571e1bf44633941c9b3dea"   # A0 捕获 commit
LEGACY_SOURCE_RELPATH   = "labs/vehicle/uds-basic/ecu/gateway.py"
LEGACY_SOURCE_BLOB_SHA  = "e64cd088fc86bf9e6b1c5dfb2bb1b80cae5ab39a"
LEGACY_SOURCE_LF_SHA256 = "754a44de007eb5bda9906166bd16b9da343278f3a2d6fbb705a7148fb423b054"
```

* **位置**：`labs/vehicle/uds-basic/tests/legacy/c2_a0_gateway_legacy_impl.py`（190 行）
* **生成方式**：`git show 4059cf5:labs/vehicle/uds-basic/ecu/gateway.py > <vendor>`
  —— 不是手写、不是改写、不是"照着写一份"
* **加载方式**：`importlib.util.spec_from_file_location` 以**独立模块名**
  `c2_a0_gateway_legacy_impl` 载入，不遮蔽 current 的 `gateway`
* **该版本的事实**：`DiagnosticGateway.__init__` 只有 `(ecus, functional_address)`；
  无 `policy` 参数；无 `PolicyTrace` / `route_with_trace` / `_route_core`

### 3.1 为什么必须这样定义（准确表述）

```
legacy **不是**一条仍并存的运行时路径，
而是 git 历史中**冻结的 pre-policy 实现**。

因此 A2 的"两条路径" == "冻结实现 vs 当前实现"，
== "同一输入在 C2 改动前后的行为"。
```

**不允许**把 A2 描述成"两条并存的运行时路径"——那不是事实。

### 3.2 双层哈希钉住 + git 交叉校验

| 层 | 手段 | 是否依赖 git |
|---|---|---|
| 1 | git blob SHA 由**内容**独立重算（`sha1("blob <len>\0" + content)`） | 否 |
| 2 | LF 规范化 SHA-256（CRLF 归一 ⇒ 平台无关） | 否 |
| 3 | 与 `git show 4059cf5:<path>` 逐字节交叉校验 | **是**（不可用时**跳过**，不失败） |

实测：

```
vendor blob  = e64cd088fc86bf9e6b1c5dfb2bb1b80cae5ab39a   MATCH
vendor lf256 = 754a44de007eb5bda9906166bd16b9da343278f3a2d6fbb705a7148fb423b054   MATCH
git xcheck   = available=True  matches=True
```

> **git 不可用（浅克隆 / 导出目录）时 A2 仍可执行** —— 第 3 层降级为
> `skipTest` 并**报告事实**，不假装校验过。

---

## 4. 独立代码 与 受控共享依赖（**不要声称"两套代码完全独立"**）

准确表述：

> **Legacy gateway 实现与 Current gateway 实现独立；
> 协议层 / ECU 基础设施属于受控共享依赖，且已通过历史版本 hash 固定验证。**

```
独立（被差分的主体）
    labs/vehicle/uds-basic/ecu/gateway.py          ← Current（policy 机制）
    tests/legacy/c2_a0_gateway_legacy_impl.py      ← Legacy（pre-policy 冻结副本）

受控共享依赖（被两条路径**共同调用**的同一份模块对象）
    uds_server.py / uds_types.py / service_spec.py / did_database.py /
    dtc_database.py / dtc_runtime.py / ecu.py / file_database.py /
    gateway_runtime.py / keygen.py / routine_database.py / transfer_database.py
```

### 4.1 为什么这种共享**不会**把 A2 退化成 Current 自测

1. 上述 12 个模块在 `4059cf5` 与 HEAD 之间**逐字节相同**（blob SHA 全部钉住）；
2. 因此两条路径的差异**只可能来自 `gateway.py` 本身** —— 这正是 C2 改动的全部范围；
3. 也就是说，A2 差分测的是"C2 改动前后的 gateway 路由语义"，而不是别的变量；
4. 反过来，若将来有阶段改动协议层，pin 会 **fail-closed**，A2 的有效性前提
   **不会被静默破坏**。

实测：

```
shared dependency drift = {}     （12/12 模块 hash 与 4059cf5 一致）
```

> **pin 的正确处置方式**：若 C3 确实需要修改协议层，**不得**静默更新 pin ——
> 必须先重新评估 A2 差分是否仍然成立，并记录理由。

### 4.2 已知的 harness 盲区（诚实记录）

两条路径共用 `c2_differential_lib` 的**实例构造**与**状态投影**口径，
因此该口径自身的 bug 对"两侧比较"不可见。

**但 A2 对此有 A1 所没有的防护**：两侧各自与**冻结 Golden** 的锚点断言
（§7）会在这个口径坏掉时失败 —— 因为 Golden 是**独立于本模块**写下的历史数据。

（A1 的 capture 与 replay 共用同一个 `c2_gateway_golden_lib`，因此 A1 对该盲区
**完全没有**防护。这是 A2 相对 A1 的净增益之一。）

---

## 5. 比较维度

| 维度 | 内容 | 是否差分轴 |
|---|---|---|
| **response** | 条数、逐条 `(response_id, payload)` 字节、顺序（有定义时）、target identity（`response_id`） | ✅ **是** |
| **state projection** | 逐 ECU 三字段（`session` / `security_level` / `did_overrides`） | ✅ **是** |
| **metadata** | `case_id` / `can_id` / `payload` / 两侧执行计数 | ✅（定位用） |
| **PolicyTrace** | —— | ❌ **不是** |

### 5.1 trace 为什么**不是**差分轴

Legacy 结构上**没有** trace（corpus 头 `policy_execution = "absent"` 是历史事实）。
把 `PolicyTrace` 当作"第三个跨实现差分维度"是**范畴错误**。

它的正确定位是 **Current-only 辅助不变量**：证明"策略**确实被求值过**"，
而不只是"什么都没发生"（设计文档 §9.2.6）。A2 中对应
`TestCurrentOnlyTraceEvidence`：
legacy 侧 `Execution.trace is None`；current 侧 trace 形状与输入一致
（空 payload / 未知 target ⇒ 0 条；physical ⇒ 1 条；functional ⇒ N 条且顺序 ==
`self.ecus` 遍历顺序）；空策略下全部 `allowed=True`。

### 5.2 response ordering：**结构上退化**，故不比较（而非"忘了比"）

当前模型下，`route()` 返回的 response 列表长度恒 ≤ 1：

```
physical 非空 payload  → 至多 1 条
physical suppress      → 0 条
functional             → **恒** 0 条（网关不收集广播响应）
unknown target         → 0 条
```

因此 response **序列**没有可比较的顺序；顺序语义只存在于
current 侧 trace 的 decision 序列（§5.1）与"逐 ECU 状态投影"（§5 第 2 行）。

### 5.3 扇出必须逐 ECU 比较（aggregate 无差分敏感性）

`F1`–`F4` 的 response 两侧都是 `[]` —— 只比 aggregate **完全无法**发现
"错误扇出 / 漏扇出"。A2 因此断言：功能寻址 case 的状态投影必须覆盖**每一个**
ECU，且**逐 ECU** 一致。

### 5.4 差分的非平凡性约束（防"同向退化"）

差分**只能证明"两者相同"**，不能证明"两者都做了该做的事"。若广播在两侧
**同向退化**成 no-op，则 response（两侧都是 `[]`）与 state（两侧都没变）
依然一致 —— 差分照样通过。

因此 A2 另行断言：功能寻址 case 中**实际发生状态变化的 ECU 集合**，必须等于
冻结 Golden 记录的集合；并进一步要求广播至少真正到达全部三个 ECU。

> 这条约束的必要性来自 A2 方法论本身：**任何"两条实现对比"都要额外证明
> 对比对象没有同时退化**。

---

## 6. 测试矩阵与执行

**输入集 = 冻结 corpus 的 20 条 case，不新增、不修改、不重生成。**

```
cases                = 20
legacy executions    = 20
current executions   = 20
response matches     = 20 / 20      ← legacy-live vs current-live（核心）
state    matches     = 20 / 20      ← 逐 ECU 三字段
legacy  anchor       = 20 / 20      ← legacy-live  vs 冻结 golden
current anchor       = 20 / 20      ← current-live vs 冻结 golden
mismatches           = 0
```

覆盖（沿用 A0 的 20 条既有分类，A2 **未扩写**）：

| 组 | case | A2 验证 |
|---|---|---|
| P / N | `0x7E0`/`0x7E1`/`0x7E2` 读；ECU 负响应转发 | response 字节 + target identity |
| S | `3E 80` 抑制 | 两侧均不发帧 |
| U | 未知物理 target `0x7E5` / `0x7E3` | 两侧均静默 |
| F | 广播 `10 03` / `22 F1 90` / `3E 80` / `2E F1 02` | **逐 ECU** 状态扇出 |
| W / I | 物理写入、多 ECU 状态隔离 | 逐 ECU 状态投影 |
| M | 空 payload、未注册 SID | 协议层处理路径一致 |
| C | 自定义 handler / 自定义拓扑 | override 基线一致 |
| D | `3E 00` 无副作用服务 | 一致 |

---

## 7. Golden 的定位（**锚点 ≠ 核心证明**）

```
legacy-live  == frozen golden     ← 证明 legacy 冻结副本的**重建是忠实的**
current-live == frozen golden     ← 回归锚点（与 C2-A1 同口径）
```

这两项**只是**"各自与历史锚点一致"的一致性证据。

**真正证明两条实现路径行为一致的是** `legacy-live == current-live`，
即 `DifferentialResult.matched` —— A2 的 gate 判据是
**`mismatches == 0`，与锚点无关**。

> 若出现"差分通过但锚点失败"，说明**两侧同时**偏离了历史事实
> （共享依赖或口径变化），那是另一类缺陷；反之"锚点通过但差分失败"
> 则是 C2 引入的真实行为变化。

---

## 8. 差分模型与失败输出

```
Execution(implementation, results, state, trace, executions)
        │
        ├── legacy  ─┐
        └── current ─┴─→ compare() → DifferentialResult
                                      ├── response_matches  → response_diffs（按位置，含缺失侧）
                                      ├── state_matches     → state_diffs（ECU → field → 两侧值）
                                      └── failure_report()  → 定位到 case/can_id/payload/ECU/field
```

失败输出示例（由 `TestFailureReportIsAuditable` 用**合成**执行结果验证，
不制造真实失配）：

```
[MISMATCH] case=X-synthetic
  description = 合成 case：只为验证失败报告的定位能力
  can_id      = 0x7E0
  payload     = 22 f1 90
  executions  = legacy=1 current=1
  response    : 条数 legacy=1 current=0
    [0] legacy  = (0x7E8, 62 f1 90)
        current = <missing>
  state       : 逐 ECU 差异
    ECU_A.session: legacy=1 current=2
```

比较器另有两条自防护断言：拒绝把两条路径的 `Execution` 传反；
一致时 `response_diffs` / `state_diffs` 必须为空（防"永远报差异"的比较器）。

---

## 9. 测试入口与结果

```bash
# A2 专项
python3 labs/vehicle/uds-basic/tests/test_c2_a2_legacy_current_differential.py
python3 -m pytest labs/vehicle/uds-basic/tests/test_c2_a2_legacy_current_differential.py

# 相关既有测试
bash labs/vehicle/uds-basic/tests/run_tests.sh unit

# 完整快速门禁
bash scripts/qa_fast.sh

# 逐 case 明细（默认只输出汇总）
C2_A2_VERBOSE=1 python3 labs/vehicle/uds-basic/tests/test_c2_a2_legacy_current_differential.py
```

（权威环境：WSL Ubuntu venv，Python 3.12.3）

| 项 | 结果 |
|---|---|
| A2 专项（unittest） | **46 / 46 PASS** |
| A2 专项（pytest） | **46 passed, 259 subtests passed** |
| A0 回归 `test_c2_a0_gateway_golden.py` | **28 / 28 PASS**（未受影响） |
| A1 回归 `test_c2_a1_empty_policy_trace.py` | **42 / 42 PASS**（语义未变） |
| `run_tests.sh unit` | **PASS 21 / WARN 0 / FAIL 0**（A1 时为 20） |
| `qa_fast.sh` | **PASS**（ruff OK / mypy OK / uds unit PASS 21 FAIL 0 / can-tools offline PASS 4 FAIL 0） |

A2 测试文件的 46 例分布：

```
TestLegacyFrozenCopy                     7   哈希 / 符号 / git 交叉校验 / 两模块不同
TestControlledSharedDependencies         2   12 模块 hash 无漂移
TestTwoPathsAreIndependentlyInstantiated 7   gateway / ECU / EcuState 均不共用
TestDifferentialGate                     9   核心差分 gate + 逐 ECU 扇出 + 非平凡性约束
TestGoldenAnchors                        6   两侧锚点（明示为一致性证据）
TestFrozenInputSetIsUntouched            3   输入集只读、20 条、未扩写
TestFailureReportIsAuditable             7   失败输出定位能力
TestCurrentOnlyTraceEvidence             4   trace 作为 Current-only 辅助不变量
TestEmitEvidenceReport                   1   汇总数字输出
```

---

## 10. Production Diff

```
production diff = 0
    labs/vehicle/uds-basic/ecu/gateway.py                  UNCHANGED
    labs/vehicle/uds-basic/ecu/**                          全 UNCHANGED
```

**未修改**：`gateway.py` / 其余 ecu 模块 / `golden/c2_gateway_legacy.json` /
`c2_gateway_golden_lib.py` / A0 测试 / A1 测试 / `docs/TEST_MATRIX.md` /
`ci.yml` / C1 面（`_SPEC_EXECUTION`）/ 0x27。

---

## 11. 已知限制（如实记录，不伪造覆盖）

1. **输入集固定 20 条**。Gateway 默认 handler 的静态表含
   `0x10 / 0x11 / 0x22 / 0x2E / 0x3E`，而冻结 Golden 只用到
   `0x10`(仅 functional) / `0x22` / `0x2E` / `0x3E`
   —— **`0x11` 与 physical `0x10` 未被覆盖**。
   本阶段**刻意不扩写输入**（不修改 Golden、不新增 case）；
   该缺口是冻结输入集本身的覆盖边界，**不是** A2 的结论适用范围之外的伪造覆盖。
2. **response ordering 结构上退化**（§5.2），故不比较。
3. **Legacy 是历史冻结实现，不是并存运行时路径**（§3.1）。
4. **共享依赖 pin 会随协议层演进而 fail-closed**（§4.1）——
   这是期望行为，但需要人工重新评估而非静默更新。
5. **harness 口径盲区**（§4.2）：由两侧 Golden 锚点部分补偿。
6. **A1 报告 §14 仍写 "C2-A2 = NOT STARTED"** —— 该文档是 A1 阶段的历史报告，
   本次**未修改**（文件范围限定）；A2 的状态以本文件为准。
7. 本阶段**未**进入 C2-A3 / A4 / A5 / C3；未做 deny / bypass 审计
   （deny 在 legacy 中不存在，**不能**作为 Legacy↔Current 差分维度——
   正式的 deny 与 bypass 审计属 **C2-A4**）。

---

## 12. 结论

| # | 判据 | 结果 |
|---|---|---|
| 1 | Legacy 冻结副本逐字节可证（blob + LF SHA256） | ✓ |
| 2 | git 可用时与 `git show 4059cf5` 交叉校验 | ✓（`matches=True`） |
| 3 | 无 git 时 A2 仍可执行 | ✓（第 3 层降级为 skip） |
| 4 | 两条路径**各自**实例化（gateway / ECU / EcuState 均不共用） | ✓ |
| 5 | 两条路径**各自**执行恰好一次 | ✓（20 + 20） |
| 6 | response 逐字节一致 | ✓ **20 / 20** |
| 7 | state 逐 ECU 三字段一致 | ✓ **20 / 20** |
| 8 | `mismatches == 0`（核心 gate 判据） | ✓ |
| 9 | 两侧与冻结 Golden 的锚点一致 | ✓ 20/20 + 20/20 |
| 10 | trace 仅作 Current-only 辅助不变量 | ✓ |
| 11 | 受控共享依赖 12 模块 hash 无漂移 | ✓ |
| 12 | 扇出**确实发生**（防同向退化，§5.4） | ✓ |
| 13 | 失败输出可定位到 case / ECU / field | ✓ |
| 14 | production diff = 0，Golden 未改动 | ✓ |
| 15 | A0 / A1 回归未受影响 | ✓ 28/28、42/42 |

```
C2-A2 = PASS
```

---

## 13. 下一步

```
C2-A3 = NOT STARTED   （full regression）
C2-A4 = NOT STARTED   （override / bypass 不变量审计，§12）
C2-A5 = NOT STARTED   （exit review）
C3    = NOT STARTED
```

**A2 完成后不自动进入 A3。**

---

# C3-A1-X1 Shared Dependency Rebaseline Addendum

> 追加日期：2026-09-26 ／ 触发阶段：**C3-A1** ／ 裁决：**X1-a**
>
> **本节为追加记录。上文第 1–13 节为 C2-A2 当时的历史事实，一字未改。**

## X1.1 原始 C2-A2 baseline（历史事实，未变）

C2-A2 当时，受控共享依赖被钉在 **`4059cf5`（C2 捕获 commit）时代** 的 blob：

```
uds_server.py       72342bc1a8decc44fdb857071e54cd87a31ec101
ecu.py              710d6515e1e50204c398f82e3368559d87b4c806
gateway_runtime.py  85604271db0640c732bb32cffd3f36047ebbcece
（另 9 个模块的 pin 一并见 tests/c2_differential_lib.py）
```

该 pin 使"**两条路径只差 `gateway.py`**"成为**可验证事实**而非假设 —
任何一项漂移都会 **fail-closed**。本节记录的原始结论**完整保留**：

```
4059cf5-era
response matches     = 20 / 20
state    matches     = 20 / 20
legacy  anchor       = 20 / 20
current anchor       = 20 / 20
mismatches           = 0
```

## X1.2 C3-A1 的有意修改

C3-A1（按 **C3 Design Freeze** 的候选 C）**有意**修改了上表中的三个模块：

```
uds_server.py       新增 security_access_services(*, now, security_config)
                    —— 0x27 运行时依赖绑定的**唯一定义**（C3-OD-4）
ecu.py              _security_services() 改为**委托**上述唯一绑定定义
gateway_runtime.py  新增入口层 runtime provider wiring
                    （_gateway_services_provider + build_runtime 参数）
```

## X1.3 三处修改的性质（逐项核实）

| 模块 | 性质 | 对 C2-A2 的 20 条差分执行路径 |
|---|---|---|
| `uds_server.py` | **纯增量**：`+25` 行 / **0 删除行**，只新增函数 | 无影响 |
| `ecu.py` | 仅改变**绑定实现**；`_now()` 仍按 **`ecu` 命名空间**解析、`SECURITY_CONFIG` 仍是**原对象引用** | 无影响 |
| `gateway_runtime.py` | **入口层**纯增量 | **C2-A2 的 20 条差分执行不经过 `gateway_runtime`** |

独立佐证（C3-A1 阶段实测）：

```
22 组输入（覆盖全部 14 个 SID + 未注册 SID + 空请求）在
    services=None  vs  {**SERVICES}  vs  {**SERVICES, 0x27}
三种解析路径下逐一比较 ⇒ 不一致数 = 0 / 0
```

另：C3-A1 对 `gateway.py` 的改动中**没有任何 policy 相关行**
（`DEFAULT_POLICY` / `PolicyDecision` / `_allow(` / `self.policy` 一处未碰）
⇒ **C2 policy 语义未被触碰**。

## X1.4 fail-closed 已正确触发

```
[FAIL] test_no_shared_dependency_drift
    "协议层共享依赖已偏离 4059cf5 —— A2 的差分前提失效，
     必须重新评估（而不是静默更新 pin）"
```

**该失败是 pin 的预期行为，不是 C3 实现缺陷。** 它精确地报告了
"C3-A1 已按冻结设计修改了被锁定的模块"这一事实。

## X1.5 本次处置的性质

```
这不是"放宽检查"，而是一次：
    "C3-authorized shared dependency baseline rebaseline"
```

具体地：

```
未删除   CONTROLLED_SHARED_DEPENDENCIES
未删除   shared_dependency_drift()
未删除   fail-closed 检查
未弱化   为模糊的语义检查（未采用 X1-c）
未修改   旧 legacy source pin（4059cf5 的 commit / blob / LF SHA 全部不变）
未修改   20 条 frozen case
未修改   c2_gateway_legacy.json
未修改   legacy gateway frozen copy
```

**只做了**：把上表中 3 个条目的 active pin 从 `4059cf5` 时代的值
**受控迁移**到 C3-A1 之后的值，并在 pin 表处与本节留下
**old → new 的显式记录**。表语义（逐模块字节同一性、任何漂移即失败）**未变**。

```
uds_server.py       72342bc1… → 94c74e12…
ecu.py              710d6515… → 0817e17b…
gateway_runtime.py  85604271… → 0c12cb7b…
（其余 9 个条目仍指向 4059cf5 时代的 blob，未变）
```

## X1.6 Rebaseline 后的重新验证

按 A2 文档 §4.1 的处置纪律（"不得静默更新 pin —— 必须先重新评估
A2 差分是否仍然成立，并记录理由"），rebaseline 之后**重新执行**了完整差分：

```
python3 labs/vehicle/uds-basic/tests/test_c2_a2_legacy_current_differential.py

Ran 46 tests … OK

cases                = 20
legacy executions    = 20
current executions   = 20
response matches     = 20 / 20
state    matches     = 20 / 20
legacy  anchor       = 20 / 20
current anchor       = 20 / 20
mismatches           = 0

test_no_shared_dependency_drift = PASS
```

同时确认：

```
frozen case set      = 仍为原 20 条（首 P1-physical-ecu-a / 末 D1-tester-present）
legacy impl blob     = e64cd088fc86bf9e6b1c5dfb2bb1b80cae5ab39a（不变）
C2 Golden blob       = 541395b82e74ce915830fea23369e21c65fe7b27（不变）
corpus 头            = policy_execution = "absent" / captured_at_commit = 83d1f52d…（不变）
C2 policy semantics  = 未被触碰
```

**结论**：原始历史结论（§X1.1）与 Rebaseline 后的重新验证（§X1.6）一致 ——
差分在 C3-A1 之后**仍然成立**。
