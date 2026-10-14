# OQ-2 — 权威 Linux 验证环境方案

> **性质**：`PROPOSAL / PLANNING`。**本文件不构成任何执行授权。**
> 在方案获得批准前：**不**安装系统组件、**不**设置持久化 Runner、
> **不**变更凭据或主机安全配置、**不**把工作流切换到尚不存在的 Runner。
>
> 日期：2026-10-14 · 基线：`main` @ `efa836ff9225f6226ca8a76615f3f45c31110d58`

---

## 1. 问题陈述（已实测的事实）

失败运行：https://github.com/Li20771/SecurityLab/actions/runs/38529912319
（`workflow_dispatch`，提交 `efa836ff…`）

| Job | 结论 |
|---|---|
| Fast QA (ruff + mypy + offline tests) | success |
| Extended security tests (vcan0, manual) | **failure** |

实际失败点（原始日志）：

```
Bring up vcan0   → FAILURE
modprobe: FATAL: Module vcan not found in directory /lib/modules/6.17.0-1022-azure
##[error]Process completed with exit code 1.
```

后续步骤 `Install CAN / capture / YARA tooling`、`Install dependencies`、
`Extended quality gate` **全部 skipped**。

**这是该作业历史上第一次真正运行**（此前 0 次 `workflow_dispatch`）。

---

## 2. 根因修正（**重要：推翻了首份报告的初步判断**）

首份报告据上述日志判断"GitHub 托管 runner 结构性不可用"。
**进一步的独立核查表明该判断过于悲观，需修正：**

经 Ubuntu 官方包库核验，**该 exact 内核版本的 vcan 模块是存在的**：

