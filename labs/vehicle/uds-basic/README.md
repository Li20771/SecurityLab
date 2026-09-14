# UDS Basic Lab

基于 vcan0 的最小 UDS 虚拟 ECU 实验。

## 1. 实验目标

建立一条完整的本地 UDS 通信链：

UDS Client
→ ISO-TP
→ SocketCAN / vcan0
→ ISO-TP
→ Virtual ECU

当前首先验证：

- Linux ISO-TP
- Python can-isotp
- udsoncan
- UDS 0x10 DiagnosticSessionControl

## 2. 实验边界

本实验只使用：

- vcan0
- Python 虚拟 ECU
- 本地 Linux / WSL 环境

不连接真实车辆、真实 ECU 或真实 CAN 适配器。

## 3. 通信参数

### CAN

接口：

vcan0

### ISO-TP

使用 11-bit Normal Addressing。

Client → ECU：

0x7E0

ECU → Client：

0x7E8

因此：

UDS Client
TX 0x7E0
RX 0x7E8

Virtual ECU
RX 0x7E0
TX 0x7E8

## 4. 文件结构

uds-basic/
├── README.md
├── client/
│   ├── isotp_client.py
│   └── uds_client.py
├── configs/
│   └── uds.yaml
└── ecu/
    └── ecu.py

## 5. Python 环境

使用：

/home/lilike/.venvs/network/bin/python

核心组件：

can-isotp 2.0.7
udsoncan 1.26.1

## 6. ECU

ECU 程序：

ecu/ecu.py

启动：

/home/lilike/.venvs/network/bin/python ecu/ecu.py

启动后应进入等待状态：

Virtual ECU is waiting for UDS requests...

## 7. 原始 ISO-TP 测试

客户端：

client/isotp_client.py

运行：

/home/lilike/.venvs/network/bin/python client/isotp_client.py

当前测试请求：

10 01

ECU 正响应：

50 01 00 32 00 64

其中：

10      DiagnosticSessionControl
01      defaultSession

50      Positive Response
01      defaultSession
00 32   P2ServerMax
00 64   P2*ServerMax

## 8. udsoncan 测试

客户端：

client/uds_client.py

运行：

/home/lilike/.venvs/network/bin/python client/uds_client.py

该客户端使用：

udsoncan.Client
→ IsoTPSocketConnection
→ Linux CAN_ISOTP
→ vcan0

当前测试目标：

DiagnosticSessionControl
extendedDiagnosticSession

对应请求：

10 03

对应正响应：

50 03 00 32 00 64

## 9. 实验分层

### Layer 1：CAN

确认 vcan0 存在并处于 UP。

### Layer 2：ISO-TP

确认 can-isotp 内核模块以及 Python isotp 正常。

### Layer 3：UDS 原始报文

通过 isotp_client.py 直接发送：

10 01

确认 ECU 能返回：

50 01 ...

### Layer 4：udsoncan

通过 uds_client.py 使用 udsoncan.Client 发送诊断会话请求。

## 10. 当前已验证能力

- SocketCAN vcan0
- Linux CAN_ISOTP
- can-isotp 2.0.7
- udsoncan 1.26.1
- ISO-TP 双向收发
- UDS 0x10
- DiagnosticSessionControl
- Virtual ECU
- udsoncan Client

## 11. 后续扩展

下一阶段逐步增加：

0x22 ReadDataByIdentifier
0x2E WriteDataByIdentifier
0x19 ReadDTCInformation
0x11 ECUReset
0x27 SecurityAccess

随后建立：

Virtual ECU State Machine
DID Database
DTC Database
SecurityAccess
UDS Fuzzing
CAN Fuzzing

最终形成完整的本地车联网安全实验靶场。
