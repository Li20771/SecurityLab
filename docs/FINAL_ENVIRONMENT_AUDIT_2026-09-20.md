# SecurityLab 最终环境审计报告（FINAL ENVIRONMENT AUDIT）

- 审计窗口：2026-09-20 22:1x → 2026-09-21 04:2x（Asia/Shanghai，跨午夜）
- 主环境：Windows 11 + WSL2 Ubuntu 24.04（主） + Kali Rolling（辅） + Docker Desktop
- 项目根：`D:\SecurityLab`（WSL：`/mnt/d/SecurityLab`）
- 分支 / HEAD：`refactor/uds-architecture` @ `d9c548c`（2026-09-17）
- 工作树：44 项未提交变更（本任务全程**零 git add/commit/push**，详见第 7、27 节）
- 状态口径：**VERIFIED** = 安装 + 运行 + 测试正确 + 可重复；**PARTIAL** = 主体可用、有明确遗留；**BLOCKED** = 受外部条件/权限/硬件阻塞；**NOT_APPLICABLE** = 本环境不适用。
- 可复跑入口：`scripts/healthcheck.sh [--extended]`、`scripts/qa_fast.sh`、`scripts/qa_extended.sh [--fuzz]`、两套 `tests/run_tests.sh`。

> 本报告只记录经实际命令验证的事实。关键数字均来自本审计窗口内的真实运行输出；历史阶段明细见 `docs/AUTONOMOUS_BUILD_STATE.md` 与 `docs/ENVIRONMENT_INVENTORY_2026-09-17.md`。

---

## 0. 总览（能力状态矩阵）

| # | 能力 | 状态 | 一句话证据 |
|---|------|------|-----------|
| 1 | Windows 宿主 | VERIFIED | Win11 26200 / i7-12700H / 16G；C 剩 61.5G、D 剩 115.7G |
| 2 | WSL2 | VERIFIED | 2.7.14.0，内核 6.18.33.2；Ubuntu/Kali/docker-desktop 三 distro |
| 3 | Ubuntu 24.04 | VERIFIED | systemd running，0 failed；工具链/venv 齐全 |
| 4 | Kali Rolling | VERIFIED | 冷启动 0 failed（修复 tpm-udev/user@1000）；辅助工具齐全 |
| 5 | Docker Desktop | VERIFIED | client=server=29.7.2，busybox E2E OK，数据未动 |
| 6 | 网络 / 代理 | VERIFIED | 动态代理探测脚本；GitHub HTTPS/SSH(经代理) 通 |
| 7 | Git | VERIFIED(只读) | HEAD d9c548c；用户改动已保护；**提交需用户授权** |
| 8 | CAN / SocketCAN | VERIFIED | vcan0 开机自启 UP(MTU2060)，can-utils 全套 |
| 9 | ISO-TP | VERIFIED | can_isotp 模块；ISO-TP 矩阵 14 例 OK |
| 10 | UDS | VERIFIED | 0x10/11/22/27/2E/3E，e2e 全绿 |
| 11 | ECU / 多 ECU / Gateway | VERIFIED + PARTIAL | 单/三 ECU 物理寻址实时通过；0x7DF 实时守护进程留路线图 |
| 12 | CAN / ISO-TP Fuzz | VERIFIED | smoke30 / dim100 / dim500 全量逐例 PASS，0 内核异常 |
| 13 | Replay | VERIFIED | 单/多/--all/重复确定性一致，summary 不误采 |
| 14 | ARM / AArch64 工具链 | VERIFIED | aarch64 + armhf gcc 13.3，静态编译+透明执行 |
| 15 | QEMU | VERIFIED | qemu-user 8.2.2，透明/显式执行 ARM64/armhf |
| 16 | GDB 远程调试 | VERIFIED | qemu -g + gdb-multiarch 双断点→continue→exit rc0 |
| 17 | 逆向（含 Ghidra） | VERIFIED | Ghidra 12.1.3 headless 反汇编 main；radare2/binutils |
| 18 | 固件分析 | VERIFIED | binwalk/SquashFS/JFFS2/UBIFS + 合成 ECU 固件 + diff |
| 19 | 通用 Fuzz（AFL++） | VERIFIED | afl-gcc 30-45s 发现栈溢出崩溃，可复现 |
| 20 | 网络安全工具 | VERIFIED | nmap/tshark/tcpdump/ncat/socat/nft/iptables，6+1 PASS |
| 21 | YARA / IOC | VERIFIED | 4.5.0，TP/TN 三断言通过并扫中合成固件 |
| 22 | 质量门禁 / CI | VERIFIED（CI 未在 runner 实测） | qa_fast/qa_extended PASS；GitHub Actions 已编写 |
| 23 | 统一 Healthcheck | VERIFIED | 快速 76 PASS/1 WARN/0 FAIL；扩展 78/2/0（冷启动实测） |
| 24 | Agent（DSH Web GUI） | VERIFIED | systemd user service + linger 开机自启，生命周期可控 |
| 25 | 存储 / 资源 / PATH | VERIFIED（只读审计） | 资源充足不调整；docker vhdx 压缩待授权 |
| — | binfmt / WSL interop | VERIFIED | WSL_INTEROP_STATUS=VERIFIED（双端 selfheal v2.2） |

**最终回归（冷启动后实测）**：`healthcheck.sh --extended` → **PASS 78 / WARN 2 / FAIL 0**；DSH 检测项优化后快速门 **PASS 76 / WARN 1 / FAIL 0**；`qa_fast.sh` → **QA FAST RESULT: PASS**；`qa_extended.sh` → **QA EXTENDED RESULT: PASS**。

---

## 1. Windows 宿主 — VERIFIED

