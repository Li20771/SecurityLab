# UDS Basic Lab

基于 vcan0 的最小 UDS（ISO 14229-1）虚拟 ECU 实验环境。

本实验在一个完全隔离的本地虚拟 CAN 总线上，建立一条完整的车用诊断链路，
用于理解 UDS 协议结构、ISO-TP 传输层、以及诊断服务的正常与异常路径。

> **安全边界**：本实验只使用 `vcan0`（虚拟 CAN，无物理收发器）和本机虚拟 ECU。
> **不连接真实车辆、真实 ECU、真实 CAN 适配器，不发送任何真实总线报文。**

---

## 1. 实验目标

建立并验证一条完整的本地 UDS 通信链：

```
UDS Client
   ↓
ISO-TP (ISO 15765-2)
   ↓
SocketCAN / vcan0
   ↓
ISO-TP
   ↓
Virtual ECU
```

具体目标：

1. 理解 CAN、ISO-TP、UDS 三层协议的分工与封装关系
2. 从零实现一个虚拟 ECU 的诊断服务分发逻辑
3. 掌握 UDS 肯定响应 / 否定响应（NRC）的判定规则
4. 能够用原始字节和 `udsoncan` 两种方式验证诊断行为
5. 建立可重复运行的自动化测试，作为后续 Fuzzing / 漏洞研究基线

---

## 2. 协议结构

### 2.1 三层关系

| 层 | 协议 | 标准 | 本实验中的作用 |
|---|---|---|---|
| 应用层 | UDS | ISO 14229-1 | 诊断服务（SID + 参数） |
| 传输层 | ISO-TP | ISO 15765-2 | 把超过 8 字节的 UDS 报文分帧传输 |
| 数据链路层 | CAN | ISO 11898-1 | 11-bit ID 的 CAN 帧 |

**关键理解**：UDS 报文不能直接塞进一帧 CAN。CAN 经典帧只有 8 字节数据，
而一次诊断请求/响应往往更长。ISO-TP 负责：

- **单帧（SF, Single Frame）**：≤ 7 字节能一次装下
- **多帧（FF + CF, First Frame + Consecutive Frame）**：更长报文分段传输
- **流控（FC, Flow Control）**：接收方控制发送节奏

因此 UDS 是"内容"，ISO-TP 是"信封"，CAN 是"邮车"。

### 2.2 寻址参数

使用 11-bit Normal Addressing：

| 方向 | CAN ID |
|---|---|
| Tester → ECU | `0x7E0` |
| ECU → Tester | `0x7E8` |

> `0x7E0` / `0x7E8` 是 ISO 15765-4 中定义的 OBD-II 诊断寻址惯例
> （功能寻址 `0x7DF`，物理寻址 `0x7E0`–`0x7E7` / 响应 `0x7E8`–`0x7EF`）。

### 2.3 报文封装示例

以 `22 F1 90`（读取 DID `0xF190`）为例：

```
UDS 层      : 22 F1 90
ISO-TP 层   : 03 22 F1 90 00 00 00 00      ← 0x03 是 SF 的长度字节
CAN 层      : ID=0x7E0  DLC=8  Data=03 22 F1 90 00 00 00 00
```

ECU 响应 `62 F1 90 + 17 字节数据`（共 20 字节）超过 7 字节，
因此 ISO-TP 自动使用多帧：

```
FF (首帧) : 10 14 62 F1 90 ...             ← 0x1014：首帧 + 总长 20
FC (流控) : 30 00 00 ...                   ← 接收方允许发送
CF (连续帧): 21 ...  22 ...
```

---

## 3. ECU 模拟器

### 3.1 文件

| 文件 | 说明 |
|---|---|
| `ecu/ecu.py` | 虚拟 ECU 主程序：ISO-TP 收发 + UDS 服务分发 |
| `ecu/did_database.py` | DID 数据库（DID → 数据字节） |

### 3.2 服务分发逻辑

`ecu/ecu.py` 的核心是纯函数：

```python
def handle_uds_request(request: bytes) -> bytes:
    ...
```

它**不依赖 CAN**，输入请求字节、输出响应字节。
因此可以直接被单元测试调用（见第 7 节），这是本实验可测试性的关键设计。

### 3.3 会话状态

ECU 维护一个全局会话状态：

```python
DEFAULT_SESSION = 0x01
CURRENT_SESSION = DEFAULT_SESSION
```

- `0x10` 切换会话时会更新 `CURRENT_SESSION`
- `0x11 01`（hardReset）会把会话复位回 `defaultSession`
- 被拒绝的请求**不会**改变会话状态（已由测试覆盖）

