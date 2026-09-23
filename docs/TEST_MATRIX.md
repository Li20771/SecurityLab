# SecurityLab 测试矩阵（TEST MATRIX）

> 最近一次全量回归：2026-09-21（工程收口分支 `chore/engineering-close-20260921`）。
> 运行约定：凡使用 vcan0 的测试（integration/e2e/fuzz）**必须独占总线、严禁并发**；离线 unit 可随时跑。
>
> **结果来源语义（三者不能混写）**——每张表的"状态"列只属于以下之一：
>
> - **Local verified**：本机 WSL Ubuntu 实测（`run_tests.sh` / `qa_*` / `scripts/repro/*` 实际运行输出）；
> - **Environment audit reported**：来自 2026-09-20/21 审计窗口的既有实测记录
>   （证据在 `tmp/audit/`，未版本化；本轮以 `scripts/repro/` 重新固化为可复现入口）；
> - **GitHub CI verified**：真实 GitHub runner **实际运行通过**。
>   当前已验证（OQ5 DTC severity）：SecurityLab CI / Fast QA / run `35851051243` /
>   commit `3349d79cac30b50593f6c826df8a1147fa9b4d34`（PR #13 merge），result = success；
>   PR #13 分支上 run `35849957710`（push）/ `35850109044`（pull_request）同为 success。
>   Extended 尚未在真实 runner 验证（仅 workflow_dispatch），见 §8。
>   **注意**：Fast CI 覆盖 ruff / mypy / UDS unit / can-tools offline ——
>   **不含需要 vcan0 的 e2e**，故 e2e 数量一律为 Local verified。

## 1. 分层与入口

| 层级 | 是否需 vcan/root | 统一入口 | 内容 |
|------|------------------|----------|------|
| Lint / Type | 否 | `scripts/qa_fast.sh`（内含） | ruff + mypy |
| Unit（离线纯协议） | 否 | `scripts/qa_fast.sh` / 各 `run_tests.sh unit` | ECU/UDS/keygen/gateway/解析器/fuzz harness |
| Integration / E2E | 需 vcan0（普通用户可开 CAN_RAW） | `scripts/qa_extended.sh` / 各 `run_tests.sh all` | 真实 SocketCAN/ISO-TP 链路、多 ECU、capture、YARA |
| Fuzz | 需 vcan0，耗时长 | `scripts/qa_extended.sh --fuzz`；最小复现 `scripts/repro/verify_fuzz_smoke.sh`；AFL 见 labs/fuzzing | CAN/ISO-TP 变异、AFL 崩溃发现 |
| Repro（可复现验证） | 部分需 vcan | `scripts/repro/verify_{arm,qemu_gdb,ghidra,fuzz_smoke}.sh` | ARM/QEMU-GDB/Ghidra headless/fuzz smoke 最小闭环 |
| Health | 快速门否 / `--extended` 需 vcan | `scripts/healthcheck.sh [--extended]` | 环境/工具/服务/链路总检 |
| CI | GitHub runner | `.github/workflows/ci.yml` | fast 自动、extended 手动；**fast 已由 run `35851051243`（main，PR #13 merge）在真实 GitHub runner 上验证通过** |

> 测试层级职责（pytest / run_tests.sh / qa_*）见 `docs/ARCHITECTURE.md` §5.1。

## 2. 车联网（uds-basic）测试明细

入口：`labs/vehicle/uds-basic/tests/run_tests.sh [all|unit|e2e]`，自动选用 `~/.venvs/network/bin/python`。
状态来源：**Local verified**（2026-09-23 本机实测，OQ5 合并后）。

