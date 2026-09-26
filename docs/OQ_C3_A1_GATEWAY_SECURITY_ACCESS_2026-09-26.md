# OQ C3-A1 —— Gateway 路径的 0x27 SecurityAccess（实现报告）

> 状态：**C3-A1 = PASS（实现完成且验证通过）**
> **但存在 1 项未授权事项，导致 CI 变红** —— 见 §8 **冲突点 C3-A1-X1**
> 日期：2026-09-26
> 分支：`audit/oq-c3-security-access-gateway-a1-implementation-20260926`
> 基线：`main = a387084656e423eb2501625257a8c81e7952d559`（= C3-A0 merge commit，PR #51）
> 设计出处：`docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md`

---

## 1. Scope

```
目标：让 Gateway 路径能够执行**既有的** 0x27 SecurityAccess 状态机。

本阶段做    production implementation（候选 C）+ C3-B1 迁移 + 契约测试 + 本报告
本阶段不做  Direct↔Gateway 差分终判（A2）/ provider bypass 完整审计（A4）/ full regression closure（A3）
```

---

## 2. 实现（DESIGN DECISION 逐条对应）

严格实现已冻结的 **候选 C**，四个文件、无第五个：

### 2.1 `uds_server.py` —— 唯一绑定定义（C3-OD-4）

```python
def security_access_services(*, now, security_config) -> dict[int, Callable]:
    """0x27 运行时依赖绑定的**唯一定义**。"""
    return {0x27: _make_security_access_handler(now=now, security_config=security_config)}
```

**纯增量**：`+25` 行、**0 删除行** —— 未改动任何既有函数。

### 2.2 `gateway.py` —— provider 注入（C3-OD-2 / OD-6）

```
新增类型别名   ServiceTable / ServicesProvider
VirtualEcu     新增可选字段 services_provider（默认 None）
_default_handler_for(state, services_provider=None)
               provider is None → **原路径**（handle_uds_request(payload, state)）
               provider 非 None → 每请求
                   handle_uds_request(payload, state,
                                      services={**uds_server.SERVICES, **services_provider()})
DiagnosticGateway(…, services_provider=None)
               应用到**未自带** provider 的 ECU（ECU 级优先）
build_default_gateway(services_provider=None)
               默认 None ⇒ **不接线**（C3-OD-7：机制与接线分离）
```

**`gateway.py` 仍不感知时钟与配置**（C3-OD-3）—— 由结构断言常驻锁定。

### 2.3 `gateway_runtime.py` —— 入口层注入（C3-OD-3）

```python
def _gateway_services_provider():
    import ecu
    return uds_server.security_access_services(
        now=ecu._now(),                    # ← 按**名字**解析，保持 patch seam
        security_config=ecu.SECURITY_CONFIG,  # ← 原对象引用
    )

build_runtime(…, wire_security_access: bool = True)   # 默认接线
```

`gateway_runtime` **仍不含** `policy` / `_allow` / `_by_request` / `PolicyDecision`（C3-OD-14 限制 3/4，常驻断言）。

### 2.4 `ecu.py` —— 委托新定义（C3-OD-4）

```python
def _security_services():
    return uds_server.security_access_services(
        now=_now(),                        # ← 仍在 ecu 命名空间解析
        security_config=SECURITY_CONFIG,   # ← 原对象引用
    )
```

`_now()` / `SECURITY_CONFIG` 的**解析语义未变** ⇒ 既有 patch seam 保持有效。

---

## 3. 接线前的等价性验证（**先证后用**）

把 gateway 的 `services=None` 换成 `{**SERVICES, **provider()}` 会改变**所有**服务的解析路径，
因此接线前必须先证明它**不改变任何既有服务**：

```
静态核对：SERVICES 与 SERVICE_SPECS 的 key set **完全相同**（各 14 个），
          且 14 个 SID 的 handler 都是**同一对象**（guards 数各自保留）

实测核对：22 组输入（覆盖全部 14 个 SID + 未注册 SID + 空请求）
          services=None  vs  {**SERVICES}  vs  {**SERVICES, 0x27}  逐一比较
          不一致数 = 0 / 0
```

