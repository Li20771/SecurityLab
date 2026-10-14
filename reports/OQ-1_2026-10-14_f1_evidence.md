# OQ-1 验证证据（F-1 修订轮）—— 零值占位断言

> 本文件是 [`OQ-1_2026-10-14_evidence.md`](OQ-1_2026-10-14_evidence.md) 的
> **后续轮次**记录：针对独立审查发现 **F-1** 所做的修订。
> **不覆盖**第一轮证据。未执行项在 §6.4 显式列为 `NOT_RUN`。

## 1. 身份

| 项 | 值 |
|---|---|
| OQ / 工作项编号 | OQ-1（F-1 修订轮） |
| 验证类型 | 测试覆盖缺口修复 + 回归 |
| 基线提交 SHA | `efa836ff9225f6226ca8a76615f3f45c31110d58`（`main`） |
| **上一轮**验证提交 SHA | `d2d753e92b225015772c2c597987659f07ee1617`（远端）／`faa0371ed88b4b20dd8ba5f22cd63b6f3a2905d2`（本地等价） |
| **本轮**验证提交 SHA | `2284f9f954ab69b18dfb5c9408ca139e8a052bf2`（远端 PR head）<br>`ba9b6fbc552d9c987968f2d97493c477c3d3624b`（本地等价提交） |
| 本轮包含的提交 | `518ae3a`（F-1 断言）· `ba9b6fb`（F-2 风险记录） |
| 被验证的分支 / PR | `fix/oq1-errno-ecomm-portability-20261014` / [PR #61](https://github.com/Li20771/SecurityLab/pull/61) |
| 验证者 | SecurityLab 主执行负责人（自验）+ 独立审查者（见 §5） |

**提交内容一致性**：本轮的远端提交 tree 与本地提交 tree **完全相同**（经 GitHub API 复核）：
`f7618f9af002837cb95a2593589b79323c81c418`（F-1 提交）。
F-2 提交本地 tree `0393a3e1841f1d67300b4f3c7b405b0082380370` 与远端一致（发布脚本内断言）。

## 2. 时间与环境

| 项 | 值 |
|---|---|
| 执行时间（时区） | 2026-10-14，Asia/Shanghai (UTC+08:00) |
| 操作系统 | Windows NT 10.0.26200 |
| Python 版本 | 3.14.4 (MSC v.1944 64-bit) |
| 关键工具及版本 | ruff 0.16.8（= CI pin）· mypy 2.1.0 · can-isotp 2.0.7 · python-can 4.6.1 · git 2.55.0.windows.2 |
| 依赖来源 | wheel 解包至 `tmp/audit-libs`（`tmp/` gitignored），经 `PYTHONPATH` 注入 |

## 3. F-1 是什么

独立审查（见 §5）发现：需求"不能让 `None` **或其他占位值**误匹配有效错误码"
只覆盖了 `None` 那一半。把实现改为用 `0` 作为"平台缺失"的占位值并关闭过滤后，
集合变成 `{0, 42, 10040}`、`OSError(0)` 被判为**可恢复**，
而**当时全部 7 个可移植性测试仍然通过** ⇒ 该半句需求**未被验证**。

`0` 是合法 errno（表示成功）。若用它当占位值，一次**成功**的路径会被误判成
**可恢复的传输损坏**，从而吞掉真实故障。

## 4. 本轮改动与结果

### 4.1 改动（**仅测试文件**）

`labs/vehicle/uds-basic/tests/test_gateway_runtime_unit.py` 新增两条断言：

| 断言 | 约束面 |
|---|---|
| `assertNotIn(0, RECOVERABLE_ISOTP_ERRNOS)` | **集合内容** |
| `assertFalse(_is_recoverable_isotp_error(OSError(0, "zero errno")))` | **判定行为** |

两者配对，使任一面被单独破坏都能被发现。

### 4.2 实际执行的命令与退出码

| # | 命令 | 退出码 | 结果摘要 |
|---|---|---|---|
| 1 | `python tests/test_gateway_runtime_unit.py`（F-1 断言后） | 0 | `Ran 62 tests ... OK`（原 60 + 新增 2） |
| 2 | **变异验证**：把实现改为 `0` 占位 + 关闭过滤（= 审查者 A 的原变异体） | 1 | `Ran 62 tests ... FAILED (failures=2)` ⇒ **现在能捕获** |
| 3 | 变异还原后 | 0 | `OK`；`restored byte-identical: True` |
| 4 | `ruff check labs/vehicle/uds-basic` | 0 | `All checks passed!` |
| 5 | `mypy labs/vehicle/uds-basic/ecu` | 0 | `Success: no issues found in 13 source files` |
| 6 | 离线测试集 30 文件（等价 `qa_fast.sh` 3/4 + 4/4） | 1 | **29 passed / 1 failed** |

**第 2 项是本轮的关键证据**：同一变异体在修订前**全部通过**，修订后**失败 2 项**
⇒ F-1 的覆盖缺口**确已关闭**，而非仅"补了两条看起来合理的断言"。

**第 6 项的唯一失败**仍为 `can-tools/tests/test_fuzz_offline.py`（30 errors，沙箱
`PermissionError [WinError 5]`，tempfile 清理阶段）。**与 OQ-1 无关**：
本轮改动不含任何 `can-tools` 文件，且该失败在第一轮即已存在。

### 4.3 冻结契约

本轮**未触碰** `ecu.py`（F-2 仅记录在测试模块 docstring 中）。
因此 `ecu.py` blob SHA 仍等于 C2-A2 的 pin：

```
working ecu.py blob : e545064625c38d0fc73248bd6589fa8a406db8a2
C2-A2 pinned value  : e545064625c38d0fc73248bd6589fa8a406db8a2
⇒ 本轮未触发新的 rebaseline
```

## 5. CI 证据（本轮）

| 项 | 值 |
|---|---|
| Run ID | `38534924996`（push）· `38534930938`（pull_request） |
| Run URL | https://github.com/Li20771/SecurityLab/actions/runs/38534924996<br>https://github.com/Li20771/SecurityLab/actions/runs/38534930938 |
| 工作流 / 事件 | `SecurityLab CI` / push · pull_request |
| 提交 SHA | `2284f9f954ab69b18dfb5c9408ca139e8a052bf2` |
| 平台 | `ubuntu-latest`（Linux） |

| Job | Step | 结论 |
|---|---|---|
| Fast QA (ruff + mypy + offline tests) | **Fast quality gate** | **success** |
| Extended security tests (vcan0, manual) | — | **skipped**（`workflow_dispatch` 专属） |

**独立审查记录**：[PR #61 GitHub Review](https://github.com/Li20771/SecurityLab/pull/61#pullrequestreview-5514890476)
（第一轮，COMMENTED）。本轮（F-1 修订后）的独立复审结论见 §7。

> **注**：先前版本曾写"独立审查者（见 `docs/` 审查报告）"，但仓库中**不存在**
> 该 `docs/` 审查报告。已更正为上述 GitHub Review 的实际引用。

## 6. 结论分类

### 6.1 PASS（本轮实际执行且通过）

| 项 | 命令 / 证据 |
|---|---|
| F-1 断言后目标测试 | `Ran 62 tests ... OK`（退出码 0） |
| **变异体被捕获** | `FAILED (failures=2)`（退出码 1）—— 证明缺口已关闭 |
| 变异还原后恢复 | `OK`；`restored byte-identical: True` |
| ruff | `All checks passed!` |
| mypy | `Success: no issues found in 13 source files` |
| 其余 25 个 uds-basic unit 文件 | 全部退出码 0 |
| can-tools 3/4 离线文件 | 退出码 0 |
| **远程 Linux CI：Fast QA** | Run `38534924996` / `38534930938`，`Fast quality gate` = success |
| 冻结 pin 未漂移 | `ecu.py` blob == C2-A2 pin `e5450646…` |

### 6.2 FAIL（本轮实际执行且失败）

| 项 | 证据 | 是否本次改动引入 |
|---|---|---|
| `can-tools/tests/test_fuzz_offline.py` | `FAILED (errors=30)`，全为沙箱 `PermissionError` | **否**（第一轮即存在；本轮不含 can-tools） |

### 6.3 BLOCKED

| 项 | 缺失的前置条件 |
|---|---|
| `bash scripts/qa_fast.sh`（原样） | bash 被沙箱阻断 ⇒ 已用等价逐文件独立进程复现 |
| WSL / vcan0 / AF_CAN / qemu / gdb / ghidra | 本机均不可用 |

### 6.4 NOT_RUN（本轮未执行 —— 禁止推测结果）

| 项 | 未执行的原因 |
|---|---|
| `qa_extended.sh` / `--fuzz` | 需 vcan0 + root + Linux |
| 13 个 `*_vcan.py` 集成测试 | 无 `AF_CAN` / 无 vcan0 |
| `scripts/healthcheck.sh`、`scripts/repro/*` | 需 WSL/Linux 与对应工具链 |
| CI `extended` 作业 | 本轮为 push/PR 触发，`skipped` |

### 6.5 UNKNOWN

| 项 | 无法判定的原因 |
|---|---|
| `extended` 在补装内核模块后能否通过 | **尚未验证**。包库中存在匹配包 ≠ 能安装 ≠ 能加载 ≠ 能建 vcan0 ≠ `qa_extended.sh` 通过（见 OQ-2 文档） |

## 7. F-2 处置（**风险接受，非修复**）

F-2：不可哈希的 `exc.errno` 现在抛 `TypeError`（原元组实现返回 `False`）。

**决定：不修复，记录为已接受风险。** 依据：

- `OSError.errno` 由内核给出，恒为 `int`；`can-isotp 2.0.7` 不构造非 `int` `errno`
  的 `OSError`；本仓库无路径手工赋非 `int` ⇒ **实际不可达**。
- 修复需改动 `ecu.py` 的判定函数 ⇒ 会再次触发 C2-A2 冻结 pin 的重新评估。
  为一个不可达输入引入一次冻结契约变更，代价大于收益。
- 触发条件与确切补救方式已写入测试模块 docstring（`if not isinstance(exc.errno, int): return False`）。

**本文件不声称 F-2 已修复。**

## 8. 来源区分（不得折叠为一个总体 PASS）

| 步骤 | 来源 |
|---|---|
| 目标测试 / 变异验证 / ruff / mypy / 离线集 | **本地等价复现** |
| Fast QA | **远程 CI 实际执行**（Run `38534924996`、`38534930938`） |
| Extended security tests | **因前置条件失败未执行**（`skipped`） |

## 9. 原始输出位置与保留期限

| 载体 | 位置 | 保留期限 | 过期后如何重跑 |
|---|---|---|---|
| 本地 | `tmp/`（gitignored，不入库） | 本机保留 | 见 §4.2 命令列 |
| CI 日志 | GitHub Actions Run 页面（URL 见 §5） | GitHub 默认期限 | 重新 push 或重跑 workflow |
| CI artifacts | **无**（该 workflow 未使用 `upload-artifact`） | N/A | N/A |

## 10. 合规声明

```
未伪造任何执行结果；未执行的测试均记为 NOT_RUN
未把第一轮结果当作 F-1 修订后的结果（第一轮记录保持不变，仅加注适用范围）
未提交大二进制 / PCAP / 固件 / 凭据
未声称 F-2 已修复（它是风险接受）
§6.1 的每项 PASS 均对应本轮实际执行的命令与退出码
```
