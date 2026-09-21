# AUTONOMOUS BUILD STATE — SecurityLab

> 断点恢复文件。任何 `wsl --terminate` / `wsl --shutdown` / Docker Desktop 重启 / 系统服务变更之前，必须先更新本文件。重启后首先读本文件继续，禁止从头再来。
>
> 规则：只记录经实际命令验证的事实；状态取值 VERIFIED / PARTIAL / BLOCKED / NOT_APPLICABLE。

## 2026-09-21 工程收口（Engineering Close Phase 1，Claude Code）

**CURRENT_PHASE**: 第一轮建设工程收口（分支 `chore/engineering-close-20260921`，
base `audit/environment-20260921`；禁止修改 main）

**COMPLETED（本分支，全部本地验证）**:

- P1 fuzz kernel oracle 三态（AVAILABLE/UNAVAILABLE/ERROR；不可用 ≠ 0 events）；
  离线测试 99 例（含新增 10 例）PASS。
- P1 binfmt selfheal v2.3 fail-closed（helper 缺失/失败绝不回落 systemd-binfmt）；
  回归 `configs/binfmt/tests/selfheal_failclosed_test.sh` 11/11 PASS。
  系统侧 `/usr/local/sbin/binfmt-selfheal.sh` 部署更新留待用户授权后执行
  （repo 内 `configs/binfmt/` 为源真）。
- P1 healthcheck QEMU fallback `&&` 短路修复；回归
  `scripts/tests/healthcheck_qemu_fallback_test.sh` 3/3 PASS；
  真实 healthcheck 75 PASS / 2 WARN / 0 FAIL。
- scripts/repro/：verify_arm 6/6、verify_qemu_gdb 4/4、verify_ghidra 6/6、
  verify_fuzz_smoke 8/8（全部本机实测 PASS）。
- 文档同步：README、uds-basic/README、SECURITY_LAB_STATUS、TEST_MATRIX、
  ARCHITECTURE、本断点文件；审计报告加"历史快照"标注。
- P2 依赖 transitive lock：已完成（`configs/environment/python-network-requirements.lock`，
  pip-compile 生成 + 全新 venv 安装验证；pip-tools 7.6.1 入 dev requirements）。

**NEXT_STEP (after user authorization)**:

- 授权 push / PR 合入 main；GitHub CI 首次真跑（fast job）。
- 部署 selfheal v2.3 到 `/usr/local/sbin/`（双端）并重跑冷启动观察。
- 第二轮能力建设（0x19/0x31/0x34-37、实时 0x7DF 网关、JFFS2/UBIFS extraction、
  Docker Lab、Obsidian）——本分支**不做**。

**KNOWN_ISSUES**: 同上文（ssh-add、vhdx 压缩、CI runner 实测见 Phase 2；
0 字节文件 `; else echo MISS` 已随 cleanup commit `chore(repo): remove
environment probe artifacts` 删除；`.p1x-probe/*-OUTPUT.txt` 已移出版本库并 gitignore）。

## 2026-09-20 (current session, Doubao)

**CURRENT_PHASE**: Phase 6 — Ubuntu/Kali 工具 apt 补齐（systemd-run 托管安装中）；Phase 2/3/4/5 已完成

### 2026-09-20 阶段4 Docker = VERIFIED

- Docker Desktop 4.90.0 / Engine 29.7.2 / containerd 2.3.3 / runc 1.4.3；开启 EnableIntegrationWithDefaultWslDistro（原配置备份 %APPDATA%\Docker\settings-store.json.bak-20260920）。
- Ubuntu `/usr/bin/docker` → /mnt/wsl/docker-desktop CLI，client/server 连通；busybox `docker run --rm` E2E 通过、--rm 自动清理。
- 资产只读枚举：当前 1 镜像(busybox)、0 容器、0 named volume、3 默认网络；数据盘 D:\Docker\wsl\DockerDesktopWSL\disk\docker_data.vhdx 物理 18.24GB（历史高水位，文件系统实际用 1.5GB：isocache 1.5G+engine data 10.6M），未 prune/未删任何数据。
- Dify 源码与 compose 在 C:\Users\20771\Desktop\dify\dify-main（bind 部署，volumes 仅 382KB，无关键数据）。新发现资产：D:\github-project\{AutoSec, Vehicle Subsystem End-to-End Security Assessment}（含 docker-compose）。
- 可选维护（未执行，需用户同意）：wsl --shutdown 后 Optimize-VHD 压缩可回收约 16GB。

### 2026-09-20 阶段5 网络/Git

- 根因：直连 github 首页可达但 git smart-HTTP(info/refs) 被干扰超时；Clash 代理(网关:7890)下公开仓库 ls-remote 成功、私有仓库返回 401（需认证）。
- 修复：`configs/network/proxy.env`（source 即用，动态探测 WSL 网关+端口活性，不硬编码 IP）；`configs/network/ssh-proxy.sh`（纯 stdlib Python HTTP CONNECT ProxyCommand，select 双向转发）；~/.ssh/config 增加 SecurityLab 块（github.com→ssh.github.com:443 经代理）。
- 验证：HTTPS 公开仓库 git ls-remote rc=0；SSH 经代理完成 banner/KEX/公钥认证，**GitHub Server accepts key（公钥已授权，纠正此前判断）**；私钥有 passphrase、ssh-agent 原未运行。
- 已部署 systemd-user `ssh-agent.service`（~/.config/systemd/user/，socket=$XDG_RUNTIME_DIR/ssh-agent.socket，.bashrc 导出 SSH_AUTH_SOCK，active）。
- **BLOCKED-USER-ACTION（唯一剩余）**：用户在 WSL 终端执行一次 `ssh-add`（输入私钥 passphrase），之后私有仓库 SSH git 全通；HTTPS 私有需 PAT（不推荐，SSH 已够）。
- 直连 SSH 22/443 均被网络干扰（TCP 连得上、握手黑洞），必须走代理。

