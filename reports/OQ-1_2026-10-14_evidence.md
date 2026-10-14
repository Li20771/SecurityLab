# OQ-1 验证证据 —— `errno.ECOMM` 跨平台缺陷修复

> 本文件是 `reports/` 的**首个真实证据记录**，同时充当格式示例。
> 所有内容均为**实际执行**所得；未执行项在 §6.4 显式列为 `NOT_RUN`。

## 1. 身份

| 项 | 值 |
|---|---|
| OQ / 工作项编号 | OQ-1 |
| 验证类型 | 缺陷修复验证 + 回归 + 冻结契约 |
| 基线提交 SHA | `efa836ff9225f6226ca8a76615f3f45c31110d58`（`main`） |
| 验证提交 SHA | `d2d753e92b225015772c2c597987659f07ee1617`（远端 PR head）<br>`faa0371ed88b4b20dd8ba5f22cd63b6f3a2905d2`（本地等价提交） |
| 被验证的分支 / PR | `fix/oq1-errno-ecomm-portability-20261014` / [PR #61](https://github.com/Li20771/SecurityLab/pull/61) |
| 验证者 | SecurityLab 主执行负责人（自验）+ 独立审查者（见 `docs/` 审查报告） |

**提交内容一致性**：本地提交 tree `5fd6899b7c28cf5f3e134bd7bf13dee1b3ba13ce`
与远端提交 tree **完全相同**（远端经 GitHub API 复核），`ecu.py` blob SHA
与本地提交值断言相等。

## 2. 时间与环境

| 项 | 值 |
|---|---|
| 执行时间（时区） | 2026-10-14，Asia/Shanghai (UTC+08:00) |
| 操作系统 | Windows NT 10.0.26200（Microsoft Windows） |
| 内核版本 | N/A（Windows 主机；CI 侧为 Linux） |
| Python 版本 | 3.14.4 (MSC v.1944 64-bit) |
| 关键工具及版本 | ruff 0.16.8（= CI pin）· mypy 2.1.0 · can-isotp 2.0.7 · python-can 4.6.1 · git 2.55.0.windows.2 |
| 虚拟环境 / 依赖来源 | 无 venv；依赖以 wheel 解包至 `tmp/audit-libs`（`tmp/` 被 gitignore），经 `PYTHONPATH` 注入 |

> **说明**：本机无 `~/.venvs/network`（WSL 路径且 WSL 不可达），
> 且 pip 无法写入其临时解包目录（沙箱限制），故改用 PyPI 直接取 wheel 解包。

## 3. 执行摘要

`_is_recoverable_isotp_error()` 原先硬引用 `errno.ECOMM`（仅 Linux 定义），
在 Windows 上调用即抛 `AttributeError`。修复改为按平台实际定义构建
`RECOVERABLE_ISOTP_ERRNOS` 集合，缺失项不进入集合且不使用占位值。

- 目标测试：修复前 **16 errors** → 修复后 **60 tests OK**
- mutation 验证：仅还原 return 行 ⇒ **17 errors**，证明回归测试确实能捕获该缺陷
- CI（Linux，真实执行）：Fast QA **pass**（2 次运行）

**注意**：本地离线测试集为 **29 / 30**，唯一失败项是既有的沙箱 tempfile 伪失败，
**与本次改动无关**（本次 diff 不含任何 `can-tools` 文件）。

## 4. 命令与结果

| # | 命令 | 退出码 | 结果摘要 |
|---|---|---|---|
| 1 | `python labs/vehicle/uds-basic/tests/test_gateway_runtime_unit.py`（修复前 / RED） | 1 | `FAILED (errors=16)` |
| 2 | 同上（修复后 / GREEN） | 0 | `Ran 60 tests ... OK` |
| 3 | mutation：仅将 return 行还原为原缺陷写法 | 1 | `FAILED (errors=17)` |
| 4 | mutation 还原后 | 0 | `OK`；文件 byte-identical 恢复（`Restored byte-identical: True`） |
| 5 | `ruff check labs/vehicle/uds-basic` | 0 | `All checks passed!` |
| 6 | `mypy labs/vehicle/uds-basic/ecu` | 0 | `Success: no issues found in 13 source files` |
| 7 | 离线测试集 30 文件（等价 `qa_fast.sh` 3/4 + 4/4） | 1 | **29 passed / 1 failed** |
| 8 | `test_c2_a2_legacy_current_differential.py` | 0 | `OK`；`mismatches = 0`，两侧锚点 20/20 |
| 9 | `test_c3_a0_baseline.py` | 0 | `OK`（0x27 行为未变） |
| 10 | `git hash-object labs/vehicle/uds-basic/ecu/ecu.py` | 0 | `e545064625c38d0fc73248bd6589fa8a406db8a2` |
| 11 | Python 独立重算 blob SHA | 0 | `e545064625c38d0fc73248bd6589fa8a406db8a2`（与 #10 一致） |

### 4.1 第 7 项明细

```
uds-basic 26 个 unit 文件          → 全部 PASS
can-tools test_parsers.py          → PASS (34 tests)
can-tools test_isotp_send_options.py → PASS
can-tools test_uds_fuzz_offline.py   → PASS
can-tools test_fuzz_offline.py     → FAIL (30 errors) —— 沙箱 tempfile 伪失败
```

失败性质：30 个错误全部为 `PermissionError [WinError 5]`，发生在 `tempfile`
清理阶段（沙箱拒绝写入临时区）。**非产品缺陷**，且改动前即已存在。

## 5. CI 证据

| 项 | 值 |
|---|---|
| Run ID | `38529721828`（push）· `38529787368`（pull_request） |
| Run URL | https://github.com/Li20771/SecurityLab/actions/runs/38529721828<br>https://github.com/Li20771/SecurityLab/actions/runs/38529787368 |
| 工作流 / 事件 | `SecurityLab CI` / push · pull_request |
| 提交 SHA | `d2d753e92b225015772c2c597987659f07ee1617` |
| 触发方式 | push + pull_request（自动） |

逐 Job / Step 结果：

| Job | Step | 结论 |
|---|---|---|
| Fast QA (ruff + mypy + offline tests) | Set up job | success |
| Fast QA | Run actions/checkout@v4 | success |
| Fast QA | Set up Python 3.12 | success |
| Fast QA | Install dependencies | success |
| Fast QA | **Fast quality gate** | **success** |
| Fast QA | Post Set up Python 3.12 | success |
| Fast QA | Post Run actions/checkout@v4 | success |
| Fast QA | Complete job | success |
| Extended security tests (vcan0, manual) | — | **skipped**（`workflow_dispatch` 专属，本次未触发） |

> **平台**：`ubuntu-latest`（Linux）。**这是本次唯一的"远程 CI 实际执行"证据。**
> 它覆盖 ruff + mypy + 离线测试；**不覆盖** vcan / e2e / fuzz（见 §6.4）。

## 6. 结论分类

### 6.1 PASS（本轮实际执行且通过）

| 项 | 命令 / 证据 |
|---|---|
| 目标测试修复后通过 | `Ran 60 tests ... OK`（退出码 0） |
| ruff lint | `All checks passed!`（退出码 0） |
| mypy 类型检查 | `Success: no issues found in 13 source files`（退出码 0） |
| C2-A2 冻结差分 | `OK`；`mismatches = 0`；锚点 20/20（退出码 0） |
| C3-A0 基线（0x27 行为未变） | `OK`（退出码 0） |
| 其余 25 个 uds-basic unit 文件 | 全部退出码 0 |
| can-tools 3/4 离线文件 | 退出码 0 |
| **远程 Linux CI：Fast QA** | Run `38529721828` / `38529787368`，`Fast quality gate` = success |
| blob SHA 双重核对 | `git hash-object` 与 Python 独立重算一致 = `e5450646…` |

### 6.2 FAIL（本轮实际执行且失败）

| 项 | 命令 / 证据 | 是否本次改动引入 |
|---|---|---|
| `can-tools/tests/test_fuzz_offline.py` | `FAILED (errors=30)`，全为沙箱 `PermissionError [WinError 5]` | **否**（改动前即失败；diff 不含 can-tools） |
| `_is_recoverable_isotp_error` 原实现（修复前） | `AttributeError: module 'errno' has no attribute 'ECOMM'` | —（即本次修复对象） |

### 6.3 BLOCKED（前置条件缺失）

| 项 | 缺失的前置条件 |
|---|---|
| `bash scripts/qa_fast.sh`（原样执行） | 本机 bash 被沙箱阻断（`couldn't create signal pipe, Win32 error 5`）⇒ 已用等价逐文件独立进程方式复现其 3/4、4/4 |
| WSL / Linux 工具链 | `Wsl/E_ACCESS_DENIED` |
| vcan0 / SocketCAN | `hasattr(socket,'AF_CAN') = False` |
| qemu / gdb-multiarch / binwalk / Ghidra | 本机均未安装 |

### 6.4 NOT_RUN（本轮未执行 —— 禁止推测结果）

| 项 | 未执行的原因 |
|---|---|
| `qa_extended.sh` / `--fuzz` | 需 vcan0 + root + Linux；本机与 GitHub 托管 runner 均不具备（见 §9 与 OQ-2） |
| 13 个 `*_vcan.py` 集成测试 | 无 `AF_CAN` / 无 vcan0 |
| `scripts/healthcheck.sh` | 需 WSL/Linux |
| `scripts/repro/*`（ARM / QEMU+GDB / Ghidra / fuzz smoke） | 需 WSL/Linux 与对应工具链 |
| CI `extended` 作业 | 本次为 push/PR 触发，该作业 `skipped` |

### 6.5 UNKNOWN

| 项 | 无法判定的原因 |
|---|---|
| `extended` 作业能否在现有托管 runner 上通过 | 已由 OQ-2 实测：**不能**（缺 `vcan` 内核模块）；但"改用专用 runner 后能否通过"尚未验证 |

## 7. 来源区分（不得折叠为一个总体 PASS）

| 步骤 | 来源 | 说明 |
|---|---|---|
| 目标测试 RED/GREEN | **本地等价复现** | 逐文件独立进程，等价 `qa_fast.sh` 3/4 |
| mutation 验证 | **本地等价复现** | 仅还原 return 行，17 errors |
| ruff / mypy | **本地等价复现** | 版本与 CI pin 一致（ruff 0.16.8） |
| 离线测试集 30 文件 | **本地等价复现** | 29 PASS / 1 沙箱伪失败 |
| Fast QA | **远程 CI 实际执行** | Run `38529721828`、`38529787368` |
| Extended security tests | **因前置条件失败未执行** | `skipped`（非 PASS，也非 FAIL） |

## 8. 原始输出位置与保留期限

| 载体 | 位置 | 保留期限 | 过期后如何重跑 |
|---|---|---|---|
| 本地 | `tmp/`（gitignored，**不入库**） | 本机保留，随时可清 | 见 §4 命令列 |
| CI 日志 | GitHub Actions Run 页面（URL 见 §5） | GitHub 默认期限（约 90 天） | 重新 push 或重跑 workflow |
| CI artifacts | **无**（该 workflow 未使用 `upload-artifact`） | N/A | N/A |

**持久化限制说明**：
本机的完整 stdout 位于 `tmp/`（gitignored），**不随仓库分发**。
因此本文件**只记录命令、退出码与结果摘要**，不搬运完整日志。
若要长期保留完整日志，需由 CI artifacts 或本地归档承担，并在本表登记。

## 9. 未决问题 / 后续动作

- `extended` 门禁在 GitHub 托管 runner 上**结构性不可用**
  （`modprobe: FATAL: Module vcan not found`）—— 由 OQ-2 处理。
- 本文件的 §6.4 各项在专用 Linux 环境就绪后**必须重新执行**并更新本文件
  （或另建一份新证据文件，不覆盖本文件）。

## 10. 合规声明

```
未伪造任何执行结果；未执行的测试均记为 NOT_RUN
未提交大二进制 / PCAP / 固件 / 凭据
历史记录未被当作当前证据
§6.1 的每项 PASS 均对应本轮实际执行的命令与退出码
```