| 项 | 值 |
|----|----|
| 版本 | Windows 11 专业版 26200 x64 |
| CPU | 12th Gen Intel Core i7-12700H（14 核 / 20 线程） |
| 内存 | 16 GB |
| C: | 200.4 GB 总，剩 61.5 GB（70% 已用） |
| D: | 274.3 GB 总，剩 115.7 GB（58% 已用） |
| Docker Desktop | 4.90.0（Engine/Client/Server 29.7.2，containerd 2.3.3，runc 1.4.3） |

- WSL 磁盘镜像（vhdx）物理占用：`D:\WSL\Ubuntu\ext4.vhdx` 11.68 GB、`D:\WSL\Kali\ext4.vhdx` 16.2 GB、`D:\Docker\wsl\DockerDesktopWSL\disk\docker_data.vhdx` 18.24 GB、`D:\WSL\swap.vhdx` 0.04 GB。
- 未对 Windows 做任何破坏性更改；Docker 设置仅开启默认 WSL 发行版集成（原件备份 `settings-store.json.bak-20260920`）。

## 2. WSL2 — VERIFIED

- WSL 版本 2.7.14.0；内核 `6.18.33.2-microsoft-standard-WSL2`。
- distro：`Ubuntu`（默认，24.04.5）、`kali-linux`（Rolling）、`docker-desktop`。
- `C:\Users\20771\.wslconfig`（实测，与背景一致，**资源充足不调整**）：
  ```ini
  [wsl2]
  memory=6GB
  processors=8
  swap=2GB
  swapFile=D:\\WSL\\swap.vhdx
  networkingMode=nat
  ```
  运行态：total 5.8 GiB / available 4.9 GiB / used 874 MiB；swap 2 GiB 全空；`nproc=8`。
- Ubuntu `/etc/wsl.conf`：`[boot] systemd=true`、`[user] default=lilike`。
- **binfmt / WSL interop 专项 = VERIFIED**（本任务重点排障，结论纠正了早期"Kali 一定会破坏 Ubuntu binfmt"的猜测）：
  - 根因：各 distro 在各自 mount namespace 挂载**同一个内核全局 binfmt_misc 注册表**（dev 0:35 / inode 1 相同）；Kali 的 `qemu-user-binfmt` 含共享表缺失的 `qemu-or1k`，旧 selfheal 每 10s 调 `systemd-binfmt`（先 flush 全表、删 WSLInterop）→ 跨 distro 振荡。
  - 修复：新增 `configs/binfmt/binfmt-register-additive.py`（additive 单条注册、绝不 flush、含 raw-NUL 护栏）；selfheal 升级 **v2.2**（path + timer，双端部署 enabled/active）；Kali 建 `/usr/libexec/qemu-binfmt` 31 个兼容 symlink。
  - 验证：真实 qemu-alpha 删/补 magic/mask/interp/flags 4 项精确一致且不误匹配；热共享 90s WSLInterop 零缺失；冷启动实验 F/G 双端一次性收敛（仅余 WSL 固有 ~2s 重挂载全空窗，不可消除）；最终双端 qemu=32、interop=YES、ARM64 透明执行=42。
  - 结论：`WSL_INTEROP_STATUS(UBUNTU)=VERIFIED`、`KALI_INTEROP=VERIFIED`、`KALI_ARM64_TRANSPARENT_EXEC=VERIFIED`。

## 3. Ubuntu 24.04（主环境） — VERIFIED

- `systemctl is-system-running = running`，**0 failed units**；用户 `lilike`(uid 1000) 属 `sudo/docker/wireshark/netdev` 组；sudo 需密码（系统级操作经 `wsl -u root` 完成，均在用户任务书授权清单内）。
- 核心工具链（实测版本）：gcc/g++ 13.3.0、clang-18 18.1.3、make 4.3、cmake 3.28.3、ninja 1.11.1、pkg-config 1.8.1、git 2.43.0、jq 1.7、tmux 3.4。
- 交叉/仿真/调试：aarch64-linux-gnu-gcc 13.3.0、arm-linux-gnueabihf-gcc 13.3.0（含 libc6-dev-armhf-cross）、qemu-user 8.2.2、gdb/gdb-multiarch 15.1。
- 逆向/动态：radare2/rabin2 5.5.0、patchelf 0.18.0、file 5.45、strace 6.8、ltrace 0.7.3、binutils 2.42。
- 固件/Fuzz/网络：squashfs-tools 4.6.1、mtd-utils 2.2（mkfs.jffs2/mkfs.ubifs/ubinize）、binwalk（Py3 重写版，自动提取需 root + `--run-as=root`）、afl++ 4.09c、yara 4.5.0、nmap/ncat 7.94、tshark 4.2.2、tcpdump 4.99.4、socat、nftables、iptables。
- Java/Ghidra：openjdk-21.0.12（系统默认，另装 17.0.20）、Ghidra 12.1.3（`/opt/ghidra`）。
- Python venv `~/.venvs/network`（Python 3.12.3，**未污染系统 Python**）：python-can 4.6.1、can-isotp 2.0.7、udsoncan 1.26.1、cantools 44.0.0、scapy 2.7.0、capstone 6.0.0a10、pwntools 4.15.0、unicorn 2.1.2、cryptography 50.0.1，以及本任务新增 pytest 9.1.1 / ruff 0.16.8 / mypy 2.3.1。
- 依赖锁定：`configs/environment/python-network-requirements.txt`（运行时 8 包钉版本）、`configs/environment/python-dev-requirements.txt`（QA 工具钉版本）。
- apt 源已切清华 TUNA（Ubuntu `ubuntu.sources`、Kali `sources.list`，均留存 `.bak`）；pip 走清华镜像直连成功。

## 4. Kali Rolling（辅助） — VERIFIED