### 2026-09-20 阶段2 binfmt 最终结论（N-01）

- 实验 E（wsl --shutdown 全冷启动）：Ubuntu 稳态 31 handler、WSLInterop OK、ARM64 OK（F1/F2/G1-3 连续稳定）；Kali 冷启动后经历 1-2 分钟挂载传播抖动（视图在 Ubuntu 集合与 Kali 自身集合间横跳），静置收敛为 2 handler（WSLInterop+python3.13）、interop 稳定 OK（H1/H2/H3）。
- **WSL_INTEROP_STATUS（Ubuntu 主环境）= VERIFIED**：热交叉启动最长中断 9s（含不可消除的 WSL 重挂载全空窗口 ~4s），通常 0-2s，selfheal v2.1（path + 10s timer）双端自动恢复。
- **Kali interop = VERIFIED**（稳态 OK）；**Kali ARM64 透明执行 = BLOCKED**（Kali 未装 qemu-user；全冷启动后 Kali 为独立 binfmt 实例，后续装 qemu-user-binfmt 不会污染 Ubuntu；热共享场景解释器路径问题待实测）。
- protectBinfmt=false 实验证明有害（全表清空），已回滚（/etc/wsl.conf.bak-20260920）。

### 2026-09-20 阶段3 Kali 系统健康 = VERIFIED

- 根因：tpm-udev.path 在无 TPM 的 WSL 中反复触发撞 start-limit；user@1000 是 WSL init 与 systemd 260 竞争 spawn（"Failed to spawn executor: Device or resource busy"）。
- 修复（均可逆）：`systemctl mask tpm-udev.path`（WSL 无 TPM，不适用）；`loginctl enable-linger lilike`（systemd 在 boot 竞争前建立 user manager）。
- 验证：两次冷启动 Kali 均 0 failed、user@1000 active(running)、is-system-running=running；Ubuntu 0 failed/running。启动横幅仍打印 WSL user session 提示但功能正常（WSL init 自身尝试失败，linger 实例已在运行）。

### 2026-09-20 binfmt 专项进展（N-01）

- 实验 A（修复前基线）：Kali 冷启动删除全局 WSLInterop，qemu/python 保留；v1 selfheal 逻辑缺陷不触发；Kali 退出时二次删除；中断持续 >50s 不恢复。
- 根因：WSL2 各 distro 在各自 mount namespace 挂载同一内核 binfmt_misc（dev 0:35/inode 1 相同，peer group 不同）；Kali 的 WSL protectBinfmt drop-in `echo -1 > WSLInterop` 删除成功、重建 Permission denied。
- 实验 B：Kali `protectBinfmt=false` 会使冷启动期间**全表清空（含 29 qemu）**且 .path 监听不触发，比默认更糟 → **已回滚**（备份 /etc/wsl.conf.bak-20260920）。
- 修复：selfheal 升级 v2.1（WSLInterop 独立愈合 + 快慢双阶段 + flock），新增 10s 周期 timer 兜底（path 监听在重挂载时可能失效）；**Ubuntu 与 Kali 均已安装 path+timer（enabled/active）**。
- 实验 A'（默认配置 Kali 冷启动）：启动 0 感知中断，退出 ≤2s 恢复。
- 实验 C（3 次连续冷启动循环，128s 采样）：累计中断 16s，最长连续 9s（含 WSL 重挂载全空窗口 4s，不可消除），全部自动恢复。
- 实验 D（Kali 运行中重启 Ubuntu，Kali 视角 102s）：累计中断 31s，最长连续 ~10s，全部自动恢复。
- 待办：实验 E（wsl --shutdown 全冷启动）；Kali 无 qemu-user（Kali 内 ARM 透明执行不可用，显式 qemu 也缺；Ubuntu 为主环境不受影响）。
- 受控源文件：`configs/binfmt/binfmt-selfheal.sh`（v2.1）、`configs/systemd/binfmt-selfheal.{service,path}`、`binfmt-selfheal-quick.service`、`binfmt-selfheal.timer`；证据 `tmp/audit/exp{A,A2,B,C,D}-*.log` 与 `.p1x-probe/`。

### COMPLETED

- Git 状态保护：分支 `refactor/uds-architecture`，HEAD `d9c548c`；未提交修改 `labs/vehicle/can-tools/fuzz/replay.py`、`labs/vehicle/can-tools/tests/test_fuzz_offline.py`（上一会话已验收的 replay --all 修复，**用户资产，禁止覆盖/丢弃/提交，除非用户授权**）；完整 diff 已归档 `tmp/audit/git-diff-20260920.patch`（tmp/ 已 gitignore）。
- Windows 盘点：Win11 Pro 26200；i7-12700H 14C/20T；16G RAM（空闲 3.8G）；C: 剩 62.2G，D: 剩 123.4G。
- WSL：2.7.14.0，kernel 6.18.33.2；distro = Ubuntu(默认)/kali-linux/docker-desktop；.wslconfig = memory 6G / processors 8 / swap 2G (D:\WSL\swap.vhdx) / NAT。
- Ubuntu 24.04.5：systemd running，0 failed units；用户 lilike 在 sudo/docker/wireshark 组；**sudo 需要密码**。
- binfmt：当前 31 handler（WSLInterop + python3.12 + 29 qemu），INTEROP_OK；selfheal.path/service 已安装且 enabled+active（/usr/local/sbin/binfmt-selfheal.sh, 2026-09-18 安装）。
- SocketCAN：vcan0.service enabled+active，vcan0 UP (MTU 2060)，can/can_raw/vcan/can-isotp 模块已加载。
- ARM64：aarch64-linux-gnu-gcc 13.2 编译静态程序 → binfmt 透明执行 OK（answer=42）→ qemu-aarch64 显式执行 OK。GDB remote 闭环待验证。
- 工具在位：can-utils 全套、isotp 全套、qemu-user 8.2.2、gdb/gdb-multiarch 15.1、binwalk、nmap、tcpdump、tshark/dumpcap、nc、socat、jq、tmux、cmake、make、gcc/g++。
- venv `~/.venvs/network`（Python 3.12.3）：python-can 4.6.1 / can-isotp 2.0.7 / udsoncan 1.26.1 / cantools 44 / scapy 2.7 / capstone 6.0.0a10 / pwntools 4.15 / unicorn 2.1.2 / cryptography 50。
- fuzz-out：30 个 testcase JSON + smoke-summary.json（2026-09-17 生成，需重跑验证）。
- Docker Desktop 4.73.1 已安装但未运行；WSL 默认发行版集成关闭；数据盘 D:\Docker\wsl\DockerDesktopWSL\disk\docker_data.vhdx = 18.24GB（**严禁删除/prune**）。
- 代理：clash-verge-service 运行，核心监听 :7890（双栈）与 127.0.0.1:7891；Windows 系统代理当前 ProxyEnable=0；WSL 默认网关 172.24.208.1。
- GitHub：直连 HTTPS FAIL；SSH publickey denied（无可用密钥）；代理转发测试结果见本次执行记录。

