# SecurityLab 测试矩阵（TEST MATRIX）

> 最近一次全量回归：2026-09-21 凌晨（冷启动后）。口径见 `FINAL_ENVIRONMENT_AUDIT_2026-09-20.md`。
> 运行约定：凡使用 vcan0 的测试（integration/e2e/fuzz）**必须独占总线、严禁并发**；离线 unit 可随时跑。

## 1. 分层与入口

| 层级 | 是否需 vcan/root | 统一入口 | 内容 |
|------|------------------|----------|------|
| Lint / Type | 否 | `scripts/qa_fast.sh`（内含） | ruff + mypy |
| Unit（离线纯协议） | 否 | `scripts/qa_fast.sh` / 各 `run_tests.sh unit` | ECU/UDS/keygen/gateway/解析器/fuzz harness |
| Integration / E2E | 需 vcan0（普通用户可开 CAN_RAW） | `scripts/qa_extended.sh` / 各 `run_tests.sh all` | 真实 SocketCAN/ISO-TP 链路、多 ECU、capture、YARA |
| Fuzz | 需 vcan0，耗时长 | `scripts/qa_extended.sh --fuzz`；AFL 见 labs/fuzzing | CAN/ISO-TP 变异、AFL 崩溃发现 |
| Health | 快速门否 / `--extended` 需 vcan | `scripts/healthcheck.sh [--extended]` | 环境/工具/服务/链路总检 |
| CI | GitHub runner | `.github/workflows/ci.yml` | fast 自动、extended 手动 |

## 2. 车联网（uds-basic）测试明细

入口：`labs/vehicle/uds-basic/tests/run_tests.sh [all|unit|e2e]`，自动选用 `~/.venvs/network/bin/python`。

| 测试文件 | 类型 | 用例数 | 覆盖 | 状态 |
|----------|------|--------|------|------|
| test_ecu_unit.py | unit | 104 | ECU 状态机、UDS 0x10/11/22/27/2E/3E、NRC、SecurityAccess 锁死/过期 | VERIFIED |
| test_keygen_unit.py | unit | 34 | seed/key 生成、derive/verify、可导入性（独立进程） | VERIFIED |
| test_services_extra_unit.py | unit | 17 | 0x2E 门控链、0x3E 正响应/抑制/非法子功能、独立 EcuState | VERIFIED |
| test_gateway_unit.py | unit | 13 | 多 ECU 拓扑、物理/功能寻址、状态隔离、广播不泄漏、构造护栏 | VERIFIED |
| test_e2e_vcan.py | e2e(vcan) | — | ECU 启动/请求响应/异常/超时/cleanup、SecurityAccess 全流程 | VERIFIED |
| test_isotp_options_vcan.py | e2e(vcan) | 14（矩阵） | ISO-TP SF/FF/CF/FC、STmin/BS、超时、malformed PCI | VERIFIED |
| test_write_did_vcan.py | e2e(vcan) | 8 | 真实解锁写 DID→读回、hardReset 恢复、3E 80 总线静默 | VERIFIED |
| test_multi_ecu_vcan.py | e2e(vcan) | 4（5.2s） | 三 ECU 进程物理寻址、会话隔离、错误地址无应答 | VERIFIED |

- 汇总：`run_tests.sh all` → **PASS 9 / WARN 0 / FAIL 0**（healthcheck 计 18 个 OK 行）。
- 已知非致命：`test_isotp_options_vcan.py:186` ResourceWarning（子进程 BufferedReader 未关）。

## 3. 车联网（can-tools）测试明细

入口：`labs/vehicle/can-tools/tests/run_tests.sh [all|offline|malformed]`。

| 测试文件 / 活动 | 类型 | 用例数 | 覆盖 | 状态 |
|-----------------|------|--------|------|------|
| test_parsers（离线） | unit | 34 | testcase/summary/metadata 解析、**不误采 summary**、模块纯净度 | VERIFIED |
| isotp options（离线部分） | unit | 50 | 选项/分类/变异纯函数 | VERIFIED |
| fuzz harness（离线） | unit | 89 | smoke 选择、PCI/SN 变异、预算与 skip 语义 | VERIFIED |
| malformed 系列 | e2e(vcan) | 47 | 畸形帧/ISO-TP 异常恢复（约 149.6s） | VERIFIED |
| replay CLI | e2e(vcan) | — | 单/多/--all/--repeat，identity/classification/退出码/确定性 | VERIFIED |
| capture_lab.sh | e2e(vcan) | — | 6 帧 → CAN log / BLF / PCAP 三格式保存→回放 diff 一致 | VERIFIED |

- 汇总：offline **PASS 3**；all（含 malformed）**PASS 5 / FAIL 0**；`CAPTURE_LAB=PASS`。

## 4. CAN / ISO-TP Fuzz 阶梯