### 3.4 可配置项

ECU 支持环境变量覆盖，便于多 ECU / 多通道实验（默认值保持标准寻址不变）：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `UDS_INTERFACE` | `vcan0` | CAN 接口 |
| `UDS_RX_ID` | `0x7E0` | ECU 接收 ID（Tester 发送 ID） |
| `UDS_TX_ID` | `0x7E8` | ECU 发送 ID（Tester 接收 ID） |

```bash
# 例：在 vcan1 上以 0x7E1/0x7E9 运行第二个 ECU
UDS_INTERFACE=vcan1 UDS_RX_ID=0x7E1 UDS_TX_ID=0x7E9 \
  python ecu/ecu.py
```

---

## 4. 支持的 SID

| SID | 服务 | 子功能 | 肯定响应 | 状态 |
|---|---|---|---|---|
| `0x10` | DiagnosticSessionControl | `0x01` default<br>`0x02` programming<br>`0x03` extended | `0x50` | 已实现 |
| `0x11` | ECUReset | `0x01` hardReset | `0x51` | 已实现 |
| `0x22` | ReadDataByIdentifier | —（支持多 DID） | `0x62` | 已实现 |

**尚未实现**（会返回 `NRC 0x11 ServiceNotSupported`）：

`0x19` ReadDTCInformation、`0x27` SecurityAccess、`0x28` CommunicationControl、
`0x2E` WriteDataByIdentifier、`0x31` RoutineControl、`0x3E` TesterPresent、
`0x85` ControlDTCSetting 等。

### 4.1 响应格式

**0x10 DiagnosticSessionControl**

```
请求: 10 <sessionType>
响应: 50 <sessionType> <P2ServerMax:2> <P2*ServerMax:2>
实际: 50 01 00 32 00 64
```

`0x0032` = 50 ms，`0x0064` = 100 ms（P2 / P2* 服务器最大响应时间）。

**0x11 ECUReset**

```
请求: 11 <resetType>
响应: 51 <resetType>
实际: 51 01
```

**0x22 ReadDataByIdentifier（支持多 DID）**

```
请求: 22 <DID:2> [<DID:2> ...]
响应: 62 <DID:2> <data...> [<DID:2> <data...> ...]
实际: 62 F1 90 <17 字节> F1 80 <13 字节>
```

### 4.2 DID 数据库

| DID | 含义 | 数据 | 长度 |
|---|---|---|---|
| `0xF190` | VIN / 车辆识别号（本实验用测试值） | `TESTECU0000000001` | 17 |
| `0xF180` | Boot Software Identification | `BOOT-SW-1.0.0` | 13 |

> 真实车辆中 `0xF190` 是 VIN（17 位 ASCII），`0xF180` 是 Boot SW 版本。
> 本实验使用固定测试值，便于断言。

---

## 5. 支持的 NRC

| NRC | 名称 | 本实验触发条件 |
|---|---|---|
| `0x11` | ServiceNotSupported | 请求了未实现的 SID |
| `0x12` | SubFunctionNotSupported | `0x10`/`0x11` 的子功能不支持 |
| `0x13` | IncorrectMessageLengthOrInvalidFormat | 报文长度错误（过短/过长/奇数长度） |
| `0x31` | RequestOutOfRange | `0x22` 请求了未知 DID |

否定响应固定格式：

```
7F <请求 SID> <NRC>
```

### 5.1 完整的错误矩阵

| 请求 | 响应 | 原因 |
|---|---|---|
| `10` | `7F 10 13` | 缺少 sessionType |
| `10 01 00` | `7F 10 13` | 多余字节 |
| `10 04` | `7F 10 12` | 会话类型不支持 |
| `11` | `7F 11 13` | 缺少 resetType |
| `11 01 00` | `7F 11 13` | 多余字节 |
| `11 02` | `7F 11 12` | resetType 未实现 |
| `22` | `7F 22 13` | 缺少 DID |
| `22 F1` | `7F 22 13` | DID 不完整（奇数长度） |
| `22 FF FF` | `7F 22 31` | 未知 DID |
| `22 F1 90 FF FF` | `7F 22 31` | 多 DID 中任一未知则整体失败 |
| `27 01` | `7F 27 11` | 服务未实现 |
| 空请求 | `7F 00 13` | 无法解析 |

**重要设计原则**：多 DID 读取时，只要有一个 DID 未知，整个请求失败，
**不返回部分数据**（避免 Tester 误解析错位的数据）。

---

## 6. 启动方法