### NEXT_STEP

1. Phase 2：binfmt Ubuntu↔Kali 交叉启动实验（脚本化采样 + journal + 功能验证），产出 WSL_INTEROP_STATUS。
2. Phase 3：Kali failed units 诊断（user@1000 / tpm-udev / accounts-daemon）。
3. Phase 4：启动 Docker Desktop，开启 Ubuntu WSL 集成（改 settings-store.json，备份原件），验证数据盘与容器/卷。
4. Phase 5：固化 WSL 代理（仅在验证有效后，以环境脚本而非硬编码方式）。
5. Phase 6+：免 sudo 项先行（venv pip、项目测试、用户级工具）；apt 级安装汇总后统一处理 sudo。

### CHANGES（本 session 实际写入）

- 仅新增 `tmp/audit/*.sh`（审计脚本）与 `tmp/audit/git-diff-*.patch|txt`（归档），tmp/ 已 gitignore，不影响 Git 工作树。
- 未修改任何系统配置、未安装/卸载软件、未触碰 Docker 数据、未改 Git 历史与已跟踪文件。
- 新增本状态文件 `docs/AUTONOMOUS_BUILD_STATE.md`（未跟踪文件，后续随文档统一处理）。

### COMMANDS（关键验证命令）

- Windows：`wsl -l -v`、`Get-CimInstance Win32_OperatingSystem/Processor/LogicalDisk`
- Ubuntu：`bash /mnt/d/SecurityLab/tmp/audit/ubuntu_audit.sh`、`ubuntu_audit2.sh`、`proxy_test.sh`

### KNOWN_ISSUES（待复核/处理）

- sudo 非交互不可用 → apt/系统级修复需要用户配合或授权方式。
- 历史 P-01 binfmt 被 Kali 冷启动清空：selfheal 已装但交叉恢复**尚未在本 session 重新验证**。
- Docker WSL 集成关闭（历史 P-03/P-12，当前 docker 命令落到 Windows 包装脚本）。
- GitHub 直连不通（历史 P-11）。
- 缺口工具：clang ninja pkg-config patchelf strace ltrace nft/iptables yara afl++ radare2 Ghidra armhf 工具链 qemu-user-static（非必需）keystone pytest(venv)。
- 仓库根存在 0 字节异常文件名 ``; else echo MISS``（2026-09-18 22:02 由错误 shell 重定向生成，疑似 .p1x-probe 脚本所致；未删除，待确认后处理）。
- PATH 在 agent 继承环境中混入 Doubao connector_runtime 与大量 Windows 目录（干净终端需复核，历史 P-13）。

### BLOCKED

- 暂无永久阻塞；sudo 与 Docker GUI 类操作届时按高风险确认流程处理。

## 2026-09-21 01:40 EMERGENCY RECOVERY CHECKPOINT (pre-terminate Ubuntu)

CURRENT_PHASE: 阶段6b binfmt 热共享振荡修复（selfheal v2.2）
COMPLETED:
- 阶段6 Ubuntu/Kali 工具链全部安装验证完成（clang-18/afl++/r2/yara/交叉gcc/ncat 等；Kali gdb17.2/qemu11.1/clang21/afl++5.02）
- 定位热共享振荡根因：Kali qemu-user-binfmt 的 binfmt.d 含 qemu-or1k（共享表缺失），v2.1 selfheal 每10s 判定缺失->调用 systemd-binfmt flush 全局表（删 WSLInterop）->跨distro振荡；Kali terminate 后振荡立即消失（30s 采样稳定）
- 编写 configs/binfmt/binfmt-register-additive.py（additive 单条注册，不 flush）
- selfheal 升级 v2.2（用 additive helper 替代 systemd-binfmt flush），Ubuntu 已部署，自测 skipped=30
INCIDENT:
- 受控测试删除 qemu-alpha 后用 Python helper 补回，helper 的 magic/mask 解码有 bug，补回的 qemu-alpha mask 过宽误匹配 x86-64 ELF，导致所有新 Linux 进程 Exec format error（bash 无法启动）。WSLInterop 仍在。
- 修复：terminate Ubuntu 冷启动，系统 systemd-binfmt 用正确 C 解码器重新注册；selfheal v2.2 additive 只补缺失不会重写已存在的正确条目。
NEXT_STEP (after reboot):
- 验证 bash 恢复、binfmt 29 qemu + WSLInterop 正确、x86 命令正常、ARM64 透明执行 OK
- 修正 binfmt-register-additive.py 的转义解码 bug（对照 /usr/lib/binfmt.d/qemu-alpha.conf 原文与注册后 handler，重点查 mask NUL/长度），在不破坏表的前提下重新单测（先在测试副本验证 mask 字节精确，再碰注册表）
- Kali 部署 v2.2 + /usr/libexec/qemu-binfmt 兼容 symlink
- 双端热共享 120s 采样验证无振荡 + 两端 ARM 执行
KNOWN_ISSUES:
- Ubuntu 源已切 tuna（备份 ubuntu.sources.bak-20260920）；Kali 源已切 tuna（备份 sources.list.bak2-20260920）
- 待用户：ssh-add passphrase；0字节文件处置；vhdx 压缩授权；git commit 授权

