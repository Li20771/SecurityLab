# SecurityLab 状态看板（SECURITY LAB STATUS）

> 更新：2026-09-21（工程收口）。权威明细见 `FINAL_ENVIRONMENT_AUDIT_2026-09-20.md`（审计快照）、`ENGINEERING_CLOSE_2026-09-21.md`（本轮收口报告）与 `TEST_MATRIX.md`；断点过程见 `AUTONOMOUS_BUILD_STATE.md`。

## 一句话现状

SecurityLab 第一轮建设及工程收口已经完成。

当前正式 review 基线：

- branch: `chore/engineering-close-20260921`
- HEAD: `03258025b5bba5179a41a4a2af392fec04fae8d2`
- GitHub Actions Fast QA：PASS（run `35575283020`）
- PR #1：OPEN / DRAFT
- main 尚未修改，当前仍为 `e215b1d6d77d0edfcfd76d6fdf9a4df3fb22c517`

当前阶段不是功能建设，而是第一轮正式合入前的最终 review。

第二轮功能建设暂不启动。

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
| Docker | VERIFIED（audit/local capability） | Ubuntu CLI → Docker Desktop E2E 已验证；当前 PR 不重新定义 Docker 当前运行态 |
| 质量门禁 | VERIFIED | `scripts/qa_fast.sh`（离线）/ `qa_extended.sh`（vcan） |
| CI | VERIFIED（Fast runner） | `.github/workflows/ci.yml`；Fast：GitHub Actions run 35575283020 = success；Extended：仅 workflow_dispatch，目前不作为自动 PR 门禁 |
| 统一健康检查 | VERIFIED | `scripts/healthcheck.sh [--extended]` |
| DSH Agent Web GUI | VERIFIED | `systemctl --user status dsh-web`，http://127.0.0.1:8731 |
| binfmt / WSL interop | REPO VERIFIED / SYSTEM DEPLOYMENT NOT REVERIFIED | `configs/binfmt/binfmt-selfheal.sh` v2.3 fail-closed（helper 缺失绝不回落 systemd-binfmt） |
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

1. binfmt 系统侧 v2.3 当前部署是否需要重新确认（`/usr/local/sbin/binfmt-selfheal.sh`
   是否升级到 repo 源真版本；当前 PR 未重新完成 sudo 部署确认）。
2. main merge 授权（PR #1 最终 review 通过后由你决定是否 merge）。
3. `docker_data.vhdx` 压缩回收约 16GB（需你在本机 `wsl --shutdown` + Optimize-VHD；
   该 shutdown 在本自动化环境被宿主策略禁止）。

> 已完成的既往待办（ssh-add、0 字节怪文件删除、本分支 push、PR 创建、
> CI 首次 runner 验证）不再列出。

### binfmt 状态口径

```text
Repository source:
    v2.3 fail-closed（configs/binfmt/binfmt-selfheal.sh，
    fail-closed 回归 11/11 PASS）

Current deployed system:
    当前 PR 未重新完成 sudo 部署确认，
    因此不把系统侧 v2.3 部署写成当前 VERIFIED。
```

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
