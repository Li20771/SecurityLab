# C1 Phase 2 Migration B0 —— 0x2E legacy golden capture（证据报告）

> 状态：**B0 = PASS**
> 日期：2026-09-25
> 分支：`audit/oq-c1-phase2-b0-0x2e-golden-20260925`
> 基线：`main = 19d146bf931b78e143d72af22dd10f05f2708f9a`（PR #36 merge commit）
> 上游设计：`docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md` §12 / §13 / §14

**本报告只记录捕获事实，不引入新的设计决策。本阶段不做 spec 对拍。**

---

## 1. 基线

| 项 | 值 |
|---|---|
| `main` commit | `19d146bf931b78e143d72af22dd10f05f2708f9a` |
| B0 分支 | `audit/oq-c1-phase2-b0-0x2e-golden-20260925` |
| B0 分支起点 | `19d146bf931b78e143d72af22dd10f05f2708f9a`（= main） |
| `working tree`（捕获前） | clean |
| Migration A | **COMPLETE**（PR #30–#36 均已合并） |

### C1 迁移状态（运行时实测）

```
_SPEC_EXECUTION = frozenset({0x22})      （uds_server.py:2397）
0x22 = ON      （_guards_for() 返回 1 个 guard）
0x2E = OFF     （_guards_for() 返回 ()）
0x27 = unchanged（∉ SERVICE_SPECS、∈ SPECS_EXCLUDED、resolve(0x27, None) is None）
```

### 捕获前 dry validation（脚本 §18）

```
_SPEC_EXECUTION   = frozenset({0x22})
0x22 enabled      = True
0x2E enabled      = False
_guards_for(0x2E) = ()
```

---

## 2. Oracle 路径（B0 最重要的一句话）

> **整个 dispatcher 并非纯 legacy。**
>
> 本 corpus 的 **target SID = 0x2E**。`0x2E ∉ _SPEC_EXECUTION`。
> 因此每个 B0 请求的 **target execution path = legacy handler**。

即：

```
target SID 0x2E
    ↓  0x2E ∉ _SPEC_EXECUTION = {0x22}
_guards_for(0x2E) == ()
    ↓
service_write_data_by_identifier()
    ↓
legacy oracle
```

**这与 A0 有本质区别**：A0 时 `_SPEC_EXECUTION` 为空，可断言"整表白名单为空"；
B0 时 0x22 已迁移，该断言**必然失败**。因此捕获脚本的护栏改为 **target SID 专项**：

```python
assert 0x2E not in _SPEC_EXECUTION      # 允许 0x22 已启用，禁止 0x2E 已启用
```

不成立即拒绝写入并 `exit 2`，避免把 spec 路径的输出误记为 legacy oracle。

---

## 3. Corpus

**文件**：`labs/vehicle/uds-basic/tests/golden/c1_phase2_0x2e_legacy.json`
（**独立文件**，不覆盖 A0 的 `c1_phase2_0x22_legacy.json`）

### 头部

```json
"schema_version": 2,
"sid": 46,                        "sid_hex": "0x2E",
"target_sid": 46,
"target_execution_mode": "legacy",
"dispatcher_mode": "legacy",
"spec_execution_at_capture": [34],
"state_projection_fields": ["session", "security_level", "did_overrides"],
"captured_at_commit": "19d146bf931b78e143d72af22dd10f05f2708f9a"
```

> **关于 `schema_version`**：A0 的 0x22 corpus 保持 **v1 不变**（其常量仍在
> `c1_golden_lib.SCHEMA_VERSION`）。B0 corpus 使用 **v2**，因为在 v1 字段集上
> **新增**了 `target_sid` / `target_execution_mode` 两个显式字段。
> 之所以升版而非"悄悄扩展 v1"：B0 时点整个 dispatcher **不是**纯 legacy，
> 单看 `dispatcher_mode` 无法无歧义表达"本 corpus 的**目标服务**走的是哪条路径"。
>
> `dispatcher_mode = "legacy"` 在 B0 corpus 中的含义是
> **"本 corpus 的 target SID 的执行路径"**；真实白名单内容见
> `spec_execution_at_capture = [34]`（即只有 0x22）。两个字段合读即无歧义。