## 2026-09-21 01:50 阶段6 + 6b 完成

阶段6 工具链（VERIFIED）：
- Ubuntu：clang/clang++-18、ninja、patchelf、strace/ltrace、nft/iptables、yara、radare2、aarch64/arm-gnueabi/arm-gnueabihf-gcc、nvim、afl++4.09c、unsquashfs、mtd_debug、ncat7.94；tuna 镜像（aliyun CDN 对 WSL NAT 假死，备份 ubuntu.sources.bak-20260920）
- Kali：gdb/gdb-multiarch 17.2、qemu-user 11.1(+binfmt)、tshark、socat、strace/ltrace、radare2、yara4.5.8、ncat7.99、hydra、nikto、patchelf、clang21、ninja、aarch64/armhf-gcc16.1、afl++5.02、python3.14-venv/dev；tuna 镜像（备份 sources.list.bak2-20260920）

阶段6b 跨distro binfmt振荡修复（VERIFIED，selfheal v2.1->v2.2）：
- 根因1：两 distro 同时运行共享内核全局 binfmt 注册表；Kali binfmt.d 含共享表缺失的 qemu-or1k，v2.1 selfheal 每10s判定缺失即调用 systemd-binfmt（先flush全表，删WSLInterop）按Kali路径重建，Ubuntu侧再改回->持续~10s振荡。Kali terminate 后振荡立即消失（取证）。
- 根因2（事故）：初版 additive helper 把 \xNN 解码成原始字节写 register，magic 第8字节 NUL 截断注册行、mask 丢失，handler 误匹配所有x86-64 ELF（bash Exec format error）；wsl --shutdown 冷启动恢复。
- 修复：新增 configs/binfmt/binfmt-register-additive.py（verbatim 写转义文本由内核解码，含 raw-NUL 拒绝护栏，additive 单条注册绝不 flush）；selfheal v2.2 用 helper 取代 systemd-binfmt；Kali 建 /usr/libexec/qemu-binfmt 31个兼容 symlink。
- 验证：真实 qemu-alpha 删/补 magic/mask/interp/flags 4项精确一致且不误匹配；热共享90s WSLInterop零缺失零翻转；实验F(Kali冷启动)Ubuntu视角interop零中断一次性收敛；实验G(Ubuntu冷启动)Kali视角2次~2s重挂载全空窗(WSL固有不可消除)后收敛无振荡；双端最终 qemu=32 interop=YES ARM64_OK=42。
- 结论：WSL_INTEROP_STATUS(UBUNTU)=VERIFIED；KALI_INTEROP=VERIFIED；KALI_ARM64_TRANSPARENT_EXEC=VERIFIED（由BLOCKED升级）。

NEXT_STEP：重启Docker Desktop(wsl --shutdown致停)复验集成 -> 阶段7 CAN/ISO-TP/UDS/ECU 真实闭环（venv python 跑 can-tools/uds-basic run_tests.sh）。

## 2026-09-21 02:40 阶段7/8/9/14-19 完成（terminate Ubuntu 前断点）

CURRENT_PHASE: 阶段6c Docker 集成复验（需 terminate Ubuntu 让 Desktop 重新注入 cli-tools 挂载）。

### COMPLETED
- 阶段7 CAN/ISO-TP/UDS/ECU（VERIFIED）：冷启动后 vcan0 自动 UP（vcan0.service，MTU2060，can/can_raw/vcan/can_isotp 模块）。uds-basic run_tests all：e2e 65例OK(36s)+ISO-TP矩阵14例OK(17s)，PASS5/FAIL0；can-tools run_tests all：malformed 47例OK(149.6s)，PASS5/FAIL0。唯一非致命：test_isotp_options_vcan.py:186 ResourceWarning（ECU 子进程 BufferedReader fd3 未关）。
- 阶段8 fuzz 阶梯（VERIFIED）：smoke30=77s（TE7/DISCARD22/NORMAL1）；dim100=277s（TE26/UDS11/DISCARD59/NORMAL4）；dim500≈23min（DISCARD387/UDS42/NORMAL28/TE43）。500例逐例 process_alive=True×500、recovery=PASS×500、new_errno=0、kernel_events=0、无残留 ecu.py、vcan0 仍UP。产物 fuzz-out/camp-{smoke30,dim100,dim500}-20260921（gitignore）。CLI：执行必须带 --smoke 开关，再用 --dimensions/--categories/--seeds 走 generate_cases，--max-cases 截断；systemd-run transient unit 会被 WSL 在发起会话退出后回收，长任务改用持久 wsl 进程（PowerShell run_in_background）。
- 阶段9 replay（VERIFIED）：单例 identity OK/class 与原始一致；--repeat 3 无分歧；目录 --all 重放30例且 [ignore] smoke-summary.json 被正确跳过；两次 --all summary 完全相同（7/22/1）。工作区修复版 replay.py 未覆盖。
- 阶段14 ARM（VERIFIED）：aarch64+armhf 静态-g 编译、qemu 透明执行（ARM_RUNTIME_OK 42）、qemu -g + gdb-multiarch target remote 双断点命中→continue→exited normally→rc0。补装 libc6-dev-armhf-cross。Ubuntu gcc13.3/qemu8.2.2/gdb15.1。
- 阶段15 逆向（VERIFIED，Ghidra 待装）：rabin2/r2 反汇编 AArch64、objdump、patchelf、binutils。
- 阶段16 动态分析（VERIFIED）：strace、ltrace、qemu-aarch64 -strace、gdb 断点。
- 阶段17 固件（VERIFIED+扩展）：binwalk 签名/root提取（-e --run-as=root）、unsquashfs/mksquashfs；JFFS2/UBIFS/UBI 制作+binwalk识别 PASS（jefferson/ubireader 可选）。新增 labs/firmware/synthetic-ecu（合成 ECU v1/v2 + build/analyze/firmware_diff/fs_formats 脚本 + README）与 labs/firmware/samples（管理规范+MANIFEST模板+images gitignore）；.gitignore 增补。
- 阶段18 AFL++（VERIFIED）：新增 labs/fuzzing/afl-demo（vuln.c 栈溢出 + build/run/README）。afl-clang-fast 的 LLVM pass 按 llvm-17 构建与 clang-18 ABI 不兼容；可靠路径 afl-gcc（另装 libclang-rt-18-dev 备用）。30-45s 发现 stack-buffer-overflow(sig6/exit134)，重放信号一致。
- 阶段19 YARA（VERIFIED，4.5.0）：新增 labs/yara-basic（规则+hit/ecu/clean 样本+scan.sh TP/TN），并扫描合成固件 ELF 命中。