- systemd 260、Python 3.14.7；冷启动 **0 failed units**。
- 修复（均可逆）：`systemctl mask tpm-udev.path`（WSL 无 TPM，反复撞 start-limit）、`loginctl enable-linger lilike`（消除 WSL init 与 systemd 竞争 spawn user@1000）。两次冷启动复验 is-system-running=running、user@1000 active。
- 工具：gdb/gdb-multiarch 17.2、qemu-user 11.1（含 binfmt）、clang 21、afl++ 5.02、aarch64/armhf-gcc 16.1、yara 4.5.8、radare2、strace/ltrace、tshark、socat、ncat 7.99、hydra、nikto、patchelf、ninja、python3.14-venv/dev。
- 未粗暴 mask 业务服务；accounts-daemon 等在 WSL 无图形会话下不构成失败单元。

## 5. Docker Desktop — VERIFIED

- WSL 默认发行版集成开启；Ubuntu `/usr/bin/docker` → `/mnt/wsl/docker-desktop/cli-tools/usr/bin/docker`，**client=server=29.7.2**。
- 冷启动后实测：`docker run --rm busybox sh -c 'echo BUSYBOX_E2E_OK'` → `BUSYBOX_E2E_OK`。
- 资产只读枚举（**未 prune、未删任何 volume/镜像/数据**）：1 镜像（busybox:latest 6.77MB）、0 容器、0 named volume、3 默认网络。
- Dify 源码/compose 位于 `C:\Users\20771\Desktop\dify\dify-main`（bind 部署，volumes 仅 382KB，无关键数据），未改动；`D:\github-project\{AutoSec, Vehicle Subsystem...}` 未擅动。
- **免提权恢复 SOP（已验证）**：当 Ubuntu 先于 Docker Desktop 启动导致 cli-tools 空挂载时，执行 `wsl --terminate docker-desktop`（只停引擎 distro，不碰数据盘），backend 约 24s（本窗口实测约 2 分钟健康周期内）自动重启引擎，再进入 Ubuntu 约 12s 挂载恢复；**切勿强杀 Docker Desktop / com.docker.backend**。

## 6. 网络 / 代理 — VERIFIED

- 根因：GitHub 直连首页可达（HTTP 200），但 smart-HTTP（info/refs）与 SSH 22/443 端点被干扰（TCP 可连、握手黑洞）。
- Clash Verge 混合代理在 WSL NAT 网关 `:7890`（HTTP+SOCKS）。**未硬编码 IP**：`configs/network/proxy.env` 运行时由 `ip route` 取网关并依次探测 7890/7897/10809/10808 活性后才导出 `http_proxy/https_proxy/all_proxy`（含大小写与 no_proxy）。
- `configs/network/ssh-proxy.sh`：纯标准库 Python 的 HTTP CONNECT ProxyCommand（select 双向转发）；`~/.ssh/config` 增 SecurityLab 块（github.com → ssh.github.com:443 经代理）。
- 验证：HTTPS 公开仓库 `git ls-remote` rc=0；SSH 经代理完成 banner/KEX，**GitHub "Server accepts key"（公钥已授权）**；Ghidra 的 GitHub API 调用与 569MB 下载、pip 安装均经代理/镜像成功。
- DNS 正常；systemd-resolved 监听 127.0.0.53/54 与 10.255.255.254:53。

## 7. Git — VERIFIED（只读保护；写操作待授权）

- 仓库：`refactor/uds-architecture` @ `d9c548c`（2026-09-17），remote 为 SSH 私有仓库 `git@github.com:Li20771/SecurityLab.git`。
- 开工前已归档用户/Trae 未提交资产：完整 diff → `tmp/audit/git-diff-20260920.patch`、status → `tmp/audit/git-status-20260920.txt`。
- 两个用户资产自始至终**未覆盖、未丢弃、未提交**：`labs/vehicle/can-tools/fuzz/replay.py`、`labs/vehicle/can-tools/tests/test_fuzz_offline.py`（replay --all 修复）。
- 本任务全程 **零 `git add` / `commit` / `push` / `reset` / `clean`**。当前工作树 44 项（新增/修改清单见第 27 节与 `AUTONOMOUS_BUILD_STATE.md`）。
- 仓库根存在 0 字节异常文件名 `` ; else echo MISS ``（2026-09-18 错误重定向产生），**未删除**，待用户处置（见第 27 节）。

## 8. CAN / SocketCAN — VERIFIED

- `vcan0.service`（systemd system unit，`configs/systemd/`）enabled + active，冷启动后 `vcan0` 自动 UP：`<NOARP,UP,LOWER_UP> mtu 2060`；内核模块 `can / can_raw / vcan / can_isotp` 已加载。
- can-utils 全套在位：cansend/candump/canplayer/cangen/isotpsend/isotprecv/isotpn（isotp）等。
- 真实闭环：capture/send/receive、ECU 请求/响应、fuzz/replay 均在 vcan0 实测（后续各节）。所有 CAN 操作**仅针对 vcan0**，未触碰真实接口/车辆/ECU（遵守 AGENTS.md 安全边界）。

## 9. ISO-TP — VERIFIED

- 内核 `can-isotp` 模块加载；Python `can-isotp` 2.0.7。
- ISO-TP 选项/时序矩阵测试 14 例 OK（单帧/首帧/连续帧/流控制、STmin/BS、超时、异常 PCI 等）；malformed 47 例 OK。
- 已知非缺陷：ISO-TP 时序用例在 vcan 上**多 ECU/多进程并发**时会出现 EILSEQ/STmin 失效——总线必须独占运行（`qa_extended.sh` 已串行化）。

## 10. UDS — VERIFIED