入口：`labs/vehicle/can-tools/fuzz/isotp_fuzz.py`（须显式 `--smoke`；`--seed 12345` 确定性）。

| 战役 | 规模 | 分类计数 | 逐例健康检查 | 状态 |
|------|------|----------|--------------|------|
| smoke30 | 30 | TE7 / DISCARD22 / NORMAL1 | process_alive/recovery 全 PASS，new_errno=0，kernel_events=0 | VERIFIED |
| dim100 | 100 | TE26 / UDS11 / DISCARD59 / NORMAL4 | 同上 | VERIFIED |
| dim500 | 500 | DISCARD387 / UDS42 / NORMAL28 / TE43 | 同上，结束后 vcan0 仍 UP、无残留 ECU | VERIFIED |

- 产物：`labs/vehicle/can-tools/fuzz-out/camp-{smoke30,dim100,dim500}-20260921/`（生成物，**勿提交 Git**）；校验脚本 `tmp/audit/verify_campaign.py`。

## 5. 固件 / 通用 Fuzz / YARA / 网络

| 实验 | 入口 | 断言 | 状态 |
|------|------|------|------|
| 合成 ECU 固件 | `labs/firmware/synthetic-ecu/{build,analyze,firmware_diff,fs_formats}.sh` | 制作/识别 SquashFS/JFFS2/UBIFS、ELF 架构、diff、strings | VERIFIED |
| binwalk 提取 | `binwalk -e --run-as=root <img>` | rootfs 提取成功 | VERIFIED |
| AFL++ 通用 fuzz | `labs/fuzzing/afl-demo/{build.sh,run.sh}` | afl-gcc 30-45s 发现栈溢出（sig6/134），重放一致 | VERIFIED |
| YARA | `labs/yara-basic/scan.sh` | TP 命中 beacon/ecu、TN clean 不告警、命中合成固件 | VERIFIED |
| 网络工具 | `scripts/nettools_check.sh` | nmap/tcpdump/tshark/ncat/socat/nc 6 PASS；nft/iptables 需 root | VERIFIED |

## 6. ARM / QEMU / GDB / Ghidra

| 能力 | 命令 / 脚本 | 断言 | 状态 |
|------|-------------|------|------|
| AArch64 编译+透明执行 | aarch64-linux-gnu-gcc -static；直接运行 | exit 42 | VERIFIED |
| armhf 编译+执行 | arm-linux-gnueabihf-gcc + qemu-arm | 正确输出 | VERIFIED |
| QEMU+GDB 远程 | `tmp/audit/arm_gdb_loop/`（qemu -g ↔ gdb target remote） | 双断点命中→continue→exited→rc0 | VERIFIED |
| Ghidra headless 分析 | `tmp/audit/ghidra_analyze.sh` | RC=0、语言 AARCH64:LE:64:v8A、ERROR_COUNT=0 | VERIFIED |
| Ghidra 反汇编 | `tmp/audit/ghidra_disasm.sh` + DumpDisasm.java | main@0x4007b8 反汇编 12 条 | VERIFIED |

## 7. 环境 / 服务 / 基础设施

| 项 | 命令 | 断言 | 状态 |
|----|------|------|------|
| 快速健康门 | `scripts/healthcheck.sh` | PASS 76 / WARN 1 / FAIL 0 | VERIFIED |
| 扩展健康门 | `scripts/healthcheck.sh --extended` | PASS 78 / WARN 2 / FAIL 0（冷启动） | VERIFIED |
| Docker | `docker run --rm busybox echo OK` | BUSYBOX_E2E_OK；资产只读不变 | VERIFIED |
| binfmt interop | selfheal v2.2 + 冷启动实验 F/G | 双端 qemu=32、interop OK、ARM64=42、无振荡 | VERIFIED |
| DSH Web 服务 | `systemctl --user status dsh-web` | active/enabled、127.0.0.1:8731、冷启动自启、NRestarts=0 | VERIFIED |
| Kali 系统健康 | `systemctl is-system-running` | running、0 failed | VERIFIED |
| GitHub HTTPS/SSH | `configs/network/proxy.env` 后 ls-remote / ssh -T | HTTPS rc0、SSH Server accepts key（ssh-add 待用户） | VERIFIED / 1 USER-ACTION |

## 8. CI 映射

- `.github/workflows/ci.yml` **fast**（push/PR）：装 python-network + python-dev requirements → `qa_fast.sh`（无需 vcan，适合云端 runner）。
- **extended**（仅 workflow_dispatch）：`sudo modprobe vcan` + `ip link add/up vcan0` + apt can-utils/tshark/yara → `qa_extended.sh`。
- Fuzz 不进 CI（耗时/不确定），归手动 extended/专项。
- 说明：CI 尚未在 GitHub runner 实跑（无 push 授权）；脚本本体在本地等价环境已全绿。