### NEXT_STEP（重启后）
1) 复验 Docker：terminate Ubuntu→冷启动（Desktop 已 Running）→/usr/bin/docker shim→docker version + busybox E2E + 只读枚举（禁 prune/删 volume）；冷启动后确认 vcan0 自愈与 binfmt interop。
2) 阶段13 CAN capture→save→replay（PCAP/BLF via python-can）。
3) 阶段10-12 UDS 服务扩展（0x2E/0x19/0x31/0x3E/0x34-37，逐服务 request/response/NRC/state/tests，不造空壳）+ 多ECU/Gateway 评估。
4) Ghidra headless（JDK+zip 走代理）。
5) 阶段21/22 统一 pytest 入口 + ruff/mypy/black + 最小 fast CI。
6) 阶段24 统一 scripts/healthcheck。
7) 阶段25/26 空间/PATH + DSH Agent systemd 评估。
8) 最终 docs/FINAL_ENVIRONMENT_AUDIT_2026-09-20.md（28节）。

### KNOWN_ISSUES
- Docker cli-tools 共享挂载在“Ubuntu 先于 Desktop 启动”时序下为空，需 terminate Ubuntu 重新注入（配置 EnableIntegrationWithDefaultWslDistro=true 未变）。
- uds-basic test_isotp_options_vcan.py:186 ResourceWarning（非致命）。
- afl-clang-fast 与 clang-18 不兼容（用 afl-gcc）。
- 待用户：ssh-add passphrase；0字节异常文件处置；vhdx 压缩授权；git add/commit 授权（本 session 零 git 写、禁 push）。

## 2026-09-21 03:30 阶段6c/13/20/24 + 阶段10-12(0x2E/0x3E) 完成

CURRENT_PHASE: 阶段12 多 ECU/Gateway 路由层（下一步）。

### COMPLETED（补记 + 新完成，均 VERIFIED）
- 阶段6c Docker 集成复验（VERIFIED，得到免提权恢复 SOP）：cli-tools 空挂载时 `wsl --terminate docker-desktop`（只停引擎 distro，不碰数据盘），backend ~24s 自动重启引擎，再冷启动 Ubuntu，cli-tools ~12s 恢复；勿强杀 Docker Desktop/com.docker.backend（Windows 服务 com.docker.service 托管，普通权限杀不掉）。busybox E2E 输出 BUSYBOX_E2E_OK；资产原样 1 镜像/0 容器/0 named volume/3 网络，未 prune。
- 阶段13 CAN capture→save→replay（VERIFIED，三格式）：新增 `labs/vehicle/can-tools/can_capture_blf.py`（BLF record/inspect/replay，guard vcan0）与 `capture/capture_lab.sh`（6 帧）。CAN log（candump -L→canplayer）、BLF（python-can BLFWriter/Reader，count=6/ids/DLC8）、PCAP（tshark -i/-r，6 CAN 帧）三闭环 diff 一致；CAPTURE_LAB=PASS。PCAP 定位为 Wireshark 分析，CAN 回放用 CAN log/BLF。
- 阶段20 网络工具（VERIFIED）：新增 `scripts/nettools_check.sh`，ncat 单向交付/socat relay/nmap -sT/tcpdump/tshark/nc，6 PASS；nft/iptables 需 root，WSL 记 WARN。
- 阶段24 统一 healthcheck（VERIFIED）：原地重写 `scripts/healthcheck.sh [--extended]`，覆盖 binfmt/WSLInterop、ARM64 编译→透明执行、交叉工具链、逆向/动态/固件/fuzz/网络工具、Docker（不通记 WARN）、CAN/ISO-TP/vcan、venv 包、Git HEAD；--extended 加跑 uds all + capture_lab + yara。快速 75 PASS/2 WARN/0 FAIL，扩展 78 PASS/2 WARN/0 FAIL（WARN=工作树未提交[预期]、dsh 不在 PATH）。
  - **并发陷阱（实证）**：vcan 测试严禁并发；run_in_background 回显 exit -1 不代表没跑，绝不能据此前台重跑，否则双 ECU 抢占致 ISO-TP EILSEQ/SecurityAccess NRC 错乱（单独低负载复跑立即恢复，非代码回归）。
