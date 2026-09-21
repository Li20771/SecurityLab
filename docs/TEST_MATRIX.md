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
>   当前已验证：SecurityLab CI / Fast QA / run `35576358620` /
>   commit `8bfd405bbbbfd215c7077bc208f0bccc64cfe6ec`，result = success。
>   Extended 尚未在真实 runner 验证（仅 workflow_dispatch），见 §8。

## 1. 分层与入口

| 层级 | 是否需 vcan/root | 统一入口 | 内容 |
|------|------------------|----------|------|
| Lint / Type | 否 | `scripts/qa_fast.sh`（内含） | ruff + mypy |
| Unit（离线纯协议） | 否 | `scripts/qa_fast.sh` / 各 `run_tests.sh unit` | ECU/UDS/keygen/gateway/解析器/fuzz harness |
| Integration / E2E | 需 vcan0（普通用户可开 CAN_RAW） | `scripts/qa_extended.sh` / 各 `run_tests.sh all` | 真实 SocketCAN/ISO-TP 链路、多 ECU、capture、YARA |
| Fuzz | 需 vcan0，耗时长 | `scripts/qa_extended.sh --fuzz`；最小复现 `scripts/repro/verify_fuzz_smoke.sh`；AFL 见 labs/fuzzing | CAN/ISO-TP 变异、AFL 崩溃发现 |
| Repro（可复现验证） | 部分需 vcan | `scripts/repro/verify_{arm,qemu_gdb,ghidra,fuzz_smoke}.sh` | ARM/QEMU-GDB/Ghidra headless/fuzz smoke 最小闭环 |
| Health | 快速门否 / `--extended` 需 vcan | `scripts/healthcheck.sh [--extended]` | 环境/工具/服务/链路总检 |
| CI | GitHub runner | `.github/workflows/ci.yml` | fast 自动、extended 手动；**fast 已由 run 35576358620 在真实 GitHub runner 上验证通过** |

> 测试层级职责（pytest / run_tests.sh / qa_*）见 `docs/ARCHITECTURE.md` §5.1。

## 2. 车联网（uds-basic）测试明细

入口：`labs/vehicle/uds-basic/tests/run_tests.sh [all|unit|e2e]`，自动选用 `~/.venvs/network/bin/python`。
状态来源：**Local verified**（2026-09-21 本机实测）。

| 测试文件 | 类型 | 用例数 | 覆盖 | 状态 |
|----------|------|--------|------|------|
| test_ecu_unit.py | unit | 104 | ECU 状态机、UDS 0x10/11/22/27/2E/3E、NRC、SecurityAccess 锁死/过期 | Local verified |
| test_keygen_unit.py | unit | 34 | seed/key 生成、derive/verify、可导入性（独立进程） | Local verified |
| test_services_extra_unit.py | unit | 17 | 0x2E 门控链、0x3E 正响应/抑制/非法子功能、独立 EcuState | Local verified |
| test_gateway_unit.py | unit | 13 | 多 ECU 拓扑、物理/功能寻址、状态隔离、广播不泄漏、构造护栏 | Local verified |
| test_e2e_vcan.py | e2e(vcan) | 65 | ECU 启动/请求响应/异常/超时/cleanup、SecurityAccess 全流程 | Local verified |
| test_isotp_options_vcan.py | e2e(vcan) | 14（矩阵） | ISO-TP SF/FF/CF/FC、STmin/BS、超时、malformed PCI | Local verified |
| test_write_did_vcan.py | e2e(vcan) | 8 | 真实解锁写 DID→读回、hardReset 恢复、3E 80 总线静默 | Local verified |
| test_multi_ecu_vcan.py | e2e(vcan) | 4（5.2s） | 三 ECU 进程物理寻址、会话隔离、错误地址无应答 | Local verified |

- 汇总：`run_tests.sh all` → **PASS 9 / WARN 0 / FAIL 0**（unit 168 + e2e 91）。
- 已知非致命：`test_isotp_options_vcan.py:186` ResourceWarning（子进程 BufferedReader 未关）。

## 3. 车联网（can-tools）测试明细

入口：`labs/vehicle/can-tools/tests/run_tests.sh [all|offline|malformed]`。
状态来源：**Local verified**（2026-09-21 本机实测）。

| 测试文件 / 活动 | 类型 | 用例数 | 覆盖 | 状态 |
|-----------------|------|--------|------|------|
| test_parsers（离线） | unit | 34 | testcase/summary/metadata 解析、**不误采 summary**、模块纯净度 | Local verified |
| isotp options（离线部分） | unit | 50 | 选项/分类/变异纯函数 | Local verified |
| fuzz harness（离线） | unit | 99 | smoke 选择、PCI/SN 变异、预算与 skip 语义、**kernel oracle 三态** | Local verified |
| malformed 系列 | e2e(vcan) | 47 | 畸形帧/ISO-TP 异常恢复（约 149.6s） | Local verified |
| replay CLI | e2e(vcan) | — | 单/多/--all/--repeat，identity/classification/退出码/确定性 | Local verified |
| capture_lab.sh | e2e(vcan) | — | 6 帧 → CAN log / BLF / PCAP 三格式保存→回放 diff 一致 | Local verified |

- 汇总：offline **PASS 3**（34+50+99）；all（含 malformed）**PASS 4 / FAIL 0**；`CAPTURE_LAB=PASS`。

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
    Fast     = YES（run 35576358620 = success）
    Extended = NO / NOT RUN（未在真实 runner 触发）
```

- Fuzz 不进 CI（耗时/不确定），归手动 extended/专项（`qa_extended.sh --fuzz` /
  `scripts/repro/verify_fuzz_smoke.sh`）。
