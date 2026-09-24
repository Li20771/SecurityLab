# Main Capability Inventory — 2026-09-24

> **性质**：只读能力盘点（read-only capability inventory）。
> 本文件**不引入任何功能、不修改生产代码、不修改既有文档**。
> 目的是为"下一项第二轮 OQ"提供事实依据；**本文不选择、不排序、不推荐** OQ8。

---

## 1. Scope and Baseline

### 1.1 本盘点的范围

回答的唯一问题：

> **当前 `main` 到底已经能做什么？**

本盘点**不做**的事：

- 不实现任何新能力；
- 不修改 `labs/**`、`scripts/**`、`.github/**` 下的任何文件；
- 不修改 `SECURITY_LAB_STATUS.md` / `TEST_MATRIX.md` / `ARCHITECTURE.md` /
  `AUTONOMOUS_BUILD_STATE.md` / `README.md` 等既有文档；
- 不主观打分、不使用"成熟/优秀/完善/不足"一类评价词；
- 不决定 OQ8（候选只做事实罗列，见 §14）。

### 1.2 状态取值（本文件只用这五个）

| 取值 | 含义 |
|------|------|
| `VERIFIED` | 有版本化的可执行入口，且断言了真实成功判据（脚本/测试实际运行过） |
| `PARTIAL` | 主体存在，但有明确、已记录的缺口或未覆盖面 |
| `NOT_IMPLEMENTED` | 仓库中不存在实现 |
| `OUT_OF_SCOPE` | 项目**明确声明**不在范围内（有书面依据），不是"忘了做" |
| `INFRASTRUCTURE_ONLY` | 只有环境/工具/占位存在，未形成可用链路 |

### 1.3 安全边界（本盘点遵守且未改变）

所有 CAN/UDS 能力一律只针对 **vcan0 + 虚拟 ECU**。本盘点未连接、未发送、
未验证任何真实车辆 / 真实 ECU / 真实 CAN 接口。

---

## 2. Main Git Evidence

本轮以**远端 `origin/main`** 为准（本地旧 `main` 已通过 `--ff-only` 快进对齐）。

| 项 | 值 |
|----|----|
| 盘点所在分支 | `audit/main-capability-inventory-20260924` |
| 起点 = 远端 `origin/main` HEAD | `77db4126f891c181b99ae96a01ae7f6fb2c74189` |
| HEAD 提交 | `Merge pull request #22 from Li20771/chore/oq7-post-merge-docs-20260924` |
| **当前生产代码基线** | **`ae22a556924d89d27e1d084ebcc5a4b4c56534f2`**（PR #21 = OQ7 merge） |
| 最近两个 merge commit | `77db4126…`（PR #22，**仅文档**）、`ae22a556…`（PR #21，**生产代码**） |
| working tree | clean（盘点开始时 `git status --short` 为空） |

> **关键区分（刻意记录，避免误写）**：PR #22 是 **docs-only** checkpoint，
> 它**不**构成新的"生产代码基线"。因此：
>
> - `main HEAD` = `77db4126…`
> - **生产代码基线仍为 `ae22a556…`（OQ7）**
>
> 两者不相同是**正确**状态，不是不一致。

OQ7 的 CAN ID 映射（本轮未重新验证，引用 OQ7 已固化证据：
`tests/test_gateway_runtime_vcan.py` 总线级抓包断言 + OQ7 PR 的动态核验）：

```
0x7E0 -> 0x7E8     0x7E1 -> 0x7E9     0x7E2 -> 0x7EA
0x7DF -> broadcast（三个 ECU 都执行，总线零响应帧）
```

---

## 3. Environment Capability

### 3.1 证据强度分级（本章内部使用）

为避免"装了工具 = 有能力"的误判，本章对每项标注**证据强度**：

| 级别 | 含义 |
|------|------|
| **E1** | 有版本化脚本**且断言了真实成功判据**（实际运行过） |
| **E2** | 有版本化的专用实验/配置产物，但**不是**验证入口 |
| **E3** | 仅存在性/存活探测（工具是否安装**不可由仓库判定**） |
| **E4** | 仅文档或注释提及，无脚本化使用 |

> **重要**：E1 只表示"配方已版本化"，**不表示"被自动重跑"**。
> 全部 6 个 `scripts/repro/*` 脚本**不被任何门禁调用**（见 §11.5 与 §13 G7-2）。
>
> 另一个系统性事实：历史审计的原始证据位于 `tmp/audit/`（**109 个文件、
> gitignored、0 个 tracked**），因此文档中所有 "Environment audit reported"
> 结论**无法从仓库本身复核** —— 这正是 `scripts/repro/` 存在的原因。

### 3.2 平台与运行时

| 能力 | 状态 | 强度 | 证据 / 边界 |
|------|------|------|-------------|
| WSL2 检测 | `INFRASTRUCTURE_ONLY` | E3 | `scripts/healthcheck.sh:44-48`（`grep microsoft /proc/version`，**WARN 级**） |
| Ubuntu 为主环境 | `INFRASTRUCTURE_ONLY` | E4 | 仅文档；**无脚本读 `/etc/os-release`** |
| Kali 辅助环境 | `INFRASTRUCTURE_ONLY` | E4 | 仅文档；**无 tracked 脚本调用过 Kali** |
| Docker | `INFRASTRUCTURE_ONLY` | E3 | `healthcheck.sh:227-235` `docker version`，**WARN 级**（引擎挂掉不会使门禁失败）；`docker run` 不在任何 tracked 文件 |
| systemd | `INFRASTRUCTURE_ONLY` | E3 | 经 `systemctl is-active/is-enabled` 探测；无"repo 单元 vs 已部署单元"一致性检查 |

### 3.3 CAN / ISO-TP 基础

| 能力 | 状态 | 强度 | 证据 / 边界 |
|------|------|------|-------------|
| vcan0 创建（systemd 单元） | `VERIFIED` | E2 | `configs/systemd/vcan0.service:9`：`modprobe can/can_raw/vcan/can-isotp` + `ip link add vcan0 type vcan` + `up`；**未显式设 MTU**（2060 是 vcan 默认） |
| vcan0 存活 | `VERIFIED` | E3 | `healthcheck.sh:188-196`（不存在/未 UP → **FAIL**） |
| vcan0 缺失时自建（门禁内兜底） | `VERIFIED` | E1 | `scripts/qa_extended.sh:46-78`（需 root/`sudo -n`，否则 `exit 2`） |
| can-utils（`cansend`/`candump`/`canplayer`） | `VERIFIED` | **E1** | `capture/capture_lab.sh`：真实发送/捕获/回放 + `diff -u` 断言 `IDENTITY = PASS`（`:63-68`），整体 `CAPTURE_LAB=PASS`（`:123-127`） |
| `isotpsend`/`isotprecv` | `INFRASTRUCTURE_ONLY` | E3 | `healthcheck.sh:184-185` 仅 `command -v`；**无任何 tracked 脚本调用** |
| SocketCAN / `CAN_ISOTP` | `VERIFIED` | **E1** | 内核模块由 `vcan0.service` 加载；真实 `CAN_ISOTP` socket 绑定的可复现入口：`scripts/repro/verify_gateway_runtime.sh`、`verify_fuzz_smoke.sh`、`verify_uds_fuzz_smoke.sh`（缺依赖 → `exit 2`） |
| ISO-TP 传输参数矩阵 | `VERIFIED` | **E1** | `tests/test_isotp_options_vcan.py`（14）；`can-tools/tests/test_isotp_malformed_vcan.py`（40） |
| python-can / can-isotp 版本钉住 | `VERIFIED` | **E1** | `configs/environment/python-network-requirements.txt`（`can-isotp==2.0.7`、`python-can==4.6.1`）；`healthcheck.sh:141-166` 版本不符 → **FAIL** |

### 3.4 交叉执行 / 逆向工具链