- 阶段10-12 UDS 扩展 0x3E TesterPresent + 0x2E WriteDataByIdentifier（VERIFIED）：
  - 源码：`ecu/did_database.py` 新增可写 DID 0xF102=b"DEFAULT_CONFIG"、WRITABLE_DIDS、WRITABLE_DID_LENGTHS(1..16)；`ecu/uds_server.py` EcuState 加 did_overrides（field(default_factory=dict)）、0x11 hardReset 清空覆盖、0x22 读 override 优先、新增 service_write_data_by_identifier(门控顺序：长度0x13→未知DID 0x31→默认会话 0x7F→未解锁 0x33→只读DID 0x22→数据长度 0x13→6E)、service_tester_present(3E 00→7E 00；3E 80→b"" 抑制；非0子功能 0x12；长度 0x13)，SERVICES 注册两服务；`ecu/ecu.py` 主循环对空响应 continue 不发空帧。
  - 测试：新增 `tests/test_services_extra_unit.py`（17 例离线，协议层独立 EcuState）、`tests/test_write_did_vcan.py`（8 例 vcan e2e，含真实 SecurityAccess 解锁写入读回、hardReset 恢复出厂、3E 80 总线静默 recv 超时）。
  - 随功能演进改写过时占位测试（照既有 0x27 先例，非回归）：test_ecu_unit.py 与 test_e2e_vcan.py 中原"0x2E/0x3E 未实现→7F .. 11"各 2 例改为断言新行为；run_tests.sh 收录两个新测试文件。
  - 验证：`run_tests.sh all` → **PASS 7 / WARN 0 / FAIL 0**（离线 ecu_unit 104 + keygen 34 + extra 17；e2e e2e_vcan + isotp 14 + write_did 8）。
  - 0x19/0x31/0x34-37 与多 ECU/Gateway 未做（不造空壳，留路线图）。

### NEXT_STEP
1) 阶段12 多 ECU/Gateway：先做协议层纯路由分发器（物理寻址单播 + 0x7DF 功能寻址广播）+ 离线单元测试，vcan 实时多实例集成随后（ecu.py 已支持 UDS_INTERFACE/UDS_RX_ID/UDS_TX_ID 环境变量）。
2) 阶段15b Ghidra headless（root apt openjdk-17 + 走代理下 zip，验证 analyzeHeadless 跑 ARM64 样本；失败记 PARTIAL）。
3) 阶段21/22 venv 内 ruff/pytest 统一入口 + 最小 fast CI（.github/workflows，install/lint/test；fuzz 归 extended）。
4) 阶段25/26 C:/D 空间、WSL 资源、PATH 重复/失效审计（不删 PATH）；DSH 入口/进程/端口/日志排查与 systemd 服务化评估（不读凭据文件）。
5) 最终 docs/FINAL_ENVIRONMENT_AUDIT_2026-09-20.md（28 节）+ TEST_MATRIX/SECURITY_LAB_STATUS/ARCHITECTURE。

### KNOWN_ISSUES（新增）
- 本 session 新增/修改的受控文件均未提交（零 git 写、禁 push）：新增 can_capture_blf.py、capture/capture_lab.sh、scripts/nettools_check.sh、healthcheck.sh(重写)、tests/test_services_extra_unit.py、tests/test_write_did_vcan.py；修改 ecu/did_database.py、ecu/uds_server.py、ecu/ecu.py、tests/test_ecu_unit.py、tests/test_e2e_vcan.py、tests/run_tests.sh；更早 labs/firmware|fuzzing|yara-basic、configs、docs 未跟踪。待用户授权后分组 commit。

## 2026-09-21 04:00 阶段12b / 21-22 / 15b 完成

CURRENT_PHASE: 阶段25/26（空间/PATH、DSH）与最终文档（仅剩这两块 + 28 节报告）。

### COMPLETED（均 VERIFIED）
- **阶段12b 多 ECU / Gateway（VERIFIED）**：新增纯协议层 `ecu/gateway.py`（VirtualEcu 独立 EcuState + DiagnosticGateway；物理寻址单播 0x7E0/1/2→0x7E8/9/A，功能寻址 0x7DF 广播改状态但不收集响应，suppress 空响应不发帧，重复 request_id/空网关拒绝）；新增 `tests/test_gateway_unit.py`（13 例离线，含只写 A 不影响 B/C 的状态隔离、0x7DF 广播三 ECU 同切 extended 且不泄漏响应）；新增 `tests/test_multi_ecu_vcan.py`（4 例 vcan 实时：起 3 个 ecu.py 进程绑不同 UDS_RX_ID/TX_ID，验证响应标识符正确、A 切 extended 后 A=7F2E33 而 B/C 仍=7F2E7F 证明会话隔离、错误地址无应答）。run_tests.sh 收录，`run_tests.sh all` → **PASS 9 / WARN 0 / FAIL 0**。
  - 遗留（PARTIAL，路线图）：0x7DF 功能寻址**实时网关守护进程**未做（协议层路由已 VERIFIED、多 ECU 物理寻址实时已 VERIFIED）；0x19/0x31/0x34-37 未实现（禁止空壳）。