| 测试文件 | 类型 | 用例数 | 覆盖 | 状态 |
|----------|------|--------|------|------|
| test_ecu_unit.py | unit | 116 | ECU 状态机、UDS 0x10/11/19/22/27/2E/3E、NRC、SecurityAccess 锁死/过期、hardReset 保留 DTC；**OQ4**：`11 02`/`11 03` 正响应、`11 04`/`11 05` → 12、resetType 三态等价（26 字段快照互比） | Local verified |
| test_keygen_unit.py | unit | 34 | seed/key 生成、derive/verify、可导入性（独立进程） | Local verified |
| test_services_extra_unit.py | unit | 17 | 0x2E 门控链、0x3E 正响应/抑制/非法子功能、独立 EcuState | Local verified |
| test_gateway_unit.py | unit | 13 | 多 ECU 拓扑、物理/功能寻址、状态隔离、广播不泄漏、构造护栏 | Local verified |
| test_dtc_unit.py | unit | 76 | 0x19/01/02/0A 报文与 mask 过滤、NRC 12/13、DTC 静态库约束、运行时状态与实例隔离；**OQ5**：`DTC_METADATA` 完整性、0x19/07 双 mask 计数（16-bit BE、无 severity availability byte）、0x19/08 六字节记录与**字段顺序** `severity→functionalUnit→DTC→status`、0x19/09 单 DTC、severity 与 0x14/0x11 正交性、wire profile 冻结 | Local verified |
| test_dtc_clear_unit.py | unit | 39 | 0x14 严格 4 字节（短/多余 → 13）、`0xFFFFFF` 全清与单清、**status 置零不删 key**（0x0A 成员保留）、未支持 DTC 与功能组取值 → 31、NRC 不改 state、幂等、24-bit big-endian、空库、hardReset 不恢复 status、实例隔离、静态库不变、不影响 DID/routine/transfer | Local verified |
| test_routine_unit.py | unit | 25 | 0x31 Start/Stop/Results 状态机、NRC 12/13/22/31、hardReset 清 routine 保 DTC、session 保留、实例隔离 | Local verified |
| test_transfer_unit.py | unit | 40 | 0x34/36/37 状态机、NRC 13/22/24/31/73、BSC 回绕与幂等重传、size accounting、hardReset/session 生命周期 | Local verified |
| test_upload_unit.py | unit | 47 | 0x35 上传方向：固定块契约与上一块重建、duplicate 幂等、32130 边界（FF→00 护栏）、方向互斥与 NRC precedence | Local verified |
| test_file_transfer_unit.py | unit | 174 | 0x38 五 mode 逐字节正响应（readDir 恰好 10 字节且不含 dirInfo 本体、readFile 16 字节、37 字节示例 `00 25`）、dirInfo 编码（count/nameLen/size、ASCII 排序、直接子项、隐式目录消失、255 边界）、九步 NRC precedence、readDir/readFile 完整 0x36/0x37（duplicate 逐字节一致、wrong BSC 73、初始 36 00→73）、addFile/replaceFile **仅 0x37 提交**（abort/incomplete/hardReset 不污染）、deleteFile one-shot、8 向互斥矩阵、hardReset 保留 files、三字段增量与禁止项(23→26) | Local verified |
| test_e2e_vcan.py | e2e(vcan) | 69 | ECU 启动/请求响应/异常/超时/cleanup、SecurityAccess 全流程；**OQ4**：`11 02`/`11 03` 正响应与 reset 后重新锁定、udsoncan 高层 API softReset | Local verified |
| test_isotp_options_vcan.py | e2e(vcan) | 14（矩阵） | ISO-TP SF/FF/CF/FC、STmin/BS、超时、malformed PCI | Local verified |
| test_write_did_vcan.py | e2e(vcan) | 8 | 真实解锁写 DID→读回、hardReset 恢复、3E 80 总线静默 | Local verified |
| test_multi_ecu_vcan.py | e2e(vcan) | 4（5.2s） | 三 ECU 进程物理寻址、会话隔离、错误地址无应答 | Local verified |
| test_dtc_vcan.py | e2e(vcan) | 16 | 0x19 正响应/NRC 真实链路、hardReset 前后 DTC 一致；**OQ5**：0x19/07 正响应（5 字节，无 severity availability byte）、0x19/08 **20 字节 MF 响应逐字节断言**（重组后 payload，非帧数）、0x19/09 正响应与字段顺序、未知 DTC → 31 | Local verified |
| test_dtc_clear_vcan.py | e2e(vcan) | 11 | 0x14 真实链路：**独立 ECU 进程**（清除在本进程内不可逆，0x11 不恢复 status）；核心顺序链路 19 0A→14(×2 幂等)→19 01 FF→19 02 FF→19 0A→14 单清已为 0→11 01→19 0A（status 全 0 且不被复位）；NRC 13/31（含字节序颠倒、功能组取值） | Local verified |
| test_routine_vcan.py | e2e(vcan) | 11 | 0x31 状态机/NRC 真实链路、hardReset 清 routine（Start→11 01→Stop=0x22） | Local verified |
| test_transfer_vcan.py | e2e(vcan) | 18 | 0x34/36/37 真实链路（含 126 字节 ISO-TP 多帧 vs UDS BSC 分层、幂等重传、hardReset abort、session mid-transfer）；**OQ4**：`11 02`/`11 03` 同样中止 download | Local verified |
| test_upload_vcan.py | e2e(vcan) | 16 | 0x35 真实链路：初始 00→73、幂等重发、多块（ISO-TP 多帧 ≠ BSC）、hardReset abort、session、34↔35 互斥 | Local verified |
| test_file_transfer_vcan.py | e2e(vcan) | 41 | 0x38 真实链路：**readDir 完整传输**（38 05→78 05…→36 01→76 01 <dirInfo>→…→37→77，非只验响应）、readDir 10 字节且不含 dirInfo 本体、多块 dirInfo（242 字节 / 2 块）与尾块 duplicate、readFile 300 字节三块、addFile/replaceFile 提交点与 abort（复位）不污染、deleteFile one-shot 后 0x34/0x35 可用、NRC、8 向互斥、hardReset 保留 files、session 不终止 | Local verified |