| 能力 | 状态 | 强度 | 证据 / 边界 |
|------|------|------|-------------|
| AArch64 交叉编译 + 执行 | `VERIFIED` | **E1** | `scripts/repro/verify_arm.sh:82-94`，断言输出 `ARM_RUNTIME_OK 42` |
| armhf（`qemu-arm`） | `VERIFIED` | **E1** | `verify_arm.sh:59-63,98-104` |
| binfmt 透明执行 | `VERIFIED` | E3+E1 | `healthcheck.sh:62-67`（handler 丢失 → FAIL）+ `:68-98`（编译+透明执行断言 exit 42） |
| QEMU **user-mode** | `VERIFIED` | **E1** | 同上（binfmt 不可用时 `qemu-aarch64` 回退） |
| QEMU **system-mode** | `NOT_IMPLEMENTED` | E4 | **`qemu-system` 在 0 个 tracked 脚本中出现**；文档明示"未实际启动过任何系统 VM" |
| `gdb-multiarch` 远程调试闭环 | `VERIFIED` | **E1** | `verify_qemu_gdb.sh:74-97`：断点命中 + qemu `rc=0` |
| Ghidra headless 分析 | `VERIFIED` | **E1** | `verify_ghidra.sh`：`AARCH64` + `ERROR_COUNT=0` + postScript 反汇编 12 条 |
| `readelf` / `strings` / `file` | `VERIFIED` | **E1** | 真实使用于 `verify_arm.sh:68,74`、`analyze.sh:21,37,46`、`firmware_diff.sh:32-33` |
| `objdump` / `nm` / `r2` / `patchelf` | `INFRASTRUCTURE_ONLY` | E3 | 仅 `healthcheck.sh:209` 存在性检查；**无 tracked 脚本调用** |
| YARA | `VERIFIED` | **E1** | `labs/yara-basic/scan.sh`：2 TP + 1 TN 断言 → `YARA_RESULT=PASS/FAIL`；由 `qa_extended.sh:102` 调用（manual 层） |
| AFL++（通用 fuzz） | `VERIFIED`（**manual-only**） | **E1** | `labs/fuzzing/afl-demo/run.sh`：要求真实崩溃**且**两次重放信号一致；**不被任何门禁调用** |
| binfmt additive helper + selfheal v2.3 | `VERIFIED`（repo 侧） | E2 | `configs/binfmt/binfmt-selfheal.sh`（fail-closed）；**系统侧部署未重新确认** |
| binfmt fail-closed 回归 | `VERIFIED`（脚本） / `INFRASTRUCTURE_ONLY`（接线） | **E1 脚本，无调用方** | `configs/binfmt/tests/selfheal_failclosed_test.sh`（3 场景 + 11/11 记录），**无门禁调用它** |
| healthcheck ARM64 回退回归 | 同上 | **E1 脚本，无调用方** | `scripts/tests/healthcheck_qemu_fallback_test.sh`（3/3 记录），**无门禁调用它** |
| `strace` / `ltrace` | `INFRASTRUCTURE_ONLY` | E3 | 仅存在性检查；**无脚本化运行**。且**文档互相矛盾**（见 §12） |
| `qemu-<arch> -strace` | `NOT_IMPLEMENTED` | E4 | 0 次调用 |
| `valgrind` | `NOT_IMPLEMENTED` | — | 全仓库 **0 命中**（脚本/配置/文档均无） |
| pwndbg / gef | `NOT_IMPLEMENTED` | E4 | 文档明示未安装 |

### 3.5 可复现入口清单（`scripts/repro/`，全部 **manual-only**）

| 脚本 | 验证内容 | 强度 |
|------|----------|------|
| `verify_arm.sh` | AArch64/armhf 编译 + 执行（断言 `ARM_RUNTIME_OK 42`） | E1 |
| `verify_qemu_gdb.sh` | `qemu-aarch64 -g` ↔ `gdb-multiarch` 断点→continue→rc0 | E1 |
| `verify_ghidra.sh` | headless 分析 → AARCH64 + `ERROR_COUNT=0` + 反汇编 12 条 | E1 |
| `verify_fuzz_smoke.sh` | ISO-TP fuzz 3 case + oracle 三态 + replay `issue(s)=0` | E1 |
| `verify_uds_fuzz_smoke.sh` | UDS 请求层：冻结常量 `(7,128,19)` + M1–M7 + admission reject + v1 golden 947 + MF basic 实打 | E1 |
| `verify_gateway_runtime.sh` | OQ7 网关守护进程 13 项断言 + SIGTERM rc=0 + 无残余进程 | E1 |

**没有任何门禁（`qa_fast` / `qa_extended` / `healthcheck` / CI）调用上述任一脚本。**

---

## 4. UDS Capability Matrix

### 4.1 实现清单（源码事实）

静态注册表 `SERVICES`（`labs/vehicle/uds-basic/ecu/uds_server.py:2130-2145`）
包含 **14 个** SID：

```
0x10 0x11 0x14 0x19 0x22 0x2E 0x31 0x34 0x35 0x36 0x37 0x38 0x3E 0x85
```

**`0x27` 不在静态表中**（见 §4.3）。

### 4.2 逐服务矩阵

| 服务 | 状态 | 已实现子功能 | 状态相关 | 主要 NRC | unit | vcan e2e | 合成模型? | 已知边界 |
|------|------|--------------|----------|----------|------|----------|-----------|----------|
| `0x10` DiagnosticSessionControl | `VERIFIED` | `01` / `02` / `03` | 是（写 `state.session`） | `12` `13` | 是 | 是 | 否（纯协议） | 无 session 门控：任何状态下都可切任何会话；P2/P2* 为每会话固定表 |
| `0x11` ECUReset | `VERIFIED` | `01` / `02` / `03` | 是（触发 22 字段复位） | `12` `13` | 是 | 是 | 否 | 三者**状态等价**（experiment-defined）；`04`/`05` → `12`；不处理 suppress bit |
| `0x14` ClearDiagnosticInformation | `VERIFIED` | 全清 `0xFFFFFF` / 单个已存在 DTC | 是（置 `dtc_records` status=0） | `13` `31` | 是 | 是 | **是**（synthetic DTC model） | **status 置零不删 key**；无功能组；不新增状态字段；幂等；不改 severity |
| `0x19` ReadDTCInformation | `VERIFIED` | **`01` `02` `07` `08` `09` `0A`** | 读（不改） | `12` `13` `31` | 是 | 是 | **是** | 其余 reportType（`03/04/05/06/0B–0E/14/15/16/1A…`）**未实现**；`08` 走 MF（20 字节） |
| `0x22` ReadDataByIdentifier | `VERIFIED` | **多 DID**（循环解析） | 读 | `13` `31` `33` | 是 | 是 | 否 | 受保护 DID `0xF187` 需 `SECURITY_LEVEL1` → `33`；**不做会话检查** |
| `0x27` SecurityAccess | `VERIFIED`（但**不在静态表**，见 §4.3） | `01` RequestSeed / `02` SendKey | 是（security_level / seed / 失败计数 / 锁定） | `12` `22` `24` `35` `36` `37` | 是 | 是（经 `ecu.py` 入口） | 否 | 单安全等级（无 Level 2）；seed 5s 过期；失败 3 次锁 10s（`UDS_LOCKOUT_SECONDS` 可覆盖） |
| `0x2E` WriteDataByIdentifier | `VERIFIED` | 可写 DID `0xF102`（长度 1–16） | 是（写 `did_overrides`） | `13` `22` `31` `33` `7F` | 是 | 是 | 否 | **唯一做会话门控的服务**（默认会话 → `7F`）；不处理 suppress bit |
| `0x31` RoutineControl | `VERIFIED` | `01` Start / `02` Stop / `03` RequestResults | 是（`active_routines` / `routine_start_counts`） | `12` `13` `22` `31` | 是 | 是 | **是**（synthetic routines `0x2001`/`0x2002`） | 不实现 routineControlOptionRecord / routineStatusRecord；无会话门控 |
| `0x34` RequestDownload | `VERIFIED` | DFI `00` / ALFID `23` | 是（`download_*` 6 字段） | `13` `22` `24` `31` `73` | 是 | 是 | **是（纯内存）** | **不接触 Flash / 文件系统**；无会话门控 |
| `0x35` RequestUpload | `VERIFIED` | DFI `00` / ALFID `23` | 是（`upload_*` 5 字段） | `13` `22` `24` `31` `73` | 是 | 是 | **是**（源为 256 字节纯函数） | 32130 上限；与 `0x34` 方向互斥 |
| `0x36` TransferData | `VERIFIED` | BSC 块序 + 幂等重传 + 回绕 | 是 | `13` `24` `31` `73` | 是 | 是 | **是（纯内存）** | 数据源三分支（download / readFile / dirInfo） |
| `0x37` RequestTransferExit | `VERIFIED` | 完整性检查后清理/提交 | 是 | `13` `24` `31` `73` | 是 | 是 | **是** | addFile/replaceFile **仅在此时提交**；plain `0x34` 不提交任何东西 |
| `0x38` RequestFileTransfer | `VERIFIED` | mode `01`–`05`：addFile / deleteFile / replaceFile / readFile / readDir | 是（`file_mode` / `file_path` / `files`） | `13` `22` `31` | 是 | 是 | **是（synthetic in-memory 文件模型）** | 非真实文件系统；**modeOfOperation `0x06`（ResumeFile）未实现**（已实现 `0x01`–`0x05`）；仅 DFI `00` |
| `0x3E` TesterPresent | `VERIFIED` | `00`（含 suppress `80`） | 否 | `12` `13` | 是 | 是 | 否 | **唯一处理 suppressPosRspMsgIndicationBit 的服务** |
| `0x85` ControlDTCSetting | `VERIFIED` | `01` ON / `02` OFF | 是（`dtc_setting_enabled`） | `12` `13` | 是 | 是 | **是** | 只开关"是否允许继续更新"，**本身不产生故障**；不处理 suppress bit；无会话/安全门控 |

未在表中出现的 SID（如 `0x28`、`0x39`）→ `NOT_IMPLEMENTED`（见 §13 G1）。

> **术语纠正（本项目既有文档中存在误称，本报告不沿用）**：
>
> | 标识 | 正确含义 | 本项目状态 |
> |------|----------|-----------|
> | `0x37` | **RequestTransferExit** | **已实现**（见上表；**不是缺口**） |
> | `0x38` | **RequestFileTransfer** | 已实现，`modeOfOperation` `0x01`–`0x05` |
> | `0x38` / `modeOfOperation = 0x06` | **ResumeFile** | **未实现** |
> | `0x39`–`0x3C` | 保留范围（本项目未使用） | `NOT_IMPLEMENTED` |
>
> 即：**ResumeFile 不是独立 SID**，而是 `0x38` 的一个 `modeOfOperation` 取值；
> **不存在**"`0x39` RequestFileTransferExit"这一服务。
> 注意 `modeOfOperation` 不是 UDS 子功能字节（非法 mode → `NRC 0x31`，
> 见 `file_database.py:55-56`）。

