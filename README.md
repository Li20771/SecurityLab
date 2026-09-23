# SecurityLab

个人网络安全与车联网安全研究环境。

## 项目定位

SecurityLab 用于长期积累网络安全、二进制逆向、固件分析和车联网安全实验。

主要研究方向：

- 网络安全
- 二进制逆向
- 固件分析
- ARM / AArch64
- Linux / Embedded Linux
- CAN / SocketCAN
- ISO-TP
- UDS
- 车联网安全
- 漏洞研究
- 安全实验自动化

## 当前目录结构

SecurityLab/
├── AGENTS.md                    # Agent 工作规范（安全边界）
├── configs/
│   ├── binfmt/                  # binfmt additive 注册器 + selfheal v2.3（fail-closed）
│   ├── environment/             # Python requirements（钉版本）
│   ├── network/                 # 代理探测 / ssh-proxy
│   └── systemd/                 # vcan0 / binfmt-selfheal / dsh-web 单元
├── labs/
│   ├── reverse/                 # 逆向基础（ARM64 / GDB / QEMU / Pwntools）
│   ├── firmware/                # binwalk-basic、synthetic-ecu、samples
│   ├── fuzzing/                 # AFL++ 通用 fuzz 靶场
│   ├── yara-basic/              # YARA 规则 + 样本 + scan.sh
│   └── vehicle/
│       ├── can-basic/           # CAN 基础练习
│       ├── can-tools/           # 收发 / 捕获 / fuzz / replay
│       └── uds-basic/           # UDS 虚拟 ECU / 客户端 / 网关 + 测试
├── scripts/
│   ├── healthcheck.sh           # 统一环境健康检查（[--extended]）
│   ├── qa_fast.sh / qa_extended.sh   # 工程级质量门禁
│   ├── nettools_check.sh
│   ├── repro/                   # 关键验证的可复现入口（ARM/QEMU-GDB/Ghidra/fuzz smoke）
│   └── tests/                   # shell 级回归测试
├── docs/                        # 审计报告、测试矩阵、状态看板、架构
├── pyproject.toml / conftest.py # ruff / mypy / pytest 配置
└── .github/workflows/ci.yml     # fast 自动 / extended 手动
（历史/用户目录：ctf data notes pcaps projects reports reverse tools artifacts —— 非本任务创建，保持原样）

## labs/reverse

逆向工程基础实验。

当前包含：

- x86-64 ELF
- ARM64 ELF
- GDB
- GDB-Multiarch
- QEMU
- Pwntools
- 基础静态分析

## labs/firmware

固件分析实验（合成固件，可复现）。

当前包含：

- SquashFS：build + identify + extract + analyze（完整链）
- JFFS2：build + identify（当前环境无完整 extraction chain）
- UBIFS/UBI：build + identify（当前环境无完整 extraction chain）
- Binwalk 签名扫描与提取
- rootfs 基础分析
- ELF 搜索与识别
- 动态库依赖分析
- firmware diff（`labs/firmware/synthetic-ecu/firmware_diff.sh`）

基础分析链：

固件
→ 文件系统识别（binwalk / fs_formats）
→ 提取（SquashFS 完整；JFFS2/UBIFS 待第二轮补 extraction chain）
→ rootfs
→ ELF
→ 架构识别
→ 依赖分析
→ 静态分析
→ 动态分析

## labs/vehicle

车联网安全实验。

当前已经验证：

- Linux SocketCAN / vcan0（systemd 自启）
- can-utils（candump / cansend / canplayer / isotpsend ...）
- python-can / cantools / DBC
- can-isotp（内核模块 + Python 2.0.7）
- udsoncan 1.26.1
- UDS 虚拟 ECU：0x10 / 0x11 / 0x14 / 0x19 / 0x22 / 0x27 / 0x2E / 0x31 / 0x34-0x38 / 0x3E（服务注册表 + dispatcher）
- 多 ECU / Gateway（`ecu/gateway.py`：物理寻址三 ECU + 功能寻址广播）
- CAN / ISO-TP 状态机 fuzz + 精确 replay（`can-tools/fuzz/`，kernel oracle 三态）
- CAN capture 三格式（CAN log / BLF / PCAP）

基础链路：

CAN
→ SocketCAN
→ vcan0
→ CAN-utils
→ python-can
→ DBC
→ ISO-TP
→ UDS

## configs

保存可以复现的环境配置。

当前包含：

- `configs/systemd/`：vcan0.service（加载 CAN/can_isotp 内核模块、
  自动创建 vcan0 并置 UP）、binfmt-selfheal（path + timer + quick）、
  dsh-web.service