- 汇总：`run_tests.sh all` → **PASS 21 / WARN 0 / FAIL 0**（unit **581** + e2e **208**，共 **789**；2026-09-23 本机实测，**Local verified**）。
- 已知非致命：`test_isotp_options_vcan.py:186` ResourceWarning（子进程 BufferedReader 未关）。

## 3. 车联网（can-tools）测试明细

入口：`labs/vehicle/can-tools/tests/run_tests.sh [all|offline|malformed]`。
状态来源：**Local verified**（2026-09-21 本机实测）。

| 测试文件 / 活动 | 类型 | 用例数 | 覆盖 | 状态 |
|-----------------|------|--------|------|------|
| test_parsers（离线） | unit | 34 | testcase/summary/metadata 解析、**不误采 summary**、模块纯净度 | Local verified |
| isotp options（离线部分） | unit | 50 | 选项/分类/变异纯函数 | Local verified |
| fuzz harness（离线） | unit | 99 | smoke 选择、PCI/SN 变异、预算与 skip 语义、**kernel oracle 三态** | Local verified |
| UDS fuzz（离线，OQ3） | unit | 120（23594 subtests） | UDS 单请求 SF/MF 编码、size-class 边界、M1–M7 传输语义、no-op/dedup、v1 golden 947 | Local verified（2026-09-23） |
| malformed 系列 | e2e(vcan) | 47 | 畸形帧/ISO-TP 异常恢复（约 149.6s） | Local verified |
| replay CLI | e2e(vcan) | — | 单/多/--all/--repeat，identity/classification/退出码/确定性 | Local verified |
| capture_lab.sh | e2e(vcan) | — | 6 帧 → CAN log / BLF / PCAP 三格式保存→回放 diff 一致 | Local verified |

- 汇总（2026-09-21 基线）：offline **PASS 3**（34+50+99）；all（含 malformed）**PASS 4 / FAIL 0**；`CAPTURE_LAB=PASS`。
- OQ3 增量（2026-09-23）：离线组新增 `test_uds_fuzz_offline.py`（120 passed / 23594 subtests），
  离线组计为 **PASS 4**；`malformed` 与 `all` 本轮未重跑，沿用 2026-09-21 结果。

## 4. CAN / ISO-TP Fuzz 阶梯

入口：`labs/vehicle/can-tools/fuzz/isotp_fuzz.py`（须显式 `--smoke`；`--seed 12345` 确定性）。
最小可复现入口：`scripts/repro/verify_fuzz_smoke.sh`（3 case：harness/schema/
分类/oracle 三态/replay 闭环，**Local verified**）。

**Kernel oracle 三态（工程收口新增）**：`result.kernel_oracle` ∈
AVAILABLE / UNAVAILABLE / ERROR。只有 AVAILABLE 时 `kernel_events`
才有意义；oracle 不可用时 `kernel_events`/`kernel_delta_lines` 记为
`None`（不可判定），**绝不伪装成 "kernel_events=0"**。smoke-summary
汇总三态计数。

| 战役 | 规模 | 分类计数 | 逐例健康检查 | 状态 |
|------|------|----------|--------------|------|
| smoke30 | 30 | TE7 / DISCARD22 / NORMAL1 | process_alive/recovery 全 PASS，new_errno=0，kernel oracle AVAILABLE 且 kernel_events=0 | Environment audit reported |
| dim100 | 100 | TE26 / UDS11 / DISCARD59 / NORMAL4 | 同上 | Environment audit reported |
| dim500 | 500 | DISCARD387 / UDS42 / NORMAL28 / TE43 | 同上，结束后 vcan0 仍 UP、无残留 ECU | Environment audit reported |

- 上表数字来自 2026-09-20/21 审计窗口实测（证据脚本 `tmp/audit/verify_campaign.py`，
  未版本化；`scripts/repro/verify_fuzz_smoke.sh` 为本轮的版本化最小复现）；
  **GitHub CI 未实跑 fuzz**（设计上不进 CI）。
- 产物：`labs/vehicle/can-tools/fuzz-out/camp-{smoke30,dim100,dim500}-20260921/`
  （生成物，已 gitignore，**勿提交 Git**）。