### 4.3 `0x27` 的两条通路（重要区分）

| 通路 | 是否支持 `0x27` | 证据 |
|------|----------------|------|
| `uds_server.handle_uds_request()` 用**静态** `SERVICES` | **否** → `7f 27 11` | 实测：默认会话下 `27 01` → `7f 27 11` |
| `ecu.handle_uds_request()`（入口层，注入 `_now()` + `SECURITY_CONFIG`） | **是** | `ecu.py:352-395`；`test_e2e_vcan.py` / `test_write_did_vcan.py` 经此通路 |
| `gateway.py` / `gateway_runtime.py` | **否** | `VirtualEcu` 用静态表；e2e 用例显式固化该边界 |

即：`0x27` 的实现**存在且已验证**，但它是**入口层注入的能力**，不是静态表能力。
`0x27` **不缺实现**，缺的是"经 Gateway Runtime 透传"（属架构选择，见 §13 G4）。

### 4.4 服务门控现状（实测，非推断）

在**默认会话**下直接发各服务（不先切会话），实测结果：

```
0x31 Start 0x2001     -> 71 01 20 01    （正响应；无会话门控）
0x34 RequestDownload  -> 74 20 00 80    （正响应；无会话门控）
0x35 RequestUpload    -> 75 20 00 80    （正响应；无会话门控）
0x14 0xFFFFFF         -> 54             （正响应；无会话门控）
0x19 0A               -> 59 …           （正响应；无会话门控）
0x85 01               -> c5 01          （正响应；无会话门控）
0x22 F1 87（受保护）   -> 7f 22 33       （安全门控生效）
0x2E F1 02（可写 DID） -> 7f 2e 7f       （会话门控生效）
```

结论：**只有 `0x2E` 做会话门控**；`0x22`/`0x2E` 做安全门控；其余服务不看会话。
见 §5.7 的架构含义。

---

## 5. State / Security Architecture

### 5.1 状态模型

唯一 state 类是 `uds_server.EcuState`（**没有**独立的 `ecu_state.py`），
共 **27 个字段**：

| 组 | 字段 | 说明 |
|----|------|------|
| 会话/安全（8） | `session` `security_level` `pending_seed` `seed_issued_at` `seed_consumed` `failed_attempts` `lockout_until` `seed_counter` | 会话与 `0x27` 安全状态 |
| DID（1） | `did_overrides` | `0x2E` 写入的 override，读优先于出厂值 |
| DTC（2） | `dtc_records` `dtc_setting_enabled` | 运行时 status；`0x85` 开关 |
| Routine（2） | `active_routines` `routine_start_counts` | `0x31` 瞬态 |
| Download（6） | `download_active` `_address` `_size` `_expected_block` `_buffer` `_last_block_request` | `0x34/0x36/0x37` |
| Upload（5） | `upload_active` `_address` `_size` `_expected_block` `_sent` | `0x35/0x36/0x37` |
| File（3） | `file_mode` `file_path` `files` | `0x38` |

### 5.2 复位语义（`_reset_volatile_state`）

`0x11 01/02/03` 共用同一转换（三者**无类型差异**，experiment-defined）：

- **复位 22 字段**：会话/安全（8）+ DID override + routine（2）+ download（6）+
  upload（5）+ file_mode/file_path + `dtc_setting_enabled`
- **刻意保留 5 字段**：`failed_attempts` / `lockout_until` / `seed_counter`
  （防"复位刷掉暴力破解惩罚"）、`dtc_records`（故障记忆）、`files`（非易失替身）

### 5.3 会话框架（结论：`PARTIAL`）

**不存在统一的 session/permission framework。** 证据（源码级）：

- `state.session` 的**全部消费者只有 3 处**：`0x10` 写入（`uds_server.py:459`）、
  复位写入（`:381`）、以及**唯一一处读取判断** `uds_server.py:758`（属于 `0x2E`）。
- `state.security_level` 的读取判断只有 2 处：`0x22`（`:567`，受保护 DID）
  与 `0x2E`（`:763`）。
- 没有装饰器、没有 registry 级前置检查、没有 permission 表、没有
  `session → permission → service` 抽象。

因此：

| 能力 | 状态 |
|------|------|
| 会话**状态机**（`0x10` 切换 + 复位回默认 + P2/P2* 表） | `VERIFIED` |
| 会话**框架**（统一的 per-service 会话门控） | `NOT_IMPLEMENTED`（仅 `0x2E` 内联实现） |
| 权限框架（Permission / 授权矩阵） | `NOT_IMPLEMENTED` |
| 服务授权 / 诊断授权 | `NOT_IMPLEMENTED` |

### 5.4 SecurityAccess（`0x27`）细节

| 项 | 现状 |
|----|------|
| 子功能 | `01` RequestSeed、`02` SendKey |
| 安全等级 | 只有 `SECURITY_LEVEL1`（**无 Level 2**，无位掩码等级） |
| 尝试与锁定 | `max_attempts=3`、`lockout_seconds=10`（`UDS_LOCKOUT_SECONDS` 可覆盖） |
| 时间依赖 | seed 有效期 5s（`seed_expiry_seconds`）；seed 单次消费（防重放） |
| 依赖注入 | `_now()`（可 mock 时钟）与 `SECURITY_CONFIG`（原对象引用）**每请求现场求值**，由 `ecu.py` 注入 |
| 每 ECU 独立性 | 是：每个 `VirtualEcu` 持有独立 `EcuState`（gateway 单测 + 多 ECU e2e 固化） |
| 与 Gateway Runtime 的关系 | **不经 gateway 层**（见 §4.3）——静态表不含 `0x27` |

`0x27` 是当前仓库中**唯一**实现"反暴力破解"语义的机制（失败计数 + 锁定窗口）。

### 5.5 suppressPosRspMsgIndicationBit

| 项 | 现状 |
|----|------|
| 已实现 | 仅 `0x3E`（`3E 80` → 不发帧，`uds_server.py:823`） |
| 明确声明未实现 | `0x19`（`uds_server.py:1097`）、`0x85`（`:958`）、`0x11`（`:493`） |
| 状态 | `PARTIAL`（单服务） |

### 5.6 负响应策略

| 项 | 现状 |
|----|------|
| 否定响应格式 | `7F <sid> <nrc>`，统一由 `uds_types.negative_response()` 构造 |
| NRC 常量集 | `0x10 0x11 0x12 0x13 0x22 0x24 0x31 0x33 0x35 0x36 0x37 0x73 0x7F` |
| 空请求 | `7F 00 13`（固定回显 SID `0x00`） |
| 未知 SID | `7F <sid> 11` |
| 统一的"负响应抑制/限速"策略 | `NOT_IMPLEMENTED` |

### 5.7 协议实现 vs 安全策略框架（本盘点的关键区分）

| 类别 | 当前规模 |
|------|----------|
| **协议实现** | 15 个 SID（14 静态 + `0x27` 注入通路）、27 字段状态机、多帧传输/文件模型、多 ECU 路由与实时 runtime |
| **安全策略框架** | **几乎没有**：无 session framework、无 permission framework、无 gateway policy、无 message filtering、无 rate limiting、无 service authorization |

已存在的**策略性**元素只有三个（且都是单点、非框架）：

1. `0x27` 的失败锁定（**反暴力破解**，作用于安全访问本身）；
2. 受保护 DID 的 `SECURITY_LEVEL1` 检查（`0x22`）；
3. `gateway_runtime.py` 的接口白名单 fail-closed（只允许 `vcan0`，**运行环境约束**，非报文策略）。

---

## 6. DTC Capability

### 6.1 分层事实

| 层 | 载体 | 是否可变 | 证据 |
|----|------|----------|------|
| 静态 DTC 定义（出厂 statusOfDTC） | `dtc_database.DTC_DATABASE`（3 个 DTC：`0x100001/2/3`） | 否（handler 从不写） | `dtc_database.py:54-58` |
| severity / functionalUnit（定义属性） | `dtc_database.DTC_METADATA`（`DtcMetadata` NamedTuple） | 否 | `dtc_database.py:113-117` |
| 运行时 statusOfDTC | `EcuState.dtc_records` | 是 | `uds_server.py:59` |
| 写入原语（受校验） | `dtc_runtime.set_dtc_status()` / `clear_dtc_status()` / `get_dtc_status()` / `is_registered_dtc()` | — | `dtc_runtime.py:89-173` |
| 导入期一致性护栏 | `DTC_DATABASE` 与 `DTC_METADATA` 键集必须相等，否则**导入失败** | — | `dtc_database.py:123-128` |

`severity` 与运行时 `status` **正交**：`0x14` 清 status 不改 severity；
`0x11` 复位保留 `dtc_records` 与 metadata。

### 6.2 故障产生机制 —— 明确结论

> **当前没有任何"故障产生"的协议能力。**

证据（`git grep` 全仓库，排除 `dtc_runtime.py` 自身）：

- `dtc_runtime.set_dtc_status()` 的调用点**全部在测试文件**中
  （`tests/test_dtc_unit.py`，约 30 处）；**生产代码零调用点**。
- 生产代码对 `dtc_runtime` 的使用**只有** `clear_dtc_status()`
  （`uds_server.py:896`、`:905`，属 `0x14`）。