### 规模与分组

```
case_count = 70
```

| 组 | 数量 | 内容 | 目标 rung |
|---|---|---|---|
| **L** | 18 | 3 条畸形长度 × 6 状态 | ① `0x13` |
| **D** | 12 | 2 个未知 DID（`0xFFFF` / `0x0000`）× 6 状态 | ② `0x31` |
| **W** | 6 | 可写 DID `0xF102` × 全部 6 状态 | ③④⑤⑥ 矩阵 |
| **R** | 18 | 3 个已知只读 DID（`0xF190` / `0xF180` / `0xF187`）× 6 状态 | ⑤ `0x22` |
| **N** | 10 | 数据长度 `{1,2,15,16,17}` × 2 可达状态 | ⑥ `0x13`（上界/越界）|
| **O** | 6 | `did_overrides` 预置与状态副作用 × 2 可达状态 | 成功路径 / ⑤ / ② |

输入空间全部取自**真实实现**：

```
DID_DATABASE         = {0xF180, 0xF190, 0xF187, 0xF102}
WRITABLE_DIDS        = {0xF102}
WRITABLE_DID_LENGTHS = {0xF102: (1, 16)}
```

### 冻结锚点

```
2E FF FF 58  + DEFAULT + LOCKED  →  7F 2E 31    （rung② 先于 rung③）
2E F1 02 58  + DEFAULT + LOCKED  →  7F 2E 7F    （rung③ DEFAULT 会话）
```

两条锚点在**同一状态**下结果不同，差异**只能**来自 payload（DID 是否已知）。

### rung 优先关系（corpus 内实测值）

```
EXTENDED + LEVEL1  →  6E F1 02      （六道全过，写入成功）
EXTENDED + LOCKED  + 只读 DID  →  7F 2E 33   （④ 先于 ⑤）
DEFAULT  + LOCKED  + 未知 DID  →  7F 2E 31   （② 先于 ③）
EXTENDED + LEVEL1  + 只读 DID  →  7F 2E 22   （⑤）
```

---

## 4. Purity（§13.2 / §13.5 单请求不变式）

```
每 case = 1 request
无 sequence
无 steps
无 setup request
无 teardown request
无 cross-service setup（特别地：**没有** "2E 写入 → 22 读回" 这类双请求序列）
```

所有前置条件（session / security_level / 既有 `did_overrides`）
**一律由 `initial_state` 一次性给出**。

> 在 B0 时点，0x22 **已经**走 ServiceSpec 路径。任何"先 `2E` 写、再 `22` 读"的
> 序列都会让第二步偏离 legacy，从而污染 oracle —— 因此被明确排除。

每条 case 独立 `build_state()` 构造**全新** `EcuState`，不复用被前一条修改过的实例
（0x2E 成功路径会写 `did_overrides`，复用会造成跨 case 污染）。

---

## 5. 状态副作用（§14）

`legacy_final_state` 记录三字段投影 `session` / `security_level` / `did_overrides`：

* **成功路径**（响应 `6E ...`）：`did_overrides` 必须变化；
* **失败路径**（响应 `7F ...`）：`did_overrides` 必须**不变**（无错误写入副作用）；
* `session` / `security_level`：0x2E 从不修改。

以上三条均由结构测试逐条断言（`TestStateSideEffectsAreCaptured`）。

> 0x2E 的 oracle **不能只比较 response bytes** —— 成功写入的副作用只体现在
> final state 上。这是 B2 必须比较的一半。

---

## 6. A0 不变性（§17 / §25）

```
A0 golden hash（B0 开始前）: d0d83244c85d7916924227c09dc3565ce33e5534e485650aa1f32efbd520cf79
A0 golden hash（B0 完成后）: d0d83244c85d7916924227c09dc3565ce33e5534e485650aa1f32efbd520cf79
```

**hash unchanged ✓** 且 `git diff` 对该文件无输出、clean-filter 哈希 == HEAD blob
（`85c28dbdd3dd…`）。