⇒ 接线是**行为保持**的；A2 的 20 条 golden 也因此不受影响（§8 实测）。

---

## 4. C3-B1 迁移（C3-OD-15）

### 4.1 迁移前后 evidence

```
迁移前（C3-A0 冻结的实现前事实，仍完整保留在
        tests/golden/c3_security_access_baseline.json —— **未被重写**）：

    Gateway 物理 27 01 → 0x7E8: 7F 27 11（serviceNotSupported）

迁移后（C3-A1 新契约）：

    Gateway 物理 27 01 → 0x7E8: 67 01 <4 字节 seed>
```

### 4.2 迁移动作

```
1  test_gateway_runtime_vcan.py
   test_security_access_is_not_reachable_through_the_gateway_layer
       → test_security_access_is_reachable_through_the_gateway_layer（改为可达契约）
   + 新增 test_other_ecus_do_not_answer_a_physical_security_access
   模块 docstring 的服务集说明同步更新
   **未新增 import**（沿用该文件"本地定义协议常量"的既有风格）

2  test_c3_a0_baseline.py（A0 文件）
   捕获护栏断言：  "机制不存在" → "机制存在 ⇒ 捕获被拒绝（脚本返回 2）"
   边界行断言：    "重放并断言等于实现前取值" → **静态**读取冻结 JSON 断言历史事实
   + 新增反向自证：边界行**不得**被重放为 live 断言

3  tests/golden/c3_security_access_baseline.json —— **未改动**（SHA256 pin 仍绿）
```

**没有"让 A0 test 继续错误地绿色"**：历史记录与 live 契约被显式分离，
两者各自有常驻断言。

---

## 5. 覆盖矩阵（对照阶段要求的 14 项）

| # | 要求 | 用例类 | 结果 |
|---|---|---|---|
| 1 | physical `27 01` → 正确 seed + 正确 response_id | `TestPhysicalRequestSeed` | ✓ |
| 2 | `27 02` 正确 key → 复用既有状态机 | `TestSendKeyReusesExistingStateMachine` | ✓ |
| 3 | 错误 key → 35 / 36 + `failed_attempts` | `TestWrongKeyAndLockout` | ✓ |
| 4 | lockout → 37，且**只影响目标 ECU** | 同上 | ✓ |
| 5 | seed 过期 → 24 | `TestSeedLifecycle` | ✓ |
| 6 | 已消费 seed 重放 → 24 | 同上 | ✓ |
| 7 | 已解锁再请求 seed → 22 | 同上 | ✓ |
| 8 | 切 session → 重新 LOCKED | `TestSessionAndResetRelock` | ✓ |
| 9 | ECU reset → 重新 LOCKED（惩罚状态保留） | 同上 | ✓ |
| 10 | 受保护 DID：locked → 33；unlocked → 肯定 | `TestProtectedDidThroughGateway` | ✓ |
| 11 | 响应 ID 映射 A→0x7E8 / B→0x7E9 / C→0x7EA | `TestPhysicalRequestSeed` | ✓ |
| 12 | policy deny → 静默丢弃 + `process()` 不被调用 + trace 正确 | `TestPolicyInteraction` | ✓ |
| 13 | policy allow → 继续进入目标 ECU 0x27 | 同上 | ✓ |
| 14 | multi-ECU 隔离（为 A2 提供实现基础） | `TestMultiEcuIsolation` | ✓ |

附加：`TestProviderSemantics`（OQ-12 冻结的 10 条限制的主要部分）、
`TestStructuralInvariants`、`TestFunctionalSecurityAccessIsTheDocumentedLimitation`。

---

## 6. policy allow / deny evidence

```
allow：policy 只看到 (0x7E0, "physical", 0x27)；随后进入目标 ECU 的 0x27 → 67 01 <seed>
deny ：results == []（静默丢弃）
       trace.decisions == [PolicyDecision(target=0x7E0, "physical", 0x27, allowed=False)]
       target.process() **不被调用**（spy：calls == []）
       目标 ECU 的 pending_seed 保持 None（0x27 状态未被推进）
provider 存在与否**不改变**策略的求值次数与顺序
```

**C2-OD-5 未被改动**：策略输入仍是三元组，0x27 只作为 `sid = 0x27` 的取值出现。