- `0x85` 只写 `state.dtc_setting_enabled`（`uds_server.py:973`），
  **不产生、不修改任何 DTC status**。
- 因此 `set_dtc_status()` 是**进程内 test seam**，不是 UDS 服务。

| 概念 | 状态 |
|------|------|
| 静态 DTC 定义 | `VERIFIED` |
| 运行时 DTC 状态 | `VERIFIED` |
| severity metadata | `VERIFIED`（`0x19/07/08/09`） |
| DTC 清除（`0x14`） | `VERIFIED` |
| DTC mutation **API**（进程内） | `VERIFIED`（仅测试调用） |
| **UDS fault-injection service** | `NOT_IMPLEMENTED` |
| UDS 侧故障产生（任何服务置位 status） | `NOT_IMPLEMENTED` —— **没有任何 UDS 服务会置位 DTC status** |
| DTC 功能组 | `NOT_IMPLEMENTED`（`0x14` 仅 `0xFFFFFF` 或单个已存在 DTC） |

> 把 `dtc_runtime.set_dtc_status()` 说成"已有 UDS 故障注入"是**错误**的。

---

## 7. Gateway / Multi-ECU Capability

### 7.1 两层结构

| 层 | 文件 | 职责 | 状态 |
|----|------|------|------|
| 协议核心 | `ecu/gateway.py`（`VirtualEcu` / `DiagnosticGateway`） | 纯路由：CAN ID + payload → 路由决策；**不 import isotp/socket** | `VERIFIED`（13 unit） |
| 实时 runtime | `ecu/gateway_runtime.py`（`IsotpTransport` / `GatewayRuntime`） | transport adapter：socket 生命周期、select 循环、信号、错误遏制 | `VERIFIED`（53 unit + 21 e2e） |

### 7.2 能力

| 能力 | 状态 | 证据 |
|------|------|------|
| 物理寻址 `0x7E0/1/2 → 0x7E8/9/A` | `VERIFIED` | 总线级抓包断言，应答不串号 |
| 功能寻址 `0x7DF` 广播（三 ECU 都执行） | `VERIFIED` | 写 DID 的 NRC `0x7F→0x33` 变化为证 |
| 功能寻址**总线静默**（零响应帧） | `VERIFIED` | raw CAN 抓包断言 |
| 未知物理 ID 丢弃 | `VERIFIED` | 总线只出现 tester 自己的请求 |
| ECU 状态隔离（各持独立 `EcuState`） | `VERIFIED` | 会话状态按 ECU 独立且可逆 |
| 真实 ISO-TP 分帧（双向 FF+CF） | `VERIFIED` | 20B 响应分帧、13B 请求重组 |
| runtime 生命周期（SIGINT/SIGTERM → 0） | `VERIFIED` | 无残余进程 + 同接口可立即重绑定 |
| 传输错误遏制（EILSEQ/ECOMM/EMSGSIZE 丢弃继续） | `VERIFIED` | unit + 沿用 `ecu.py` 策略 |
| 接口 fail-closed（只允许 `vcan0`） | `VERIFIED` | `can0/can1/vcan1` → exit 2，无 socket |
| 可复现入口 | `VERIFIED` | `scripts/repro/verify_gateway_runtime.sh`（13 项断言） |

### 7.3 明确**未完成**的 Gateway 能力

| 能力 | 状态 |
|------|------|
| gateway security policy（报文级策略/授权） | `NOT_IMPLEMENTED` |
| advanced filtering（ID 白/黑名单、速率限制） | `NOT_IMPLEMENTED` |
| `0x27` 经网关透传 | `NOT_IMPLEMENTED`（架构边界，非缺陷） |
| 真实 CAN 总线 | `OUT_OF_SCOPE`（安全边界） |
| DoIP | `NOT_IMPLEMENTED` |
| 多进程 ECU orchestration / IPC / supervisor | `NOT_IMPLEMENTED`（v1 刻意单进程） |

> **`0x7DF` Gateway Runtime 已完成并合入 main（OQ7 / PR #21）**，
> **不得**再列为未实现能力（当前文档中仍有一处如此表述，见 §12）。

---

## 8. CAN / ISO-TP / Fuzz Capability

### 8.1 模块清单（`labs/vehicle/can-tools/fuzz/`，9 个 tracked 模块）

```
__init__.py  case_schema.py  isotp_corpus.py  isotp_fuzz.py  isotp_mutators.py
replay.py    uds_corpus.py   uds_fuzz.py      uds_mutators.py
```

> **纠正一个易犯错的说法**：`fuzz-out/` 是**生成物目录**，`git ls-files` 显示
> **0 个 tracked 文件**（已 gitignore，磁盘上 3 个条目）。
> 它是**未跟踪产物**，不是"已提交的语料"。

### 8.2 Fuzz 分层

| 层 | 状态 | 入口 | 说明 |
|----|------|------|------|
| ISO-TP 分帧 fuzz（**8 个维度**：`pci, sf_length, ff_length, sn, fc_status, bs, stmin, padding`） | `VERIFIED` | `fuzz/isotp_fuzz.py` | 主引擎 |
| **CAN 层** fuzz（CAN ID / 任意原始帧） | `PARTIAL` | 同上 + `--dimensions padding` | **只有 padding/DLC 一个维度**；README 明示"不做 CAN ID fuzzing" —— **不存在**任意原始帧 fuzz |
| UDS 请求层 SF fuzz（**4 个维度**：`uds_len, uds_sid, uds_sub, uds_payload`） | `VERIFIED` | `fuzz/uds_fuzz.py` | 复用 `isotp_fuzz.execute_case`，不是第二个引擎 |
| UDS **MF** fuzz（8–128B，FF+CF） | `VERIFIED` | 同一入口的 size-class 分支 | **无独立 MF CLI** |
| kernel oracle（**三态** `AVAILABLE`/`UNAVAILABLE`/`ERROR`） | `VERIFIED` | `execute_case` 内 | `case_schema.py`；只读 `dmesg` 快照，**从不 `dmesg -c`**；不可用时 `kernel_events=None`（**"不可用 ≠ 0 事件"**） |
| replay（逐帧重放 + 恢复探测复现） | `VERIFIED` | `fuzz/replay.py <case|dir> [--all] [--repeat N]` | 重放 `frames[]` 原样；`--repeat` 检测非确定性；**能识别并跳过 summary/metadata** |
| capture 三格式 | `VERIFIED`（2/3 有回环断言） | `capture/capture_lab.sh` | 见 §8.5 |

### 8.3 OQ3 冻结契约（逐个复核，非照抄文档）

| 界 | 值 | 源码证据 |
|----|----|----------|
| SF 上限 | **7 字节** | `uds_corpus.py:63` `MAX_UDS_REQUEST_LEN = 7` |
| MF 范围 | **8–128 字节** | `uds_corpus.py:72` `MAX_MF_UDS_REQUEST_LEN = 128` |
| > 128 | **admission reject（抛出，不截断）** | `uds_corpus.py`（`UdsEncodeError`）；smoke 断言 L=129/200/300 |
| 每 case 最大帧数 | **19** | `uds_corpus.py:80` `MAX_FRAMES_PER_CASE = 19` |
| 维度集 | `(uds_len, uds_sid, uds_sub, uds_payload)` —— **不得新增第五维** | `uds_mutators.py:84` |
| v1 golden | **947 case** + 2 个冻结 digest | `scripts/repro/verify_uds_fuzz_smoke.sh` |

**M1–M7 变异算子**（`uds_mutators.py:123-131`，全部位于 `uds_len` 维度）：
全部只改 **PCI 字节 / 帧数，从不改 payload 字节**。

| 算子 | 变异 | 代号 |
|------|------|------|
| `mf_ff_declared_zero` | FF 声明长度 := 0 | M1 |
| `mf_ff_declared_minus_one` | FF 声明长度 := L−1 | M2 |
| `mf_ff_declared_plus_one` | FF 声明长度 := L+1 | M3 |
| `mf_ff_declared_max` | FF 声明长度 := `0x0FFF` | M4 |
| `mf_cf_drop_last` | 删除最后一个 CF | M5 |
| `mf_cf_sn_zero` | 指定 CF 的 SN := 0（CF#16 排除为 no-op） | M6 |
| `mf_cf_sn_off_by_one` | 指定 CF 的 SN := (SN+1) mod 16 | M7 |

### 8.4 离线 vs 在网 / CI 归属

| 范围 | 是否需要 vcan0 | 是否在 CI |
|------|----------------|-----------|
| `case_schema` / `isotp_corpus` / `isotp_mutators` / `uds_corpus` / `uds_mutators`（生成、去重、排序） | 否 | **是（Fast job）** |
| `test_fuzz_offline.py`(99) / `test_uds_fuzz_offline.py`(120) | 否（**但需 python-can/can-isotp 已安装**，不开 socket） | **是（Fast job）** |
| `execute_case`（起 ECU 子进程 + 原始通道） | **是** | 否 |
| `test_isotp_malformed_vcan.py`(40) / `test_uds_fuzz_vcan.py`(8) | **是** | 否（Extended / manual） |
| `capture_lab.sh` | **是** | 否（Extended） |
| `verify_fuzz_smoke.sh` / `verify_uds_fuzz_smoke.sh` | **是** | **否（manual-only）** |
| 长 campaign（`isotp_fuzz.py --smoke` = 30 case；更大规模） | **是** | 否（CI 明确排除） |

### 8.5 Capture 三格式

