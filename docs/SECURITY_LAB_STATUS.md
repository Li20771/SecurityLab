# SecurityLab 状态看板（SECURITY LAB STATUS）

> 更新：2026-09-21（工程收口）。权威明细见 `FINAL_ENVIRONMENT_AUDIT_2026-09-20.md`（审计快照）、`ENGINEERING_CLOSE_2026-09-21.md`（本轮收口报告）与 `TEST_MATRIX.md`；断点过程见 `AUTONOMOUS_BUILD_STATE.md`。

## 一句话现状

本地车联网安全实验室已建成并通过冷启动回归；2026-09-21 完成第一轮工程收口（分支 `chore/engineering-close-20260921`）：kernel oracle 三态 fail-closed、binfmt selfheal fail-closed（不回落 systemd-binfmt）、healthcheck QEMU fallback 修复、关键验证可复现入口（`scripts/repro/`）、文档与源码对齐。**快速门 75 PASS / 2 WARN / 0 FAIL；qa_fast / qa_extended 均 PASS**。CAN→ISO-TP→UDS→多 ECU→fuzz→replay、ARM/AArch64 编译→QEMU→GDB、Ghidra/radare2 逆向、固件分析、AFL++、YARA、网络工具、Docker、DSH Web GUI 均 VERIFIED。

## 能力看板

| 能力 | 状态 | 怎么用（入口） |
|------|------|----------------|
| SocketCAN / vcan0 | VERIFIED | 开机自启；`ip link show vcan0` |
| can-utils | VERIFIED | `candump/cansend/canplayer/isotpsend ...` |
| ISO-TP | VERIFIED | `run_tests.sh all`（uds-basic） |
| UDS（10/11/22/27/2E/3E） | VERIFIED | `labs/vehicle/uds-basic`（ecu.py / isotp_client.py） |
| 多 ECU / Gateway | VERIFIED + PARTIAL | `ecu/gateway.py`；实时 0x7DF 守护进程待建 |
| CAN/ISO-TP fuzz | VERIFIED | `fuzz/isotp_fuzz.py --smoke ...`（见 TEST_MATRIX）；最小复现 `scripts/repro/verify_fuzz_smoke.sh` |
| Replay | VERIFIED | `fuzz/replay.py <path> [--all] [--repeat N]` |
| Kernel oracle（fuzz） | VERIFIED | 三态 AVAILABLE/UNAVAILABLE/ERROR；不可用 ≠ 0 events（收口新增） |
| CAN capture 三格式 | VERIFIED | `can-tools/capture/capture_lab.sh`（CAN log/BLF/PCAP） |
| ARM/AArch64 工具链 | VERIFIED | aarch64/armhf-linux-gnu-gcc |
| QEMU + GDB 远程调试 | VERIFIED | `qemu-aarch64 -g` + `gdb-multiarch target remote` |
| 逆向（Ghidra/r2/binutils） | VERIFIED | `/opt/ghidra/support/analyzeHeadless`、r2、objdump |
| 动态分析 | VERIFIED | strace / ltrace / qemu -strace / gdb |
| 固件分析 | VERIFIED | `labs/firmware/synthetic-ecu/`、binwalk、squashfs/jffs2/ubifs |
| 通用 fuzz（AFL++） | VERIFIED | `labs/fuzzing/afl-demo/`（用 afl-gcc） |
| YARA / IOC | VERIFIED | `labs/yara-basic/scan.sh` |
| 网络安全工具 | VERIFIED | nmap/tshark/tcpdump/ncat/socat/nft；`scripts/nettools_check.sh` |
| Docker | VERIFIED | Ubuntu CLI → Docker Desktop 29.7.2 |
| 质量门禁 | VERIFIED | `scripts/qa_fast.sh`（离线）/ `qa_extended.sh`（vcan） |
| CI | 已建 / runner 未实测 | `.github/workflows/ci.yml` |
| 统一健康检查 | VERIFIED | `scripts/healthcheck.sh [--extended]` |
| DSH Agent Web GUI | VERIFIED | `systemctl --user status dsh-web`，http://127.0.0.1:8731 |
| binfmt / WSL interop | VERIFIED | selfheal v2.3 双端（fail-closed：helper 缺失绝不回落 systemd-binfmt） |
| Kali 辅助环境 | VERIFIED | 冷启动 0 failed |

状态图例：VERIFIED = 装得上+跑得通+测试对+可重复；PARTIAL = 主体可用、遗留明确；BLOCKED = 受外部条件阻塞。

## 日常快速开始

```bash
cd /mnt/d/SecurityLab
bash scripts/healthcheck.sh            # 秒级体检
bash scripts/qa_fast.sh                # 离线全量门禁（不占 vcan）
# 占 vcan 的扩展门（独占，勿与其它 CAN 测试并行）
bash scripts/healthcheck.sh --extended

# 车联网实验
bash labs/vehicle/uds-basic/tests/run_tests.sh all
bash labs/vehicle/can-tools/tests/run_tests.sh all

# 关键验证可复现（换机即可重跑）
bash scripts/repro/verify_arm.sh
bash scripts/repro/verify_qemu_gdb.sh
bash scripts/repro/verify_ghidra.sh
bash scripts/repro/verify_fuzz_smoke.sh   # 独占 vcan

# DSH Web GUI（开机自启；仅本机回环）
systemctl --user status dsh-web
journalctl --user -u dsh-web -f        # 取首次访问 token
```

## 待你处理（不可代做）

1. WSL 终端执行一次 `ssh-add`（输入私钥 passphrase）→ 解锁私有仓库 SSH（公钥已授权）。
2. 仓库根 0 字节怪文件 `` ; else echo MISS `` 的删除授权（git 未跟踪，不属本分支）。
3. `docker_data.vhdx` 压缩回收约 16GB（需你在本机 `wsl --shutdown` + Optimize-VHD；该 shutdown 在本自动化环境被宿主策略禁止）。
4. 本分支 push / PR 合入 `main` 的授权（工程收口分支 `chore/engineering-close-20260921`，已本地分组 commit，零 push）。

## 明确的 PARTIAL / 路线图（非空壳，第二轮能力建设）

- 0x7DF 功能寻址**实时网关守护进程**（协议层与多 ECU 物理寻址实时均已通过）。
- UDS `0x19 / 0x31 / 0x34 / 0x35 / 0x36 / 0x37`（按 request/response/NRC/state/unit/e2e 六件套逐个补）。
- JFFS2 / UBIFS 完整 extraction chain（当前 build + identify 已通过）。
- ✅ 已收口：Python 依赖 transitive lock（`python-network-requirements.lock`）。
- CI 在真实 GitHub runner 上的运行验证（push 后）。
- 不启动：Docker Security Lab、Obsidian 系统、新真实 CAN 接口（需明确授权）。
- 可选：jefferson/ubireader、pwndbg/gef、libFuzzer/Honggfuzz、DBC 解析。

## 安全边界（持续生效）

- 所有 CAN/UDS 实验锁定 **vcan0 + 虚拟 ECU**，不连接真实车辆/ECU/适配器，不发送真实诊断/控制/刷写报文。
- 不删除看不懂的文件、不 prune Docker、不删数据卷/vhdx/WSL distro；系统级变更均可逆并留备份。
- 不读取/输出 `~/.dsh/.credentials.yaml` 等凭据；不把密钥/token 写入 Git。