- 已实现并测试的服务：`0x10` SessionControl、`0x11` ECUReset、`0x22` ReadDataByIdentifier、`0x27` SecurityAccess（seed/key）、`0x2E` WriteDataByIdentifier、`0x3E` TesterPresent。
- 寻址：Tester→ECU TX=0x7E0 / ECU→Tester RX=0x7E8，ISO-TP Normal_11bits，RECV_TIMEOUT=1.5s。
- 会话：DEFAULT=01 / PROGRAMMING=02 / EXTENDED=03；安全等级 LOCKED=00 / LEVEL1=01。
- NRC 覆盖：0x10/0x11/0x12/0x13/0x22/0x24/0x31/0x33/0x35/0x36/0x37，以及 `0x7F = serviceNotSupportedInActiveSession`（0x2E 默认会话门控）。
- SecurityAccess：KEY_LENGTH=4，max_attempts=3、lockout 10s（`UDS_LOCKOUT_SECONDS` 可覆盖，e2e 设 2s）、seed 有效期 5s；`keygen.generate_seed/derive_key/verify_key`。
- 0x2E 门控链：长度<4→13；未知 DID→31；默认会话→7F；未解锁→33；只读 DID（如 F190）→22；长度越界→13；成功写 `state.did_overrides[did]` 返回 6E+DID。可写 DID 0xF102（长度 1..16）。0x11 hardReset 清空覆盖恢复出厂。
- 0x3E：`3E 00→7E 00`；`3E 80→` 空响应（ecu.py 主循环 continue 抑制，不发空帧）；非法子功能→12；长度错→13。
- 测试：离线 `test_ecu_unit.py` 104 例、`test_keygen_unit.py` 34 例、`test_services_extra_unit.py` 17 例；vcan e2e（含真实 SecurityAccess 解锁写入读回、hardReset 恢复、3E 80 总线静默）全绿。

## 11. ECU / 多 ECU / Gateway — VERIFIED + PARTIAL

- 单 ECU test double：完整生命周期（启动→请求/响应→异常请求→超时→cleanup），多次运行稳定。
- **多 ECU / Gateway 协议层（VERIFIED）**：新增 `ecu/gateway.py`
  - `VirtualEcu`（name/request_id/response_id/独立 EcuState/可选 handler）、`DiagnosticGateway.route(can_id,payload)`、`build_default_gateway()`。
  - 物理寻址单播：ECU_A 0x7E0/0x7E8、ECU_B 0x7E1/0x7E9、ECU_C 0x7E2/0x7EA；功能寻址 `FUNCTIONAL_ADDRESS=0x7DF` 广播给所有 ECU 改状态但**返回 []**（避免总线仲裁冲突、不泄漏正响应）；未知 ID 返回 []；空响应不发帧；空网关/重复 request_id 抛 ValueError。
  - 离线 `test_gateway_unit.py` **13 例 OK**（拓扑、响应标识符、未知 ID 无路由、3E 正响应/抑制、否定路由、**只写 A 不影响 B/C 的状态隔离**、0x7DF 广播三 ECU 同切 extended 且不泄漏响应、空/重复构造拒绝、自定义 handler）。
  - vcan 实时 `test_multi_ecu_vcan.py` **4 例 OK（5.2s）**：3 个独立 ecu.py 进程（`UDS_INTERFACE/UDS_RX_ID/UDS_TX_ID` 绑不同 ID）各自应答 22 F1 90 与 3E；只把 A 切 extended 后 A 写 DID=7F2E33 而 B/C 仍=7F2E7F（会话隔离）；0x7E5/0x7ED 无应答超时。
- **PARTIAL（明确路线图，不造空壳）**：0x7DF 功能寻址的**实时网关守护进程**尚未实现（协议层路由 + 多 ECU 物理寻址实时均已 VERIFIED）；UDS `0x19/0x31/0x34/0x35/0x36/0x37` 未实现（每个服务须 request/response/NRC/state/tests/integration 齐备才做）。

## 12. CAN / ISO-TP Fuzz — VERIFIED

- 入口 `isotp_fuzz.py`：必须显式 `--smoke`（不带返回 2），再以 `--dimensions/--categories/--seeds` 走 generate_cases（`--limit-per-seed` 默认 1、`--max-cases` 截断、`--seed 12345` 确定性）。
- 阶梯实测（产物 `labs/vehicle/can-tools/fuzz-out/camp-*-20260921`，已 gitignore）：
  - smoke30：TE 7 / DISCARD 22 / NORMAL 1（约 77s）
  - dim100：TE 26 / UDS 11 / DISCARD 59 / NORMAL 4（约 277s）
  - dim500：DISCARD 387 / UDS 42 / NORMAL 28 / TE 43（约 23min）
- 逐例验证：process_alive=True 全量、recovery=PASS 全量、**new_errno=0、kernel_events=0**、无残留 ecu.py、结束后 vcan0 仍 UP、ECU cleanup 正常。
- replay 身份/分类确定性一致（见第 13 节）。未制造"漂亮结果"，异常分类如实记录。

## 13. Replay — VERIFIED

- `replay.py <path> [--all] [--repeat N] [--show-frames] [--no-artifacts]`。
- 修复并验证：目录中同时存在 testcase JSON、summary JSON、metadata 时，`--all` **不误采 summary/metadata**（显式 `[ignore]`）。
- 实测：单 testcase identity OK、分类与原始一致；多 testcase；`--all` 重放 30 例；`--repeat 3` 无分歧；两次 `--all` summary 完全相同（7/22/1）；退出码正确。
- 工作区中的用户修复版 `replay.py` / `test_fuzz_offline.py` 全程保留未覆盖。

## 14. ARM / AArch64 工具链 — VERIFIED

- aarch64 与 armhf（hard-float）交叉 gcc 13.3.0，静态 `-g` 编译通过；透明执行（binfmt）与显式 `qemu-aarch64`/`qemu-arm` 均返回预期结果（answer=42）。
- 编译 → 执行 → 静态检视（file/readelf/objdump/rabin2）闭环通过。

