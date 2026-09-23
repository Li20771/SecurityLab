# SecurityLab 状态看板（SECURITY LAB STATUS）

> 更新：2026-09-23（PR #13 / OQ5 合并后封存）。权威明细见 `FINAL_ENVIRONMENT_AUDIT_2026-09-20.md`（审计快照）、`ENGINEERING_CLOSE_2026-09-21.md`（第一轮收口报告）与 `TEST_MATRIX.md`；断点过程见 `AUTONOMOUS_BUILD_STATE.md`。

## 一句话现状

SecurityLab 第一轮建设及工程收口已经完成；第二轮已连续交付三项能力
（OQ3 MF-v1、OQ4 ECUReset 扩展、OQ5 DTC severity），
**最新一项 OQ5 已通过 PR #13 合入 main**。

第一轮正式基线（历史）：

- PR #1：**MERGED / CLOSED**
- merge commit：`89a12a0cf43c6a23bf9cb5c6d393d1aa0198f87a`

当前 main 基线：

- PR #13（`feat(uds): add 0x19 DTC severity reports`）：**MERGED / CLOSED**
- merge commit / main HEAD：`3349d79cac30b50593f6c826df8a1147fa9b4d34`
- post-merge GitHub Actions Fast QA：PASS（run `35851051243`，event=push，branch=main）

当前阶段：**Phase 4 — Second-Round / Post-OQ5 Baseline**——OQ5 已合入并封存，
下一项第二轮能力待**重新盘点当前 main 真实状态后确定**（不预设具体功能）；
仍按"小功能 → feature branch → 小 PR → 可验证 → 合入"节奏推进。

## 第二轮已完成能力

### OQ5 — 0x19 DTC severity（最新，PR #13）

| 项 | 值 |
|----|----|
| OQ5 | **MERGED / COMPLETED** |
| PR | #13 **MERGED / CLOSED** |
| merge commit | `3349d79cac30b50593f6c826df8a1147fa9b4d34` |
| feature branch | `feature/uds-dtc-severity-v1` |
| 新增 reportType | `0x19/07` reportNumberOfDTCBySeverityMaskRecord<br>`0x19/08` reportDTCBySeverityMaskRecord<br>`0x19/09` reportSeverityInformationOfDTC |
| severity metadata | 静态 `DTC_METADATA`（`severity` + `functional_unit`，与运行时 status **正交**） |
| severity 位 | `0x80` checkImmediately / `0x40` checkAtNextHalt / `0x20` maintenanceOnly |
| MF | `0x19/08` 在 3 DTC 时为 **20 字节** → 走 kernel ISO-TP MF（e2e 逐字节断言重组后 payload） |
| wire profile | **正响应不回显 reportType（既有契约不变）**；count 型（01/07）不输出 `DTCSeverityAvailabilityMask`；记录顺序 `severity → functionalUnit → DTC → status` |
| 验证 | UDS unit **581** / e2e **208** / all **789**；qa_fast PASS —— 均为 **Local verified** |

### OQ4 — 0x11 ECUReset 扩展（PR #12）

| 项 | 值 |
|----|----|
| OQ4 | **MERGED / COMPLETED** |
| merge commit | `c96f858668d7fa6aa17bcafe4f9d920abe140d32` |
| 内容 | 新增 `0x11 02` keyOffOnReset、`0x11 03` softReset；三者在本实验模型中**状态等价**（experiment-defined，三态互比由测试固化） |
| 边界 | 仅 hardReset / keyOffOnReset / softReset；沿用既有 21 复位 / 5 保留 字段划分 |

### OQ3 — UDS MF-v1（PR #10）

| 项 | 值 |
|----|----|
| OQ3 MF-v1 | **MERGED** |
| PR | #10 **MERGED / CLOSED** |
| merge commit | `abcb8b4d21ac678fb6c86ec607c79e17e3177980` |
| 契约 | SF ≤ 7B；MF 8–128B；>128B admission reject；max 19 frames |
| 变异 | M1–M7（冻结的七个 `mf_*`，全部位于 `uds_len` 维度） |
| 验证 | offline 120 passed / 23594 subtests；OQ3 smoke PASS 10 / FAIL 0 |
| v1 golden | 947 cases；`V1_GOLDEN_DIGEST` + `V1_SEED_IDS_DIGEST` unchanged |
| OUT OF SCOPE | stateful / multi-request sequences、多 ECU、CAN ID、coverage-guided、新 schema / oracle / action |

## 能力看板