### 6.1 前置条件

| 项目 | 要求 |
|---|---|
| Python | `/home/lilike/.venvs/network/bin/python`（Python 3.12） |
| 依赖 | `can-isotp==2.0.7`、`udsoncan==1.26.1` |
| 接口 | `vcan0` 已存在且为 UP |
| 权限 | 无（vcan 不需要 root；创建 vcan0 需要一次 root） |

检查环境：

```bash
bash scripts/healthcheck.sh          # 仓库根目录
```

确认 vcan0：

```bash
ip -details link show vcan0
```

若无 vcan0：

```bash
sudo systemctl start vcan0.service   # 仓库自带 configs/systemd/vcan0.service
```

### 6.2 启动虚拟 ECU

需要一个终端保持运行：

```bash
cd labs/vehicle/uds-basic
/home/lilike/.venvs/network/bin/python -u ecu/ecu.py
```

正常输出：

```
ISO-TP version: 2.0.7
CAN interface: vcan0
RX ID: 0x7E0
TX ID: 0x7E8
ISO-TP socket bound successfully.
Current UDS session: 0x01
Virtual ECU is waiting for UDS requests...
```

`-u` 关闭输出缓冲，便于实时观察。`Ctrl+C` 停止。

---

## 7. 测试方法

### 7.1 一键运行全部测试

```bash
cd labs/vehicle/uds-basic
bash tests/run_tests.sh
```

可只运行其中一类：

```bash
bash tests/run_tests.sh unit   # 只跑离线单元测试
bash tests/run_tests.sh e2e    # 只跑 vcan0 端到端测试
```

### 7.2 离线单元测试（不需要 CAN）

```bash
/home/lilike/.venvs/network/bin/python tests/test_ecu_unit.py
```

直接调用 `handle_uds_request()`，覆盖 **39 个用例**：

- `0x10` 各会话、异常子功能、长度错误、状态不被破坏
- `0x11` 正常复位、复位后会话回归、不支持 resetType、长度错误
- `0x22` 单 DID、多 DID、顺序保持、未知 DID、奇数长度
- 未实现服务（`0x19`/`0x27`/`0x2E`/`0x31`/`0x3E`/未知 SID）
- 否定响应结构恒为 3 字节
- DID 数据库约束（每个 DID 都可读）

特点：**不需要 vcan0、不需要 root、可在任意机器运行**，适合作为 CI 基线。

### 7.3 vcan0 端到端测试

```bash
/home/lilike/.venvs/network/bin/python tests/test_e2e_vcan.py
```

该测试会**自动启动和关闭**虚拟 ECU 子进程，覆盖 **30 个用例**：

| 测试类 | 内容 |
|---|---|
| `TestRawIsotp` | 原始字节级请求/响应断言（含全部 NRC 路径） |
| `TestUdsoncanClient` | `change_session` / `ecu_reset` / `read_data_by_identifier` |
| `TestIsotpFraming` | 用 `python-can` 抓包验证 SF / FF / CF 分帧 |
| `TestEcuProcess` | ECU 进程存活与日志内容 |

> 注意：端到端测试会占用 `0x7E0`/`0x7E8`，运行前请停止手动启动的 ECU，
> 否则两个 ISO-TP socket 会竞争同一对 CAN ID。

### 7.4 手动客户端

**原始字节方式**（`client/isotp_client.py`）：

```bash
PY=/home/lilike/.venvs/network/bin/python

$PY client/isotp_client.py                  # 默认 10 01
$PY client/isotp_client.py 11 01            # ECUReset
$PY client/isotp_client.py 22 F1 90         # 读单个 DID
$PY client/isotp_client.py 22 F1 90 F1 80   # 读多个 DID
$PY client/isotp_client.py 27 01            # 未实现服务
```

**udsoncan 方式**（`client/uds_client.py`）：

```bash
$PY client/uds_client.py read      # 0x22 单 DID + 多 DID
$PY client/uds_client.py session   # 0x10 会话切换
$PY client/uds_client.py reset     # 0x11 硬复位
$PY client/uds_client.py all       # 依次执行全部
```

### 7.5 用 can-utils 旁路观察

另开终端可看到真实的 CAN 帧（含 ISO-TP 分帧）：

```bash
candump vcan0
candump -ta vcan0        # 带时间戳
```

---

## 8. 原始报文示例

以下为在 vcan0 上**实测**的输出。

### 8.1 0x10 切换会话

```bash
$ python client/isotp_client.py 10 01
TX UDS: 10 01
RX UDS: 50 01 00 32 00 64
  → DiagnosticSessionControl 肯定响应，会话 0x01
```