- 完整 campaign 仍是实验行为；默认门禁只跑 `scripts/repro/verify_fuzz_smoke.sh`
  与可选的 `qa_extended.sh --fuzz`（smoke 30）。

### 4.1 UDS fuzz（请求层，OQ3 MF-v1 已合入）

入口：`labs/vehicle/can-tools/fuzz/uds_fuzz.py`；
最小可复现：`scripts/repro/verify_uds_fuzz_smoke.sh`（独占 vcan）。
状态来源：**Local verified**（2026-09-23）；PR = **#10**；main merge = `abcb8b4`。

冻结契约（OQ3 MF-v1）：

| 项 | 契约 |
|----|------|
| SF | ≤ 7B（原 v1 路径，逐字节不变） |
| MF | 8–128B（ISO-TP FF + CF） |
| Oversize | > 128B **admission reject**（不生成、不截断） |
| Max frames | 19 |
| MF 变异 | M1–M7（冻结的七个 `mf_*`，全部位于 `uds_len` 维度） |
| Stateful / 多请求序列 | **OUT OF SCOPE** |

验证结果：

| 检查 | 结果 |
|------|------|
| offline | **120 passed / 23594 subtests passed** |
| OQ3 smoke | **PASS 10 / FAIL 0**（exit 0） |
| v1 golden | **947** cases；`V1_GOLDEN_DIGEST` + `V1_SEED_IDS_DIGEST` **unchanged** |
| MF 边界 | `7 → SF`；`8 → MF`；`128 → MF`；`129 → reject`；`200 → reject`；`300 → reject` |
| max frames | 19 |
| M1–M7 | PASS（逐项传输语义核对） |
| no-op | PASS |
| dedup | PASS |
| MF basic | PASS（8B FF+CF 真打 vcan 虚拟 ECU → `UDS_RESPONSE`，recovery PASS） |
| `git diff --check` | PASS |

> 边界 reject 属 **admission reject**：发生在生成期、不产生任何帧，
> 因此不可能被分类成 `ECU_CRASH` / `STATE_CORRUPTION` / `HANG` /
> `TRANSPORT_ERROR`。完整 campaign 仍属实验行为，不进默认 CI。

**CI 口径**：GitHub CI **Fast = SUCCESS**（PR #10 branch push run `35834595473` /
pull_request run `35834682934`；post-merge main run `35835334696`）。
**Extended = manual / skipped** —— 未在 GitHub runner 上实跑，
不得写成 GitHub CI verified。

## 5. 固件 / 通用 Fuzz / YARA / 网络

状态来源：合成固件/网络工具为 **Local verified**；AFL/YARA 为 **Environment audit
reported**（含于 `qa_extended.sh`，本机可重跑）。

| 实验 | 入口 | 断言 | 状态 |
|------|------|------|------|
| 合成 ECU 固件 | `labs/firmware/synthetic-ecu/{build,analyze,firmware_diff,fs_formats}.sh` | 制作/识别 SquashFS（全链）、JFFS2/UBIFS（build+identify）、ELF 架构、diff、strings | Local verified |
| binwalk 提取 | `binwalk -e --run-as=root <img>` | rootfs 提取成功（SquashFS） | Environment audit reported |
| AFL++ 通用 fuzz | `labs/fuzzing/afl-demo/{build.sh,run.sh}` | afl-gcc 30-45s 发现栈溢出（sig6/134），重放一致 | Environment audit reported |
| YARA | `labs/yara-basic/scan.sh` | TP 命中 beacon/ecu、TN clean 不告警、命中合成固件 | Environment audit reported |
| 网络工具 | `scripts/nettools_check.sh` | nmap/tcpdump/tshark/ncat/socat/nc 6 PASS；nft/iptables 需 root | Local verified |

## 6. ARM / QEMU / GDB / Ghidra

状态来源：**Local verified**（2026-09-21，经 `scripts/repro/` 版本化入口实测；
这些入口取代了原 `tmp/audit/` 证据脚本，换机可重跑）。