| 段 | 格式 | 回环断言 |
|----|------|----------|
| A | CAN log（`candump -L` → `canplayer -I`） | **有**：`diff -u` → `IDENTITY = PASS` |
| B | BLF（`can_capture_blf.py` record/inspect/replay） | **有**：diff + inspect 计数必须为 6 |
| C | PCAP（`tshark -w` / `-r`） | **无回放**：仅解析计数（供 Wireshark 分析） |

> 回环断言是**归一化 `ID#DATA` 的集合相等**，不是逐帧/带时间戳的严格相等。

### 8.6 明确未实现（Fuzz 面）

| 项 | 状态 | 证据 |
|----|------|------|
| **Stateful / 多请求序列 fuzz**（如 `10 03 → 27 01 → 27 02 → 2E`） | **`OUT_OF_SCOPE`** | `uds_mutators.py:8`、`uds_corpus.py:15-19/408`、`docs/*` 多处明示；每个 case 起**全新 ECU 进程**，状态不跨 case |
| CAN ID fuzz / 任意原始帧 | `NOT_IMPLEMENTED` | `can-tools/README.md:445` |
| Coverage-guided fuzz（instrumentation） | `NOT_IMPLEMENTED` | README 明示"不做 coverage-guided" |
| libFuzzer / Honggfuzz | `NOT_IMPLEMENTED` | 未部署 |
| AFL 接入 CI | `NOT_IMPLEMENTED` | 无 workflow / `qa_*` 调用 |
| PCAP 回放 | `NOT_IMPLEMENTED` | 仅捕获 |
| ISO-TP 时序/间隔变异 | `OUT_OF_SCOPE` | `isotp_corpus.py:69` 明示留给后续 |
| `0x27` seed→key 正流程作为 fuzz 路径 | `OUT_OF_SCOPE` | `uds_corpus.py:382` 标为 NEXT DESIGN |
| 多 ECU fuzz | `OUT_OF_SCOPE` | `can-tools/README.md:540` |

> **易误读点**：ISO-TP 模块自述含 "stateful"，指的是**单次连接内的 ISO-TP
> 传输状态机**（FF/FC/CF 交互），**不是**跨请求的 UDS 会话序列。

---

## 9. Firmware / File Transfer Capability

### 9.1 UDS 传输服务：全部为 synthetic in-memory 模型

| 服务 | 后端模型 | 终态数据落点 | 证据 |
|------|----------|--------------|------|
| `0x34` RequestDownload | 纯内存 | 置标志 + 清 `bytearray` | `uds_server.py:1385-1450` |
| `0x35` RequestUpload | 纯函数 | 源为 `bytes(range(256))` | `transfer_database.py:81` |
| `0x36` TransferData | 纯内存 | `download_buffer.extend()` | `uds_server.py:1661` |
| `0x37` RequestTransferExit | 提交点 | 写入 `state.files[path]`（Python dict） | `uds_server.py:1732-1733` |
| `0x38` RequestFileTransfer | in-memory 文件模型 | `files: dict[str, bytes]` | `uds_server.py:307` |

模块自述（权威声明）：

- `transfer_database.py:26-27`："纯内存 synthetic 目标——**不是真实 Flash、不接触
  文件系统、不执行真实刷写**"
- `file_database.py:19-21`："**这不是真实文件系统**：不访问磁盘、不做路径解析、
  不做符号链接、不做权限检查、不递归遍历"

**下载数据的终点**：`0x36` → RAM `bytearray` →（仅 `0x37` 且 `file_mode` 为
addFile/replaceFile 时）→ 进程内 dict。**不持久化、不落盘**。
plain `0x34` 下载（`file_mode == 0`）在 `0x37` **不提交任何东西**（`uds_server.py:1731`）。

### 9.2 完整性校验 —— 明确结论

> **传输路径上没有任何完整性校验。**

对 `uds_server.py` / `transfer_database.py` / `file_database.py` 检索
`checksum|crc32|sha256|md5|hmac|signature|hashlib` → **零匹配**。

唯一的门是**长度比较**（`len(buffer) < download_size` → `NRC 0x22`，
`uds_server.py:1723`）以及 `0x38` 的"声明长度必须相等"规则
（`compressed != uncompressed → 0x31`）。

即：**无摘要、无签名、无回滚保护、无 secure boot 链路。**

### 9.3 固件分析链路（filesystem extraction）

| 格式 | 构建 | 识别 | **真解包** | 状态 |
|------|------|------|-----------|------|
| SquashFS | `mksquashfs` | `file` + `binwalk` + `unsquashfs -s` | **是**：`unsquashfs -d` | `VERIFIED` |
| JFFS2 | `mkfs.jffs2` | `binwalk` | 否（unpacker 未安装，仅探测） | `PARTIAL` |
| UBIFS | `mkfs.ubifs` | `binwalk` | 否 | `PARTIAL` |
| UBI | `ubinize` | `binwalk` | 否 | `PARTIAL` |

- SquashFS 真解包证据：`labs/firmware/synthetic-ecu/analyze.sh:30`、
  `firmware_diff.sh:16`（`unsquashfs -d`）。
- JFFS2/UBIFS/UBI 停在识别；`fs_formats.sh:48-50` **只探测** `jefferson` /
  `ubireader_extract_files` 是否存在（未安装）。
- **无任何 tracked 脚本调用 `binwalk -e`**（repo-wide `git grep` 零匹配）；
  `binwalk -e` 只出现在 README 的手动说明中。
- 全部固件脚本为 **manual-only**：不被 `ci.yml` / `qa_fast.sh` /
  `qa_extended.sh` 调用。
- 样本集 `samples/images/` 为**空**（按设计）；整条链路只跑过**手工合成镜像**
  （`build_fw.sh` 自述："NOT real vehicle/ECU firmware"）。
- `samples/MANIFEST.template.yml` 是**模板即文档**：无任何代码解析/校验它。

### 9.4 明确未实现（Firmware / Embedded）

```
真实 flash 写入（无 /dev/mtd / mtd_write / spi_flash / 块设备访问）
签名验证 / secure boot（secure_boot=on 只是合成镜像里的一行配置文本）
固件数据完整性校验（无 CRC/SHA/HMAC）
真实文件系统（自述：不访问磁盘）
JFFS2 / UBIFS / UBI 解包链路
binwalk -e 的自动化调用
真实厂商样本的自动提取（样本集为空）
MANIFEST 的机器可校验（无代码读取）
DoIP / automotive Ethernet / 任何 UDP 诊断传输（见 §9.5）
压缩 / 加密固件传输（0x38 仅 DFI 00，且拒绝 compressed != uncompressed）
硬件信任根 / 回滚保护 / 启动链分析
```

### 9.5 DoIP / Automotive Ethernet

**不存在。** repo-wide 检索 `doip` 只命中**声明其不存在的文档**；
`13400` 唯一出处是合成镜像里的配置文本 `diag_udp=13400`（非代码）。
tracked lab 代码中**无 UDP / AF_INET / SOCK_DGRAM 套接字**。
全部车辆能力均为 **SocketCAN over vcan0**。

---

## 10. ARM / Reverse Engineering Capability

| 能力 | 状态 | 证据 / 边界 |
|------|------|-------------|
| AArch64 / armhf 交叉编译 | `VERIFIED` | `scripts/repro/verify_arm.sh`（实编译 + 实执行，断言 `ARM_RUNTIME_OK 42`） |
| QEMU user-mode 执行 | `VERIFIED` | 同上（binfmt 或 `qemu-aarch64` 回退） |
| QEMU + GDB remote 调试 | `VERIFIED` | `verify_qemu_gdb.sh`：断点命中 → continue → rc0 |
| QEMU **system-mode** | `INFRASTRUCTURE_ONLY` | 仅文档提及，**无代码调用** |
| Ghidra headless 分析 | `VERIFIED` | `verify_ghidra.sh`：AARCH64 + `ERROR_COUNT=0` + main 反汇编 12 条 |
| `readelf` / `strings` / `nm` / `objdump` | `PARTIAL` | `readelf`/`strings` 在固件脚本中**真实使用**；`r2` / `objdump` / `nm` 在 tracked 代码中**仅有存在性检查** |
| binfmt_misc additive 注册 + selfheal | `VERIFIED`（repo 侧） | `configs/binfmt/binfmt-selfheal.sh` v2.3 fail-closed + 版本化回归；**系统侧部署未重新确认** |
| 固件提取（SquashFS） | `VERIFIED` | 见 §9.3（真解包） |
| 固件提取（JFFS2/UBIFS/UBI） | `PARTIAL` | 仅 build + identify |
| YARA 扫描 | `VERIFIED` | `labs/yara-basic/scan.sh`（2 TP + 1 TN 断言，`YARA_RESULT=PASS/FAIL`）；由 `qa_extended.sh:102` 调用（manual） |
| AFL++ 通用 fuzz | `VERIFIED`（**manual-only**） | `labs/fuzzing/afl-demo/run.sh`：真实 crash + 确定性重放断言；**不在任何门禁内** |
| 逆向教学靶场 | `VERIFIED`（manual） | `labs/reverse/{hello,arm64}`：源码与 README tracked，**二进制 gitignore** |
| 动态分析（strace/ltrace） | `INFRASTRUCTURE_ONLY` | 仅 `healthcheck.sh` 存在性检查，tracked 代码无脚本化运行 |
| `scripts/repro/*` 整体定性 | **manual-only** | 不被 `ci.yml` / `qa_fast.sh` / `qa_extended.sh` 调用 |

