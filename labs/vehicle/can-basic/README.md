# CAN 基础环境实验

## 实验目的

验证 Ubuntu 车联网安全环境中的基础 CAN 能力。

本实验不连接真实车辆，也不使用真实 ECU。

使用 Linux SocketCAN 创建虚拟 CAN 总线 vcan0，并验证：

- SocketCAN 内核支持
- vcan0
- can-utils
- python-can
- cantools
- DBC 文件读取
- udsoncan 环境

## 1. SocketCAN

确认内核已经提供：

- can.ko
- can-dev.ko
- vcan.ko

创建虚拟 CAN 接口：

sudo modprobe can
sudo modprobe can_raw
sudo modprobe vcan

sudo ip link add dev vcan0 type vcan
sudo ip link set vcan0 up

检查：

ip -details link show vcan0

实验结果：

vcan0 成功创建并处于 UP 状态。

## 2. can-utils

安装：

sudo apt install -y can-utils

验证：

command -v cansend
command -v candump

实验结果：

/usr/bin/cansend
/usr/bin/candump

## 3. CAN 帧收发

接收：

candump vcan0

发送：

cansend vcan0 123#1122334455667788

实验结果：

vcan0 成功接收到 CAN ID 0x123 的 8 字节数据。

数据：

11 22 33 44 55 66 77 88

## 4. python-can

Python 环境：

~/.venvs/network

版本：

python-can 4.6.1

使用 python-can 连接 SocketCAN：

interface = socketcan
channel = vcan0

验证发送 CAN 帧：

CAN ID：
0x123

数据：

11 22 33 44

启用：

receive_own_messages=True

可以成功接收到自己发送的 CAN 帧。

实验结果：

Python → SocketCAN → vcan0 → CAN 收发链路正常。

## 5. cantools

版本：

cantools 44.0.0

建立：

demo.dbc

定义 CAN 消息：

EngineData

CAN ID：

0x123

消息长度：

8 bytes

定义信号：

EngineSpeed
VehicleSpeed

cantools dump demo.dbc

可以正常读取并显示消息和信号布局。

## 6. udsoncan

版本：

udsoncan 1.26.1

当前仅完成 Python 包安装与版本验证。

后续将基于：

SocketCAN
+
python-can
+
udsoncan

建立 UDS 诊断实验环境。

## 当前环境结构

Ubuntu

↓

SocketCAN

↓

vcan0

↓

can-utils
python-can

↓

cantools

↓

DBC

↓

udsoncan

## 实验结论

CAN 基础环境已经验证成功。

当前环境已经具备：

- 虚拟 CAN 总线
- CAN 原始帧收发
- Python CAN 操作
- DBC 文件解析
- UDS Python 库

下一阶段进入：

CAN 信号编码与解码
↓
UDS
↓
ISO-TP
↓
诊断服务
↓
ECU 模拟环境