### 8.2 0x11 ECUReset

```bash
$ python client/isotp_client.py 11 01
TX UDS: 11 01
RX UDS: 51 01
  → ECUReset 肯定响应，resetType 0x01
```

不支持的 resetType：

```bash
$ python client/isotp_client.py 11 02
TX UDS: 11 02
RX UDS: 7f 11 12
  → 否定响应：请求 SID 0x11，NRC 0x12
```

### 8.3 0x22 多 DID 读取（触发 ISO-TP 多帧）

```bash
$ python client/isotp_client.py 22 F1 90 F1 80
TX UDS: 22 f1 90 f1 80
RX UDS: 62 f1 90 54 45 53 54 45 43 55 30 30 30 30 30 30 30 30 30 31
        f1 80 42 4f 4f 54 2d 53 57 2d 31 2e 30 2e 30
  → ReadDataByIdentifier 肯定响应：
      DID 0xF190: 54 45 53 54 45 43 55 30 30 30 30 30 30 30 30 30 31
                  'TESTECU0000000001'
      DID 0xF180: 42 4f 4f 54 2d 53 57 2d 31 2e 30 2e 30
                  'BOOT-SW-1.0.0'
```

响应共 35 字节，ISO-TP 自动分帧（`candump` 可见 `10 22 ...` 首帧与 `21`/`22` 连续帧）。

### 8.4 未知 DID

```bash
$ python client/isotp_client.py 22 FF FF
TX UDS: 22 ff ff
RX UDS: 7f 22 31
  → 否定响应：请求 SID 0x22，NRC 0x31
```

### 8.5 未实现服务

```bash
$ python client/isotp_client.py 27 01
TX UDS: 27 01
RX UDS: 7f 27 11
  → 否定响应：请求 SID 0x27，NRC 0x11
```

---

## 9. udsoncan 示例

### 9.1 实测输出

```bash
$ python client/uds_client.py all
```

```
--- 0x22 ReadDataByIdentifier ---
[1] 读取单个 DID 0xF190
    肯定响应: True
    DID 0xF190 = 'TESTECU0000000001'
[2] 一次读取多个 DID [0xF190, 0xF180]
    肯定响应: True
    DID 0xF190 = 'TESTECU0000000001'
    DID 0xF180 = 'BOOT-SW-1.0.0'

--- 0x10 DiagnosticSessionControl ---
[*] change_session(extendedDiagnosticSession)
    肯定响应: True
    会话回显: 0x03
    P2ServerMax:  0.05 s (原始值 0x0032 = 50 ms)
    P2*ServerMax: 1.0 s (原始值 0x0064 = 100 ms)

--- 0x11 ECUReset ---
    肯定响应: True
    resetType 回显: 0x01
```

### 9.2 最小代码骨架

```python
import isotp
import udsoncan
from udsoncan.client import Client
from udsoncan.connections import IsoTPSocketConnection

address = isotp.Address(
    isotp.AddressingMode.Normal_11bits,
    rxid=0x7E8,   # 收
    txid=0x7E0,   # 发
)

connection = IsoTPSocketConnection(interface="vcan0", address=address)

config = {
    "data_identifiers": {
        0xF190: udsoncan.AsciiCodec(17),   # 长度必须与 ECU 一致
        0xF180: udsoncan.AsciiCodec(13),
    }
}

with Client(connection, config=config) as client:
    # 0x10
    client.change_session(
        udsoncan.services.DiagnosticSessionControl.Session.extendedDiagnosticSession
    )

    # 0x11
    client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)

    # 0x22
    response = client.read_data_by_identifier([0xF190, 0xF180])
    print(response.service_data.values)
```

---

## 10. 常见错误

| 现象 | 原因 | 解决 |
|---|---|---|
| `OSError: vcan0 not found` / bind 失败 | vcan0 未创建或未 UP | `sudo systemctl start vcan0.service`，或 `sudo ip link add dev vcan0 type vcan && sudo ip link set vcan0 up` |
| `isotp.protocol.errors.IsoTpError` bind 失败 | 同一对 CAN ID 已被占用 | 停止另一个 ECU / 客户端进程 |
| 客户端一直 timeout | ECU 未启动，或 RX/TX ID 写反 | 确认 ECU 已启动；Tester 用 `txid=0x7E0, rxid=0x7E8` |
| `InvalidResponseException: Response data must be at least 1 bytes` | 收到否定响应，但用了解释型 API | 否定响应要用原始报文或 `raw_request` 检查 |
| `ConfigError: ... contains no definition for data identifier` | `read_data_by_identifier()` 要求 DID 预先在 `data_identifiers` 中配置 | 在 config 中补充该 DID，或改用原始报文 |
| `InvalidResponseException`（长度不符） | udsoncan 中 DID 的 codec 长度与 ECU 实际数据长度不一致 | 让 `data_identifiers` 的长度与 `did_database.py` 保持一致 |
| P2 时间显示为 `0.05` 而非 `50` | udsoncan 把原始毫秒值换算为**秒** | 原始报文是 `0x0032` = 50 ms |
| 端到端测试偶发失败 | vcan0 上有上一次实验的残留帧 | 测试已在 `setUp` 中自动清空残留帧；手动实验后重跑即可 |