## 15. QEMU — VERIFIED

- qemu-user 8.2.2；`qemu-aarch64`、`qemu-arm` 可显式运行 AArch64/armhf 静态程序；binfmt 透明执行双端（Ubuntu/Kali）VERIFIED。
- `qemu-<arch> -strace` 可输出 syscall 轨迹，纳入动态分析工作流。

## 16. GDB 远程调试 — VERIFIED

- gdb-multiarch 15.1。真实闭环：`qemu-aarch64 -g <port> ./hello_arm64` ↔ `gdb-multiarch` `target remote :<port>`，**双断点命中 → continue → 程序 exited normally → rc=0**。
- 形成"编译 → QEMU 挂起 → GDB 远程断点/单步/继续 → 退出"完整链路（证据脚本 `tmp/audit/arm_gdb_loop/`）。

## 17. 逆向工程（含 Ghidra headless） — VERIFIED

- 传统链：radare2/rabin2 5.5、objdump/readelf/nm/strings/file、patchelf 0.18、gdb（AArch64/ARM/x86 ELF 均可基本逆向）。
- **Ghidra 12.1.3 headless（本任务新装并 VERIFIED）**：
  - 安装：openjdk-21（Ghidra 12 要求）+ Ghidra 12.1.3 解压 `/opt/ghidra_12.1.3_PUBLIC`，软链 `/opt/ghidra`；安装包 sha256 `93a5d11a…1fd54`（569,445,154 字节，与 GitHub API 一致），下完已删临时 zip。
  - 验证 1（导入 + 自动分析）：RC=0、**Using Language/Compiler: AARCH64:LE:64:v8A:default**、ERROR_COUNT=0（`R_AARCH64_IRELATIVE` 为静态二进制的 INFO 级提示，非错误）。
  - 验证 2（postScript `tmp/audit/DumpDisasm.java` 反汇编）：PROCESS_RC=0，`main @ 0x4007b8`，反汇编 12 条 AArch64 指令（`stp x29,x30,[sp,#-0x20]!` 序言 / `bl` / `adrp+add` 取串 / `ldp x29,x30` / `ret`）。
- 静态（Ghidra/r2/binutils）+ 动态（QEMU+GDB、strace/ltrace）双侧闭环。pwndbg/gef 未安装（非必需，避免改动用户 GDB 环境，列入可选后续）。

## 18. 固件分析 — VERIFIED

- 能力：binwalk 签名扫描与 rootfs 提取（`-e --run-as=root`）、SquashFS（unsquashfs/mksquashfs 4.6.1）、JFFS2（mkfs.jffs2）、UBIFS/UBI（mkfs.ubifs/ubinize，mtd-utils 2.2）+ binwalk 识别，均 PASS。
- 新增可复现实验：`labs/firmware/synthetic-ecu/`（合成 ECU 固件 v1/v2 + build/analyze/firmware_diff/fs_formats 脚本 + README），覆盖文件系统识别、ELF 架构识别、strings/metadata、权限、启动脚本、网络配置、凭据搜寻线索、web 服务识别、**firmware diff**；`labs/firmware/samples/`（样本管理规范 + MANIFEST 模板 + images gitignore）。
- 未伪造真实车载固件；无可安全获取的公开样本时以合成固件承载流程。`sasquatch/jefferson/ubireader` 未装（可选；WSL 无 MTD 设备，见第 26 节）。

## 19. 通用 Fuzz（AFL++） — VERIFIED

- AFL++ 4.09c；新增 `labs/fuzzing/afl-demo/`（`vuln.c` 栈溢出靶 + `build.sh`/`run.sh`/README）。
- 实测：`afl-gcc` 插桩后 30-45s 发现 stack-buffer-overflow（SIGSEGV / exit 134），崩溃样本重放信号一致（crash discovery + corpus + repro 三要素齐备）。
- 已知环境约束：`afl-clang-fast` 的 LLVM pass 按 llvm-17 构建、与 clang-18 ABI 不兼容；**可靠路径为 afl-gcc**（已另装 libclang-rt-18-dev 备用；若需 clang 模式，装 clang-17 + libclang-rt-17-dev 并 `AFL_CC=clang-17`）。libFuzzer/Honggfuzz 未单独部署（AFL++ 已满足通用 fuzz 框架要求）。

## 20. 网络安全工具 — VERIFIED

- `scripts/nettools_check.sh`：nmap `-sT`、tcpdump、tshark、ncat 单向交付、socat relay、nc 共 6 PASS；nft/iptables 在位（实际建规则需 root，无 root 时记 WARN）。
- mitmproxy/proxychains 未无意义堆砌（代理链路已由 `configs/network/` 承担），如后续有明确用途再装。

## 21. YARA / IOC — VERIFIED

- YARA 4.5.0；新增 `labs/yara-basic/`（`rules/demo.yar` + beacon/ecu/clean 样本 + `scan.sh`）。
- 实测：TRUE_POSITIVE 命中 beacon 与 ecu、TRUE_NEGATIVE clean 不告警，三断言 PASS；并对合成固件 ELF 扫描命中。已纳入 `qa_extended.sh` 与 healthcheck `--extended`。

## 22. 代码质量门禁 / CI — VERIFIED（CI 配置未在 GitHub runner 实测）