- **阶段21/22 质量门禁 + CI（VERIFIED，CI 配置未在 GitHub runner 实测）**：venv 装入 pytest 9.1.1 / ruff 0.16.8 / mypy 2.3.1（未动系统 Python）；新增根 `pyproject.toml`（ruff 选 E/F/I/UP/B 忽略 E501、mypy py312 ignore_missing_imports、pytest 注册 vcan marker + testpaths）、根 `conftest.py`（按文件名含 `_vcan` 程序化打 marker，零改动既有大测试文件）。
  - ruff 从 41 项修到 All checks passed（33+ 项安全 --fix：import 排序/未用 import；手修 client/isotp_client.py B904 异常链、test_isotp_options 死变量、test_multi_ecu B007）；mypy 从 5 项修到 0（gateway handler Optional 收窄/路由变量改名、uds_server 0x22 data: bytes|None 与 bytearray→bytes、ecu.py errno int|None）。
  - 新增 `scripts/qa_fast.sh`（ruff+mypy+uds unit+can-tools offline，无需 vcan/root，独立进程规避"模块纯净度"用例的 sys.modules 单会话假设）实测 **QA FAST RESULT: PASS**（uds unit 168 例 + can-tools offline 173 例）。
  - 新增 `scripts/qa_extended.sh`（自动确保 vcan0，串行独占：uds all + can-tools all + capture_lab 三格式 + yara，`--fuzz` 可选）实测 **QA EXTENDED RESULT: PASS**（CAPTURE_LAB=PASS、YARA 三断言 PASS）。
  - 新增 `configs/environment/python-dev-requirements.txt`（钉 pytest/ruff/mypy 版本）与 `.github/workflows/ci.yml`（fast job  push/PR 自动跑 qa_fast；extended job 仅 workflow_dispatch，runner 上 modprobe vcan + apt can-utils/tshark/yara 后跑 qa_extended；fuzz 不进 CI）。
  - 注意：pytest 单会话 `pytest -m 'not vcan'` 下两个"导入纯净度"用例（test_keygen_unit / test_parsers 断言 sys.modules 不含 isop/can）会因跨模块收集污染而失败，这是用例天然要求独立进程所致、非缺陷；权威离线入口是 qa_fast.sh / run_tests.sh。
- **阶段15b Ghidra headless（VERIFIED）**：root apt(tuna) 装 openjdk-17 + openjdk-21（系统默认 java=21.0.12，Ghidra 12 需 21）；走代理下 Ghidra 12.1.3（569445154 字节，sha256 93a5d11a…，下完已删 /tmp/ghidra.zip）解压 /opt/ghidra_12.1.3_PUBLIC 并软链 /opt/ghidra。
  - 验证 1：`analyzeHeadless -import tmp/audit/arm-lab/hello_arm64` RC=0、**Using Language AARCH64:LE:64:v8A:default**、ERROR_COUNT=0（R_AARCH64_IRELATIVE 仅 INFO）。
  - 验证 2：postScript `tmp/audit/DumpDisasm.java`（-process）反汇编 main@0x4007b8 出 12 条 AArch64 指令（stp x29,x30 序言 / bl / adrp+add 取串 / ldp…ret），PROCESS_RC=0。
  - 静态（Ghidra/r2/binutils）+ 动态（qemu-aarch64 + gdb-multiarch 远程断点，阶段14 已 VERIFIED）双侧闭环。

### NEXT_STEP
1) 阶段25：C:/D 空间、WSL memory/CPU/swap/disk、PATH 重复/失效/冲突审计（只记录与建议，不删 PATH、不盲目加资源）。
2) 阶段26：DSH Agent 实际入口/进程/端口/日志排查，评估能否安全建 systemd user service（不读 ~/.dsh/.credentials.yaml）；不适合则记录原因。
3) 最终：docs/FINAL_ENVIRONMENT_AUDIT_2026-09-20.md（28 节，附命令/结果/版本/HEAD/工作树）+ TEST_MATRIX.md / SECURITY_LAB_STATUS.md / ARCHITECTURE.md（优先更新已有同类）。
4) 待用户：ssh-add passphrase；0 字节文件 `; else echo MISS` 处置；docker_data.vhdx Optimize-VHD 授权；git add/commit 授权（仍零 git 写、禁 push）。

### 本阶段新增未提交文件（追加）
- 新增：`ecu/gateway.py`、`tests/test_gateway_unit.py`、`tests/test_multi_ecu_vcan.py`、`pyproject.toml`、`conftest.py`、`scripts/qa_fast.sh`、`scripts/qa_extended.sh`、`configs/environment/python-dev-requirements.txt`、`.github/workflows/ci.yml`。
- 修改（ruff --fix + 类型修复 + 测试收录）：`client/isotp_client.py`、`ecu/gateway.py`、`ecu/uds_server.py`、`ecu/ecu.py`、`tests/test_isotp_options_vcan.py`、`tests/test_multi_ecu_vcan.py`、`tests/run_tests.sh`（其余 ruff --fix 触及的 uds-basic 测试文件以 import 排序为主，git diff 可查）。
- 系统侧（不在 Git）：apt openjdk-17/21、/opt/ghidra_12.1.3_PUBLIC（软链 /opt/ghidra）。

## 2026-09-21 04:05 阶段25/26 完成 + PRE-TERMINATE 冷启动验证检查点

CURRENT_PHASE: 准备 `wsl --terminate Ubuntu` 冷启动，复验开机自启（dsh-web / vcan0 / binfmt），随后进入最终 28 节报告。

### 阶段25 空间 / 资源 / PATH（VERIFIED，只读审计，未做任何删除/改 PATH）
- 空间：C: 200.4G 剩 61.5G（70%）；D: 274.3G 剩 115.7G（58%）；WSL 根 /dev/sdd 1007G 动态盘用 9.7G。vhdx 物理：Kali 16.2G、Ubuntu 11.68G、docker_data 18.24G（高水位，实用约 1.5G，Optimize-VHD 可回收约 16G，**待授权**）、swap 0.04G。/opt/ghidra 881M。
- 资源：free 总 5.8Gi / available 4.9Gi，swap 2Gi 未用，nproc=8；.wslconfig = memory6G/processors8/swap2G(swapFile=D:\WSL\swap.vhdx)/nat，与目标一致，**资源充足，不调整**。
- PATH：当前 agent 会话 51 条、0 重复、14 条 MISSING——经核对全部是 Doubao connector_runtime/sandbox 临时注入目录（会话外不存在，非用户持久 PATH）+ WindowsApps python alias / go/bin / UPX（.exe 被 -d 误判，无害）。持久配置（/etc/environment 标准 7 条 + .bashrc 仅追加 .local/bin/.npm-global/bin 并排除一个 /mnt/c/nodejs 路径）**无重复无失效**，不需修改。