- `configs/binfmt/`：binfmt-register-additive.py（additive 注册器，
  绝不 flush）与 binfmt-selfheal.sh v2.3（helper 缺失时 fail-closed，
  不回落 systemd-binfmt）
- `configs/environment/`：python-network / python-dev requirements（钉版本）
- `configs/network/`：proxy.env（动态探测网关代理）、ssh-proxy.sh

## Git

SecurityLab 使用 Git 管理实验代码、配置和文档。

基本原则：

- 实验源码和文档进入 Git
- 大型固件样本不直接进入 Git
- 提取目录不直接进入 Git
- 临时生成物不直接进入 Git
- 凭据、Token、私钥等敏感信息不得进入 Git

重要 Git 操作必须由用户明确发起。

## DSH

SecurityLab 使用 DeepSeek Harness（DSH）作为主要 Agent。

DSH Workspace：

/mnt/d/SecurityLab

DSH 负责：

- 阅读和分析项目
- 辅助编写实验代码
- 执行测试
- 分析固件和 ELF
- 协助构建安全实验环境

DSH 的具体工作规范见：

AGENTS.md

## 当前环境

核心宿主环境：

- Windows
- WSL2
- Ubuntu
- Kali Linux
- Docker Desktop

SecurityLab 主工作区：

/mnt/d/SecurityLab

## 安全边界

默认车联网实验均使用：

- vcan0
- 虚拟 ECU
- 模拟 CAN 总线
- 本地实验环境

涉及真实车辆、真实 ECU、真实 CAN 适配器或其他真实设备时，必须先明确目标、测试范围和授权环境。

## 当前阶段

当前已经完成（第一轮建设 + 2026-09-21 工程收口）：

1. SecurityLab Git 仓库初始化
2. 逆向基础实验（ARM64 ELF / GDB / GDB-Multiarch / QEMU / Pwntools）
3. 固件合成实验（SquashFS 完整链；JFFS2/UBIFS build+identify）
4. SocketCAN / vcan0（systemd 自动启动）
5. CAN 工具链 + DBC 基础环境
6. ISO-TP 环境（内核 can_isotp + can-isotp 2.0.7）
7. UDS Python 环境（udsoncan 1.26.1）
8. UDS Basic 虚拟 ECU：0x10 / 0x11 / 0x14 / 0x19 / 0x22 / 0x27 / 0x2E /
   0x31 / 0x34-0x38 / 0x3E（服务注册表 + dispatcher，非 if/elif）
9. UDS SecurityAccess（0x27）：seed/key、失败计数、锁定、防重放
10. 受保护/可写 DID 的 Session × Security 权限模型
11. 多 ECU / Gateway（物理寻址 + 功能寻址广播）
12. CAN / ISO-TP 状态机 fuzz + 精确 replay（kernel oracle 三态）
13. CAN capture 三格式（CAN log / BLF / PCAP）
14. 自动化测试：UDS 离线单元 581 + vcan0 端到端 208（共 789）+
    can-tools 离线 183 + malformed（2026-09-23 实测，**Local verified**）
15. 质量门禁（qa_fast / qa_extended / healthcheck）+ CI workflow
16. binfmt selfheal v2.3（additive-only，fail-closed，双端部署）
17. 关键验证可复现入口（`scripts/repro/`：ARM / QEMU-GDB / Ghidra /
    fuzz smoke）

UDS Basic 实验位于 `labs/vehicle/uds-basic/`，详见该目录 README。
能力看板见 `docs/SECURITY_LAB_STATUS.md`，测试矩阵见 `docs/TEST_MATRIX.md`。

运行测试：

```bash
bash scripts/healthcheck.sh                 # 环境体检（秒级）
bash scripts/qa_fast.sh                     # 离线质量门禁（ruff/mypy/unit）
bash labs/vehicle/uds-basic/tests/run_tests.sh all    # UDS 全量（unit + e2e）
```

后续方向（第二轮能力建设，见 `docs/SECURITY_LAB_STATUS.md` 路线图）：

- UDS 剩余扩展（`0x19` 其余 reportType、`0x11` 04/05、`0x28` / `0x85`、
  Permission / session framework）
- 0x7DF 功能寻址的用户态实时网关守护进程
- JFFS2 / UBIFS 完整 extraction chain
- Docker Security Lab / Obsidian 知识系统
- 大型 fuzz campaign 与真实 CAN 接口实验（需明确授权范围）
- ARM / Embedded Linux 固件分析、QEMU 固件仿真、GDB 动态调试
- 自动化安全测试、车联网安全漏洞研究
