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

> **已于 2026-10-14 实跑验证**（不再是"尚未验证"）：
> run [`38536799332`](https://github.com/Li20771/SecurityLab/actions/runs/38536799332)
> 实际安装成功并加载了 vcan：
>
> ```
> Get:3 ... linux-modules-extra-6.17.0-1022-azure amd64 6.17.0-1022.22 [51.0 MB]
> Setting up linux-modules-extra-6.17.0-1022.azure (6.17.0-1022.22) ...
> vcan module present for 6.17.0-1022.azure
> vcan                   12288  0
> 5: vcan0: <NOARP,UP,LOWER_UP> mtu 72 ...
> ```
>
> ⇒ **包库查询的结论被实跑证实**。但请注意：这只证明 **vcan 部分**可行，
> **不**代表 extended 门禁通过 —— 后者因缺 ISO-TP 而**真实失败**（见 §2.2）。

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

### 2.1 为什么容器**不能**解决这个问题

| 方案 | 能否引入 vcan / ISO-TP | 原因 |
|---|---|---|
| Docker/Podman 容器 | **不能** | 容器共享宿主内核，**无法加载宿主内核未构建的模块**。容器只能使用宿主**已有**的模块（并需 `--cap-add=NET_ADMIN` 或 `--privileged` 才能 `modprobe`/建 vcan） |
| WSL2 | **不能** | 实测其内核配置 `CONFIG_CAN_VCAN is not set` —— **根本没编译进去**，`linux-modules-extra` 也无济于事 |
| GitHub 托管 runner | **vcan 可以（已实跑 PASS）；ISO-TP 不可以（已确证）** | vcan：补装 `linux-modules-extra` 后实测成功（§2 F-B/F-C）。ISO-TP：该内核 `# CONFIG_CAN_ISOTP is not set`（§2.2.1），需**重编内核**，托管 runner 不支持 |

**关键区分**：
- "宿主内核**有**该模块但未安装" → 容器/补装**可能**可行（vcan 即此类）
- "宿主内核**没编译**该模块"（WSL2 的 vcan；托管 runner 的 ISO-TP） → 容器**绝无可能**可行，
  只能**重编内核**或更换带该配置的内核

---

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

### 2.2.1 可行性探测的**决定性证据**（内核配置层面）

为判定"在托管 runner 上是否**可能**启用 ISO-TP"，2026-10-14 执行了一次
**隔离的可行性探测**（诊断作业，**不属于门禁**）：
run [`38546434121`](https://github.com/Li20771/SecurityLab/actions/runs/38546434121)

**实际输出（P8 汇总，原文）**：

```
================ FEASIBILITY SUMMARY ================
kernel              : 6.17.0-1022-azure
CONFIG_MODULES=y
# CONFIG_MODULE_SIG_FORCE is not set
CONFIG_CAN=m
# CONFIG_CAN_ISOTP is not set          ← 内核**未编译** ISO-TP
headers installed   : /usr/src/linux-headers-6.17.0-1022-azure
isotp modules found : 0
socket capability   : CAN_ISOTP UNAVAILABLE
```

补充事实（同一次探测，逐项实测）：

| 探测项 | 结果 |
|---|---|
| `/boot/config-$(uname -r)` 中 `CONFIG_CAN_ISOTP` | **`# CONFIG_CAN_ISOTP is not set`**（第 2081 行） |
| `CONFIG_MODULES` | `=y`（模块机制本身可用） |
| `CONFIG_MODULE_SIG_FORCE` | **`not set`** ⇒ 签名强制**不是**阻碍 |
| `modinfo isotp` / `can-isotp` / `can_isotp` | 三者均 `Module not found` |
| `/lib/modules/$(uname -r)` 下匹配 `*isotp*` 的文件 | **0 个** |
| `kernel/net/can/` 目录 | **该目录在 `linux-modules` 中，但无 isotp** |
| 内核头文件 | **已安装**（`linux-headers-6.17.0-1022-azure`） |
| `/usr/src` 下 `isotp.c` | **不存在**（头文件包不含 `net/can/` 源码） |
| 项目接口 `socket(AF_CAN, SOCK_DGRAM, CAN_ISOTP)` | **UNAVAILABLE** |

**结论（可行性判定已闭合）**：

```
托管 runner 上**无法**启用 ISO-TP。
原因不是"模块没装"、不是"签名被拒"、不是"名字写错"，
而是内核配置 `CONFIG_CAN_ISOTP is not set` —— 协议**从未被编译**。

⇒ 即便：
   - 已安装匹配的内核头文件；
   - 模块签名强制关闭；
   - 模块机制可用（CONFIG_MODULES=y）；
   也**无法**加载一个不存在的协议实现。

⇒ "自己构建 can-isotp 模块"这条路径在**托管 runner 上不可行**：
   头文件包不提供 `net/can/isotp.c`，而 `CONFIG_CAN_ISOTP is not set`
   意味着该协议未纳入构建配置；需**重新配置并重编内核**，托管 runner 不支持。
```

**含义（对方案的影响）**：

- 托管 runner **即使 vcan 可用，也无法运行依赖 ISO-TP 的那部分 extended 门禁**
  —— 这已由内核配置层面确证，不再是推测；
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
| 是否保证 vcan | **已实跑证实**（run `38536799332`）：包存在、安装成功、`vcan0` UP。残留风险仅剩 §5 的 R-1（镜像滚动） |
| 成本 | 极低（每次约 +50 MB 下载 + 安装时间） |
| 维护 | 几乎为零；但**`$(uname -r)` 随 runner 镜像滚动**，包若某日缺席会失败 |
| 隔离 | 与现状相同（ephemeral runner） |
| 残留风险 | 见 §5 |

> **⚠ 方案 A 的能力边界（2026-10-14 探测后修正）**
>
> 方案 A **只解决 vcan**，**不能解决 ISO-TP**。
> run [`38546434121`](https://github.com/Li20771/SecurityLab/actions/runs/38546434121)
> 已确证托管 runner 的内核 `# CONFIG_CAN_ISOTP is not set`，
> 且头文件包不含 `net/can/isotp.c` ⇒ **"自建模块"路径在托管 runner 上不可行**。
>
> ⇒ 方案 A 的结论是：**vcan 可用（已 PASS），但依赖 ISO-TP 的测试在此环境永远无法通过**。
> 因此**方案 B 不再是"仅在 A 失败时"的备选，而是 ISO-TP 测试的必经路径**。

### 3.2 方案 B：自托管 Runner（专用 Linux VM）—— **ISO-TP 的唯一可行路径**

#### 3.2.1 内核配置硬性要求（**必须逐项确认，不得假设**）

```
必须满足（任一不满足则该环境不可用）：
  CONFIG_CAN=m 或 =y
  CONFIG_CAN_ISOTP=m 或 =y     ← 决定性项；托管 runner 为 "is not set"
  CONFIG_CAN_VCAN=m 或 =y      ← vcan 设备
  CONFIG_CAN_RAW=m 或 =y       ← 若测试用到 RAW socket
  CONFIG_MODULES=y             （若 CAN_ISOTP=m）
  CONFIG_MODULE_SIG_FORCE 应 not set，或自建模块已正确签名
```

Ubuntu 的 `linux-generic`（非 azure/cloud 裁剪版）通常 **启用** `CONFIG_CAN_ISOTP`。
**但必须在部署前用 `zgrep CONFIG_CAN_ISOTP /boot/config-$(uname -r)` 实测确认**，
不得依据发行版名声推断。

#### 3.2.2 备选内核来源

| 选项 | 说明 | 是否需实测确认 |
|---|---|---|
| Ubuntu **generic** 内核 VM | 最可能自带 `CONFIG_CAN_ISOTP` | **是** |
| Ubuntu **cloud/azure** 内核 | **已知不满足**（托管 runner 即此类） | 已确证不可用 |
| 自定义内核 | `CONFIG_CAN_ISOTP=m` 后重编 | 是（成本高，仅在无现成内核时） |
| 容器 | **不可行** | 容器共享宿主内核，无法引入宿主未编译的模块 |

#### 3.2.3 隔离与安全设计

```
1. 仅可信分支 + 手动 workflow_dispatch
   —— 绝不对来自 fork 的 PR 自动运行。
      自托管 Runner 上运行 fork PR 等于在自有基础设施执行任意代码。
      必要时用 GitHub Environments + required reviewers 二次把关。
2. 最小权限
   —— permissions: contents: read；不使用 secrets；不持久化凭据。
3. 隔离
   —— 专用 VM；快照/回滚；每次运行后重置；限制出网。
4. 不写回仓库
   —— 只读仓库；不 push、不建 release、不上传含敏感信息的 artifact。
5. vcan0 生命周期
   —— qa_extended.sh 只创建不删除（无 `ip link del`）；
      自托管 VM 上应在作业末尾显式清理。
6. 网络
   —— 仅允许 apt/PyPI 出网（若需）；不做入站暴露。
```

#### 3.2.4 部署前验收测试（**只有真跑过才算 PASS**）

```
步骤 1  内核配置取证
        zgrep -E 'CONFIG_CAN_ISOTP|CONFIG_CAN_VCAN' /boot/config-$(uname -r)
        期望：两者均为 =m 或 =y

步骤 2  模块加载
        sudo modprobe vcan && sudo modprobe can && sudo modprobe can_raw
        lsmod | grep -E 'vcan|can_raw'

步骤 3  设备创建
        sudo ip link add dev vcan0 type vcan && sudo ip link set vcan0 up
        ip -details link show vcan0        ← 须显示 UP

步骤 4  项目接口验证（**决定性**）
        python3 -c "import socket; socket.socket(socket.AF_CAN,
                    socket.SOCK_DGRAM, socket.CAN_ISOTP).close()"
        ← 必须**成功**。这是唯一权威判据。

步骤 5  真实运行 extended 门禁
        bash scripts/qa_extended.sh
        ← 记录 Run URL / 提交 SHA / 逐 Job·Step 结果

步骤 6  证据归档
        按 reports/ 纪律记录，明确区分 PASS / FAIL / SKIPPED
```

**明确不得**：

```
✗ 以"VM 建好了"代替步骤 4 成功
✗ 以步骤 1–3 通过推断步骤 4/5 会通过
✗ 以单次 extended 通过声称 fuzz / repro / 硬件测试通过
✗ 把 skipped 或未执行项记为 PASS
```

#### 3.2.5 成本与维护

| 维度 | 评估 |
|---|---|
| 成本 | 一台 VM（常驻或按需）；磁盘 + 补丁 + 注册维护 |
| 维护 | Runner 版本、补丁、凭据轮换、快照管理 |
| 残留风险 | 见 §5（R-1/R-3 仍属宿主假设） |

> **本文件不启动部署。** 上述内容为**待批准方案**；
> 部署需**单独授权**（见 §8）。本轮**未**设置任何持久化 Runner、**未**新增凭据。

### 3.3 方案 C：仅本地权威环境（无 CI）

| 维度 | 评估 |
|---|---|
| 是否保证 vcan | 取决于本机虚拟化（当前：WSL 被拒、无 VM 工具、Docker 引擎不可达） |
| 是否保证 ISO-TP | 同上；且 **WSL2 内核 `CONFIG_CAN_VCAN is not set`** ⇒ 本机 WSL 路径**不可行** |
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

## 5. 残留风险（**已在实跑中部分证伪；剩余项仍不得假设**）

下表已按 **2026-10-14 的实际运行结果**更新。**已证伪的项不再列为风险**，
但**未证伪的项仍不得假设**：

| ID | 风险 | 状态 | 证据 / 剩余动作 |
|---|---|---|---|
| R-1 | `linux-modules-extra-$(uname -r)` 在镜像滚动后尚未发布 | **本次未发生**（残留） | run `38536799332` 安装成功；**镜像滚动后仍可能发生** ⇒ 需持续观察；若出现则 pin runner 镜像或改方案 B |
| R-2 | module signature / lockdown 拒绝 `modprobe` | **已证伪** | run `38536799332`：`vcan 12288 0` + `vcan0 UP` ⇒ 模块加载与设备创建均成功，未受签名/lockdown 阻断 |
| R-3 | 免密 `sudo` 假设变化 | **本次未发生**（残留） | 全部 `sudo` 步骤成功；仍属托管环境假设 |
| R-4 | `tshark` 在 vcan0 上抓包权限 | **仍为风险** | run `38540870651` 中 `PCAP=FAIL`（`capture_lab.sh` 已预设失败可降级）；**具体是权限还是其它原因尚未单独定位** |
| R-5 | `capture_lab.sh` 硬编码 `$HOME/.venvs/network/bin/python` ⇒ BLF 段失败却被上报为 `[ OK ]` | **已修复并实跑验证** | 解释器改为 `$PYTHON`→venv→PATH 顺序；退出码改为 `CAPTURE_LAB=CHECK` ⇒ `exit 1`。run `38540870651` 实测 `BLF=PASS` 且 `[FAIL] capture lab` 如实上报 |
| **R-6（新）** | **该内核 `CONFIG_CAN_ISOTP is not set`** ⇒ 依赖 ISO-TP 的测试在托管 runner 上**无法通过** | **已确证（硬阻塞）** | 见 §2.2 / §2.2.1（run `38546434121` 内核配置取证）；唯一路径为方案 B 自托管 VM |
| **R-7（新）** | 自托管 Runner 若内核同样裁剪掉 `CAN_ISOTP`（如误用 cloud/azure 内核） | **需在部署前实测** | 按 §3.2.4 步骤 1 与步骤 4 确认；**不得**凭发行版名推断 |

---

## 5.1 最新实测结论（2026-10-14，五次 workflow_dispatch 实跑）

| # | Run | 结果 | 关键进展 |
|---|---|---|---|
| 1 | [38529912319](https://github.com/Li20771/SecurityLab/actions/runs/38529912319) | failure | `modprobe: Module vcan not found`（历史首次运行） |
| 2 | [38536799332](https://github.com/Li20771/SecurityLab/actions/runs/38536799332) | failure | **模块装上、vcan0 UP**；e2e 报 `EPROTONOSUPPORT` |
| 3 | [38538604194](https://github.com/Li20771/SecurityLab/actions/runs/38538604194) | failure | 曾把 `can_isotp` 硬失败 ⇒ 自我修正 |
| 4 | [38539974828](https://github.com/Li20771/SecurityLab/actions/runs/38539974828) | failure | 确证 `isotp` 无法加载 + socket 失败 |
| 5 | [38540870651](https://github.com/Li20771/SecurityLab/actions/runs/38540870651) | failure | **首次跑完全流程**，逐项如实报告 |

**run 5 的逐项结果（不得折叠为单一结论）**：

| 子项 | 结果 |
|---|---|
| UDS 离线单元测试 | **PASS**（27 项 `[ OK ]`） |
| vcan 端到端（依赖 ISO-TP） | **FAIL 11 项**（全部缺协议，非逻辑缺陷） |
| can-tools 离线（含 capture_lab 退出码测试） | **PASS** |
| capture lab | **FAIL 但如实上报**（`CAN_LOG=PASS BLF=PASS PCAP=FAIL`） |
| YARA | **PASS** |
| 汇总 | `QA EXTENDED RESULT: FAIL` |

> **明确**：**模块安装成功 ≠ extended 通过**（已实测：模块装上、vcan0 UP，门禁仍 FAIL）。
> extended 也**不含** `--fuzz` 与 `scripts/repro/*`，故其通过**不**代表 fuzz / repro / 硬件测试通过。

---

## 6. 建议的 OQ 拆分（各自独立、互不混杂）

```
OQ-2a  修正 ci.yml 中错误的 Runner 能力假设          ← 已完成（PR #64）
OQ-2b  在托管 runner 上补装 modules-extra 并实跑      ← 方案 A：**已完成并实测**
                                                       vcan PASS；ISO-TP 确证不可行
OQ-2c  CAN_ISOTP 可行性探测                           ← **已完成**（run 38546434121）
                                                       结论：托管 runner 不可行
OQ-2d  修正 capture_lab.sh 的 $HOME 硬编码 + 退出码   ← 已完成（PR #64）
OQ-2e  自托管 Linux VM（内核含 CONFIG_CAN_ISOTP）     ← **待批准**（方案 B）
```

> **拆分状态更新（2026-10-14）**：2a / 2b / 2d 已在 PR #64 完成；
> 2c 探测已完成并**排除了托管 runner 路径**；
> **2e 是 ISO-TP 测试的唯一剩余路径，等待批准**。

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
未切换任何工作流（本文件提交时）
未修改 ci.yml（仅引用其现有行号）
未修改凭据或主机安全配置

【本文件提交时的实测状态（2026-10-14）】
F-A = 已核实（仓库外官方资料）
F-B = **PASS**（run 38536799332：模块安装并加载成功）
F-C = **PASS**（run 38536799332：vcan0 实际创建并 UP）
F-D = **FAIL（硬阻塞）**（该内核未构建 CAN_ISOTP；详见 §2.2）
包库结论已被实跑证实（vcan 部分），但 **extended 门禁真实失败，不得记为 PASS**

【关键的授权边界】
**合并本文件不自动授权执行它描述的变更。**
接受本方案 ≠ 批准安装内核模块包 ≠ 批准修改 ci.yml ≠ 批准切换 extended 目标 Runner
≠ 批准部署自托管 Runner。
§8 的每一项都需要**单独**的明确批准。
```