| 核验项 | 结果 | 官方来源（直接链接） |
|---|---|---|
| 包 `linux-modules-extra-6.17.0-1022-azure` 是否存在 | **存在**，位于 `noble-updates`，版本 `6.17.0-1022.22` | [包详情页](https://packages.ubuntu.com/noble-updates/linux-modules-extra-6.17.0-1022-azure) |
| 该包 amd64 文件清单是否含 `vcan.ko` | **含** `/lib/modules/6.17.0-1022-azure/kernel/drivers/net/can/vcan.ko.zst` | [amd64 文件清单](https://packages.ubuntu.com/noble-updates/amd64/linux-modules-extra-6.17.0-1022-azure/filelist) |
| 同包是否还含 `vxcan.ko` | 含 `…/drivers/net/can/vxcan.ko.zst` | 同上文件清单 |
| 该包大小（amd64） | 约 49,810.2 kB（安装后约 49,446.0 kB） | 包详情页 |
| 包名跨 suite 检索 | `noble`（security）与 `noble-updates` 均命中，`amd64` / `arm64` | [包名检索](https://packages.ubuntu.com/search?keywords=linux-modules-extra-6.17.0-1022-azure&searchon=names&suite=all&section=all) |
| 同族 vcan.ko 归属模式（参考：noble 初始内核） | `linux-modules-extra-*`（azure/gcp/oracle/generic） | [noble amd64 vcan.ko 内容检索](https://packages.ubuntu.com/search?searchon=contents&keywords=vcan.ko&mode=filename&suite=noble&arch=amd64) |

**结论修正**：
runner 内核**并非没有 vcan**，而是 **runner 镜像未安装 `linux-modules-extra-$(uname -r)`**。
这是**打包/安装缺口**，不是"内核不可能支持"。

> **注意**：以上均为**仓库外的官方包库查询**，属**未在本项目执行**的外部资料。
> 该假设**尚未在 CI 中实跑验证**。在真正跑通之前，**不得**记为 PASS。

### 2.0 四个**互相独立**的验收事实（**严禁混写、严禁递推**）

下表四行是**依次递进但彼此独立**的事实。**前一项成立不能推出后一项成立**，
每一项都必须**单独实测**并留下证据：

| # | 验收事实 | 当前状态 | 证据 |
|---|---|---|---|
| **F-A** | 包库中存在与该内核版本匹配的 `linux-modules-extra` | **已核实（仓库外资料）** | 见 §2 上表官方链接 |
| **F-B** | 该模块能在**实际 runner** 上**安装并加载**（`apt-get install` 成功 ∧ `modprobe vcan` 成功） | **PASS（已实跑）** | run `38536799332`：`Setting up linux-modules-extra-6.17.0-1022-azure (6.17.0-1022.22)` → `vcan module present` → `vcan 12288 0` |
| **F-C** | `vcan0` 能**成功创建** | **PASS（已实跑）** | run `38536799332`：`5: vcan0: <NOARP,UP,LOWER_UP> ...` |
| **F-D** | **完整** `scripts/qa_extended.sh` **真正通过** | **FAIL（已实跑，硬阻塞）** | run `38536799332` / `38539974828`：见 §2.2 |

**F-A 的检索记录（可复核）**：
§2 上表链接于 **2026-10-14** 实际抓取，观察到的关键内容为：

```
包详情页         ：版本 6.17.0-1022.22；Source: linux-azure-6.17
                   amd64 包大小 49,810.2 kB；安装后 49,446.0 kB
文件清单页       ：含 /lib/modules/6.17.0-1022-azure/kernel/drivers/net/can/vcan.ko.zst
                   同页含 vxcan.ko.zst
包名检索页       ："Found 1 matching packages"
                   noble (24.04LTS) [security]: amd64
                   noble-updates: amd64 arm64
内容检索页       ：noble/amd64 下 vcan.ko 命中 9 条，涵盖
                   linux-modules-extra-{azure,gcp,oracle,generic} 等
```

> 若上述链接因包更新而内容变化，应以**当时实际返回**为准重新核验；
> 本记录不保证链接永久不变。

### 2.2 F-D 失败的**确切原因**：该内核未构建 ISO-TP（硬阻塞）

`vcan0` 建成之后，e2e 仍然失败，根因经三次实跑逐步定位并最终确证：

```
run 38536799332  e2e 报 OSError: [Errno 93] Protocol not supported
run 38539974828  逐个尝试协议模块：
                   modprobe can      : loaded
                   modprobe can_raw  : loaded
                   modprobe can_bcm  : loaded
                   modprobe can_gw   : loaded
                   modprobe isotp    : not available
                   modprobe can-isotp: not available
                   modprobe can_isotp: not available
                 真实 socket 构造仍报 OSError: [Errno 93] Protocol not supported
```

**包级确证**（Ubuntu 文件清单，2026-10-14 抓取）：

| 包 | `kernel/net/can/` 下提供 | 有 iso-tp 模块吗 |
|---|---|---|
| `linux-modules-6.17.0-1022-azure` | `can.ko` `can-bcm.ko` `can-gw.ko` `can-raw.ko` `can-j1939.ko` | **没有** |
| `linux-modules-extra-6.17.0-1022-azure` | 各厂商设备驱动（`vcan.ko` `vxcan.ko` 等） | **没有** |

⇒ **该 Azure 内核未构建 `CAN_ISOTP`**。这不是脚本错误、不是模块名写错、
也不是"再 modprobe 一次就好"，而是**内核能力缺失**。

**含义（对方案的影响）**：

- 托管 runner **即使 vcan 可用，也无法运行依赖 ISO-TP 的那部分 extended 门禁**；
- 依赖 ISO-TP 的测试需要**自托管 VM**（内核带 `CAN_ISOTP`）或**自建 runner 镜像**；
- 模块安装步骤对 vcan 仍然**正确且必要**（F-B/F-C 已 PASS），不受此影响；
- `capture_lab` / YARA 等**不依赖 ISO-TP** 的部分**仍可运行**（见 §2.3）。

### 2.3 各子项的独立状态（**不得折叠为一个总体结论**）

run `38536799332` 的逐项输出（`extended` 门禁内 4 步）：

| 子项 | 结果 | 说明 |
|---|---|---|
| `[1/4]` UDS 单元测试（离线部分） | **PASS**（27 项中多数） | `[ OK ]` 多项 |
| `[1/4]` vcan 端到端（依赖 ISO-TP） | **FAIL**（11 项） | 全部因 `EPROTONOSUPPORT`，**非逻辑缺陷** |
| `[2/4]` can-tools 离线 | **部分 PASS** | 含新增的 capture_lab 退出码测试 |
| `[3/4]` capture lab | **FAIL**（但**如实上报**） | `CAN_LOG=PASS BLF=PASS PCAP=FAIL` → `CAPTURE_LAB=CHECK` → `[FAIL]` |
| `[4/4]` YARA | 未能取到独立结论 | 见运行日志 |

> **关键进步**：`[3/4] capture lab` 现已**如实报 FAIL**。
> 修复前它会打印 `CAPTURE_LAB=CHECK` 却**以 0 退出**，被调用方上报成 `[ OK ]`。
> 这正是 OQ-2d 要修的 fail-open，已由实跑证明修复生效。
> 同时 `BLF=PASS` 证明**解释器硬编码修复**也生效（此前该段在非 WSL 环境必然失败）。



**明确禁止的推理**：

```
✗ F-A 成立 ⇒ 断言 F-B 成立（"包装得上"不等于"模块加载得了"）
✗ F-B 成立 ⇒ 断言 F-C 成立（"模块加载了"不等于"vcan0 建得起来"）
✗ F-C 成立 ⇒ 断言 F-D 成立（"vcan0 有了"不等于"完整 extended 会通过"）
✗ F-D 成立 ⇒ 断言 fuzz / repro / 硬件相关测试通过（extended 不含 --fuzz，
   也不含 scripts/repro/*）
```

**F-A 是仓库外的官方资料查询，其本身也不是本项目执行的测试**；
它只用于**选择方案**，不构成任何"已通过"的声明。

同样地，**模块安装成功 ≠ 完整 extended 通过**；
**extended 通过 ≠ fuzz / repro / 硬件相关测试通过**。

### 2.1 为什么容器**不能**解决这个问题

| 方案 | 能否引入 vcan | 原因 |
|---|---|---|
| Docker/Podman 容器 | **不能** | 容器共享宿主内核，**无法加载宿主内核未构建的模块**。容器只能使用宿主**已有**的模块（并需 `--cap-add=NET_ADMIN` 或 `--privileged` 才能 `modprobe`/建 vcan） |
| WSL2 | **不能** | 实测其内核配置 `CONFIG_CAN_VCAN is not set` —— **根本没编译进去**，`linux-modules-extra` 也无济于事 |
| GitHub 托管 runner | **可能可以**（若补装 modules-extra，见 §3.1） | runner 是 Azure VM，有真实内核 + passwordless sudo |

**关键区分**：
- "宿主内核**有**该模块但未安装" → 容器/补装**可能**可行
- "宿主内核**没编译**该模块"（WSL2） → 容器**绝无可能**可行

---

## 3. 方案比较

### 3.1 方案 A（**首选，成本最低**）：在托管 runner 上补装 modules-extra

不新增基础设施，只在 `Bring up vcan0` 之前补一步安装。

```yaml
# 草稿，尚未应用
- name: Install kernel extra modules (provides vcan)
  run: sudo apt-get update && sudo apt-get install -y \
         linux-modules-extra-$(uname -r)

- name: Bring up vcan0
  run: |
    sudo modprobe vcan
    sudo ip link add dev vcan0 type vcan
    sudo ip link set vcan0 up
    ip -details link show vcan0
```

| 维度 | 评估 |
|---|---|
| 是否保证 vcan | **高（但未实测）**：包存在且含 `vcan.ko`；仍受 §5 两个残留风险影响 |
| 成本 | 极低（每次约 +50 MB 下载 + 安装时间） |
| 维护 | 几乎为零；但**`$(uname -r)` 随 runner 镜像滚动**，包若某日缺席会失败 |
| 隔离 | 与现状相同（ephemeral runner） |
| 残留风险 | 见 §5 |

### 3.2 方案 B：自托管 Runner（专用 Linux VM）

| 维度 | 评估 |
|---|---|
| 是否保证 vcan | **最高**：VM 自带内核，可装 `linux-modules-extra` 后确认 `modprobe vcan` 成功 |
| 成本 | 需一台常驻/按需 VM + 维护 |
| 维护 | 补丁、注册、凭据轮换、磁盘 |
| 隔离 | **必须**专用 VM + 快照回滚 + 仅跑可信分支 |
| 安全 | 见 §4（**绝不可**对 fork PR 开放） |

### 3.3 方案 C：仅本地权威环境（无 CI）

| 维度 | 评估 |
|---|---|
| 是否保证 vcan | 取决于本机虚拟化（当前：WSL 被拒、无 VM 工具、Docker 引擎不可达） |
| 成本 | 零 CI 成本，但需先解决本机虚拟化 |
| 证据 | 只能产出本地证据（按 `reports/` 纪律记录），**无 Run URL** |
| 适用 | 过渡期；不能替代可复核的远程执行 |

---

## 4. 安全与隔离设计（任何方案都必须满足）

```
1. 仅可信分支 + 手动 workflow_dispatch
   —— 绝不对来自 fork 的 PR 自动运行（自托管 Runner 上等于在自有基础设施
      执行任意代码）。必要时用 environments + required reviewers 二次把关。
2. 最小权限
   —— permissions: contents: read；不使用 secrets；不持久化凭据。
3. 隔离
   —— 专用 VM；快照/回滚；每次运行后销毁或重置；限制出网。
4. 不写回仓库
   —— 验证作业只读仓库；不 push、不建 release、不上传含敏感信息的 artifact。
5. vcan0 生命周期
   —— qa_extended.sh 只创建不删除（无 `ip link del`）；设备随 runner/VM 消失。
      在自托管 VM 上应在作业末尾显式清理。
```

---

## 5. 残留风险（**必须在实跑中证伪，不得假设**）

| ID | 风险 | 如何证伪 |
|---|---|---|
| R-1 | 某日 `linux-modules-extra-$(uname -r)` 在镜像滚动后**尚未发布** | 实跑；若失败则需 pin runner 镜像或改方案 B |
| R-2 | runner 内核可能启用 **module signature enforcement / lockdown / secure boot**，导致 `modprobe` 被拒 | 实跑；失败日志会显示 `Operation not permitted` 或签名错误 |
| R-3 | `sudo` 无密码假设变化 | 实跑 |
| R-4 | `tshark` 在 vcan0 上抓包权限 | 实跑（`capture_lab.sh` 已预设失败可降级） |
| R-5 | `capture_lab.sh:13` 硬编码 `$HOME/.venvs/network/bin/python`，runner 上不存在 ⇒ BLF 段可能失败，但脚本不 abort，`qa_extended.sh` 仍报 `[ OK ]` | **正确性缺口**（非安全问题），应单独记录 |

---

## 6. 建议的 OQ 拆分（各自独立、互不混杂）

```
OQ-2a  修正 ci.yml 中错误的 Runner 能力假设          ← 纯文本，零风险，可立即做
OQ-2b  在托管 runner 上补装 modules-extra 并实跑      ← 方案 A
OQ-2c  （仅在 2b 失败时）评估自托管 Runner             ← 方案 B
OQ-2d  修正 capture_lab.sh 的 $HOME 硬编码缺口        ← 独立小项
```

### 6.1 OQ-2a：修正 `ci.yml` 的错误假设（**建议优先，且可独立先做**）

现状 `.github/workflows/ci.yml:7-10`：

```
# 扩展安全测试（extended）：需要 vcan0 与 can-utils/tshark/yara，
#   对应 scripts/qa_extended.sh。GitHub runner 内核通常带 vcan 模块，
#   但可用性取决于托管环境，故仅在手动 workflow_dispatch 时运行
#   （日常本地 WSL Ubuntu 是权威的扩展测试环境）。
```

**问题**：
1. "GitHub runner 内核通常带 vcan 模块" —— 与实测**不符**：
   内核镜像未安装 `linux-modules-extra`，`vcan.ko` **不在** `/lib/modules/<ver>` 下。
2. "日常本地 WSL Ubuntu 是权威的扩展测试环境" —— WSL2 内核
   `CONFIG_CAN_VCAN is not set`，**结构上不可能**提供 vcan。

⇒ 该注释同时高估了 CI 与本机两条路径。**建议改写为与实测一致的事实陈述**，
并注明"权威 vcan 环境待 OQ-2b 建立"。

### 6.2 OQ-2b 的验收条件（**只有真跑过才算 PASS**）

必须按 §2.0 的 **F-A → F-B → F-C → F-D** 顺序**逐项**取证，
且**每一项都必须单独实测**，不得由前一项推断后一项：

```
F-B  模块安装并加载成功
     —— 证据：apt-get install 的实际输出 + `modprobe vcan` 退出码
     —— 若失败：记录失败日志（package 不可得 / signature / lockdown）

F-C  vcan0 创建成功
     —— 证据：`ip -details link show vcan0` 的实际输出（须显示 UP）

F-D  完整 extended quality gate 真实运行
     —— 证据：scripts/qa_extended.sh 的实际输出与退出码
```

并且必须留下：

```
1. Run URL + 提交 SHA + 逐 Job/Step 结果
2. 明确区分 PASS / FAIL / SKIPPED（严禁把 skipped 记为 PASS）
3. 所有未执行项的清单及原因
4. 证据按 reports/ 纪律归档（见 OQ-3 / PR #62）
```

**明确不得**：

```
✗ 由 F-A（包存在）推断 F-B（能装能加载）
✗ 由 F-B 推断 F-C，或由 F-C 推断 F-D
✗ 因"模块安装成功"就声称 extended 可运行
✗ 因单次 extended 通过就声称所有 fuzz / repro / 硬件相关测试通过
  （extended 不含 --fuzz，也不含 scripts/repro/*）
```

---

## 7. 本机环境诊断（只读，未做任何变更）

| 项 | 实测 | 判定 |
|---|---|---|
| WSL CLI | `Wsl/E_ACCESS_DENIED`（磁盘上有 Ubuntu 24.04 / kali / docker-desktop 三个发行版） | **不可用** |
| WSL2 内核 vcan | `CONFIG_CAN_VCAN is not set`（据 WSL2 官方内核配置） | **结构上不可能** |
| Docker 客户端 | 存在（Docker Desktop） | — |
| Docker 引擎 | **不可达**（`npipe:////./pipe/dockerDesktopLinuxEngine` 不存在） | **不可用** |
| VirtualBox / VMware / qemu / podman | 均**不存在** | 不可用 |
| Hyper-V / WSL 功能状态 | `Get-WindowsOptionalFeature` 需提权 → **无法判定** | UNKNOWN |
| `AF_CAN`（Windows Python） | `False` | 不可用 |
| qemu / gdb-multiarch / ghidra / binwalk | 均未安装 | 不可用 |

**未执行任何安装、未启动/停止任何服务、未修改主机或 WSL 配置。**

---

## 8. 待批准事项

1. 是否批准 **OQ-2a**（修正 `ci.yml` 注释）—— 建议批准，纯文档、零风险。
2. 是否批准 **OQ-2b** 按方案 A 实跑（会向托管 runner **安装**一个内核模块包）。
3. 若 2b 失败，是否推进 **OQ-2c**（自托管 VM）。
4. 是否批准 **OQ-2d**（`capture_lab.sh` 硬编码缺口）。

---

## 9. 状态声明

```
【性质】本文件为 PROPOSAL / PLANNING（方案 / 规划）。

本文件为方案，未执行任何变更
未安装任何系统组件
未设置任何 Runner
未切换任何工作流
未修改 ci.yml（仅引用其现有行号）
未修改凭据或主机安全配置
§2 的包库结论为仓库外官方资料，尚未在本项目实跑验证 → 不得记为 PASS
F-B / F-C / F-D 三项均为 UNKNOWN，尚未验证

【关键的授权边界】
**合并本文件不自动授权执行它描述的变更。**
接受本方案 ≠ 批准安装内核模块包 ≠ 批准修改 ci.yml ≠ 批准切换 extended 目标 Runner。
§8 的每一项都需要**单独**的明确批准。
```