- 工具入 venv（未动系统 Python）：pytest 9.1.1 / ruff 0.16.8 / mypy 2.3.1（钉版本 `python-dev-requirements.txt`）。
- 新增根 `pyproject.toml`：ruff（line-length 100，select E/F/I/UP/B，ignore E501，exclude tmp/artifacts/data/pcaps/build/fuzz-out；tests 忽略 B905/E741）、mypy（py312，ignore_missing_imports，check_untyped_defs）、pytest（注册 `vcan` marker、testpaths、addopts -ra）。
- 新增根 `conftest.py`：按文件名含 `_vcan` 程序化打 `pytest.mark.vcan`（零改动既有大测试文件）；离线收集 341 例 / 取消选择 138 例 vcan。
- 代码整改：ruff 从 41 项修到 **All checks passed**（安全 --fix 处理 import 排序/未用 import/现代化；手修 B904 异常链、B007、死变量等）；mypy 从 5 项修到 **0 issues**（Optional 收窄、bytes|None 注解、errno int|None 等）。
- 门禁脚本：
  - `scripts/qa_fast.sh`（无需 vcan/root，独立进程；ruff + mypy + uds unit + can-tools offline）→ 本窗口最终实测 **QA FAST RESULT: PASS**（uds unit 168 例 + can-tools offline 173 例）。
  - `scripts/qa_extended.sh [--fuzz]`（自动确保 vcan0，**串行独占**：uds all → can-tools all → capture 三格式 → yara；fuzz 可选）→ **QA EXTENDED RESULT: PASS**。
- CI：新增 `.github/workflows/ci.yml`，fast job（push/PR/workflow_dispatch：装两类 requirements 跑 qa_fast）+ extended job（needs fast，仅 workflow_dispatch：runner 上 modprobe vcan + apt can-utils/tshark/yara 后跑 qa_extended）；**fuzz 明确不进普通 CI**。
- 已知非缺陷：单会话 `pytest -m 'not vcan'` 下两个"模块导入纯净度"用例（断言 sys.modules 不含 isotp/can）会因跨模块收集污染而失败——它们天然要求独立解释器进程；权威离线入口是 qa_fast.sh / run_tests.sh（每文件独立进程）。
- **CI 状态说明**：本环境禁止未经授权 push，workflow 尚未在 GitHub runner 上实际触发，标 VERIFIED（脚本本体本地全绿）/ CI 运行链路未实测。

## 23. 统一 Healthcheck — VERIFIED

- `scripts/healthcheck.sh [--extended]`，覆盖：WSL/systemd、binfmt/WSLInterop、ARM64 编译→透明执行、Git HEAD/工作树、DSH 服务、Python venv 包版本、CAN/vcan0、交叉工具链、逆向/动态/固件/fuzz/网络工具、Docker、离线单元测试；`--extended` 追加 uds all + capture 三格式 + YARA。
- 输出 PASS/WARN/FAIL 与详细原因，有 FAIL 退出 1、WARN 不致失败。
- 冷启动后实测：扩展 **PASS 78 / WARN 2 / FAIL 0**；随后将 DSH 检测从"PATH 是否有 dsh"升级为"systemd user service 是否 active"，快速门 **PASS 76 / WARN 1 / FAIL 0**（唯一 WARN = 工作树未提交，符合实验期预期）。
- 重要操作约束：`--extended` 与 qa_extended 及一切 vcan e2e **独占总线、严禁并发**（并发会导致 ISO-TP EILSEQ/SecurityAccess NRC 错乱，非代码回归）。

## 24. Agent（DSH Web GUI） — VERIFIED

- DSH = `@deepseek-ai/dsh@0.1.5-rc.2`（DeepSeek Harness），wrapper `~/.local/bin/dsh`（npx 缓存），node 24.21.0 在 /usr/bin；Web 子命令 `dsh web`（--host/--port/--no-open）。原状态：未运行、无端口、无 unit、linger=no。
- 服务化（用户级、可逆、不动 /etc）：新增可复现单元 `configs/systemd/dsh-web.service`，部署到 `~/.config/systemd/user/` 并 enable：
  `ExecStart=dsh web --no-open --host 127.0.0.1 --port 8731`，Restart=on-failure，WantedBy=default.target；`loginctl enable-linger lilike`（Linger=yes）。
- 验证：
  - start 后约 4-6s 监听 `127.0.0.1:8731`，is-active=active / is-enabled=enabled；带一次性 token 的 UI 返回 HTTP 303（token fence 接受并跳转；token 未写入任何交付物）。
  - 生命周期：stop → inactive 且端口释放；start → 4s 恢复（LIFECYCLE_OK=1）。
  - **开机自启实测**：`wsl --terminate Ubuntu` 会重启 Ubuntu 的 distro systemd/PID1 与 user manager（共享 VM 内核 uptime 不变，但用户空间服务全部重新走启动流程）；每次重启后 dsh-web 均由 enabled+linger 自动 Started，且在**无 lilike 交互登录会话**（root 冷进入、list-sessions 仅 root）时 lilike 的 dsh 进程已在运行。停止 terminate 拉锯静置后，pid 连续 4 轮（8/16/24/32s）不变、端口持续 LISTENING、**NRestarts=0**（非崩溃循环）。
- 管控：`systemctl --user {status,stop,start,restart} dsh-web`、`journalctl --user -u dsh-web -f`；仅绑回环、不对外，Windows 宿主经 WSL localhost 转发访问。
- 全程**未读取** `~/.dsh/.credentials.yaml`。
- 未覆盖项：全 VM `wsl --shutdown` 冷启动被**宿主安全策略硬阻断**（不可执行），故最底层 VM 冷启动未实测；Ubuntu 用户空间冷启动自启已实测 VERIFIED。

## 25. 存储 / 资源 / PATH — VERIFIED（只读审计，未做删除/改 PATH）