---

## 7. multi-ECU preliminary evidence（终判属 A2）

```
INV-1  A 解锁后 B/C 仍 LOCKED                    ✓
INV-2  A 进入 lockout 后 B/C 仍可正常 27 01        ✓
INV-3  seed_counter / pending_seed 严格 per ECU   ✓（A=2 时 B=C=0）
INV-5  每个 ECU 的 handler 绑定自己的 EcuState     ✓（三 ECU 各自 counter 独立递增）
EcuState 对象两两不同（id 去重后数量不变）          ✓
```

**functional 0x27 = ALLOW（C3-OD-8）如实保留**，其 known limitation 被显式断言：

```
functional 27 01 → []（恒不收集响应）
但 A/B/C 三个 ECU 的 pending_seed 与 seed_counter **都被推进**
security_level 不变、failed_attempts 不变
策略在 functional 下**逐 ECU** 求值（三元组顺序 == self.ecus 遍历顺序）
```

并附反向自证：C3 **未**为 functional 添加任何专门分支
（`gateway.py` / `gateway_runtime.py` 中不存在 `is_functional` 等标记）。

---

## 8. ⚠️ 冲突点 C3-A1-X1 —— C2-A2 的 shared-dependency pin 失败

### 8.1 现象

```
[FAIL] C2 A2 legacy / current differential audit failed

test_no_shared_dependency_drift
    {'ecu.py':            ('710d6515…', '0817e17b…'),
     'gateway_runtime.py':('85604271…', '0c12cb7b…'),
     'uds_server.py':     ('72342bc1…', '94c74e12…')}
    "协议层共享依赖已偏离 4059cf5 —— A2 的差分前提失效，
     必须重新评估（而不是静默更新 pin）"
```

### 8.2 性质分析

**差分本体仍然成立**（实测）：

```
cases = 20 ／ legacy 20 ／ current 20
response matches = 20/20     state matches = 20/20
legacy anchor = 20/20        current anchor = 20/20
mismatches = 0
```

失败的**只是结构性 pin** —— 它检查"C2 捕获时的那 12 个协议层模块是否仍逐字节相同"。

三个模块的漂移**性质**（逐项核实）：

| 模块 | 漂移性质 | 是否影响 A2 的 20 条 case |
|---|---|---|
| `uds_server.py` | **纯增量**（+25 / **0 删除**，新增一个函数） | 否 |
| `ecu.py` | `_security_services()` 改为委托，**参数与命名空间解析完全不变** | 否 |
| `gateway_runtime.py` | 纯增量（新增 provider + 1 个参数；A2 路径不使用） | 否 |

另有 §3 的 22 组输入等价性实测作为独立佐证。

### 8.3 为什么**没有**自行更新该 pin

```
1  C3 Design Freeze 只授权了一处 pin 迁移 —— C3-OD-15 明确指向 **vcan 旧 pin**；
   设计文本与 §9.3 CONSTRAINT 列表**均未提及** A2 的 shared-dependency pin。
2  C3-A1 的授权范围是
       "production implementation / 对应新·迁移测试 / 必要的 vCAN e2e 更新 / 必要文档同步"，
   而该 pin 属 **C2-A2 的冻结证据**，修改它会改动 C2 阶段的被测文件。
3  A2 文档自身写明处置纪律：
       "若 C3 确实需要修改协议层，**不得**静默更新 pin ——
        必须先重新评估 A2 差分是否仍然成立，并记录理由。"
   ⇒ 正确的动作是**重新评估 + 记录理由 + 由授权方决定**，不是自行改。
4  用户本轮明示：
       "如果过程中发现设计与实现发生冲突，必须停止实现性扩张，
        只报告冲突点，不能自行修改 C3 Design Freeze。"
```

因此本阶段**未修改任何 C2 测试文件**，并把该点作为**待裁决冲突**上报。

### 8.4 可选处置（**需你明确选择**）