捕获过程中曾观察到该文件在 `git status` 中显示为 `M`。按 §17 的要求**先调查后结论**，
经三重证据确认**内容未变**（sha256 一致 / clean-filter 哈希一致 / diff 为空），
`M` 仅为 `core.autocrlf` 检出后的 **stat 缓存**假象（Issue #28 的同一环境现象）。

**未重新捕获、未重新生成、未修改 A0 corpus 的任何字节。**

---

## 7. 边界

| 项 | 状态 |
|---|---|
| production code（`uds_server.py` / `ecu.py` / `gateway.py` / `gateway_runtime.py` / `service_spec.py`） | **unchanged** |
| `_SPEC_EXECUTION` | **unchanged**（`frozenset({0x22})`） |
| 0x22 行为 | **unchanged** |
| 0x2E 行为 | **unchanged**（仍为 legacy execution，**未开启**） |
| 0x27 | **unchanged** |
| A0 corpus | **unchanged** |
| 正式测试断言 | 未修改 / 未放宽 |

### 本阶段新增

```
labs/vehicle/uds-basic/tests/c1_golden_2e_lib.py                 0x2E case 定义 + 捕获
labs/vehicle/uds-basic/tests/capture_c1_phase2_b0_0x2e_golden.py 捕获脚本（fail-closed）
labs/vehicle/uds-basic/tests/test_c1_phase2_b0_0x2e_golden.py    结构测试（27 例）
labs/vehicle/uds-basic/tests/golden/c1_phase2_0x2e_legacy.json   corpus（70 条）
labs/vehicle/uds-basic/tests/run_tests.sh                        +8（接入）
docs/OQ_C1_PHASE2_B0_0X2E_GOLDEN_2026-09-25.md                   本报告
```

**复用而非复制**：`snapshot` / `build_state` / `execute` / `serialize_case` /
`deserialize_case` / `git_commit` 全部直接复用 `c1_golden_lib` 中与 SID 无关的助手，
**未修改** A0 已冻结的实现（方案 A，最小变更）。

---

## 8. 测试

| 测试 | 结果 |
|---|---|
| `test_c1_phase2_b0_0x2e_golden.py` | **27/27 PASS**（新增） |
| `qa_fast.sh` | **PASS**（24 项 OK / 0 FAIL，1127 用例） |
| UDS unit | **824**（797 + 27；16/16 文件，FAIL 0） |
| UDS e2e | **234**（12 项，FAIL 0） |

**未执行** spec 对拍 —— 0x2E 的 spec 执行属 **B1**，legacy ↔ spec 逐字节对拍属 **B2**。
本文件中的 roundtrip 检查只证明 **corpus 自洽**（两侧当前都走 legacy），
**不是** B2 的等价性验证。

---

## 9. 最终判定

| 判定项 | 结果 |
|---|---|
| target SID = 0x2E | ✓ |
| 0x2E ∉ `spec_execution_at_capture` | ✓ |
| corpus 非空、case_id 唯一 | ✓（70 条） |
| 全部 request 以 `2e` 开头 | ✓ |
| 单请求不变式（无 sequence / steps） | ✓ |
| 前置条件全部由 `initial_state` 给出 | ✓ |
| 每条 case 有 response + final_state | ✓ |
| 冻结锚点逐字节正确 | ✓ |
| A0 corpus unchanged | ✓（sha256 一致） |
| production code unchanged | ✓ |
| `_SPEC_EXECUTION` unchanged | ✓ |
| `qa_fast` | ✓ PASS |

```
B0 = PASS
```

### 下一步（本阶段**未**执行）

```
B1 = NOT STARTED   （开启 0x2E 的 spec execution）
B2 = NOT STARTED   （legacy ↔ spec 逐字节对拍）
B3 = NOT STARTED
B4 = NOT STARTED
C2 = NOT STARTED
C3 = NOT STARTED

0x22 = ON
0x2E = OFF
0x27 = unchanged

Migration A = COMPLETE
Migration B = NOT COMPLETE
```

**B0 结束后不自动开启 B1。**