- 资源：见第 1、2 节。.wslconfig 6G/8C/2G 与实际负载匹配（available 4.9G、swap 未用），**不盲目增配**。WSL 根文件系统为 1007G 动态盘、仅用 9.7G。
- PATH：agent 会话 51 条、0 重复、14 条 MISSING——经逐条核对，全部是 Doubao `connector_runtime/sandbox` **临时注入目录**（会话外不存在，非用户持久 PATH），外加 WindowsApps python alias、`~/go/bin`（未创建）、UPX（.exe 被 `-d` 误判）等**无害项**。
- 持久配置（权威）：`/etc/environment` 标准 7 目录；`~/.bashrc` 仅追加 `~/.local/bin`、`~/.npm-global/bin` 并排除一个 /mnt/c/nodejs 路径；`~/.profile` 追加 ~/bin、~/.local/bin。**无重复、无真正失效，未删改任何 PATH**。
- 待授权维护：`docker_data.vhdx` 物理 18.24GB / 实用约 1.5GB（历史高水位），`wsl --shutdown` 后 `Optimize-VHD` 可回收约 16GB（需用户授权，且 shutdown 受宿主策略限制，需用户在本机执行）。

## 26. Known issues（已知问题 / 非阻塞瑕疵）

1. `test_isotp_options_vcan.py:186` ResourceWarning：ECU 子进程 BufferedReader fd3 未关闭，非致命。
2. ISO-TP 时序用例在 vcan 多进程并发时 EILSEQ/STmin 失效——测试须独占总线（已由 qa_extended/healthcheck 串行化保证）。
3. AFL `afl-clang-fast` 与 clang-18 ABI 不兼容——用 afl-gcc（或装 clang-17 套件）。
4. pytest 单会话收集下两个"导入纯净度"用例失败——天然要求独立进程，权威入口为 qa_fast.sh/run_tests.sh。
5. Docker cli-tools 在"Ubuntu 先于 Desktop 启动"时序下空挂载——按第 5 节 SOP `wsl --terminate docker-desktop` 恢复。
6. `jefferson/ubireader/sasquatch` 未安装（可选；WSL 无 MTD，JFFS2/UBIFS 已用 mtd-utils 覆盖制作/识别）。
7. CI workflow 未在真实 GitHub runner 触发（无 push 授权）。
8. 全 VM `wsl --shutdown` 被宿主安全策略禁止，最底层冷启动未实测（Ubuntu 用户空间冷启动已实测）。
9. `fuzz-out/` 为大规模生成产物，当前出现在 `git status` 未跟踪列表；**不应提交 Git**（建议后续在 `.gitignore` 增补其路径规则，此项改动留待用户确认，未擅自修改用户已改过的 `.gitignore`）。

## 27. Blocked items / 待用户操作（不可代做）

| 项 | 现状 | 需要用户做什么 | 后续验证 |
|----|------|----------------|----------|
| GitHub SSH 私钥 passphrase | 公钥已授权、agent 服务已部署，但 key 有 passphrase | 在 WSL 终端执行一次 `ssh-add` 输入 passphrase | 之后私有仓库 `git fetch/pull`（SSH 经代理）全通 |
| 0 字节异常文件 `` ; else echo MISS `` | 仓库根，2026-09-18 错误重定向产生，未删除 | 确认后授权删除（或保留） | `git status` 不再出现该怪名 |
| docker_data.vhdx 高水位 | 物理 18.24G/实用 ~1.5G | 授权后在本机 `wsl --shutdown` + `Optimize-VHD`（shutdown 受宿主策略限制，需用户本机执行） | vhdx 回收约 16G |
| Git 提交 | 44 项新增/修改未提交，本任务零 git 写 | 授权后分组 commit（**不会擅自 push**） | 见下方清单与 `AUTONOMOUS_BUILD_STATE.md` |
| 真实硬件 / 车辆 / ECU | 无 | 接入真实 CAN 适配器/ECU 并明确授权范围后再做 | 现全部实验锁定 vcan0，不接真实设备 |
| 0x19/0x31/0x34-37、实时 0x7DF 网关 | 路线图（非空壳） | 如需推进，按"服务六件套"逐服务补齐测试 | 纳入 qa_extended |

**本任务新增（未跟踪）主要文件**：`.github/workflows/ci.yml`、`pyproject.toml`、`conftest.py`、`configs/{binmt,network,systemd,environment}/...`（含 `dsh-web.service`、selfheal、proxy.env、requirements）、`scripts/{qa_fast,qa_extended,nettools_check,healthcheck}.sh`、`ecu/gateway.py`、`tests/test_gateway_unit.py`、`tests/test_multi_ecu_vcan.py`、`tests/test_services_extra_unit.py`、`tests/test_write_did_vcan.py`、`can_capture_blf.py`、`capture/capture_lab.sh`、`labs/{firmware,fuzzing,yara-basic}/...`、`docs/...`。
**本任务修改（已跟踪）主要文件**：`.gitignore`、`ecu/{uds_server,ecu,did_database,uds_types}.py`、`client/{isotp_client,uds_client}.py`、`tests/{run_tests.sh,test_e2e_vcan,test_ecu_unit,test_isotp_options_vcan,test_keygen_unit}.py`、`scripts/healthcheck.sh`（ruff/mypy 整改 + 功能扩展）。
**用户资产（保持原样未提交）**：`can-tools/fuzz/replay.py`、`can-tools/tests/test_fuzz_offline.py`。
**系统侧（不在 Git）**：apt 多组工具与 openjdk-17/21、`/opt/ghidra_12.1.3_PUBLIC`、binfmt selfheal v2.2 双端单元、vcan0.service、dsh-web user service + linger、apt 源 TUNA（均有备份或可逆）。

## 28. Recommended next engineering tasks（建议的后续工程任务，按优先级）