---

## 11. Testing and CI Coverage

### 11.1 计数（独立复核）

按 `def test_` 逐文件统计（与 `run_tests.sh` 的 `Ran N tests` 口径一致）：

| 层 | 用例数 |
|----|--------|
| unit（11 个文件） | **673** |
| e2e / vcan（11 个文件） | **234** |
| **合计** | **907** |

其中 Gateway Runtime 自身：**unit 53 / e2e 21**。

### 11.2 门禁分层

| 门禁 | 内容 | 触发 | 需要 vcan0 |
|------|------|------|-----------|
| `scripts/qa_fast.sh` | ruff + mypy + `run_tests.sh unit` + can-tools offline | CI **每次** push/PR | 否 |
| `scripts/qa_extended.sh` | `run_tests.sh all` + capture + YARA | CI **仅 workflow_dispatch** | 是 |
| `scripts/healthcheck.sh [--extended]` | 环境/工具/服务/链路总检 | 手动 | `--extended` 需要 |
| `labs/**/tests/run_tests.sh` | 各实验离线/在网测试 | 手动或经 qa_* | 部分 |
| `scripts/repro/verify_*.sh` | 最小可复现闭环（6 个） | **手动，均不在 CI** | 部分 |

### 11.3 按能力的自动化覆盖

| 覆盖面 | 结论 |
|--------|------|
| **unit（离线）全部进 CI** | 是 —— `qa_fast.sh` 调 `run_tests.sh unit`，因此**每个有 unit 的能力都被 CI 覆盖** |
| **e2e / vcan 全部不进 CI** | 是 —— Fast job 明确不含需 vcan0 的 e2e；Extended 仅手动。**所有在网行为均为 Local verified** |
| can-tools 离线（含 fuzz harness 离线） | 进 CI |
| fuzz campaign / capture / malformed vcan / UDS-fuzz vcan smoke | 不进 CI（Extended 或纯手动） |
| AFL demo / 六个 repro 脚本 / 固件脚本 / ARM-QEMU-Ghidra | **均不进任何门禁（manual-only）** |

### 11.4 覆盖对称性（哪些只有 unit、哪些只有 e2e）

| 能力 | unit | e2e |
|------|------|-----|
| `0x10/0x11/0x22/0x27/0x2E/0x3E` | 有 | 有 |
| `0x14` / `0x19` / `0x31` / `0x34`–`0x38` / `0x85` | 有 | 有 |
| keygen（`0x27` 算法） | 有（34） | 无独立 e2e（经 `0x27` 端到端间接覆盖） |
| gateway 协议层 | 有（13） | 有（多 ECU e2e 4） |
| gateway runtime | 有（53） | 有（21） |
| ISO-TP 传输参数矩阵 | 无独立 unit | 有（14，在网） |
| 本盘点未发现"只有 e2e、没有 unit"的能力；也未发现"只有 unit、完全没有 e2e 验证"的核心服务 | | |

### 11.5 未经本轮执行的 gate（如实标注）

| gate | 本轮执行情况 |
|------|--------------|
| `run_tests.sh all`（907） | **NOT RUN**（本任务不修改生产代码，故未重跑；引用 OQ7 post-merge 已记录结果） |
| `qa_fast.sh` | **NOT RUN** |
| `qa_extended.sh` | **NOT RUN** |
| `healthcheck.sh` | **NOT RUN** |
| GitHub CI | OQ7/OQ7-docs 的既有 run 见 `TEST_MATRIX.md`；**本轮未触发新 run** |

---

## 12. Documentation Drift

> **本章只记录，不修改**（本任务禁止改既有文档）。
> 仅报告"**当前状态文档**与代码不一致"；历史审计/过程记录中的过时表述属正当历史，
> 不计入漂移（判定依据见 §12.3）。

### 12.1 确认的漂移

#### A. `docs/AUTONOMOUS_BUILD_STATE.md`（断点文件，CURRENT STATE）

| # | 位置 | 文档声称 | 代码/事实 | 备注 |
|---|------|----------|-----------|------|
| D1 | `:136`（CURRENT STATE 的 `REMAINING GAPS`） | `0x7DF 功能寻址**实时网关守护进程**` 列为**未实现** | **已实现并合入 main**（OQ7 / PR #21）；`ecu/gateway_runtime.py` 存在，53 unit + 21 e2e | **同文件自相矛盾**：同一 CURRENT STATE 已在 `:91-93` 写明 OQ7 已合入 |

#### B. `labs/vehicle/uds-basic/README.md`

| # | 位置 | 文档声称 | 代码/事实 |
|---|------|----------|-----------|
| D2 | `:1625` | `test_ecu_unit.py` **105** 用例 | **116** |
| D3 | `:1630` | `test_dtc_unit.py` **30** 用例 | **115** |
| D4 | `:1636` | `test_e2e_vcan.py` **65** 用例 | **69** |
| D5 | `:1641` | `test_dtc_vcan.py` **8** 用例 | **21** |
| D6 | `:1644` | `test_transfer_vcan.py` **17** 用例 | **18** |
| D7 | `:1245` | `handle_uds_request()` 覆盖 **106** 用例 | **116** |
| D8 | `:1291` | `test_e2e_vcan.py` 覆盖 **65** 用例 | **69** |
| D9 | `:1724`、`:2321` | 网关 "48 单元 + 20 e2e" | **53 / 21** —— 且与**同文件** `:1728-1729`（写 53 / 21）**自相矛盾** |
| D10 | `:1258` | `0x27` 单元测试"细分为**五个类**" | 其**自身表格列了 7 行**，而 `test_ecu_unit.py` 实有 **8** 个 `Test*` 类 —— **三处互不相等** |

> D2–D6 全在 **§12 文件结构**注释里；同文件 **§8.1「当前测试规模」表格是正确的**
> （本轮已逐文件核对，合计 907 一致）。因此是"同文件内新旧两处不同步"。

#### C. `README.md`（**仓库根**，此前未被发现的漂移集中区）

| # | 位置 | 文档声称 | 代码/事实 |
|---|------|----------|-----------|
| D11 | `:212-213` | "UDS 离线单元 **581** + vcan0 端到端 **208**（共 **789**）+ can-tools 离线 **183**"（标 "2026-09-23 实测"） | **673 / 234 / 907**；can-tools 离线 **303**（183 漏掉了 `test_uds_fuzz_offline.py` 的 120） |
| D12 | `:232` | 把 **`0x85`** 列在"UDS 剩余扩展"（未实现） | `0x85` **已实现**（OQ6-B / PR #17，注册于 `uds_server.py:2144`） |
| D13 | `:234` | "0x7DF 功能寻址的用户态实时网关守护进程"列在后续方向 | **已交付**（OQ7 / PR #21） |
| D14 | `:104-105` | UDS 服务清单**不含 `0x85`**；Gateway 只列 `ecu/gateway.py` | `0x85` 已注册；`gateway_runtime.py` 已存在（根 README 全文未提及两者） |

#### D. `docs/TEST_MATRIX.md`

| # | 位置 | 文档声称 | 事实 |
|---|------|----------|------|
| D15 | `:239` | 小节标题 `**Post-merge（main）证据（当前生产代码基线）：**` 之下列的是 `c112f022…`（PR #17 = OQ6-B） | 当前生产代码基线是 **`ae22a556…`（PR #21 = OQ7）**；同文件 `:12`/`:15` 已正确区分（`c112f022` 标为**上一轮**基线）—— **同文件自相矛盾** |

**汇总**：15 处漂移，涉及 4 个当前状态文档；其中 4 处是**同文件内自相矛盾**
（D1、D9、D10、D15），其余为与代码不符。D1/D12/D13 三处均**低估**了实际能力。

### 12.2 跨文档的环境事实矛盾（历史 vs 历史，无仲裁者）

以下矛盾存在于**历史审计文档之间**，且**没有任何脚本可以仲裁**（仅存在性探测）：

| 项 | 文档 A | 文档 B | 现状 |
|----|--------|--------|------|
| `strace` / `ltrace` | `ENVIRONMENT_INVENTORY_2026-09-17.md`：「NOT FOUND / 未安装」 | `FINAL_ENVIRONMENT_AUDIT_2026-09-20.md`：「strace 6.8、ltrace 0.7.3」 | 两者相差 3 天，**仓库无法判定谁对**（`healthcheck.sh` 仅 `command -v`） |
| binfmt selfheal 部署版本 | `docs/phases/phase-01-wsl.md:60`「enabled + active」（v1 时代） | `FINAL_ENVIRONMENT_AUDIT:86`「v2.2 双端部署」；`README.md:136`「v2.3 双端部署」 | **三种说法**；`SECURITY_LAB_STATUS.md` 自陈「SYSTEM DEPLOYMENT NOT REVERIFIED」 |

> 这两项按 §12.3 属于**历史文档之间**的矛盾，不列为"当前状态文档漂移"，
> 但它们是**可核查性缺口**（见 §13 G7）。

### 12.3 明确**不算**漂移的历史记录（已核查并排除）

`docs/AUTONOMOUS_BUILD_STATE.md:149-153` **自行声明**：

> 「以下各节为历史断点（Historical checkpoints），按当时状态记录，**不再回改**。
> … **判定当前状态一律以本文档顶部的 CURRENT STATE 为准。**」