### 阶段26 DSH Agent 服务化（VERIFIED）
- DSH = @deepseek-ai/dsh@0.1.5-rc.2（DeepSeek Harness），wrapper `~/.local/bin/dsh`（npx 缓存），node 24.21.0 在 /usr/bin；`dsh web` 提供 Web UI（--host/--port/--no-open）。原状态：未运行、无端口、无 unit、linger=no。
- 新增可复现单元 `configs/systemd/dsh-web.service`（用户级，ExecStart `dsh web --no-open --host 127.0.0.1 --port 8731`，Restart=on-failure，WantedBy=default.target），部署到 ~/.config/systemd/user/ 并 enable。
- 验证：start 后 4s 监听 127.0.0.1:8731（active/enabled）；带一次性 token 的 UI 返回 HTTP 303（token fence 接受并跳转，token 未写入任何交付物）；stop→inactive 且端口释放，start→4s 恢复 active（LIFECYCLE_OK=1）；root `loginctl enable-linger lilike`（Linger=yes），保证 WSL 启动无登录也自启。
- 管控：`systemctl --user {status,stop,start,restart} dsh-web`、`journalctl --user -u dsh-web -f`；仅绑回环不对外。未读取 ~/.dsh/.credentials.yaml。

### 冷启动待复验项（terminate Ubuntu 后）
1) dsh-web：启动约 10-15s 后 127.0.0.1:8731 监听、is-active=active（linger user service）。
2) vcan0：vcan0.service 自启，operstate=up/MTU2060。
3) binfmt：WSLInterop + qemu handler 在、ARM64 透明执行 42、selfheal active。
4) 若 docker shim 为空，按既有 SOP：`wsl --terminate docker-desktop` 等 ~24s 引擎自重启，再启动 Ubuntu（勿强杀 backend）。
5) 跑 scripts/healthcheck.sh 快速门作为冷启动回归。

### 本阶段新增未提交文件（追加）
- `configs/systemd/dsh-web.service`（部署副本在 ~/.config/systemd/user，不属 Git）。
- 系统侧（不在 Git）：apt openjdk-17/21-jdk-headless、/opt/ghidra、linger 启用。

### 冷启动自启实测结果（VERIFIED）
- `wsl --shutdown` 被宿主安全策略**硬阻断**（不可覆盖，未执行）；改用 `wsl --terminate Ubuntu`。实测 terminate 会重启 Ubuntu 的 distro systemd/PID1 与 user manager（共享 VM 内核 uptime 不变，但用户空间 pid 重新编号、systemd 实例重启，journal 中 user manager 由 systemd[253]→[247]→[243]→[245]），等价于 Ubuntu 用户空间冷启动；Docker Desktop WSL 集成会保活/快速重连。
- 每次 systemd 重新初始化后，**dsh-web 均由 enabled+linger 自动 Started（约 5-6s 输出 URL 并监听 8731），无需 lilike 交互登录**（以 root 冷进入、list-sessions 仅 root pts/1 时，lilike 的 dsh 进程已在运行）。停止 terminate 静置后：pid 293 连续 4 轮（8/16/24/32s）不变、端口持续 LISTENING、is-active=active、**NRestarts=0**（非崩溃循环，早期 etimes=0 只是撞上启动 4-6s 窗口）。
- 同实例复验：vcan0 UP(MTU2060)、binfmt status=enabled + WSLInterop OK + qemu-aarch64 handler OK + 32 handlers、selfheal timer/path active。
- 结论：**阶段26 DSH 服务化 VERIFIED**（systemctl --user 管控 + journal 日志 + 回环绑定 + token fence + 开机自启）；唯一未做的是全 VM `wsl --shutdown` 冷启动（宿主策略禁止，非环境缺陷）。
- 下一步：最终文档 docs/FINAL_ENVIRONMENT_AUDIT_2026-09-20.md（28 节）+ TEST_MATRIX / SECURITY_LAB_STATUS / ARCHITECTURE（先查 docs/ 已有同类，优先更新）。

## 2026-09-21 04:25 全部阶段完成（FINAL）

CURRENT_PHASE: **DONE**。27 个阶段全部审计/修复/验证完毕，最终文档交付。

最终冷启动回归（本窗口实测）：
- Docker：冷启动 cli-tools 空挂载 → 按 SOP `wsl --terminate docker-desktop`，引擎自动恢复，client=server=29.7.2，busybox E2E=BUSYBOX_E2E_OK，资产原样 1 镜像/0 容器/0 volume/3 网络（未 prune）。
- `qa_fast.sh` → QA FAST RESULT: PASS（uds unit 168 + can-tools offline 173）。
- `healthcheck.sh --extended` → PASS 78 / WARN 2 / FAIL 0（冷启动后）；将 DSH 检测升级为 systemd 服务判定后，快速门 PASS 76 / WARN 1 / FAIL 0（唯一 WARN=工作树未提交，预期）。
- DSH 服务化经 terminate 用户空间冷启动实测自启（无登录会话、NRestarts=0）。
- 交付文档（新建，未跟踪）：docs/FINAL_ENVIRONMENT_AUDIT_2026-09-20.md（28 节+附录）、docs/TEST_MATRIX.md、docs/SECURITY_LAB_STATUS.md、docs/ARCHITECTURE.md。
- 唯一被宿主硬阻断：`wsl --shutdown`（安全策略禁止，不可覆盖）；已用 terminate Ubuntu（重启 distro systemd/PID1）等价验证 Ubuntu 侧开机自启，报告如实标注。

剩余仅"待用户操作"（不可代做，见报告第 27 节）：ssh-add passphrase；0 字节怪文件处置；docker_data.vhdx Optimize-VHD 授权；git add/commit 授权（本任务全程零 git 写、禁 push）。PARTIAL 路线图：0x7DF 实时网关守护进程、UDS 0x19/0x31/0x34-37、CI runner 实跑。