| 能力 | 命令 / 脚本 | 断言 | 状态 |
|------|-------------|------|------|
| AArch64 编译+执行 | `scripts/repro/verify_arm.sh` | aarch64-gcc 静态编译；透明执行（binfmt，qemu-aarch64 回退）输出 ARM_RUNTIME_OK 42 | Local verified（6/6） |
| armhf 编译+执行 | 同上 | armhf-gcc 编译 + qemu-arm 执行输出 ARM_RUNTIME_OK 42；readelf 架构检查 | Local verified |
| QEMU+GDB 远程 | `scripts/repro/verify_qemu_gdb.sh` | qemu-aarch64 -g ↔ gdb-multiarch target remote；main 断点命中→continue→exited→rc0 | Local verified（4/4） |
| Ghidra headless 分析 | `scripts/repro/verify_ghidra.sh` | AArch64 ELF（脚本自生成）→ RC=0、Architecture=AARCH64、ERROR_COUNT=0、main 反汇编 12 条 | Local verified（8/8） |
| fuzz smoke 最小复现 | `scripts/repro/verify_fuzz_smoke.sh` | 3 case：harness/分类/oracle 三态/replay issue(s)=0 | Local verified（8/8） |

> 历史证据脚本仍在 `tmp/audit/`（gitignore）；凡与仓库未来状态相关的验证
> 均已由 `scripts/repro/` 承担（见 `scripts/repro/README.md`）。

## 7. 环境 / 服务 / 基础设施

| 项 | 命令 | 断言 | 状态 |
|----|------|------|------|
| 快速健康门 | `scripts/healthcheck.sh` | PASS 75 / WARN 2 / FAIL 0（2026-09-21 收口实测） | Local verified |
| 扩展健康门 | `scripts/healthcheck.sh --extended` | PASS 78 / WARN 2 / FAIL 0（2026-09-21 凌晨冷启动，审计窗口） | Environment audit reported |
| Docker | `docker run --rm busybox echo OK` | BUSYBOX_E2E_OK；资产只读不变 | Environment audit reported |
| binfmt interop | selfheal v2.3 + 冷启动实验 F/G | 双端 qemu=32、interop OK、ARM64=42、无振荡；fail-closed 回归 `configs/binfmt/tests/selfheal_failclosed_test.sh` 11/11 | Environment audit reported（冷启动）/ Local verified（fail-closed 回归） |
| DSH Web 服务 | `systemctl --user status dsh-web` | active/enabled、127.0.0.1:8731、冷启动自启、NRestarts=0 | Environment audit reported |
| Kali 系统健康 | `systemctl is-system-running` | running、0 failed | Environment audit reported |
| GitHub HTTPS/SSH | `configs/network/proxy.env` 后 ls-remote / ssh -T | HTTPS rc0、SSH Server accepts key；ssh-add 已完成，本分支 push 均经此链路成功 | Environment audit reported / Local verified（push 实测） |

## 8. CI 映射

### GitHub CI

**Fast job：**

- workflow：SecurityLab CI
- event：push
- branch：`chore/engineering-close-20260921`
- commit：`8bfd405bbbbfd215c7077bc208f0bccc64cfe6ec`
- run：`35576358620`
- result：**success**
- 覆盖：ruff / mypy / UDS unit / can-tools offline
- 历史证据：run `35575283020`（commit `03258025…`）为上一轮 push 的
  Fast QA 结果，同为 success——仅作历史记录，不作当前状态。

**Extended job：**

- 当前 workflow 设计为 workflow_dispatch
- 本 PR 未将 extended 作为自动 push 门禁
- 本地 qa_extended 已通过（Local verified）
- 不把本地 qa_extended 写成 GitHub CI verified

```text
GitHub CI verified:
    Fast     = YES（branch run 35576358620 = success；
              post-merge main run 35582069002 = success）
    Extended = NO / NOT RUN（未在真实 runner 触发）
```

**Post-merge（main）证据（当前）：**

- workflow：SecurityLab CI
- event：push
- branch：main
- commit：`3349d79cac30b50593f6c826df8a1147fa9b4d34`（PR #13 merge commit）
- run：`35851051243`
- result：**success**
- 覆盖：ruff / mypy / UDS unit / can-tools offline（**不含 e2e**）

**Post-merge（main）历史证据（OQ3 基线）：**

- commit：`abcb8b4d21ac678fb6c86ec607c79e17e3177980`（PR #10 merge commit）
- run：`35835334696`，result：**success**——仅作历史记录，不作当前状态。

**分支 CI 证据（PR #13 / OQ5）：**

- commit：`5d3e43ae58ff8f9a83562c55ec6ca7995b141244`
- run：`35849957710`（push）/ `35850109044`（pull_request），result：**success**

**Post-merge（main）历史证据（第一轮基线）：**

- commit：`89a12a0cf43c6a23bf9cb5c6d393d1aa0198f87a`（PR #1 merge commit）
- run：`35582069002`，result：**success**——仅作历史记录，不作当前状态。

- Fuzz 不进 CI（耗时/不确定），归手动 extended/专项（`qa_extended.sh --fuzz` /
  `scripts/repro/verify_fuzz_smoke.sh`）。