因此以下命中**均为正当历史**，不计入漂移：

- 同文件 `:227`、`:411`、`:414`、`:429`、`:497` 的「0x7DF 实时网关守护进程未做」
  ——位于历史段（`:149` 之后），有明文豁免；
- `labs/vehicle/uds-basic/README.md` **§14.7** 中按阶段/日期标注的历史基线
  （`:1940` 的 104、`:1943` 的 312、`:1949` 的 143、`:1974-1976` 的 can-tools 183、
  Healthcheck 31 等）——每块都写明是"Phase X 之后"或"OQ6 合并后"的实测快照；
- `docs/TEST_MATRIX.md:97`（标"2026-09-21 基线"且在 `:98-99` 自行更正为 PASS 4）、
  `:115-117`（2026-09-20/21 审计窗口）、`:201-202`（带日期的 healthcheck 计数）；
- `docs/ENGINEERING_CLOSE_2026-09-21.md`、`docs/ENVIRONMENT_INVENTORY_2026-09-17.md`、
  `docs/PHASE0_PHASE1_EXECUTION_2026-09-17.md`、`docs/FINAL_ENVIRONMENT_AUDIT_2026-09-20.md`
  中的一切过时状态（Docker/fuzz/CI 等）——自述为带日期的冻结快照
  （如 ENVIRONMENT_INVENTORY 头部写明"代表 2026-09-17 15:57–16:20 的冻结基线"）；
- 历史 commit message。

> 判定口径：**带阶段/日期标注的实测快照 = 历史**（保留）；
> **以"当前/最新"自居的表述 = 当前状态**（漂移，已列入 §12.1）。

### 12.4 本轮核查无漂移的当前状态文档

| 文档 / 区段 | 结论 |
|-------------|------|
| `docs/SECURITY_LAB_STATUS.md` | **无漂移**：OQ3–OQ7 均标为已合入；计数 673/234/907 正确；`qa_extended`/`healthcheck` 的 NOT RUN 标记正确；`0x27` 的边界说明与代码一致 |
| `docs/TEST_MATRIX.md` **§2 / §3**（除 D15 的小标题） | **无漂移**：§2 的 22 个逐文件计数**全部**与 `def test_` 实测吻合 |
| `docs/ARCHITECTURE.md` | **无漂移**：§3.1.1 网关 runtime 标为 VERIFIED；无 `PARTIAL`/`待建`/`待 PR` 冲突文字 |
| `scripts/repro/README.md` | **无漂移**：6 个脚本均存在；`verify_gateway_runtime.sh` 的 "13 项断言 / PASS 7" 与脚本实际 `ok()`/`record()` 调用数相符 |
| `labs/vehicle/uds-basic/README.md` **§8.1** | **正确**：907 与逐文件计数一致（漂移只在 §12 与 §13 等其它区段） |

**未误报的检查**（说明判据的严格性）：`docs/TEST_MATRIX.md:93` 的
"malformed 系列 47"经核对**是正确的**——`test_isotp_malformed_vcan.py` 自身定义 40 个
用例，另有 7 个由 `SequentialSnBase` / `FcParamCase` 子类**继承**，
unittest 计数为 47（`def test_` 静态计数会少算继承用例）。本盘点对该文件采用
`run_tests.sh` 的口径，未把它误列为漂移。

---

## 13. Remaining Gaps

缺口分类：G1 协议能力 / G2 状态机·架构 / G3 安全策略 / G4 Gateway·Network /
G5 Firmware·Embedded / G6 Fuzz·Testing / G7 Infrastructure·Automation / G8 Documentation Drift。

| ID | 缺口 | 证据 | 当前影响 | 与已有架构关系 | 是否适合独立 OQ |
|----|------|------|----------|----------------|-----------------|
| G1-1 | `0x28` CommunicationControl | 不在 `SERVICES`（`uds_server.py:2130-2145`） | 无通信控制能力 | 纯新增 handler + 状态字段 | 是（小） |
| G1-2 | `0x38` RequestFileTransfer 的 **ResumeFile（`modeOfOperation = 0x06`）尚未实现**（`0x37` RequestTransferExit **已存在，不是缺口**） | 已实现 mode 仅 `0x01`–`0x05`（`file_database.py:62-78`、`SUPPORTED_FILE_MODES`）；`test_file_transfer_unit.py:25` 明列 out of scope | 被中断的文件传输无法续传 | 需在 `file_database` 新增 mode `0x06` + 扩展 `EcuState` 传输状态 | 是（中） |
| G1-3 | `0x19` 其余 reportType（`03/04/05/06/0B–0E/14/15/16/1A…`） | 仅 6 个 reportType 有分支（`uds_server.py:1106-1215`） | DTC 报告面不完整 | 复用既有 DTC 模型 | 是（可分批） |
| G1-4 | `0x11 04/05`、`0x31` 的 option/status record | `SUPPORTED_RESET_TYPES` 仅 3 值；`0x31` 无 record 处理（`uds_server.py:1284`） | 少量子功能缺失 | 局部扩展 | 是（小） |
| G1-5 | DTC 功能组（`0x14` groupOfDTC） | `0x14` 仅 `0xFFFFFF` 或单个已存在 DTC | 无法按功能组清码 | 需定义功能组语义 | 是（中） |
| G1-6 | 压缩/加密固件传输（`0x38` 仅 DFI `00`） | `compressed != uncompressed → 0x31` | 无真实固件语义 | 依赖 G5 | 否（需先有 G5） |
| G2-1 | **Session framework 缺失** | `state.session` 只有 1 处读取判断（`uds_server.py:758`，属 `0x2E`） | 除 `0x2E` 外所有服务无视会话（实测 `0x34/0x35/0x31/0x14/0x19/0x85` 默认会话即正响应） | 需要 registry 级前置检查抽象 | 是（**架构级**） |
| G2-2 | **Permission / 授权框架缺失** | 无 permission 表/装饰器；`security_level` 仅 2 处读取 | 无统一"服务授权"模型 | 依赖 G2-1 | 是（架构级） |
| G2-3 | `0x27` 仅单安全等级（无 Level 2） | 仅 `SECURITY_LEVEL1` | 无法表达多级权限 | 需等级位掩码 | 是（中） |
| G2-4 | suppressPosRspMsgIndicationBit 仅 `0x3E` 实现 | `0x19:1097`、`0x85:958`、`0x11:493` 明示未实现 | 响应抑制不一致 | 需统一请求预处理层 | 是（小-中） |
| G3-1 | Gateway security policy（报文级策略） | 无任何报文策略代码 | 网关透明转发，无过滤/授权 | 依存 G4 与 G2 | 是（架构级） |
| G3-2 | Rate limiting / 报文级反滥用 | 仅 `0x27` 有失败锁定 | 无总线级限速 | 可独立 | 是（中） |
| G3-3 | 诊断授权 / 服务授权矩阵 | 不存在 | 无服务级授权 | 依赖 G2-2 | 是 |
| G4-1 | `0x27` 经 Gateway Runtime 透传 | 静态表不含 `0x27`；e2e 显式固化该边界 | 网关实验里无法做安全访问 | 需解决"每 ECU 独立注入 vs 注入语义只定义一处" | 是（中） |
| G4-2 | 多进程 ECU orchestration / IPC | 无（v1 刻意单进程） | 单进程内三 ECU | 与现有单进程模型冲突，需设计决策 | 需 design review |
| G4-3 | DoIP | 全仓库无代码 | 无以太网诊断 | 全新传输栈 | 是（大） |
| G5-1 | 真实 Flash 写入 / 刷写 | 无 `/dev/mtd` 等访问 | 传输终点是内存 | 需硬件/仿真后端 | 需 design review |
| G5-2 | 固件签名 / 完整性校验 | 传输路径零 `crc/sha/hmac/signature` | 无真实性保证 | 可作用于 synthetic 模型 | 是（中） |
| G5-3 | JFFS2 / UBIFS / UBI 解包链路 | `fs_formats.sh:48-50` 仅探测工具 | 两类格式无法解包 | 需装 jefferson/ubireader | 是（小-中） |
| G5-4 | 真实厂商样本 + MANIFEST 机器校验 | 样本集为空；模板无代码 | 未跑过真实样本 | 依赖外部样本与授权 | 需外部条件 |
| G6-1 | **Stateful / 多请求序列 fuzz** | `uds_mutators.py:8`、`uds_corpus.py:15-19/408` 明列 OUT OF SCOPE | 无法 fuzz `10 03→27 01→27 02→2E` 类链 | 需新维度 + 跨请求状态模型 | 是（**架构级**） |
| G6-2 | CAN 层 fuzz（CAN ID / 任意原始帧） | 仅 `padding`/DLC 一维；README 明示"不做 CAN ID fuzzing" | 无 CAN 层攻击面覆盖 | 复用既有 harness | 是（中） |
| G6-3 | Coverage-guided fuzz | 无 instrumentation | 无覆盖率反馈 | 需引入工具链 | 是（大） |
| G6-4 | 0x27 seed→key 正流程作为 fuzz 路径 | `uds_corpus.py:382` 标为 NEXT DESIGN | 安全流程未被 fuzz | 依赖 G6-1 | 是 |
| G7-1 | e2e 不在自动 CI | `ci.yml` fast job 不含 vcan0 e2e | 全部在网行为仅 Local verified | **需先验证 runner 的 vcan / `CAN_ISOTP` 支持与权限**（未验证，不在本盘点范围） | 是（中） |
| G7-2 | 六个 repro 脚本 + AFL + 固件脚本均不在门禁 | 无 workflow/`qa_*` 调用 | 无自动回归保护 | 需权衡运行时长 | 是（中） |
| G7-3 | `healthcheck.sh` 无 CI 证据 | 本轮 NOT RUN | 环境健康无自动证据 | 可加入 CI | 是（小） |
| G8-1 | **当前状态文档低估实际能力**：`0x7DF` 网关守护进程（D1/D13）、`0x85`（D12/D14）被列为未实现/缺失 | §12.1 A/C | 读者会以为 OQ6-B/OQ7 未交付 | 纯文档 | 是（小，纯文档） |
| G8-2 | **测试计数漂移**：`uds-basic/README.md` 9 处（D2–D10）+ 根 `README.md`（D11）与代码不符 | §12.1 B/C | 引用计数时会拿到错值；D9/D10 还同文件自相矛盾 | 纯文档 | 是（小，纯文档） |
| G8-3 | **同一文件内部自相矛盾** 4 处（D1、D9、D10、D15） | §12.1 | 读者无法判断哪个数字为准 | 纯文档 | 是（小，纯文档） |
| G8-4 | `docs/TEST_MATRIX.md:239` 的 §8 小标题把 OQ6-B 标为"当前生产代码基线" | §12.1 D（D15） | 与同文件 `:12`/`:15` 冲突 | 纯文档 | 是（小，纯文档） |
| G8-5 | **根 `README.md` 整体未跟上 OQ6-B / OQ7**（服务清单、剩余方向、计数共 4 处） | §12.1 C（D11–D14） | 仓库门面文档滞后最多 | 纯文档 | 是（小，纯文档） |