| 选项 | 内容 | 影响 |
|---|---|---|
| **X1-a** | 授权在 C3-A1 **内**更新 A2 的 `CONTROLLED_SHARED_DEPENDENCIES` pin，并**附上本节的重新评估理由**写入 A2 文档的补充说明 | 需改 `test_c2_a2_legacy_current_differential.py` + A2 报告；CI 转绿 |
| **X1-b** | 把该 pin 更新推到 **C3-A3（full regression）** 统一处理 | 本 PR 的 CI 保持**红色**，A3 前一直如此 |
| **X1-c** | 改为**按语义而非字节**检查共享依赖（例如只断言"已被 C3 显式评审过的改动"） | 需要重新设计与评审，属新工作 |

**我未采取任何一项** —— 等你裁决。

---

## 9. 回归结果

| Gate | 结果 | 说明 |
|---|---|---|
| C3-A1 专项 | **48 / 48 PASS**（skipped 0） | 新增 |
| C3-A0 专项（已迁移） | **35 / 35 PASS** | golden 未重写，SHA256 pin 仍绿 |
| `run_tests.sh unit` | **PASS 23 / WARN 0 / FAIL 1**（1158 cases） | FAIL 1 = C2-A2 的 pin（§8） |
| `run_tests.sh all` | **PASS 35 / WARN 0 / FAIL 1** | 同上；**vcan e2e 全部通过** |
| `test_gateway_runtime_vcan.py`（vcan0） | **22 / 22 PASS**（23.4s） | 含迁移后的可达契约 |
| can-tools `run_tests.sh all` | **PASS 7 / WARN 0 / FAIL 0**（358 cases） | 未受影响 |
| `qa_fast.sh` | **FAIL（exit=1）** | ruff OK / mypy OK / uds unit FAIL 1 / can-tools OK |
| ruff / mypy | **OK** | —— |

```
除 §8 的 C2-A2 pin 外，全部 PASS；FAIL 数 = 1（唯一且已归因）。
```

---

## 10. 已知限制

```
1  functional 0x27 的空转副作用按设计保留（§7）——
   专门抑制机制属 DEFERRED（C3-OQ-10）。
2  Direct ECU ↔ Gateway 的**等价性终判**不在本阶段（C3-A2）。
3  provider override 的**完整 bypass 审计**不在本阶段（C3-A4）。
4  vcan 层只覆盖了契约迁移与新增的 2 个用例，未做 0x27 的完整 vcan 矩阵（属 A3/A4 范围）。
5  `services_provider` 与 `handler` 一样是**构造期**绑定：构造后再赋值不会重新绑定
   （与既有 `handler` 字段语义一致，已由用例固化）。
```

---

## 11. 结论

| # | 判据 | 结果 |
|---|---|---|
| 1 | 严格实现候选 C（四文件，无第五个） | ✓ |
| 2 | 0x27 状态机**仍只有一份**（`uds_server.service_security_access`） | ✓ |
| 3 | 未把 0x27 加入 `SERVICES` / `SERVICE_SPECS` | ✓ |
| 4 | C1 guard 语义未被绕过（provider 换 handler ≠ 换授权） | ✓ |
| 5 | C2 policy 维度仍是 `(target, addressing_mode, SID)` | ✓ |
| 6 | `gateway.py` 不感知时钟 / 配置 | ✓ |
| 7 | `ecu._now` patch seam 与 `SECURITY_CONFIG` 引用语义保持 | ✓ |
| 8 | `now` 每请求重新求值、不缓存 | ✓ |
| 9 | 14 项覆盖全部具备证据 | ✓ |
| 10 | C3-B1 迁移完成（vcan pin + A0 边界断言），golden **未重写** | ✓ |
| 11 | 接线前等价性已先证后用 | ✓ |
| 12 | **C2-A2 的 shared-dependency pin** | ✗ **未授权事项（§8）** |

```
C3-A1 = PASS（实现与验证）
       + 1 项待裁决冲突点 C3-A1-X1（C2-A2 pin），非实现缺陷
```

---

## 12. Next Stage

```
C3-A1（本文档）           ← 实现完成
      ↓ 需先裁决 C3-A1-X1
C3-A2  Direct ECU ↔ Gateway differential
      ↓
C3-A3  Full regression
      ↓
C3-A4  Override / bypass audit（含 provider seam）
      ↓
C3-A5  Exit Review
```

**未自行 merge；未进入 A2。**