---

## 11. 文件结构

```
uds-basic/
├── README.md
├── client/
│   ├── isotp_client.py      # 原始字节客户端（可传任意请求）
│   └── uds_client.py        # udsoncan 客户端（read/session/reset/all）
├── configs/
│   └── uds.yaml             # 通信参数记录
├── ecu/
│   ├── ecu.py               # 虚拟 ECU（ISO-TP 收发 + UDS 分发）
│   └── did_database.py      # DID 数据库
└── tests/
    ├── run_tests.sh         # 测试入口（unit / e2e / all）
    ├── test_ecu_unit.py     # 离线单元测试（39 用例，无需 CAN）
    └── test_e2e_vcan.py     # vcan0 端到端测试（30 用例）
```

---

## 12. 当前已验证能力

- [x] SocketCAN / vcan0 虚拟总线
- [x] Linux `CAN_ISOTP` 内核模块（`can_isotp`）
- [x] can-isotp 2.0.7 双向收发
- [x] udsoncan 1.26.1 客户端
- [x] `0x10` DiagnosticSessionControl（01/02/03 + NRC 12/13）
- [x] `0x11` ECUReset（01 + NRC 12/13）
- [x] `0x22` ReadDataByIdentifier（单 DID、多 DID + NRC 31/13）
- [x] `0x7F` 否定响应与 NRC 判定
- [x] 未实现服务统一返回 NRC 0x11
- [x] 会话状态管理与复位回归
- [x] ISO-TP 单帧 / 多帧分帧验证
- [x] 离线单元测试 39 PASS
- [x] vcan0 端到端测试 30 PASS

---

## 13. 后续扩展方向

### 13.1 协议能力

- `0x27` SecurityAccess：种子/密钥、错误密钥、尝试次数锁定（NRC 0x35/0x36/0x37）
- `0x2E` WriteDataByIdentifier：DID 写入与权限校验
- `0x19` ReadDTCInformation：DTC 数据库与状态掩码
- `0x31` RoutineControl：例程控制
- `0x3E` TesterPresent：会话保持与 S3 超时
- `0x28` CommunicationControl、`0x85` ControlDTCSetting
- `0x78` ResponsePending 与 P2* 超时处理
- 功能寻址（`0x7DF`）与多 ECU 响应
- 会话相关的服务权限（如非默认会话才允许 `0x2E`）

### 13.2 状态机与架构

- 把 `if/elif` 分发重构为**服务注册表 + 会话状态机**
- 引入权限矩阵（会话 × 安全等级 × 服务）
- DID 数据库支持数据类型（ASCII / 数值 / 位域）与缩放
- 多 ECU 拓扑（多对 CAN ID、网关模拟）

### 13.3 安全测试

- UDS Fuzzing：随机 SID / 长度 / DID 组合，监控异常与崩溃
- ISO-TP 层 Fuzzing：异常分帧、流控滥用、序号错乱
- CAN Fuzzing（配合 `labs/vehicle/can-basic`）
- 基于本测试套件建立**回归基线**，确保 Fuzzing 前后行为一致

### 13.4 工程化

- 把测试接入 `scripts/healthcheck.sh`，形成统一入口
- 建立 CI（本地 runner）自动跑离线测试
- 用 `cantools` + DBC 描述诊断报文，与 `can-basic` 实验打通
- 输出 UDS 测试报告到 `reports/`

---

## 14. 参考

- ISO 14229-1：UDS 应用层协议
- ISO 15765-2：ISO-TP 传输层协议
- ISO 15765-4：OBD-II 诊断寻址
- `configs/systemd/vcan0.service`：vcan0 自动创建
- `labs/vehicle/can-basic/README.md`：CAN / SocketCAN 基础实验
- 仓库根 `README.md`、`AGENTS.md`：项目总览与工作规范