---

## 14. Candidate OQ Set

> **本节只罗列事实属性，不做排序、不做推荐、不给优先级。**
> 是否成为 OQ8 由后续 design review 决定。

| 候选 | 描述 | 前置条件 | 影响范围 | 依赖 | 风险 | 测试难度 | 能否独立 feature branch |
|------|------|----------|----------|------|------|----------|-------------------------|
| OQ-C1 Permission / Session Framework | 引入 registry 级 session→permission→service 前置检查抽象 | 需先确认"门控加入后既有服务语义不变"的迁移策略 | `uds_server.py` dispatcher + 全部 handler；`EcuState` 可能新增字段 | 无（G2-1/G2-2） | **高**：会改变多个既有服务的可调用条件，牵动大量既有测试 | 高 | 是（架构级，需谨慎设计） |
| OQ-C2 Gateway Security Policy | 网关层报文级策略（ID 过滤/授权/限速） | 需先定义策略模型与配置载体 | `gateway_runtime.py` + 新策略模块 | G2-2 可选 | 中：易与"纯路由"职责混淆 | 中 | 是 |
| OQ-C3 `0x27` 经 Gateway 透传 | 让 runtime 提供每 ECU 独立的安全访问注入 | 需解决"每 ECU 独立注入"与"注入语义只定义一处" | `gateway.py` 工厂 / `gateway_runtime.py` | G4-1 | 中：可能触碰 `gateway.py` 既有边界 | 中 | 是 |
| OQ-C4 DTC Fault Injection Model | 让故障"产生"成为受控的 fault-injection seam，或在既有 UDS 服务语义中定义明确扩展；**具体采用何种协议载体留待 design review 决定** | 需决定是 UDS 服务还是仅进程内 API | `dtc_runtime`（并视 design review 结论决定是否扩展状态/服务） | 无 | 中：容易越界成"新增 UDS 服务" | 中 | 是 |
| OQ-C5 suppressPosRspMsgIndicationBit 统一化 | 把响应抑制提升为统一请求预处理 | 需保证 `0x3E` 既有行为逐字节不变 | `uds_server.py` dispatcher + 多服务 | G2-4 | 低-中 | 低-中 | 是 |
| OQ-C6 Firmware transfer security / ResumeFile + integrity | `0x38` 的 ResumeFile（`modeOfOperation = 0x06`）与传输完整性（摘要/签名） | 需先确定 synthetic 模型上的续传与签名语义 | `file_database` / `transfer_database` / handlers | G5-2、G1-2 | 中：易变成"空壳声明" | 中 | 是 |
| OQ-C7 Stateful UDS Fuzz | 跨请求序列 fuzz（会话/安全/写入链） | 需新维度 + 跨请求状态模型 + 可重放 case schema | `uds_corpus` / `uds_mutators` / `uds_fuzz` / `case_schema` | G6-1 | **高**：会动 OQ3 冻结的 schema/golden | 高 | 是（需先 design review 冻结契约） |
| OQ-C8 DoIP | 以太网诊断传输 | 全新传输栈 + 依赖 | 新 lab 目录 | 无 | 高（范围大） | 高 | 是（大） |
| OQ-C9 真实 Flash 抽象 | 把 `0x34`–`0x38` 接到可插拔后端（如文件/块设备仿真） | 需定义后端接口与"真实"的判定标准 | `transfer_database` / handlers | G5-1 | 中-高 | 中-高 | 是 |
| OQ-C10 `0x28` CommunicationControl | 通信控制服务 | 无 | `uds_server.py` + `EcuState` | 无 | 低 | 低 | 是（小） |
| OQ-C11 `0x19` 其余 reportType 扩展 | 补齐 `03/04/05/06/0B–0E/14…` | 无 | `uds_server.py` + DTC 模型 | 无 | 低（逐条可验证） | 低-中 | 是（可分批） |
| OQ-C12 e2e 进 CI | 让 vcan0 e2e 在 CI runner 自动跑 | **需先验证 runner 是否提供 vcan / `CAN_ISOTP` 所需的内核能力与权限**（能否建立 vcan0、能否绑定 ISO-TP socket 尚未验证）；另需评估运行时长 | `.github/workflows/ci.yml` | G7-1 | 低-中（**环境可行性未验证**） | 低 | 是 |

---

## 15. Evidence Index

| 主题 | 权威证据位置 |
|------|--------------|
| 服务注册表 | `labs/vehicle/uds-basic/ecu/uds_server.py:2130-2145` |
| 状态字段与复位语义 | `uds_server.py:177-320`、`_reset_volatile_state` |
| 会话/安全消费者 | `uds_server.py:381,459,567,657,710,758,763` |
| `0x27` 注入边界 | `ecu.py:352-395`；`gateway.py:63-70` |
| DTC 三层模型 | `dtc_database.py:54-128`、`dtc_runtime.py:89-173` |
| 传输为纯内存模型 | `transfer_database.py:26-27`、`file_database.py:19-27` |
| SquashFS 真解包 | `labs/firmware/synthetic-ecu/analyze.sh:30`、`firmware_diff.sh:16` |
| 无完整性校验 | 对三模块检索 `crc32/sha256/hmac/signature` = 0 匹配 |
| Fuzz 冻结契约 | 见 §8 |
| Gateway 能力 | `ecu/gateway.py`、`ecu/gateway_runtime.py`、`tests/test_gateway_runtime_{unit,vcan}.py` |
| 测试计数 | 各 `labs/vehicle/uds-basic/tests/test_*.py` 的 `def test_` 计数 |
| 门禁分层 | `scripts/qa_fast.sh`、`scripts/qa_extended.sh`、`.github/workflows/ci.yml` |
| 历史 vs 当前状态分界 | `docs/AUTONOMOUS_BUILD_STATE.md:149-153`（自行声明历史段不回改） |

---

## 16. Conclusion

**事实摘要**

1. `main` 上的**生产代码基线**是 OQ7 的 `ae22a556…`；`main HEAD` 因 docs-only 的
   PR #22 而前移到 `77db4126…` —— 两者不同是正确状态。
2. 协议面：**15 个 UDS 服务**可用（14 静态 + `0x27` 入口注入），状态机 27 字段，
   多 ECU 路由与 vcan0 实时网关 runtime 均已合入并有 unit + e2e。
3. 测试面：**907** 用例（unit 673 + e2e 234）；**unit 进 CI，e2e 不进 CI**。
4. **架构面存在明显不对称**：协议实现密度高（服务、状态机、传输、多 ECU），
   而**安全策略框架几乎不存在** —— 无 session framework、无 permission framework、
   无 gateway policy、无服务授权。会话目前实际只影响 `0x2E` 一个服务。
5. **固件面**：所有传输服务都是 synthetic in-memory；无完整性校验、无签名、
   无真实 Flash；仅 SquashFS 有真解包链路。DoIP 完全不存在。
6. **Fuzz 面**：ISO-TP 8 维 + UDS SF/MF + 三态 kernel oracle + replay 齐备，
   但**跨请求序列（stateful）明确 OUT OF SCOPE**，且 fuzz campaign 不在 CI。
7. **文档漂移**：共 **15 处**，集中在 4 个当前状态文档；其中 4 处是**同一文件
   内部自相矛盾**，3 处**低估**了已交付能力（`0x7DF` 网关守护进程、`0x85`）。
   根 `README.md` 滞后最多（4 处，含服务清单与剩余方向）。见 §12（**只记录，不修改**）。

**本盘点未做的事**：未修改任何生产代码 / 既有文档；未选择 OQ8；未排序候选。

**下一步（由你们决定）**：基于 §13 缺口表与 §14 候选集做 **design review**，
再确定下一项第二轮能力。