1. **P0 解锁协作链**：用户 `ssh-add`（解锁私有仓库 SSH）；随后授权把本任务成果按"车联网核心 / QA-CI / 固件-fuzz / 文档 / 系统配置"分组提交（先补 `.gitignore` 排除 `fuzz-out/`）。
2. **P1 实时网关守护进程**：实现 0x7DF 功能寻址的用户态 CAN 网关（Tester→CAN→Gateway→ECU A/B/C 拓扑），复用已 VERIFIED 的 `gateway.py` 协议层与多 ECU 进程模型，补 vcan e2e 与 qa_extended 收录。
3. **P1 UDS 服务扩面**：按 request/positive/NRC/state/unit/e2e 六件套逐个实现 0x19（读 DTC）、0x31（例程控制）、0x34/36/37（请求下载/传输数据/退出传输，形成刷写流程最小闭环）。
4. **P2 CI 真跑**：push 后确认 GitHub Actions fast job 全绿；extended job 手动 workflow_dispatch 验证 vcan runner。
5. **P2 崩溃/语料沉淀**：把 AFL demo 的崩溃发现接入 extended（默认不跑），CAN fuzz 语料纳入最小回归语料库与覆盖率统计。
6. **P3 可选增强**：jefferson/ubireader（JFFS2/UBI 深度解析）、Ghidra 项目模板/脚本库、pwndbg 或 gef（隔离安装不破坏现有 GDB）、BLF 更多场景与 CANdb DBC 解析实验。
7. **P3 空间维护**：择机执行 docker_data.vhdX 压缩回收（需用户本机 shutdown + Optimize-VHD）。

---

## 附录 A：一键复跑命令

```bash
# 快速离线门（无需 vcan/root，秒级~1 分钟）
bash scripts/qa_fast.sh
bash scripts/healthcheck.sh

# 扩展闭环（独占 vcan；会自动确保 vcan0，串行）
bash scripts/healthcheck.sh --extended
bash scripts/qa_extended.sh            # 含 uds all + can-tools all + capture + yara
bash scripts/qa_extended.sh --fuzz     # 再加 CAN fuzz smoke（更长）

# 分实验室
bash labs/vehicle/uds-basic/tests/run_tests.sh all
bash labs/vehicle/can-tools/tests/run_tests.sh all
bash labs/vehicle/can-tools/capture/capture_lab.sh
bash labs/yara-basic/scan.sh
(cd labs/fuzzing/afl-demo && bash build.sh && bash run.sh)

# DSH Web GUI
systemctl --user status dsh-web        # 日志 journalctl --user -u dsh-web -f
# 访问 http://127.0.0.1:8731 （首次 token 见 journal，仅回环）

# Ghidra headless
/opt/ghidra/support/analyzeHeadless <projdir> <projname> -import <elf>
```

## 附录 B：关键版本速查

- Ubuntu 24.04.5 / systemd 255 / kernel 6.18.33.2-microsoft-standard-WSL2 / WSL 2.7.14.0
- gcc 13.3.0 / clang-18.1.3 / aarch64+armhf gcc 13.3.0 / qemu-user 8.2.2 / gdb-multiarch 15.1
- Python 3.12.3 venv：python-can 4.6.1 / can-isotp 2.0.7 / udsoncan 1.26.1 / cantools 44 / scapy 2.7 / capstone 6.0.0a10 / pwntools 4.15 / unicorn 2.1.2 / cryptography 50.0.1 / pytest 9.1.1 / ruff 0.16.8 / mypy 2.3.1
- Ghidra 12.1.3 / openjdk 21.0.12 / radare2 5.5.0 / patchelf 0.18 / strace 6.8 / ltrace 0.7.3
- AFL++ 4.09c / YARA 4.5.0 / binwalk(Py3) / squashfs-tools 4.6.1 / mtd-utils 2.2
- nmap/ncat 7.94 / tshark 4.2.2 / tcpdump 4.99.4 / node 24.21.0 / Docker 29.7.2
- Kali：systemd 260 / Python 3.14.7 / gdb 17.2 / qemu 11.1 / clang 21 / afl++ 5.02 / yara 4.5.8

## 附录 C：最终回归记录（本窗口实测）

| 检查 | 命令 | 结果 |
|------|------|------|
| 离线质量门 | `qa_fast.sh` | **QA FAST RESULT: PASS**（uds unit 168 + can-tools offline 173） |
| 扩展安全门 | `qa_extended.sh` | **QA EXTENDED RESULT: PASS**（uds/can-tools all、capture 三格式、YARA） |
| 快速健康门 | `healthcheck.sh` | **PASS 76 / WARN 1 / FAIL 0**（DSH 检测优化后） |
| 扩展健康门 | `healthcheck.sh --extended` | **PASS 78 / WARN 2 / FAIL 0**（冷启动后；优化前 DSH 计 WARN） |
| CAN fuzz | smoke30 / dim100 / dim500 | 全量 process_alive/recovery PASS，new_errno=0、kernel_events=0 |
| Docker | `docker run --rm busybox` | BUSYBOX_E2E_OK，资产 1 镜像/0 容器/0 volume/3 网络 |
| Ghidra | headless import + postScript | RC=0，AARCH64:LE:64:v8A，main 反汇编 12 条，ERROR_COUNT=0 |
| ARM 调试 | qemu -g + gdb remote | 双断点→continue→exit rc0 |
| DSH | terminate 冷启动 + 生命周期 | 自动起、无登录自启、NRestarts=0、stop/start 可控 |
| Git | `rev-parse` / `status` | HEAD d9c548c，44 项未提交，零 git 写，用户资产完好 |

> 证据脚本与日志归档于 `tmp/audit/`（已 gitignore）：version_snapshot.sh、cold_boot_verify.sh、dsh_settle.sh、deploy_dsh.sh、ghidra_analyze.sh、DumpDisasm.java、qa_*.sh、verify_campaign.py 等；阶段断点见 `docs/AUTONOMOUS_BUILD_STATE.md`。
