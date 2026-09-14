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
├── AGENTS.md
├── configs/
│   └── systemd/
│       └── vcan0.service
├── labs/
│   ├── reverse/
│   ├── firmware/
│   └── vehicle/
└── .gitignore

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

固件分析实验。

当前包含：

- SquashFS
- Binwalk
- 固件文件系统提取
- rootfs 基础分析
- ELF 搜索与识别
- 动态库依赖分析

基础分析链：

固件
→ 文件系统识别
→ 提取
→ rootfs
→ ELF
→ 架构识别
→ 依赖分析
→ 静态分析
→ 动态分析

## labs/vehicle

车联网安全实验。

当前已经验证：

- Linux SocketCAN
- vcan0
- can-utils
- python-can
- cantools
- DBC
- can-isotp
- udsoncan

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

configs/systemd/vcan0.service

该服务用于：

- 加载 CAN 内核模块
- 加载 ISO-TP 内核模块
- 自动创建 vcan0
- 自动将 vcan0 设置为 UP

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

当前已经完成：

1. SecurityLab Git 仓库初始化
2. 逆向基础实验
3. 固件 Binwalk 基础实验
4. SocketCAN / vcan0
5. CAN 工具链
6. DBC 基础环境
7. ISO-TP 环境
8. UDS Python 环境
9. vcan0 systemd 自动启动
10. DSH + SecurityLab Workspace
11. AGENTS.md Agent 工作规范
12. UDS Basic 虚拟 ECU（0x10 / 0x11 / 0x22 + NRC）
13. UDS Basic 自动化测试（离线单元 39 例 + vcan0 端到端 30 例）

UDS Basic 实验位于 `labs/vehicle/uds-basic/`，详见该目录 README。

运行测试：

```bash
bash labs/vehicle/uds-basic/tests/run_tests.sh
```

后续方向：

- UDS 扩展服务（0x27 SecurityAccess / 0x2E 写入 / 0x19 DTC）
- UDS / CAN Fuzzing
- ARM / Embedded Linux 固件分析
- QEMU 固件仿真
- GDB 动态调试
- 自动化安全测试
- 车联网安全漏洞研究