| 能力 | 状态 | 怎么用（入口） |
|------|------|----------------|
| SocketCAN / vcan0 | VERIFIED | 开机自启；`ip link show vcan0` |
| can-utils | VERIFIED | `candump/cansend/canplayer/isotpsend ...` |
| ISO-TP | VERIFIED | `run_tests.sh all`（uds-basic） |
| UDS（10/11/14/19/22/27/2E/31/34/35/36/37/38/3E） | VERIFIED | `labs/vehicle/uds-basic`（ecu.py / isotp_client.py）；`0x11` 支持 01/02/03（三态等价）；**`0x19` 支持 01/02/07/08/09/0A**；0x31 / 0x34-0x37（download + upload）/ 0x38 file transfer 均已合入 main 并有 unit + e2e 验证；**边界**：0x14 是 synthetic DTC model 的 status 清除，本 ECU 无 DTC 产生/故障注入机制；`0x19` 其余 reportType（03/04/05/06/0B–0E/14/15/16/1A…）未实现；0x38 为 synthetic in-memory 文件模型（非真实文件系统）；功能组、0x28 / 0x85、0x39 / 0x06 未实现 |
| 多 ECU / Gateway | VERIFIED + PARTIAL | `ecu/gateway.py`；实时 0x7DF 守护进程待建 |
| CAN/ISO-TP fuzz | VERIFIED | `fuzz/isotp_fuzz.py --smoke ...`（见 TEST_MATRIX）；最小复现 `scripts/repro/verify_fuzz_smoke.sh` |
| UDS fuzz v1（请求层 SF） | VERIFIED | `fuzz/uds_fuzz.py --seed 12345 --max-cases N --out-dir …`；单请求 UDS 变异（SF ≤ 7B）；最小复现 `scripts/repro/verify_uds_fuzz_smoke.sh` |
| UDS MF-v1（OQ3，已合入） | VERIFIED / MERGED | 同 UDS fuzz 入口；MF 8–128B（FF + CF）、>128B admission reject、max 19 frames、M1–M7 传输变异；**stateful / multi-request sequences = OUT OF SCOPE** |
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
| CI | VERIFIED（Fast runner，branch + main 双证） | `.github/workflows/ci.yml`；Fast：PR #13 branch push run `35849957710` / pull_request run `35850109044` = success，main post-merge run `35851051243` = success；Extended：仅 workflow_dispatch，目前不作为自动 PR 门禁 |
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
   是否升级到 repo 源真版本；第一轮未重新完成 sudo 部署确认）。
2. `docker_data.vhdx` 压缩回收约 16GB（需你在本机 `wsl --shutdown` + Optimize-VHD；
   该 shutdown 在本自动化环境被宿主策略禁止）。

> 已完成的既往待办（ssh-add、0 字节怪文件删除、本分支 push、PR #1 创建与
> merge、CI runner 验证）不再列出。

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
- 真实 Flash / firmware flashing / 签名校验、0x39 / 0x06、Permission /
  session framework、`0x19` 其余 reportType（03/04/05/06/0B–0E/14/15/16/1A…；
  07/08/09 已随 OQ5 落地）、0x31 的 option/status
  record、0x28 / 0x85、DTC 功能组与 DTC 产生/故障注入
  （0x19/0x31/0x34-0x38 已落地；0x38 为 synthetic in-memory
  文件模型，不含真实文件系统；0x14 只清 runtime status）。
- JFFS2 / UBIFS 完整 extraction chain（当前 build + identify 已通过）。
- ✅ 已收口：Python 依赖 transitive lock（`python-network-requirements.lock`）。
- ✅ 已完成：CI Fast 在真实 GitHub runner 上的运行验证
  （最新 post-merge main run `35851051243` = success）；Extended 仍仅 workflow_dispatch。
- 不启动：Docker Security Lab、Obsidian 系统、新真实 CAN 接口（需明确授权）。
- 可选：jefferson/ubireader、pwndbg/gef、libFuzzer/Honggfuzz、DBC 解析。

## 安全边界（持续生效）

- 所有 CAN/UDS 实验锁定 **vcan0 + 虚拟 ECU**，不连接真实车辆/ECU/适配器，不发送真实诊断/控制/刷写报文。
- 不删除看不懂的文件、不 prune Docker、不删数据卷/vhdx/WSL distro；系统级变更均可逆并留备份。
- 不读取/输出 `~/.dsh/.credentials.yaml` 等凭据；不把密钥/token 写入 Git。
