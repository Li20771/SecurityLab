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

### 3.1 主要文件

| 文件 | 说明 |
|---|---|
| `ecu/ecu.py` | **入口层**：SocketCAN / ISO-TP、运行时依赖、兼容 facade（不含 UDS 业务逻辑） |
| `ecu/uds_server.py` | **UDS 协议层**：`EcuState`、服务 handler、registry、dispatcher |
| `ecu/uds_types.py` | 协议常量与响应构造（零依赖） |
| `ecu/did_database.py` | DID 数据库（DID → 数据字节）与受保护 DID 标记 |
| `ecu/dtc_runtime.py` | DTC **运行时状态**写入原语（受校验入口；不新增 UDS 服务，见 3.5） |
| `ecu/keygen.py` | 教学用 seed/key 算法（**不依赖 CAN / ISO-TP**） |

各文件的完整职责边界与依赖方向见 **第 14 节 架构**。

`keygen.py` 与 `uds_types.py` 都单独成文件的原因相同：它们没有任何第三方依赖，
因此诊断客户端与测试可以只 `import` 它们而无需拖入 ISO-TP 依赖，
算法与常量也能脱离 CAN 独立测试。

### 3.2 服务分发逻辑

服务分发（dispatch）不在 `ecu.py`，而在 `ecu/uds_server.py`：

```python
# ecu/uds_server.py
def handle_uds_request(request, state, *, services=None) -> bytes:
    ...
```

它**不依赖 CAN**：输入请求字节与 ECU 状态，输出响应字节。
`ecu.py` 提供的是**入口包装**（把 0x27 所需的运行时依赖注入后转交），
因此单元测试既可以直接调用 `ecu.handle_uds_request(request)`
（见第 8 节），也可以在需要时绕过入口层直接测试协议层。
这是本实验可测试性的关键设计。

### 3.3 会话状态与安全状态

运行时状态**不再是模块级全局变量**，而是集中在一个显式对象
`uds_server.EcuState` 中，由单例 `uds_server.STATE` 持有（详见第 14.4 节）：

```python
# ecu/uds_server.py
@dataclass
class EcuState:
    session           = SESSION_DEFAULT
    security_level    = SECURITY_LOCKED
    pending_seed      = None
    seed_issued_at    = None
    seed_consumed     = False
    failed_attempts   = 0
    lockout_until     = None
    seed_counter      = 0
```

- `0x10` 切换会话时更新 `STATE.session`，并**清除解锁状态与 seed**
- `0x11 01/02/03`（hardReset / keyOffOnReset / softReset）把会话复位回
  `defaultSession`，同样清除解锁状态（三者状态等价，见 4.10）
- 被拒绝的请求**不会**改变会话状态（已由测试覆盖）
- **失败计数与锁定在会话切换 / 复位后保留**（本教学 ECU 的设计选择，见 6.4）
- `ecu.STATE` 只是 `uds_server.STATE` 的 re-export，**不是第二份状态**

### 3.4 可配置项

ECU 支持环境变量覆盖，便于多 ECU / 多通道 / 测试实验（默认值保持标准行为不变）：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `UDS_INTERFACE` | `vcan0` | CAN 接口 |
| `UDS_RX_ID` | `0x7E0` | ECU 接收 ID（Tester 发送 ID） |
| `UDS_TX_ID` | `0x7E8` | ECU 发送 ID（Tester 接收 ID） |
| `UDS_LOCKOUT_SECONDS` | `10` | SecurityAccess 锁定时长（秒） |

```bash
# 例：在 vcan1 上以 0x7E1/0x7E9 运行第二个 ECU
UDS_INTERFACE=vcan1 UDS_RX_ID=0x7E1 UDS_TX_ID=0x7E9 \
  python ecu/ecu.py
```

> 端到端测试使用 `UDS_LOCKOUT_SECONDS=2` 缩短锁定窗口，
> 因为锁定无法通过协议提前解除，测试只能等它自然到期。

### 3.5 DTC 运行时状态模型（`ecu/dtc_runtime.py`）

DTC 的三层职责**刻意分离**，互不越界：

| 层 | 位置 | 内容 | 谁能改 |
|---|---|---|---|
| 定义 | `dtc_database.DTC_DATABASE` | 出厂默认 `statusOfDTC` | 静态常量，运行时**从不**被写入 |
| 定义属性 | `dtc_database.DTC_METADATA` | `severity` / `functional_unit` | 静态常量，**没有任何服务会改它** |
| 运行时状态 | `EcuState.dtc_records` | 当前 `statusOfDTC` | 经 `dtc_runtime` 的写入原语 |

`dtc_runtime` 是**运行时 DTC 状态的唯一受校验入口**，且**不新增任何 UDS 服务**
（故障产生只经 Python API / test seam，不把它做成协议能力）：

```python
import dtc_runtime

dtc_runtime.is_registered_dtc(0x100001)            # True
dtc_runtime.set_dtc_status(state, 0x100001, 0x09)  # 产生 / 更新（受校验）→ True
dtc_runtime.get_dtc_status(state, 0x100001)        # 读回
dtc_runtime.clear_dtc_status(state, 0x100001)      # 清除（即 0x14 的写入落点）
```

| 函数 | 校验 | 受 0x85 开关约束？ | 用途 |
|---|---|---|---|
| `set_dtc_status(state, dtc, status)` | 已注册 DTC + `status ∈ [0x00, 0xFF]` | **是** | 产生 / 更新；返回是否写入了 |
| `clear_dtc_status(state, dtc)` | **不做注册校验** | 否 | 0x14 的写入落点 |
| `get_dtc_status(state, dtc)` | 已注册 DTC | 否 | 读取 |

**两个写入入口的契约差异是刻意的**：`0x14` 接受 `dtc_records` 中实际存在的
任何 key（含运行时 / 测试注入的 DTC），给它加注册校验会**改变 0x14 行为**，
故 `clear_dtc_status` 不校验；而 `set_dtc_status` 是"产生故障"的入口，必须
拒绝未注册 DTC —— **不静默创建没有 metadata 的新 DTC**。

失败语义：未注册 → `UnknownDtcError`；status 越界 → `DtcStateError`；
**两项校验都发生在任何写入之前**，被拒的调用不触碰 state。

**0x85 开关**：`EcuState.dtc_setting_enabled` 为 `False`（DTC setting OFF，见 4.11）
时 `set_dtc_status()` **不写任何东西**并返回 `False`；已有 status 冻结。
OFF 被抑制**刻意不抛异常** —— 那是正常配置状态而非错误。
`clear_dtc_status()` / `get_dtc_status()` **完全不受该开关影响**。

闭环（fault generation 只经该 Python API，不经协议）：

```text
set_dtc_status(dtc, 非零)   → DTC active
        ↓  0x19/01、02、07、08、09 观察到变化
0x14（→ clear_dtc_status）  → status 0x00，key 保留
        ↓  各报告反映清除结果（0x09 仍返回该 DTC，severity 不变）
0x11 01/02/03               → DTC 保持（故障记忆，不复位清除）
```

---

## 4. 支持的 SID

| SID | 服务 | 子功能 | 肯定响应 | 状态 |
|---|---|---|---|---|
| `0x10` | DiagnosticSessionControl | `0x01` default<br>`0x02` programming<br>`0x03` extended | `0x50` | 已实现 |
| `0x11` | ECUReset | `0x01` hardReset<br>`0x02` keyOffOnReset<br>`0x03` softReset | `0x51` | 已实现（见 4.10） |
| `0x14` | ClearDiagnosticInformation | —（`groupOfDTC` 参数） | `0x54` | 已实现（见 4.9） |
| `0x19` | ReadDTCInformation | `0x01` numberOfDTCByStatusMask<br>`0x02` DTCByStatusMask<br>`0x07` numberOfDTCBySeverityMaskRecord<br>`0x08` DTCBySeverityMaskRecord<br>`0x09` severityInformationOfDTC<br>`0x0A` supportedDTC | `0x59` | 已实现（见 4.4） |
| `0x22` | ReadDataByIdentifier | —（支持多 DID） | `0x62` | 已实现 |
| `0x27` | SecurityAccess | `0x01` RequestSeed<br>`0x02` SendKey | `0x67` | 已实现 |
| `0x2E` | WriteDataByIdentifier | —（可写 DID 见 4.3） | `0x6E` | 已实现 |
| `0x31` | RoutineControl | `0x01` Start<br>`0x02` Stop<br>`0x03` RequestResults | `0x71` | 已实现（见 4.5） |
| `0x34` | RequestDownload | —（DFI=00，ALFID=23） | `0x74` | 已实现（见 4.6） |
| `0x35` | RequestUpload | —（DFI=00，ALFID=23，≤32130 字节） | `0x75` | 已实现（见 4.7） |
| `0x36` | TransferData | —（BSC 块序） | `0x76` | 已实现（见 4.6） |
| `0x37` | RequestTransferExit | — | `0x77` | 已实现（见 4.6） |
| `0x38` | RequestFileTransfer | `0x01` addFile<br>`0x02` deleteFile<br>`0x03` replaceFile<br>`0x04` readFile<br>`0x05` readDir | `0x78` | 已实现（见 4.8） |
| `0x3E` | TesterPresent | `0x00` zeroSubFunction<br>`0x80` 抑制正响应 | `0x7E` / 无响应 | 已实现 |
| `0x85` | ControlDTCSetting | `0x01` DTC setting on<br>`0x02` DTC setting off | `0xC5` | 已实现（见 4.11） |

**尚未实现**（会返回 `NRC 0x11 ServiceNotSupported`）：

`0x06` ResumeFile、`0x39` RequestFileTransferExit、`0x19` 的其余 reportType
（03/04/05/06/0B–0E/14/15/16/1A…；07/08/09 已实现）、
`0x28` CommunicationControl 等。

### 4.1 响应格式

**0x10 DiagnosticSessionControl**

```
请求: 10 <sessionType>
响应: 50 <sessionType> <P2ServerMax:2> <P2*ServerMax:2>
```

各会话返回自己的时间参数：

| 会话 | 响应 | P2 / P2* |
|---|---|---|
| `01` defaultSession | `50 01 00 32 00 64` | 50 ms / 100 ms |
| `02` programmingSession | `50 02 00 C8 13 88` | 200 ms / 5000 ms |
| `03` extendedDiagnosticSession | `50 03 00 64 03 E8` | 100 ms / 1000 ms |

**0x11 ECUReset**

```
请求: 11 <resetType>
响应: 51 <resetType>
实际: 51 01 / 51 02 / 51 03（三个 resetType 状态等价，见 4.10）
```

**0x22 ReadDataByIdentifier（支持多 DID）**

```
请求: 22 <DID:2> [<DID:2> ...]
响应: 62 <DID:2> <data...> [<DID:2> <data...> ...]
实际: 62 F1 90 <17 字节> F1 80 <13 字节>
```

**0x27 SecurityAccess**

```
RequestSeed: 27 01
响应:        67 01 <seed:4>
实际:        67 01 85 EB DA 6B

SendKey:     27 02 <key:4>
响应:        67 02
```

**0x2E WriteDataByIdentifier**

```
请求: 2E <DID:2> <data...>
响应: 6E <DID:2>
```

写入值进入 `state.did_overrides`，随后 `0x22` 读到新值；
`0x11` hardReset 清空覆盖、恢复出厂默认值。门控顺序见 4.3 与 5.1。

**0x3E TesterPresent**

```
请求: 3E 00 → 响应: 7E 00
请求: 3E 80 → 无响应（suppressPosRspMsgIndicationBit，ecu.py 抑制空帧）
```

TesterPresent 在任意会话可用、不改变任何状态（本实验 ECU 不实现
S3* 自动退会话，故无副作用）。

### 4.2 DID 数据库

| DID | 含义 | 数据 | 长度 | 权限 |
|---|---|---|---|---|
| `0xF190` | VIN / 车辆识别号（本实验用测试值） | `TESTECU0000000001` | 17 | 公开（只读） |
| `0xF180` | Boot Software Identification | `BOOT-SW-1.0.0` | 13 | 公开（只读） |
| `0xF187` | Spare Part Number | `SPARE-PART-0001` | 15 | **受保护**（只读） |
| `0xF102` | 车辆配置名（厂商自定义教学 DID） | `DEFAULT_CONFIG` | 1–16 | **可写**（`0x2E`） |

> 真实车辆中 `0xF190` 是 VIN（17 位 ASCII），`0xF180` 是 Boot SW 版本，
> `0xF187` 是备件号。本实验使用固定测试值，便于断言。
>
> `0xF187` 是本实验的**受保护 DID**：只有在
> `extendedDiagnosticSession` 且通过 SecurityAccess 解锁后才能读取，
> 否则返回 `NRC 0x33 SecurityAccessDenied`。详见第 6 节。
>
> `0xF102` 刻意选在厂商自定义区间（避开 UDS 标准保留的 0xF18x/0xF19x），
> 是本实验唯一的**可写 DID**（`WRITABLE_DIDS`），写入见 4.3。

### 4.3 0x2E 写入权限与门控

`0x2E WriteDataByIdentifier` 只允许写 `0xF102`（长度 1–16 字节）。
门控顺序（刻意把"只读 DID"放在会话/安全检查之后，语义最清晰）：

1. 长度不足（< 4 字节）→ `NRC 0x13`
2. DID 未知 → `NRC 0x31`
3. defaultSession → `NRC 0x7F`（serviceNotSupportedInActiveSession）
4. 未解锁 → `NRC 0x33`（securityAccessDenied）
5. 已知但只读的 DID（如 VIN `0xF190`）→ `NRC 0x22`
6. 数据长度越界（非 1–16）→ `NRC 0x13`
7. 写入 `state.did_overrides`，返回 `6E F1 02`

写入后 `0x22` 读到新值（override 优先于出厂默认值）；`0x11` hardReset
清空全部 override 恢复出厂。

### 4.4 0x19 ReadDTCInformation

本实验实现六个 reportType（其余 → `NRC 0x12`）。

**wire profile（本实验刻意区别于 ISO 14229-1）**：

```
ISO 14229-1 : 59 <reportType> <DTCStatusAvailabilityMask> ...
SecurityLab : 59 <DTCStatusAvailabilityMask> ...
```

正响应第 2 字节**不是 reportType 回显**，而是 `DTCStatusAvailabilityMask`。
这是本实验**既有**的 wire contract（`01`/`02`/`0A` 自首次实现起即如此，
且被 unit + e2e 逐字节固化）。OQ5 新增的 `07`/`08`/`09` **沿用同一 profile**：
既不回头改动既有三个类型（会破坏已有 wire contract），也不在新类型上单独
引入回显（否则同一个服务会出现两套响应解析约定）。

> **去掉 reportType 只意味着少一个字段**，不代表可以改变其余字段的存在性
> 或顺序。本实验的 count 型正响应（`01` / `07`）到 `<DTCFormatIdentifier>`
> 为止，**不输出 `DTCSeverityAvailabilityMask`**；记录型正响应（`08` / `09`）
> 的每条记录字段顺序为 **`severity` → `functionalUnit` → `DTC` → `status`**。

**status 家族**：

```
19 01 <DTCStatusMask>                   → 59 <DTCStatusAvailabilityMask>
                                             <DTCFormatIdentifier>
                                             <DTCCount_H> <DTCCount_L>

19 02 <DTCStatusMask>                   → 59 <DTCStatusAvailabilityMask>
                                             [(DTC:3) <statusOfDTC>]*

19 0A                                   → 59 <DTCStatusAvailabilityMask>
                                             [(DTC:3) <statusOfDTC>]*
```

**severity 家族**（OQ5 新增）：

```
19 07 <DTCSeverityMask> <DTCStatusMask> → 59 <DTCStatusAvailabilityMask>
                                             <DTCFormatIdentifier>
                                             <DTCCount_H> <DTCCount_L>

19 08 <DTCSeverityMask> <DTCStatusMask> → 59 <DTCStatusAvailabilityMask>
                                             [<severity:1> <functionalUnit:1>
                                              <DTC:3> <statusOfDTC>]*

19 09 <DTC:3>                           → 59 <DTCStatusAvailabilityMask>
                                             <severity:1> <functionalUnit:1>
                                             <DTC:3> <statusOfDTC>
```

- `DTCStatusAvailabilityMask = 0xFF`、`DTCFormatIdentifier = 0x01`
  （ISO 14229-1 DTC format），本实验固定。
- **本实验不在正响应中输出 `DTCSeverityAvailabilityMask`**（`0xE0` 仅作为
  "本实验支持的 severity 位集合"的定义常量，见 `dtc_database.py`）。
- **severity 位定义**（本实验采用的三个有效位）：
  `0x80` checkImmediately、`0x40` checkAtNextHalt、`0x20` maintenanceOnly；
  `0x00` 表示 noSeverityAvailable。低 5 bit 不参与本实验 severity 语义。
- `0x19/01`：DTCCount = `(statusOfDTC & mask) != 0` 的 DTC 数，
  16-bit big-endian；`0x19/02` 只返回匹配项、**不含 DTCCount**；
  `0x19/0A` 报告全部支持的 DTC（不受 status 过滤）。
- `0x19/07` / `0x19/08`：两个 mask 为**逻辑与** ——
  `(severity & severityMask) != 0` **且** `(status & statusMask) != 0`。
  `0x19/07` 与 `0x19/01` **同形**（只有计数口径不同）；
  `0x19/08` 每条记录 **6 字节**，字段顺序固定为
  **`severity`(1) → `functionalUnit`(1) → `DTC`(3) → `status`(1)**。
- `0x19/09`：按 DTC 查询；未知 DTC → `NRC 0x31`（与"未知 DID / 未知 RID →
  0x31"的项目惯例一致）。
- 记录按 DTC 升序输出，顺序稳定。
- 长度规则（含 reportType 字节）：`01`/`02` 为 3，`07`/`08` 为 4，
  `09` 为 5，`0A` 为 2；不符 → `NRC 0x13`。

**数据模型（两组字段刻意分离）**：

| 存放位置 | 内容 | 谁改它 |
|---|---|---|
| `EcuState.dtc_records` | 运行时 `statusOfDTC` | `dtc_runtime` 的写入原语；`0x14` 置 `0x00`（key 保留） |
| `ecu/dtc_database.py` → `DTC_METADATA` | **定义属性** `severity` / `functional_unit` | **没有任何服务会改它** |

`severity` / `functional_unit` 是 **DTC 的定义属性**，与故障状态正交：

- `0x14` 清除某 DTC 后，`0x19/09` 仍返回该 DTC、`status` 变 `0x00`，
  而 `severity` / `functionalUnit` **保持不变**（清除的是故障，不是 DTC 的严重级别）；
- `0x11 01/02/03` 不清 DTC、也不改 metadata，`0x19/07/08/09` 结果不变。

> `DTC_METADATA` 与 `DTC_DATABASE` 的 DTC 键集合必须在导入期一致
> （`dtc_database` 内有 fail-fast 护栏），否则模块无法导入。

- DTC 静态定义在 `ecu/dtc_database.py`（3 个合成 DTC，status 与 severity
  刻意各不相同）；运行时状态在 `EcuState.dtc_records`，跨请求保持。
- **hardReset 不删除 DTC**：DTC 是故障历史记忆，与 DID override 的
  "复位即恢复出厂"语义刻意区分（unit + e2e 固化）。
- suppressPosRspMsgIndicationBit 不在本 feature 范围（后续服务框架统一处理）。

---

### 4.5 0x31 RoutineControl

本实验实现三个子功能，请求固定 4 字节（不实现 routineControlOptionRecord）：

```
31 01 <RID:2> → 71 01 <RID:2>     StartRoutine
31 02 <RID:2> → 71 02 <RID:2>     StopRoutine
31 03 <RID:2> → 71 03 <RID:2> <result:4>   RequestRoutineResults
```

- 本 feature 实现两个 synthetic routine（`ecu/routine_database.py`）：
  `0x2001` start-counter（Results 返回 Start 次数的 4 字节 big-endian）、
  `0x2002` magic-echo（Results 返回固定魔数 `31 41 59 26`）。
- 状态机：Start 进入 active（重复 Start → `NRC 0x22` 且状态不变）；
  Stop 要求 active（否则 `NRC 0x22` 且状态不变）；Results 是纯查询，
  未 Start 也可查（start-counter 初始 count=0）。
- NRC：subFunction 非 01/02/03 → `0x12`；长度 ≠ 4 → `0x13`；
  未知 RID → `0x31`。**不实现** 0x33 / 0x7E / 0x7F（权限/会话门控
  属未来 framework），正响应不携带 routineStatusRecord（最小模型）。
- 生命周期：**hardReset 清空** routine 状态（active 集合 + 计数）；
  **session 切换不影响** routine 状态；DTC（0x19）不受 routine reset
  影响（故障记忆保留，测试成对固化）。

### 4.6 0x34/0x36/0x37 Download Transfer（atomic feature）

synthetic in-memory 下载目标（**不是真实 Flash、不接触文件系统、
不执行真实刷写**）：

```
34 00 23 <ADDR:3> <SIZE:2>   → 74 20 00 80   RequestDownload
36 <BSC:1> <data...>         → 76 <BSC>      TransferData
37                           → 77            RequestTransferExit
```

- 0x34：DFI=0x00、ALFID=0x23（高半字节=sizeLength 2，低半字节=
  addressLength 3）为唯一支持值（其它 → `NRC 0x31`）；size 必须
  1..65535（0 或超限 → `0x31`；65535 = 2 字节 wire 字段最大值，
  超限护栏仅内部 helper 单测）；已有 active → `0x22`。
  `maxNumberOfBlockLength = 128` 是**完整 TransferData 请求**长度上限，
  纯 data 上限 = 126。
- 0x36：BSC 从 1 起、+1 回绕（…FF→00→01）；错序/跳号 → `0x73`；
  **完全重复上一条请求 → 幂等重传**（正响应、不追加、不推进）；
  无 active → `0x24`；越界（received 会超过 requested）→ `0x13`。
- 0x37：received < requested → `0x22`（保持 active 可继续）；
  received == requested → `77` 并**立即清理全部 transfer 状态**回
  IDLE；无 active → `0x24`。
- 生命周期：**hardReset 清空** transfer 状态（routine 清 / DID 清 /
  DTC 保留的四路语义）；**session 切换不终止** download（0x10 未改）。
- 层次区分：ISO-TP 的 FF/CF/FC 分段是传输层信封，与 UDS 层
  blockSequenceCounter 完全无关。

### 4.7 0x35 RequestUpload（synthetic in-memory source）

与 download 共享同一 8 字节请求结构（方向相反），数据源是
256 字节循环模式（`transfer_database.UPLOAD_SOURCE`，**不是真实
Flash / firmware 文件、不做 extraction**）：

```
35 00 23 <ADDR:3> <SIZE:2>   → 75 20 00 80   RequestUpload
36 <BSC:1>                   → 76 <BSC> <data≤126>   TransferData（ECU→Tester）
37                           → 77            RequestTransferExit
```

- 大小上限：`MAX_UPLOAD_SIZE = 32130 = 255 × 126`——BSC 单字节，
  单会话 ≤255 块避免回绕后“重复上一条/新块”歧义（教学模型限制）。
- 固定块契约：每块 `min(126, remaining)`（最后一块截短）；上一块
  可确定性重建，duplicate 请求（`upload_sent > 0` 且 BSC ==
  (expected−1)&0xFF）→ 幂等重发同一块，sent/expected 不变。
- 状态：5 个 `upload_*` 字段（active/address/size/expected_block/
  sent）；`upload_sent` 是唯一发送进度（duplicate 前置、块定位、
  0x37 完成判定）。初始 `36 00` → 0x73（sent==0 不判 duplicate）；
  255 块后 expected=0x00、`36 00` → 0x22（无剩余数据）。
- 互斥：download 与 upload 共享 transfer 会话槽（0x34/0x35 建立时
  互查对方 → 0x22）。hardReset 清空 upload 5 字段（五路语义）；
  session 切换不终止 upload。

### 4.8 0x38 RequestFileTransfer（synthetic in-memory 文件模型）

> **本节是本实验冻结的 project profile，不是 ISO 14229-1 的一比一实现。**
> 下面三处刻意与标准写法不同，阅读时不要按 ISO 自行"修正"。

**0x38 不引入新的传输机制**：它只负责**建立/收尾** file transfer，真正的
数据搬运完全复用既有 0x36/0x37。

| mode | 含义 | 是否建立 transfer | 数据方向 |
|---|---|---|---|
| `0x01` | addFile | ✅ download | Tester → ECU |
| `0x02` | deleteFile | ❌ **one-shot** | — |
| `0x03` | replaceFile | ✅ download | Tester → ECU |
| `0x04` | readFile | ✅ upload | ECU → Tester |
| `0x05` | readDir | ✅ upload | ECU → Tester |

`modeOfOperation` **不是 UDS 子功能字节**：非法 mode（`0x00`/`0x06`/`0xFF`…）
一律 `NRC 0x31`，**不是** `0x12`。

**请求格式**（总长度 = `pathLen + 固定尾部`）：

```
addFile/replaceFile  38 01|03 <pathLen:2> <path> <DFI:1> <fspLen:1> <sizeU:4> <sizeC:4>   = pathLen + 14
deleteFile           38 02    <pathLen:2> <path>                                            = pathLen + 4
readFile             38 04    <pathLen:2> <path> <DFI:1>                                    = pathLen + 5
readDir              38 05    <pathLen:2> <path>                                            = pathLen + 4
```

本实验固定 `DFI = 0x00`、`fspLen = 0x04`；无压缩模型要求
`sizeCompressed == sizeUncompressed`，且 `sizeCompressed == 0` 一律拒绝。

**路径约束**（本实验模型约束，不声称都是 ISO 强制限制）：ASCII 可打印
（`0x20`–`0x7E`）、非空、以 `/` 开头、≤ 64 字节；文件路径不得以 `/`
结尾；readDir 路径必须为 `/` 或以 `/` 结尾。

**肯定响应**：

```
addFile      78 01 02 00 80 00                          (6 bytes)
replaceFile  78 03 02 00 80 00                          (6 bytes)
deleteFile   78 02                                      (2 bytes，只有 mode echo)
readFile     78 04 02 00 80 00 00 04 <sizeU:4> <sizeC:4>  (16 bytes)
readDir      78 05 02 00 80 00 00 02 <dirInfoLength:2>    (10 bytes)
```

⚠️ **三处 profile 说明（务必区分）**：

1. **0x38 的 `lengthFormatIdentifier` 固定 `0x02`**，是本实验 profile 的
   取值。它**不是** 0x34/0x35 那套"高半字节 = `maxNumberOfBlockLength`
   字节数"的 ISO 编码（那里是 `0x20`）。两套编码含义不同，不可混用。
2. **readFile 响应中的 `fileSizeOrDirInfoParameterLength = 0x0004`**
   表示紧随其后的**每个 size 字段**用 4 bytes 编码（因此响应是
   8 字节头部 + 4 + 4 = 16 字节）。
3. **readDir 响应中同一字段 = `0x0002`**，表示紧随其后的
   **`dirInfoLength`** 用 2 bytes 编码（因此响应是 8 字节头部 + 2 =
   **10 字节**）。标准写法会给出 9 字节，本实验**刻意**是 10 字节。

**readDir 响应绝不携带 dirInfo 本体**：目录数据只能经 0x36/0x37 送出：

```
38 05 "/data/"   → 78 05 02 00 80 00 00 02 00 1B     (dirInfoLength = 27)
36 01            → 76 01 <dirInfo>
37               → 77
```

**dirInfo 编码**（`file_database._build_dir_info()`，纯函数、确定性）：

```
[count:2] ( [nameLen:2] <name> [size:4] )*
```

- 只含**直接**子项（不递归），`name` 是 basename；
- `name` 按 **ASCII byte order** 升序；
- 文件 `size` = uint32 big-endian；**隐式目录**条目 `size` = 0；
- 空目录合法：`count = 0` → 恰好 2 字节；
- 目录是隐式的（只作为 `state.files` 的 key 前缀存在）：**目录下文件删净后
  该隐式目录自动消失**，`/` 永远是目录；
- `len(dirInfo) ≤ 255` 是本实验约束，超限 → `NRC 0x31`。

**共享命名空间与隐式目录模型**（⚠️ **本项目的模型约束，不要当作
ISO 14229-1 的规定**）：文件名与目录名**共用同一命名空间**，因此
addFile 有两类必须拒绝的冲突：

| 冲突 | 例子 | 结果 |
|---|---|---|
| 目标名字已被占用（已存在的文件 **或** 已存在的隐式目录） | 已有 `/data/` 时 `38 01 /data` | `0x22` |
| 目标路径的某个**祖先已是文件** | 已有文件 `/a` 时 `38 01 /a/b` | `0x22` |

第二类必须拒绝的原因：否则 `/a` 会同时是"文件"与"隐式目录"，而 dirInfo
里一个名字只能带一个 `size`，两种语义无法唯一编码——`/a` 会退化成
ReadDir 列不出、却能 ReadFile 读到的**矛盾条目**（两个视图互相打架）。
与之对称：replaceFile / readFile / deleteFile 对目录路径一律 `0x31`，
readDir 对文件路径一律 `0x31`。删除父文件后，其下路径重新可创建
（隐式目录随内容变化自动出现/消失）。

**运行时状态**：本 feature 只给 `EcuState` 增加 **3 个字段**
（`file_mode` / `file_path` / `files`），**不新增任何 file 专用计数器或
缓冲**——没有 `dir_buffer`、没有 `upload_last_block`、没有
`DirectoryTransferState`、没有通用 Transfer framework。读文件复用既有
upload 5 字段，写文件复用既有 download 6 字段：

- `file_mode`：`0` = idle（走既有 0x34/0x35 行为）、`1/3` = download
  方向、`4/5` = upload 方向——它是 0x36/0x37 的**分流开关**。
- `file_path`：目标路径（只存字符串；**ReadDir 连 dirInfo 都不缓存**，
  每次 0x36 都经 `_build_dir_info()` 确定性重新生成，因此 duplicate
  重发必然逐字节一致）。
- `files`：synthetic in-memory 存储（`path → bytes`），默认复制静态
  `file_database.FILE_DATABASE`；**运行期只读写 `state.files`，静态
  FILE_DATABASE 永不被修改**，且实例之间相互隔离。

**0x36 数据源三分支**（`_upload_source_chunk()`）：

| `file_mode` | 数据源 |
|---|---|
| `4` readFile | `state.files[file_path]` 的切片 |
| `5` readDir | `_build_dir_info(files, file_path)` 的切片 |
| `0`（既有 0x35） | `transfer_database._upload_chunk()` 循环模式（行为与 4.7 **逐字节一致**） |

**0x37 收尾**：readFile/readDir 要求 `upload_sent == upload_size`（否则
`0x22`），成功后清 upload 5 字段 + `file_mode` + `file_path`。
addFile/replaceFile 要求 `len(download_buffer) == download_size`，**在清理
之前**才把 buffer 提交进 `state.files[file_path]`——即**只有 0x37 完成时
才提交**：中途 abort、传输不完整、hardReset 都到不了提交点，既不会新建
也不会覆盖目标路径。

**deleteFile 是 one-shot**：成功即 `del state.files[path]` 并返回 `78 02`，
不建立 transfer、不改动任何 counter、不保留 `file_mode`/`file_path`；
因此完成后 0x36/0x37 直接 `0x24`，而 0x34/0x35 立刻可用。

**互斥（8 向）**：0x38 复用既有 `download_active` / `upload_active` 会话槽，
所以 0x34/0x35 的 handler **零改动**即获得完整互斥：

| 方向 | 结果 |
|---|---|
| `0x38` while `0x34` active | `0x22` |
| `0x38` while `0x35` active | `0x22` |
| `0x38` while `0x38` active（含 readDir） | `0x22` |
| `0x34` while `0x38` add / replace / readFile / readDir active | `0x22` |
| `0x35` while 任一 `0x38` active | `0x22` |

**NRC 判定顺序**（严格遵守）：

```
1. 长度不足 / 取不到 pathLen:2                    → 0x13
2. mode ∉ {1,2,3,4,5}                             → 0x31（不是 0x12）
3. pathLen 超过实际 payload                       → 0x13
4. path 非法                                      → 0x31
5. mode 专属结构错（字段多 / 缺）                  → 0x13
6. 不支持的值（DFI / fspLen / size==0 /
   size > MAX_FILE_SIZE / compressed!=uncompressed）→ 0x31
7. transfer slot 忙                               → 0x22
8. 文件状态（addFile 已存在 → 0x22；
   replace/readFile/deleteFile 不存在 → 0x31；
   readDir 不是目录 → 0x31；dirInfo > 255 → 0x31）  → 0x22 / 0x31
9. 肯定响应
```

**hardReset**：清 upload 5 字段、download 6 字段、`file_mode`、
`file_path`、routine 状态、DID overrides；**保留 DTC 与 `files`**——
复位中止的是**传输**，不是已提交的数据。session 切换**不终止** file
transfer。

**边界与不做的部分**：`MAX_FILE_SIZE = 32130 = 255 × 126`（与
`MAX_UPLOAD_SIZE` 同源：单字节 BSC 决定单会话最多 255 块，超过该值的
文件无法在一次 readFile 会话中读完）。本 feature **不是真实文件系统**：
不访问磁盘、不做路径解析/符号链接/权限检查，不做真实 Flash、不做
firmware extraction、不做压缩/加密/签名校验；`0x06` ResumeFile 与
`0x39` RequestFileTransferExit 不在本 PR 范围。

### 4.9 0x14 ClearDiagnosticInformation

```
14 <groupOfDTC:3>   → 54
```

- `groupOfDTC` 为 **24-bit big-endian**，与 `0x19` 输出 DTC 的
  `(dtc>>16, dtc>>8, dtc)` 编码一致。
- 正响应**严格 1 字节 `0x54`，不回显 `groupOfDTC`**（ISO 14229-1 对该
  服务的正响应即无参数；本项目只在确有需要处声明 profile 偏离，此处无需）。
- 长度 ≠ 4（过短**或**多余字节）→ `NRC 0x13`。

**支持集合（本项目 profile）**

| `groupOfDTC` | 语义 | 结果 |
|---|---|---|
| `0xFFFFFF` | 全部 DTC | 全部 status 置 `0x00` |
| `state.dtc_records` 中实际存在的单个 DTC | 该条 | 其 status 置 `0x00` |
| 其它任何值（含 ISO 功能组 `0xFFFF00`–`0xFFFFFE`、`0x000000`、未支持 DTC） | 不支持 | `NRC 0x31` |

**清除语义：status 置 `0x00`，绝不删除 key。**

- key 集合即"支持的 DTC"集合，`0x19/0A` 报告的正是它。删 key 会让已清除
  的 DTC 从 `0x0A` 消失（与"supported ≠ 当前故障"矛盾），也会让第二次
  清除退化为 `0x31`（破坏幂等）。
- 因此 `0xFFFFFF` 是"遍历并置零"，**不是** `dict.clear()`；空库执行
  all-clear 仍成功返回 `54`。
- 清除不存在/未支持的 DTC 返回 `0x31` 而**不是** no-op 成功 —— 与
  "未知 DID → `0x31`""未知 RID → `0x31`"的项目惯例一致。
- 静态 `dtc_database.DTC_DATABASE` **永不被写入**；`dtc_records` 也不重新绑定。

**0x19 的可观察变化**（全清后）：`19 01 FF` → DTCCount `0`；
`19 02 FF` → 只回 availability mask；**`19 0A` 仍返回全部成员，只有
status 字节变 `0x00`**。

**无 session / security 门控**（项目 profile 决定）：`0x11 hardReset` 是更
彻底的破坏性操作（清 DID override 与 routine 状态）且零门控，若给 `0x14`
加门控而 `0x11` 不加则语义不自洽；`0x19` 读 DTC 亦零门控。门控抽象属
路线图中的 Permission / session framework，不在本 PR。

**⚠️ 本 feature 的边界（如实声明）**

1. `0x14` 是**当前 synthetic DTC model 的 runtime status 清除** —— 它只把
   `EcuState.dtc_records` 中条目的 status 置零，**不触碰**任何静态数据。
2. **本 ECU 没有任何 UDS 服务会置位 DTC status**：`0x85 ControlDTCSetting`
   只开关"是否允许继续更新"，本身不产生故障；产生 / 更新只能经进程内的
   `dtc_runtime.set_dtc_status()`（fault generation 不是协议能力）。因此
   真实链路只能验证"清除 → 观察 → 复位保留"三段语义，**不能**验证
   "故障发生 → DTC 置位 → 清除"。
3. **functional group 不在本 PR**：现模型 `{DTC: statusOfDTC}` 不含任何
   分组元数据，表达功能组需要新增映射结构。因此功能组取值自然落入
   `0x31`。**这是本项目 profile，不声称是 ISO 功能组语义的完整实现。**
4. `0x19` 其余 reportType / `0x28` / `0x39` / `0x06` 均不在本 PR。

**生命周期**：`0x14` 之后 `hardReset` **不恢复** status（`0x11` 刻意保留
`dtc_records` 内容）；`hardReset` 之后 `0x14` 正常工作；`0x14` 不影响
`did_overrides` / routine 状态 / transfer 字段；`EcuState` **不新增字段**。

### 4.10 0x11 ECUReset（resetType 01 / 02 / 03）

```
请求: 11 <resetType>
响应: 51 <resetType>

11 01 → 51 01      hardReset
11 02 → 51 02      keyOffOnReset
11 03 → 51 03      softReset
11 04 → 7F 11 12   enableRapidPowerShutDown（未实现）
11 05 → 7F 11 12   disableRapidPowerShutDown（未实现）
11 FF → 7F 11 12   未知 resetType
11    → 7F 11 13   缺少 resetType
```

**三个 resetType 在本实验模型中状态等价（`experiment-defined`）**：

| resetType | 状态转换 |
|---|---|
| `0x01` hardReset | ┐ |
| `0x02` keyOffOnReset | ├─ 共用同一个 reset-sensitive state transition |
| `0x03` softReset | ┘ |

区分三者需要**电源域、硬件复位深度、应用层与固件层之分、NVM controller**
等语义，本教学模型一概不具备。因此本实验**不制造人为差异** ——
这**不是 ISO 14229-1 的强制要求**，而是本 ECU 的明确设计选择，
由 `test_ecu_unit.py::TestEcuResetEquivalence` 逐字段固化为可执行断言
（27 字段快照 + 三态互比），任何"给某个 resetType 加差异"的改动都会立刻失败。

复位影响 `EcuState` 的 27 个字段中：

- **复位 22 个**：`session`、`security_level`、`pending_seed`、
  `seed_issued_at`、`seed_consumed`、`did_overrides`、`active_routines`、
  `routine_start_counts`、download 6 字段、upload 5 字段、
  `file_mode`、`file_path`、`dtc_setting_enabled`
- **保留 5 个**：`failed_attempts`、`lockout_until`、`seed_counter`
  （防复位绕过暴力破解惩罚）、`dtc_records`（故障历史记忆）、
  `files`（"非易失存储"替身）

**未实现**：`0x04` / `0x05`（快速下电），以及
`suppressPosRspMsgIndicationBit`（本实验不为 `0x11` 实现该位）。

### 4.11 0x85 ControlDTCSetting

```
请求: 85 <DTCSettingType>      严格 2 字节
响应: C5 <DTCSettingType>      **回显** DTCSettingType

85 01    → C5 01        DTC setting ON
85 02    → C5 02        DTC setting OFF
85 03    → 7F 85 12     未实现的 DTCSettingType
85 00    → 7F 85 12     （同上）
85 FF    → 7F 85 12     （同上）
85       → 7F 85 13     缺少 DTCSettingType
85 01 00 → 7F 85 13     多余字节
```

**本服务不"清 DTC"**：它控制的是 DTC status bits *是否允许继续被 runtime
mutation 更新*，本身不改变任何已有 status。

| DTCSettingType | 对 `dtc_runtime.set_dtc_status()` 的效果 |
|---|---|
| `0x01` ON（**默认**） | 正常更新 status |
| `0x02` OFF | **冻结** —— 不写入任何东西，已有 status 保持不变，函数返回 `False` |

**与 0x14 的关系（本项目 profile）**：两者**语义独立**。`0x85 02`（OFF）
**不**阻止 `0x14` 清除 DTC —— OFF 冻结的是"故障产生 / 更新"这条路径，
不是"清除"这条路径。因此 OFF 期间 `0x14` 仍可清除，`0x19` 仍能读到清除结果。

**易失性**：`EcuState.dtc_setting_enabled` 是**易失的控制模式**（不是故障
记忆），因此 `0x11 01/02/03` **恢复为上电默认 ON**。理由：若复位保留 OFF，
Tester 可让故障检测被无限期停用 —— 一个任何标准复位都清不掉的"安全相关"
状态，与 0x11 幂等复位的语义冲突。

**本服务不设 session / security 门控** —— 与 `0x11` / `0x14` / `0x19` 的
既有 zero-gated profile 一致；门控抽象属路线图中的 Permission / session
framework。`suppressPosRspMsgIndicationBit` 不在本 feature 范围。

## 5. 支持的 NRC

| NRC | 名称 | 本实验触发条件 |
|---|---|---|
| `0x11` | ServiceNotSupported | 请求了未实现的 SID |
| `0x12` | SubFunctionNotSupported | `0x10`/`0x11`/`0x27` 的子功能不支持 |
| `0x13` | IncorrectMessageLengthOrInvalidFormat | 报文长度错误（过短/过长/奇数长度/key 长度不符） |
| `0x22` | ConditionsNotCorrect | `0x27`：已解锁状态下再次 RequestSeed |
| `0x24` | RequestSequenceError | `0x27`：无 seed 就 SendKey、重放已消费 seed、seed 超时 |
| `0x31` | RequestOutOfRange | `0x22` 请求了未知 DID |
| `0x33` | SecurityAccessDenied | `0x22` 读取受保护 DID 但未解锁 |
| `0x35` | InvalidKey | `0x27`：key 错误（未达失败上限） |
| `0x36` | ExceedNumberOfAttempts | `0x27`：错误 key 使失败次数达到上限，同时进入锁定 |
| `0x37` | RequiredTimeDelayNotExpired | `0x27`：处于锁定期间，任何子功能都被拒绝 |
| `0x7F` | ServiceNotSupportedInActiveSession | `0x2E`：defaultSession 下写入（先切会话再解锁） |

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
| `11 04` / `11 05` | `7F 11 12` | resetType 未实现（快速下电） |
| `11 FF` | `7F 11 12` | 未知 resetType |
| `14` / `14 FF FF` | `7F 14 13` | 缺少 groupOfDTC（必须严格 4 字节） |
| `14 FF FF FF 00` | `7F 14 13` | 多余字节 |
| `14 FF FF FF` | `54` | 清除全部 DTC 的 status（正响应仅 1 字节） |
| `14 10 00 01` | `54` | 清除单个 DTC；已为 0 再清仍 `54`（幂等） |
| `14 10 00 09` | `7F 14 31` | 未支持的单个 DTC |
| `14 FF FF 00` | `7F 14 31` | 功能组取值（本 profile 不支持功能组） |
| `14 00 00 00` | `7F 14 31` | `0x000000` 不在支持集合 |
| `22` | `7F 22 13` | 缺少 DID |
| `22 F1` | `7F 22 13` | DID 不完整（奇数长度） |
| `22 FF FF` | `7F 22 31` | 未知 DID |
| `22 F1 90 FF FF` | `7F 22 31` | 多 DID 中任一未知则整体失败 |
| `22 F1 87`（未解锁） | `7F 22 33` | 受保护 DID 权限不足 |
| `27` | `7F 27 13` | 缺少子功能 |
| `27 01 00` | `7F 27 13` | 多余字节 |
| `27 03` / `27 05` / `27 FF` | `7F 27 12` | 子功能不支持 |
| `27 02` | `7F 27 13` | SendKey 缺少 key |
| `27 02 <3 或 5 字节>` | `7F 27 13` | key 长度不符 |
| `27 02 <key>`（无 seed） | `7F 27 24` | 请求顺序错误 |
| `27 01`（已解锁） | `7F 27 22` | 条件不满足 |
| `27 02 <错误 key>` ×1–2 | `7F 27 35` | key 错误 |
| `27 02 <错误 key>` ×3 | `7F 27 36` | 达到失败上限，进入锁定 |
| 锁定期间任意 `27 xx` | `7F 27 37` | 锁定未解除 |
| `2E`（长度 < 4） | `7F 2E 13` | 缺少 DID 或数据 |
| `2E FF FF xx` | `7F 2E 31` | DID 未知 |
| `2E F1 02 xx`（默认会话） | `7F 2E 7F` | 默认会话不支持写入 |
| `2E F1 02 xx`（未解锁） | `7F 2E 33` | 未通过 SecurityAccess |
| `2E F1 90 xx`（只读 DID） | `7F 2E 22` | 已知但只读的 DID |
| `2E F1 02` + 数据长度越界 | `7F 2E 13` | 数据不在 1–16 字节 |
| `2E F1 02 <data:1-16>`（满足门控） | `6E F1 02` | 写入成功（0x22 可见新值） |
| `3E`（长度错） | `7F 3E 13` | TesterPresent 必须恰好 2 字节 |
| `3E 01` / `3E FF` | `7F 3E 12` | 子功能不支持（bit7 剥离后非 0） |
| `3E 00` | `7E 00` | 肯定响应 |
| `3E 80` | （无响应） | 抑制正响应，ecu.py 不发帧 |
| `85` | `7F 85 13` | 缺少 DTCSettingType |
| `85 01 00` / `85 02 00` | `7F 85 13` | 多余字节 |
| `85 00` / `85 03` / `85 FF` | `7F 85 12` | DTCSettingType 未实现 |
| `85 01` | `C5 01` | DTC setting ON（开启 runtime mutation） |
| `85 02` | `C5 02` | DTC setting OFF（冻结 runtime mutation；**不**阻止 0x14） |
| `19` | `7F 19 13` | 缺少 reportType |
| `19 01` / `19 02`（缺 mask） | `7F 19 13` | 长度不足 |
| `19 01 FF 00` / `19 02 FF 00` / `19 0A 00` | `7F 19 13` | 多余字节 |
| `19 03` / `19 7F` / 其余未实现 reportType | `7F 19 12` | 子功能不支持 |
| `19 01 FF` | `59 FF 01 00 03` | 3 个 DTC 匹配（DTCCount=3） |
| `19 02 01` | `59 FF 10 00 01 01` | 只返回 status bit0 置位的 DTC |
| `19 0A` | `59 FF 10 00 01 01 10 00 02 02 10 00 03 04` | 全部支持的 DTC |
| `19 07 E0` / `19 08 E0` | `7F 19 13` | severity 家族缺 statusMask（07/08 严格 4 字节） |
| `19 07 E0 FF 00` / `19 08 E0 FF 00` | `7F 19 13` | severity 家族多余字节 |
| `19 09 10 00` / `19 09 10 00 01 00` | `7F 19 13` | 0x09 严格 5 字节 |
| `19 09 10 00 09` | `7F 19 31` | 未知 DTC（与"未知 DID/RID → 31"惯例一致） |
| `19 07 E0 FF` | `59 FF 01 00 03` | severity + status 双 mask，3 个 DTC 全匹配（同 0x19/01 形状） |
| `19 07 80 FF` | `59 FF 01 00 01` | 只有 checkImmediately 的那一个 DTC |
| `19 07 00 FF` / `19 07 E0 00` | `59 FF 01 00 00` | 任一 mask 为 0 → 无匹配（逻辑与） |
| `19 08 E0 FF` | `59 FF 80 01 10 00 01 01 …`（20 字节） | 每条记录 6 字节，顺序 severity→funcUnit→DTC→status；**超过单帧 → 走 ISO-TP MF** |
| `19 09 10 00 01` | `59 FF 80 01 10 00 01 01` | 单 DTC：severity + functionalUnit + DTC + status |
| `31 01`（长度 ≠ 4） | `7F 31 13` | 请求形态固定 4 字节 |
| `31 04 20 01` | `7F 31 12` | 子功能不支持 |
| `31 01 AA AA` | `7F 31 31` | 未知 routineIdentifier |
| `31 01 20 01`（重复 Start） | `7F 31 22` | 已 active，状态不变 |
| `31 02 20 01`（未 active） | `7F 31 22` | 未 active，状态不变 |
| `31 01 20 01` | `71 01 20 01` | Start 成功（0x2001 count+1） |
| `31 03 20 01`（Start 1 次后） | `71 03 20 01 00 00 00 01` | Results（4 字节 BE 计数） |
| `31 03 20 02` | `71 03 20 02 31 41 59 26` | magic-echo 固定结果 |
| `34`（长度 ≠ 8） | `7F 34 13` | 结构畸形 |
| `34 01 23 ...` | `7F 34 31` | DFI 不支持 |
| `34 00 32 ...` | `7F 34 31` | ALFID 不支持 |
| `34 00 23 ... 00 00` | `7F 34 31` | size=0 |
| `34 00 23 ...`（已有 active） | `7F 34 22` | 重复 RequestDownload |
| `34 00 23 <ADDR:3> <SIZE:2>` | `74 20 00 80` | 建立 download session |
| `36`（无 active） | `7F 36 24` | 序列错误 |
| `36 02 ...`（期望 1） | `7F 36 73` | BSC 错序（真正的 sequence error） |
| `36 01 ...`（完全重复上一条） | `76 01` | 幂等重传（不追加、不推进） |
| `36 <BSC> <data≤126>`（正常块） | `76 <BSC>` | 追加数据，counter+1 回绕 |
| `37`（未传完） | `7F 37 22` | 条件不满足（保持 active） |
| `37`（传完） | `77` | 成功并清理全部 transfer 状态 |
| `35`（长度 ≠ 8） | `7F 35 13` | 结构畸形 |
| `35 01 23 ...` / `35 00 32 ...` | `7F 35 31` | DFI/ALFID 不支持 |
| `35 00 23 ... 00 00` / `>32130` | `7F 35 31` | size 非法 |
| `35 00 23 ...`（任一方向已 active） | `7F 35 22` | transfer 互斥 |
| `35 00 23 <ADDR:3> <SIZE:2>` | `75 20 00 80` | 建立 upload session |
| `36 <BSC>`（上传正常块） | `76 <BSC> <data≤126>` | ECU 发送块，sent 累计 |
| `36 <BSC>`（重复上一条） | 同块重发 | 幂等（sent/expected 不变） |
| `38`（长度 < 4） | `7F 38 13` | 取不到 pathLen:2 |
| `38 00 …` / `38 06 …` / `38 FF …` | `7F 38 31` | mode 非法（**不是** `0x12`） |
| `38 05 00 05 2F`（声明 5 只有 1） | `7F 38 13` | pathLen 超过实际 payload |
| `38 05 00 04 data`（无 `/` 开头） | `7F 38 31` | path 非法 |
| `38 04 <path>/` | `7F 38 31` | 文件路径不得以 `/` 结尾 |
| `38 05 … /data`（readDir 无尾斜杠） | `7F 38 31` | readDir 路径须为 `/` 或 `/` 结尾 |
| `38 04 <path> [size 字段]` | `7F 38 13` | readFile 不允许携带 size |
| `38 01 … <DFI=01> …` | `7F 38 31` | DFI 不支持 |
| `38 01 … <fspLen≠04>` | `7F 38 31` | fileSizeParameterLength 不支持 |
| `38 01 … <sizeU=0>` / `<sizeC=0>` | `7F 38 31` | 大小非法 |
| `38 01 … <sizeC ≠ sizeU>` | `7F 38 31` | 压缩/未压缩不一致 |
| `38 01 … <sizeU=32131>` | `7F 38 31` | 超过 MAX_FILE_SIZE |
| `38 …`（任一方向 active） | `7F 38 22` | transfer 互斥（含 0x38 自身） |
| `38 01 /data/config.bin …` | `7F 38 22` | addFile 路径已存在 |
| `38 01 /data …` | `7F 38 22` | addFile 命中已存在的隐式目录 |
| `38 01 /a/b …`（已有文件 `/a`） | `7F 38 22` | addFile 的祖先已是文件（共享命名空间冲突） |
| `38 03 /nope …` | `7F 38 31` | replaceFile 路径不存在 |
| `38 04 /nope` | `7F 38 31` | readFile 路径不存在 |
| `38 04 /data` | `7F 38 31` | 目录不是文件 |
| `38 02 /nope` | `7F 38 31` | deleteFile 路径不存在 |
| `38 05 /nope/` | `7F 38 31` | readDir 路径不是目录 |
| `38 05 /`（dirInfo > 255） | `7F 38 31` | 目录信息超长 |
| `38 01|03 <path> <DFI> <fsp> <size> <size>` | `78 01|03 02 00 80 00` | 建立 download transfer |
| `38 04 /data/config.bin 00` | `78 04 02 00 80 00 00 04 <130:4> <130:4>` | 建立 upload transfer（16 字节） |
| `38 05 /data/` | `78 05 02 00 80 00 00 02 00 1B` | 建立 upload transfer（**10 字节**） |
| `38 02 /data/sub/a.txt` | `78 02` | one-shot 删除，不建立 transfer |
| `36 01`（readDir/readFile 会话） | `76 01 <dirInfo / 文件切片>` | 数据源按 `file_mode` 分流 |
| 空请求 | `7F 00 13` | 无法解析 |

**重要设计原则**：

1. 多 DID 读取时，只要有一个 DID 未知**或权限不足**，整个请求失败，
   **不返回部分数据**（避免 Tester 误解析错位的数据，也避免泄露部分信息）。
2. `0x36` **只出现在触发锁定的那一次错误 key**；此后直到锁定到期，
   所有 `0x27` 请求统一返回 `0x37`。

### 5.2 NRC 判定顺序（0x27）

实现严格按以下顺序判定，这个顺序本身也是被测试固化的行为：

```
1. 基础格式（能否取到子功能字节）   → 0x13
2. 子功能是否支持 {01, 02}          → 0x12
3. 子功能对应的长度是否正确          → 0x13
4. 是否处于锁定                      → 0x37
5. session / security 条件           → 0x22
6. 请求顺序（seed 生命周期）         → 0x24
7. key 是否正确                      → 0x35 / 0x36
8. 肯定响应                          → 0x67
```

注意第 4 步位置：**锁定检查早于顺序与 key 检查**，因此锁定期间
即使 key 正确也返回 `0x37`；但子功能/长度错误仍优先返回 `0x12`/`0x13`
（因为锁定检查在第 3 步之后）。

---

## 6. SecurityAccess（0x27）与权限模型

### 6.1 状态机

ECU 维护以下安全访问状态（`ecu/ecu.py`）：

| 状态 | 类型 | 含义 |
|---|---|---|
| `CURRENT_SECURITY_LEVEL` | `0x00` / `0x01` | 当前安全等级（锁定 / Level 1） |
| `PENDING_SEED` | `bytes` 或 `None` | 已下发但尚未使用的 seed |
| `SEED_ISSUED_AT` | `float` 或 `None` | seed 下发时刻（用于判断过期） |
| `SEED_CONSUMED` | `bool` | seed 是否已被成功消费（用于检测重放） |
| `FAILED_ATTEMPTS` | `int` | 连续失败次数 |
| `LOCKOUT_UNTIL` | `float` 或 `None` | 锁定解除时刻 |
| `SEED_COUNTER` | `int` | seed 序号，使每次 seed 不同 |

参数集中在 `SECURITY_CONFIG`，可由环境变量覆盖：

```python
SECURITY_CONFIG = {
    "max_attempts": 3,          # 连续失败上限
    "lockout_seconds": 10,      # 锁定时长（UDS_LOCKOUT_SECONDS 可覆盖）
    "seed_expiry_seconds": 5,   # seed 有效期
}
```

### 6.2 状态流转

```
                27 01 (locked)
   ┌─────────┐ ───────────────► PENDING_SEED 已设置
   │ LOCKED  │                        │
   └─────────┘                        │ 27 02 <正确 key>
        ▲                             ▼
        │                        Level 1 已解锁
        │                             │
        │  10 xx / 11 01              │
        └─────────────────────────────┘
              （会话切换或复位清除解锁）

   错误 key ×1..2  → 0x35，失败计数 +1
   错误 key ×3     → 0x36，设置 LOCKOUT_UNTIL，进入锁定
   锁定期间任意 27 → 0x37（含正确的 key 与 RequestSeed）
   锁定到期        → 失败计数清零，可重新 RequestSeed
```

### 6.3 seed / key 算法（教学用途）

算法在 `ecu/keygen.py`，**故意做成公开、可复现、无密码学强度**：

```
seed:  raw = (SEED_BASE + counter * 0x9E3779B1 + level * 0x85EBCA6B) & 0xFFFFFFFF

key:   value = seed
       重复 4 轮：
           value = (value * 0x41C64E6D + 0x3039 + SECRET) & 0xFFFFFFFF
           value ^= value >> 13
           value  = rotl32(value, 7)
       key = value ^ seed
```

⚠️ **重要说明**：

1. **不是任何真实厂商算法**。真实车辆的 seed-key 算法属于厂商机密。
2. **`SECRET = 0x5A17C0DE` 是公开的固定常数**，写在源码里。
   任何人都能算出 key —— 这是**刻意设计**，因为目的是理解协议流程与
   失败状态机，而不是提供真实保护。
3. **不具备密码学安全性**：LCG 混合可被逆向，key 比较使用普通 `==`
   （无抗时序攻击设计）。
4. 真实场景中这个模块**绝不能分发给诊断客户端**；本实验为了让客户端与
   测试能独立算出期望 key，刻意公开它。

**关于 seed/key 长度**：本实验**自行约定** seed = 4 字节、key = 4 字节。
ISO 14229-1 并未把"seed 与 key 必须等长、且必须为 4 字节"规定为普遍
强制要求；标准只要求 Tester 发送的 key 长度与 ECU 返回的 seed 长度相匹配，
具体长度由 ECU 决定（常见 2、4、8、16 字节）。
本实验选 4 字节是为了教学与测试方便：`67 01 <seed>` 共 6 字节，
可装入 ISO-TP 单帧，使 SecurityAccess 测试不必纠结分帧细节。

### 6.4 会话切换与 ECUReset 对安全状态的影响

| 事件 | 安全等级 | seed 状态 | 失败计数 / 锁定 |
|---|---|---|---|
| `0x10` 切换到任意会话 | 重置为 LOCKED | 清除 | **保留** |
| `0x11 01` hardReset | 重置为 LOCKED | 清除 | **保留** |
| `0x11 02` keyOffOnReset | 重置为 LOCKED | 清除 | **保留** |
| `0x11 03` softReset | 重置为 LOCKED | 清除 | **保留** |

**为什么会话切换必须清除解锁状态**：否则 Tester 可在
`extendedDiagnosticSession` 解锁后切回 `defaultSession` 并保持解锁，
形成真实的权限绕过漏洞。本实验用测试固化了这一语义
（`test_protected_did_denied_after_session_downgrade`）。

**为什么 `0x11 01/02/03` 保留失败计数与锁定**：防止通过复位 ECU
刷掉暴力破解的惩罚状态。三个 resetType 在本模型中状态等价（见 4.10），
因此这一安全姿态对三者必须一致 —— 否则 `11 02` 会成为标准协议级的
锁定绕过入口（尝试若干次 → 复位清零 → 继续尝试）。

> ⚠️ **这不是所有真实 ECU 都必然如此。**
> 部分真实 ECU 会在复位时清空失败计数与锁定。
> 本实验选择"保留"是**本教学 ECU 的明确设计选择**，
> 目的是演示"复位无法绕过锁定"这一安全属性，并非行业统一行为。

### 6.5 权限模型：Session → Security → Permission

```
Session（0x10 切换，0x11 复位）
    ↓  会话决定粗粒度可用性
Security Level（0x27 解锁，会话切换/复位即失效）
    ↓  安全等级决定敏感资源访问
Service Permission（本实验当前作用于 DID 级）
```

**当前实现的权限点**是 DID 级（读取走 `0x22`、写入走 `0x2E`，
两者共用同一 Session × Security 门控）：

| DID | Default + Locked | Extended + Locked | Extended + Level 1 |
|---|---|---|---|
| `0xF190`（公开） | ✅ 可读 | ✅ 可读 | ✅ 可读 |
| `0xF187`（受保护） | ❌ `0x33` | ❌ `0x33` | ✅ 可读 |

**多 DID 混合请求**：只要其中任一 DID 权限不足，**整体拒绝**并返回 `0x33`，
不返回部分数据。这样既避免 Tester 误解析错位数据，也避免泄露公开部分的
数据（测试 `test_mixed_request_denied_does_not_leak_public_data` 固化了这一点）。

**关于权限不足的 NRC 选择**：本实验返回 `0x33 SecurityAccessDenied`，
使权限语义更清晰。另一种可选设计是返回 `0x31 RequestOutOfRange`
以隐藏 DID 的存在性（防枚举探测）——两种都有真实 ECU 采用，
本实验刻意选择 `0x33`。

**`0x2E` 已沿用同一模型**（见 4.3：会话门控 `0x7F` + 安全门控 `0x33` +
只读 DID `0x22`）；后续新增写类服务（如 `0x31`）继续沿用：
服务级检查"会话是否允许 + 安全等级是否足够"，权限不足返回 `0x33`，
会话不允许则返回 `0x7F ServiceNotSupportedInActiveSession`。

---

## 7. 启动方法

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

## 8. 测试方法

### 8.1 一键运行全部测试

```bash
cd labs/vehicle/uds-basic
bash tests/run_tests.sh
```

可只运行其中一类：

```bash
bash tests/run_tests.sh unit   # 只跑离线单元测试
bash tests/run_tests.sh e2e    # 只跑 vcan0 端到端测试
```

当前测试规模（2026-09-23 实测，`bash tests/run_tests.sh all`；**Local verified**）：

| 测试 | 数量 | 需要 vcan0 |
|---|---|---|
| `tests/test_ecu_unit.py` | 116 | 否 |
| `tests/test_keygen_unit.py` | 34 | 否 |
| `tests/test_services_extra_unit.py` | 17 | 否 |
| `tests/test_gateway_unit.py` | 13 | 否 |
| `tests/test_dtc_unit.py` | 115 | 否 |
| `tests/test_dtc_clear_unit.py` | 39 | 否 |
| `tests/test_routine_unit.py` | 25 | 否 |
| `tests/test_transfer_unit.py` | 40 | 否 |
| `tests/test_upload_unit.py` | 47 | 否 |
| `tests/test_file_transfer_unit.py` | 174 | 否 |
| `tests/test_e2e_vcan.py` | 69 | 是 |
| `tests/test_isotp_options_vcan.py` | 14 | 是 |
| `tests/test_write_did_vcan.py` | 8 | 是 |
| `tests/test_multi_ecu_vcan.py` | 4 | 是 |
| `tests/test_dtc_vcan.py` | 21 | 是 |
| `tests/test_dtc_clear_vcan.py` | 11 | 是 |
| `tests/test_routine_vcan.py` | 11 | 是 |
| `tests/test_transfer_vcan.py` | 18 | 是 |
| `tests/test_upload_vcan.py` | 16 | 是 |
| `tests/test_file_transfer_vcan.py` | 41 | 是 |
| **合计** | **833**（unit 620 + e2e 213） | — |

> 表中数字为 **Local verified**（本机 `run_tests.sh all` 实测）。
> GitHub CI 的 fast job 只覆盖 ruff / mypy / **unit** / can-tools offline；
> **e2e（需 vcan0）不在 CI 内**，因此 213 / 833 不能标为 CI verified。

### 8.2 离线单元测试（不需要 CAN）

```bash
/home/lilike/.venvs/network/bin/python tests/test_ecu_unit.py
```

直接调用 `handle_uds_request()`，覆盖 **106 个用例**：

- `0x10` 各会话、异常子功能、长度错误、状态不被破坏、各会话 P2/P2*
- `0x11` 正常复位、复位后会话回归、不支持 resetType、长度错误
- `0x22` 单 DID、多 DID、顺序保持、未知 DID、奇数长度
- `0x27` RequestSeed / SendKey 全部路径（见下）
- `0x2E` / `0x3E` 已实现：默认会话写 DID 走门控返回 `0x7F`、
  `3E 00` 返回 `7E 00`（更多细节在 `test_services_extra_unit.py`）
- 未实现服务（`0x31`/未知 SID）统一返回 NRC 0x11
- 受保护 DID 的四象限权限矩阵与整体拒绝
- 否定响应结构恒为 3 字节
- DID 数据库约束

`0x27` 的单元测试细分为五个类：

| 测试类 | 内容 |
|---|---|
| `TestSecurityAccessRequestSeed` | seed 格式、长度、非零、每次变化、旧 seed 失效 |
| `TestSecurityAccessSendKey` | 正确 key 解锁、错误 key、全 0 key、跨 seed key |
| `TestSecurityAccessSequence` | 无 seed、重放、超时（注入时钟，不 sleep） |
| `TestSecurityAccessAttemptLimit` | 35/35/36 序列、锁定、锁定到期 |
| `TestSecurityAccessValidation` | 子功能、长度、NRC 优先级 |
| `TestSecurityStateTransitions` | 会话切换 / 复位对安全状态的影响 |
| `TestProtectedDidPermission` | Session × Security 权限矩阵 |

**keygen 算法独立测试**（`tests/test_keygen_unit.py`，34 个用例）：

```bash
/home/lilike/.venvs/network/bin/python tests/test_keygen_unit.py
```

覆盖 seed 确定性与变化性、key 派生、雪崩效应、secret 参与运算、
非法输入处理，并验证 `keygen.py` **不依赖 CAN / ISO-TP**。

特点：**不需要 vcan0、不需要 root、可在任意机器运行**，适合作为 CI 基线。

**关于时钟注入**：seed 过期与锁定到期依赖时间，测试通过
`unittest.mock.patch("ecu._now", ...)` 注入假时钟，
因此**不需要 sleep 10 秒**，且完全确定。

### 8.3 vcan0 端到端测试

```bash
/home/lilike/.venvs/network/bin/python tests/test_e2e_vcan.py
```

该测试会**自动启动和关闭**虚拟 ECU 子进程，覆盖 **65 个用例**：

| 测试类 | 内容 |
|---|---|
| `TestRawIsotp` | 原始字节级请求/响应断言（含全部 NRC 路径） |
| `TestIsotpFraming` | 用 `python-can` 抓包验证 SF / FF / CF 分帧 |
| `TestUdsoncanClient` | `change_session` / `ecu_reset` / `read_data_by_identifier` |
| `TestSecurityAccessHappyPath` | seed/key 正常流程 + `unlock_security_access()` |
| `TestSecurityAccessFailures` | 错误 key、尝试上限（错误 NRC 走 raw 路径） |
| `TestSecurityAccessErrors` | 顺序错误、子功能不支持、malformed |
| `TestSecurityStateInteraction` | 会话切换 / 复位后重新锁定、重放被拒 |
| `TestProtectedDidPermission` | 受保护 DID 的端到端权限行为 |
| `TestSecurityAccessLockoutTrigger` | 35/35/36 触发锁定（必须最后执行） |
| `TestSecurityAccessLockout` | 锁定期间的全部行为 |
| `TestEcuProcess` | ECU 进程存活与日志内容 |

**测试隔离策略**（这部分对理解输出很重要）：

- **每个测试类启动一个全新的 ECU 进程**。原因：失败计数与锁定是
  进程内状态且无法通过标准协议清除，重启进程是唯一不改变协议语义的隔离方式。
- **每个测试开始前把 ECU 切回 `defaultSession`**，清除"已解锁"状态。
- 测试端用 `_note_wrong_key()` 自行记账失败次数——因为 ECU 的
  `FAILED_ATTEMPTS` 无法通过标准协议读回。
- 锁定测试类放在文件末尾，且 `WAIT_FOR_LOCKOUT_CLEAR = False`，
  以保留它们要断言的锁定状态。
- e2e 使用 `UDS_LOCKOUT_SECONDS=2` 缩短锁定窗口（ECU 默认仍是 10 秒），
  使"等待锁定到期"的代价可接受。

> 注意：端到端测试会占用 `0x7E0`/`0x7E8`。
> 运行前请停止手动启动的 ECU（`pgrep -af ecu.py` 确认），
> 否则手动 ECU 会与测试的 ECU 争抢同一对 CAN ID，
> 导致大量难以定位的随机失败。

### 8.4 手动客户端

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

### 8.5 用 can-utils 旁路观察

另开终端可看到真实的 CAN 帧（含 ISO-TP 分帧）：

```bash
candump vcan0
candump -ta vcan0        # 带时间戳
```

---

## 9. 原始报文示例

以下为在 vcan0 上**实测**的输出。

### 9.1 0x10 切换会话

```bash
$ python client/isotp_client.py 10 01
TX UDS: 10 01
RX UDS: 50 01 00 32 00 64
  → DiagnosticSessionControl 肯定响应，会话 0x01
```

### 9.2 0x11 ECUReset

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

### 9.3 0x22 多 DID 读取（触发 ISO-TP 多帧）

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

### 9.4 未知 DID

```bash
$ python client/isotp_client.py 22 FF FF
TX UDS: 22 ff ff
RX UDS: 7f 22 31
  → 否定响应：请求 SID 0x22，NRC 0x31
```

### 9.5 0x2E / 0x3E（已实现）

```bash
# 0x2E 写入需要：非默认会话 + 已解锁 + 可写 DID
$ python client/isotp_client.py 10 03          # extendedDiagnosticSession
$ python client/isotp_client.py 27 01          # 取 seed，再用 keygen 派生 key
$ python client/isotp_client.py 27 02 <key>
$ python client/isotp_client.py 2E F1 02 43 4F 4E 46 49 47
TX UDS: 2e f1 02 43 4f 4e 46 49 47
RX UDS: 6e f1 02
  → WriteDataByIdentifier 肯定响应；随后 22 F1 02 读回新值

# 默认会话下写入 → 门控拒绝
$ python client/isotp_client.py 2E F1 02 00
RX UDS: 7f 2e 7f
  → 否定响应：NRC 0x7F（默认会话不支持该服务）

# 0x3E TesterPresent
$ python client/isotp_client.py 3E 00
TX UDS: 3e 00
RX UDS: 7e 00
  → 肯定响应；3E 80 抑制正响应（总线上无应答帧）
```

### 9.6 未实现服务

```bash
$ python client/isotp_client.py 19 01 08
TX UDS: 19 01 08
RX UDS: 7f 19 11
  → 否定响应：请求 SID 0x19（ReadDTCInformation），NRC 0x11
```

### 9.7 0x27 SecurityAccess

请求 seed：

```bash
$ python client/isotp_client.py 27 01
TX UDS: 27 01
RX UDS: 67 01 85 eb da 6b
```

用 seed 算出 key 后发送（key 由 `ecu/keygen.py` 派生）：

```bash
$ python -c "import sys; sys.path.insert(0,'ecu'); import keygen; \
print(keygen.derive_key(bytes.fromhex('85ebda6b')).hex(' '))"
2a 14 e4 82

$ python client/isotp_client.py 27 02 2A 14 E4 82
TX UDS: 27 02 2a 14 e4 82
RX UDS: 67 02
  → SecurityAccess 肯定响应（Level 1 已解锁）
```

错误 key：

```bash
$ python client/isotp_client.py 27 02 00 00 00 00
RX UDS: 7f 27 35
  → 否定响应：请求 SID 0x27，NRC 0x35 (InvalidKey)
```

受保护 DID：

```bash
# 未解锁
$ python client/isotp_client.py 22 F1 87
RX UDS: 7f 22 33
  → 否定响应：请求 SID 0x22，NRC 0x33 (SecurityAccessDenied)

# 解锁后（先 10 03，再 27 01 / 27 02）可读
$ python client/isotp_client.py 22 F1 87
RX UDS: 62 f1 87 53 50 41 52 45 2d 50 41 52 54 2d 30 30 30 31
  → DID 0xF187 = 'SPARE-PART-0001'
```

> 上述 seed / key 数值来自实测。注意 **seed 每次都不同**
> （由 `SEED_COUNTER` 驱动），所以直接照抄上面的 key 不会成功，
> 必须针对当次返回的 seed 重新派生 key。

---

## 10. udsoncan 示例

### 10.1 实测输出

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

### 10.2 最小代码骨架

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
        0xF187: udsoncan.AsciiCodec(15),   # 受保护 DID
    },
    # udsoncan 在 unlock_security_access() 中调用本函数派生 key。
    # 这里复用与虚拟 ECU 相同的教学算法（真实场景不应把算法放到客户端）。
    "security_algo": lambda seed, level: keygen.derive_key(seed),
}

with Client(connection, config=config) as client:
    # 0x10：受保护 DID 需要非默认会话
    client.change_session(
        udsoncan.services.DiagnosticSessionControl.Session.extendedDiagnosticSession
    )

    # 0x27：自动完成 RequestSeed -> 计算 key -> SendKey
    client.unlock_security_access(1)

    # 0x22：解锁后读取受保护 DID
    print(client.read_data_by_identifier(0xF187).service_data.values)

    # 0x11
    client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)
```

> 关于错误 NRC：`udsoncan` 在收到否定响应时会抛
> `NegativeResponseException`，并可能在解释响应数据时再抛
> `InvalidResponseException`。因此**错误 NRC 的断言建议走原始报文路径**
> （`open_isotp_socket()` + `send/recv`，或自定义客户端的 `raw_request()`），
> 不要强行让 udsoncan 解释一个它认为非法的响应。

---

## 11. 常见错误

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
| **测试大面积随机失败** | **有一个手动启动的 ECU 仍在运行**，与测试的 ECU 争抢 `0x7E0`/`0x7E8` | `pgrep -af ecu.py` 确认并停止手动 ECU 后再跑测试 |
| `27 01` 返回 `7F 27 37` | ECU 处于锁定状态（连续 3 次错误 key 触发，ECU 默认锁定 10 秒） | 等待锁定到期（默认 10 秒），或重启 ECU 进程 |
| `27 01` 返回 `7F 27 22` | ECU **已经解锁**，此时再请求 seed 无意义 | 正常现象。会话切换或 `11 01` 会重新锁定 |
| `27 02` 返回 `7F 27 24` | 没有有效 seed：未请求、已消费（重放）、或 seed 超过 5 秒过期 | 重新发送 `27 01` 获取新 seed，并在 5 秒内 SendKey |
| 照抄 README 里的 key 却失败 | **seed 每次都不同**，key 必须针对当次 seed 重新派生 | 用 `keygen.derive_key(seed)` 计算当前 seed 的 key |
| `22 F1 87` 返回 `7F 22 33` | 受保护 DID 需要 extended session + 已解锁 | 先 `10 03`，再 `27 01` / `27 02` 解锁 |

---

## 12. 文件结构

```
uds-basic/
├── README.md
├── client/
│   ├── isotp_client.py      # 原始字节客户端（可传任意请求）
│   └── uds_client.py        # udsoncan 客户端（read/session/reset/all）
├── configs/
│   └── uds.yaml             # 通信参数记录
├── ecu/
│   ├── ecu.py               # 入口层：transport / runtime 依赖 / 兼容 facade
│   ├── uds_server.py        # 协议层：EcuState / handlers / registry / dispatcher
│   ├── uds_types.py         # 协议常量 + 响应构造（零依赖）
│   ├── did_database.py      # DID 数据库（受保护/可写 DID 标记）
│   ├── dtc_database.py      # DTC 静态数据库（合成 DTC + mask/format 常量 + DTC_METADATA）
│   ├── dtc_runtime.py       # DTC 运行时状态写入原语（受校验入口）
│   ├── file_database.py     # 0x38 文件模型（FILE_MODE_*/出厂文件/dirInfo 编码）
│   ├── routine_database.py   # Routine 静态数据库（0x2001/0x2002 定义）
│   ├── transfer_database.py   # Download/Upload 静态配置（DFI/ALFID/上限/上传源）
│   ├── gateway.py           # 多 ECU 路由（VirtualEcu / DiagnosticGateway）
│   └── keygen.py            # 教学用 seed/key 算法（无 CAN 依赖）
└── tests/
    ├── run_tests.sh             # 测试入口（unit / e2e / all）
    ├── test_ecu_unit.py         # 离线单元测试（105 用例，无需 CAN）
    ├── test_keygen_unit.py      # keygen 算法测试（34 用例，无需 CAN）
    ├── test_services_extra_unit.py  # 0x2E/0x3E 单元测试（17 用例，无需 CAN）
    ├── test_gateway_unit.py     # 多 ECU 路由测试（13 用例，无需 CAN）
    ├── test_dtc_unit.py         # 0x19 单元测试（30 用例，无需 CAN）
    ├── test_dtc_clear_unit.py   # 0x14 单元测试（39 用例，无需 CAN）
    ├── test_routine_unit.py     # 0x31 单元测试（25 用例，无需 CAN）
    ├── test_transfer_unit.py    # 0x34/36/37 单元测试（40 用例，无需 CAN）
    ├── test_upload_unit.py      # 0x35 单元测试（47 用例，无需 CAN）
    ├── test_file_transfer_unit.py   # 0x38 单元测试（174 用例，无需 CAN）
    ├── test_e2e_vcan.py         # vcan0 端到端测试（65 用例）
    ├── test_isotp_options_vcan.py   # ISO-TP 传输参数矩阵（14 用例，需 vcan0）
    ├── test_write_did_vcan.py   # 0x2E/0x3E 真实解锁写入（8 用例，需 vcan0）
    ├── test_multi_ecu_vcan.py   # 三 ECU 物理寻址（4 用例，需 vcan0）
    ├── test_dtc_vcan.py         # 0x19 真实链路（8 用例，需 vcan0）
    ├── test_dtc_clear_vcan.py   # 0x14 真实链路（11 用例，需 vcan0）
    ├── test_routine_vcan.py     # 0x31 真实链路（11 用例，需 vcan0）
    ├── test_transfer_vcan.py    # 0x34/36/37 真实链路（17 用例，需 vcan0）
    ├── test_upload_vcan.py      # 0x35 真实链路（16 用例，需 vcan0）
    └── test_file_transfer_vcan.py   # 0x38 真实链路（41 用例，需 vcan0）
```

> 注意：`ecu.py` 不是 UDS 业务实现层。协议实现位于 `uds_server.py`，
> 二者边界见第 14 节。

---

## 13. 当前已验证能力

- [x] SocketCAN / vcan0 虚拟总线
- [x] Linux `CAN_ISOTP` 内核模块（`can_isotp`）
- [x] can-isotp 2.0.7 双向收发
- [x] udsoncan 1.26.1 客户端（含 `unlock_security_access()`）
- [x] `0x10` DiagnosticSessionControl（01/02/03 + 各自 P2/P2* + NRC 12/13）
- [x] `0x11` ECUReset（01/02/03，三者状态等价 + NRC 12/13）
- [x] `0x22` ReadDataByIdentifier（单 DID、多 DID + NRC 31/13）
- [x] `0x27` SecurityAccess（RequestSeed / SendKey 全路径）
- [x] `0x2E` WriteDataByIdentifier（门控链：13→31→7F→33→22→13→6E）
- [x] `0x3E` TesterPresent（`3E 00→7E 00`；`3E 80` 抑制；非法子功能 12；长度错 13）
- [x] `0x19` ReadDTCInformation（reportType **01/02/07/08/09/0A**；mask 过滤、
  稳定排序、DTCCount 16-bit BE；severity 记录字段序为
  `severity→functionalUnit→DTC→status`；非法 reportType 12、长度错 13）
- [x] `0x31` RoutineControl（Start/Stop/RequestResults；0x2001 start-counter +
  0x2002 magic-echo；状态机 22、未知 RID 31、长度 13、子功能 12）
- [x] routine 生命周期：hardReset 清空 routine 状态、session 切换保留、
  DTC 故障记忆不受影响（unit + e2e 固化）
- [x] `0x34/0x36/0x37` Download Transfer（synthetic in-memory 目标；
  DFI=00/ALFID=23；BSC 块序 + 幂等重传 + 回绕；size accounting 防越界；
  0x37 完整性检查 + 状态清理）
- [x] transfer 生命周期：hardReset 中止下载、session 切换保持 active、
  四路 reset 语义（transfer/routine/DID 清、DTC 保留）
- [x] `0x35` RequestUpload（synthetic 256 字节循环源；固定块契约
  min(126,remaining) + 上一块确定性重建；duplicate 幂等重发；
  32130 上限消除 BSC 回绕歧义；0x36/0x37 方向分流、download 路径不变）
- [x] 方向互斥：0x34/0x35 共享 transfer 会话槽（四向 0x22 测试固化）；
  hardReset 五路语义（upload/download/routine/DID 清、DTC 保留）
- [x] `0x38` RequestFileTransfer（mode 1–5：addFile/deleteFile/replaceFile/
  readFile/readDir；synthetic in-memory 文件模型，**不是真实文件系统**；
  只新增 `file_mode`/`file_path`/`files` 三个状态字段，**无任何 file 专用
  计数器或缓冲**；readDir 不缓存 dirInfo，每次经 `_build_dir_info()`
  确定性重算）
- [x] 0x38 复用既有 transfer 槽：0x36 数据源三分支（文件 / dirInfo /
  既有 `_upload_chunk`）、0x37 收尾按 `file_mode` 分流；addFile/replaceFile
  **只在 0x37 完成时提交** `files`（abort/incomplete/hardReset 不污染）
- [x] 8 向互斥矩阵（0x38 ↔ 0x34/0x35/0x38；readDir 计为 active），
  0x34/0x35 handler 零改动；hardReset 清 transfer + file 会话标识
  但**保留 files**（与保留 DTC 同侧）
- [x] DTC 模型：静态 `dtc_database.py` + `EcuState.dtc_records` 运行时状态；
  hardReset 保留 DTC（故障记忆语义，unit + e2e 固化）
- [x] `0x14` ClearDiagnosticInformation（synthetic DTC model 的 runtime
  status 清除：`0xFFFFFF` 全清 / 单个 DTC；**status 置零不删 key**，
  因此 `0x19/0A` 仍报告全部成员；未支持 DTC 与功能组取值 → `0x31`；
  无 session/security 门控；`EcuState` 零新增字段，0x11/0x19 零改动）
- [x] `ecu/dtc_runtime.py`（OQ6-A）：DTC 运行时状态的**唯一受校验写入入口**
  （已注册 DTC + status 单字节范围校验；未知 DTC **不静默创建**；
  `get_dtc_status()` 对"已注册但未写入"返回 `0x00` 而非 KeyError）；
  **不新增任何 UDS 服务** —— fault generation 只经 Python API / test seam
- [x] `0x85` ControlDTCSetting（OQ6-B）：`85 01 → C5 01` / `85 02 → C5 02`
  （byte-exact 回显）；`0x85 02` 冻结 `dtc_runtime.set_dtc_status()` 的后续
  更新（返回 `False`、已有 status 保持不变）；**不阻止 `0x14` 清除**；
  无 session/security 门控、不处理 suppressPosRspMsgIndicationBit；
  `0x11 01/02/03` 将其恢复为上电默认 ON
- [x] `0x7F` 否定响应与 NRC 判定（0x11/0x12/0x13/0x22/0x24/0x31/0x33/0x35/0x36/0x37/0x7F）
- [x] 未实现服务统一返回 NRC 0x11
- [x] 会话状态管理与复位回归
- [x] 安全状态管理：失败计数、尝试上限锁定、seed 过期、防重放
- [x] 会话切换 / ECUReset 清除解锁状态（防权限绕过）
- [x] 受保护 DID 的 Session × Security 权限矩阵与整体拒绝
- [x] 教学用 seed/key 算法（公开常数、可复现、雪崩效应）
- [x] ISO-TP 单帧 / 多帧分帧验证
- [x] 多 ECU 拓扑（`ecu/gateway.py`）：物理寻址三 ECU、功能寻址广播、
  状态隔离、构造护栏（13 单元 + 4 e2e）
- [x] 显式 `EcuState`（27 字段）与单一事实源 `STATE`
- [x] 静态服务 registry + 协议层 dispatcher
- [x] transport / protocol 分层与单向依赖（`uds_server` 不反向依赖 `ecu`）
- [x] 离线单元测试 620 PASS（116 ECU + 34 keygen + 17 services-extra + 13 gateway + 115 dtc + 39 dtc-clear + 25 routine + 40 transfer + 47 upload + 174 file-transfer）
- [x] vcan0 端到端测试 213 PASS（69 e2e + 14 isotp-matrix + 8 write-did + 4 multi-ecu + 21 dtc + 11 dtc-clear + 11 routine + 18 transfer + 16 upload + 41 file-transfer）
- [x] **三轮连续稳定性验证全部通过**（见第 14.7 节）

---

## 14. 架构（Architecture）

> 本节描述 Phase 3 重构后的**实际**分层与依赖边界，与当前代码一一对应。

### 14.1 分层职责

| 层 | 文件 | 职责 | 不负责 |
|---|---|---|---|
| Entry / Transport | `ecu.py` | CAN / ISO-TP 收发、进程入口、运行时依赖持有、兼容 facade | UDS service business logic |
| Protocol | `uds_server.py` | 状态、service handlers、registry、dispatcher | CAN / ISO-TP transport |
| Protocol Types | `uds_types.py` | session / security / NRC 常量、response builders | runtime state |
| DID Data | `did_database.py` | DID 数据与受保护 DID 标记 | transport、协议逻辑 |
| Security Algorithm | `keygen.py` | seed / key 计算 | UDS dispatch、状态管理 |

### 14.2 依赖方向

```text
ecu.py  ──>  uds_server.py  ──>  uds_types.py
   │              │           ├─> did_database.py
   │              │           └─> keygen.py
   │              └──> keygen.py / uds_types.py / did_database.py
   └──> uds_types.py / did_database.py / isotp / os / time

uds_types.py / did_database.py / keygen.py  ──> （零依赖叶子模块）
```

关键约束（已由静态检查验证）：

- `uds_server.py` **不 import `ecu.py`**（无反向依赖、无循环）
- `uds_server.py` 单独 import 时不会拉入 `ecu` / `isotp` / `can`
- `uds_server.py` **不含任何 transport 代码**：无 `import socket/can/isotp`、
  无 `RX_ID` / `TX_ID` / `INTERFACE` / `os.environ`

### 14.3 `ecu.py`：入口层（不是 UDS 业务实现层）

`ecu.py` 当前承担四类职责：

**① transport / entry**

```text
INTERFACE / RX_ID / TX_ID / _env_int()
main()                          —— ISO-TP socket 绑定与收发生命周期
```

**② runtime dependency ownership**

```text
_now()                          —— 时钟（唯一 patch 目标）
SECURITY_CONFIG                 —— 安全访问参数（含 UDS_LOCKOUT_SECONDS 派生）
```

这两项的职责是**持有并在进入协议层时转交**；它们不参与任何 UDS service 判定。
`SECURITY_CONFIG` 以**原对象引用**传入协议层，不作 `dict()` / `.copy()` 快照。

**③ 0x27 request-time dependency injection**

```text
_security_services()            —— 每次请求现场构造 SecurityAccess handler
```

`service_security_access()` 是纯函数，需要 `now` 与 `security_config` 两项运行时依赖。
`_security_services()` 在**每次请求时**求值 `_now()` 并绑定进 handler，从而：

- 保持测试中 `mock.patch.object(ecu, "_now", ...)` 的时钟注入语义仍然生效；
- 避免 `uds_server.py` 反向依赖 `ecu.py`；
- `uds_server._make_security_access_handler()` 只是**依赖绑定工厂**
  （接收已求值的 `now` 与配置本体），**不是通用 transport 组件**。

**④ compatibility facade**（见 14.5）

> ⚠️ 注意：`ecu.py` 中**没有** service handler、没有 registry、
> 没有 dispatch 分支、没有 NRC / response 组装。
> 全部 UDS 业务逻辑位于 `uds_server.py`。

### 14.4 `uds_server.py`：UDS 协议层

| 组成 | 说明 |
|---|---|
| `EcuState` | 运行时状态对象，**恰好 23 个字段**（见下） |
| `STATE` | 默认单例，**唯一事实源** |
| `reset_security_state(state)` | 清除解锁与 seed 生命周期，保留失败计数与锁定 |
| `required_security_level(did)` | DID → 所需安全等级 |
| `service_diagnostic_session_control` | 0x10 handler |
| `service_ecu_reset` | 0x11 handler |
| `service_read_data_by_identifier` | 0x22 handler（含 DID 级权限与 all-or-nothing） |
| `service_security_access` | 0x27 handler（纯函数，依赖以 keyword-only 传入） |
| `_make_security_access_handler(now, security_config)` | 0x27 依赖绑定工厂 |
| `service_write_data_by_identifier` | 0x2E handler（门控链见 4.3，写入 `did_overrides`） |
| `service_tester_present` | 0x3E handler（`3E 80` 返回空字节由入口层抑制发帧） |
| `service_clear_diagnostic_information` | 0x14 handler（`groupOfDTC` 校验 + status 置零，见 4.9；`EcuState` 零新增字段） |
| `service_read_dtc_information` | 0x19 handler（reportType 01/02/07/08/09/0A；status 只读 `dtc_records`，severity 只读 `DTC_METADATA`，见 4.4） |
| `service_routine_control` | 0x31 handler（Start/Stop/Results 状态机，见 4.5） |
| `service_request_download` | 0x34 handler（DFI/ALFID 校验 + 建立 download session，见 4.6；含 upload 互斥 guard） |
| `service_request_upload` | 0x35 handler（DFI/ALFID/size 校验 + 建立 upload session，见 4.7） |
| `service_transfer_data` | 0x36 handler（顶层方向分流：upload 发送块 / download 既有逻辑） |
| `service_request_transfer_exit` | 0x37 handler（顶层方向分流：upload 以 sent 判定 / download 既有逻辑；按 `file_mode` 决定是否提交 `files`） |
| `service_request_file_transfer` | 0x38 handler（mode 1–5；九步 NRC 优先级；建立/收尾 file transfer，见 4.8） |
| `_upload_source_chunk(state, position, length)` | 0x36 upload 分支的数据源三分支（文件 / dirInfo / 既有循环模式） |
| `SERVICES` | **静态** registry（0x10 / 0x11 / **0x14** / 0x19 / 0x22 / 0x2E / 0x31 / 0x34 / 0x35 / 0x36 / 0x37 / 0x38 / 0x3E） |
| `handle_uds_request(request, state, *, services=None)` | 协议层 dispatcher |

**`EcuState` 的 26 个字段**（当前实现，不多不少）：

```text
session          当前诊断会话
security_level   当前安全等级
pending_seed     已下发未使用的 seed
seed_issued_at   seed 下发时刻
seed_consumed    seed 是否已被成功消费（防重放）
failed_attempts  连续失败次数
lockout_until    锁定解除时刻
seed_counter     seed 序号
did_overrides    0x2E 写入的 DID 运行时覆盖（hardReset 清空）
dtc_records      0x19 的运行时 DTC 状态（hardReset **保留**，故障记忆）
active_routines  0x31 active 状态集合（hardReset 清空，session 切换保留）
routine_start_counts 0x31 start-counter 计数（hardReset 清空）
download_active  0x34/36/37 download 会话是否进行中
download_address 0x34 memoryAddress metadata
download_size    0x34 声明的下载总长（完整性/防越界依据）
download_expected_block 期望的下一个 BSC（1 起，&0xFF 回绕）
download_buffer  合成内存缓冲（received = len(buffer) 推导）
download_last_block_request 上一条被接受的完整 0x36 请求（幂等重传判定）
upload_active    0x35 upload 会话是否进行中（与 download 互斥）
upload_address   0x35 memoryAddress metadata
upload_size      0x35 声明的上传总长（1..32130）
upload_expected_block 期望的下一个 BSC（1 起，&0xFF）
upload_sent      已发送字节数（唯一进度：duplicate 前置/块定位/0x37 完成判定）
file_mode        0x38 modeOfOperation（0=IDLE；1/3=download、4/5=upload 分流开关）
file_path        0x38 目标路径（只存字符串；dirInfo 从不缓存）
files            0x38 synthetic in-memory 文件存储（hardReset **保留**，实例隔离）
```

> 当前**不包含** S3 定时器、`last_request_sid`、`unlocked_at`、
> 请求上下文、clock 或 config —— 这些不是状态字段。

**`STATE` 是唯一事实源**：不存在第二份模块级状态变量，
也没有为了同步状态而建立的 module proxy / PEP 562 机制。

### 14.5 `ecu.py` 兼容 facade

为保持既有调用方与测试兼容，以下符号保留在 `ecu.py`。
它们**不是**协议业务逻辑，也不是第二份实现：

| 符号 | 性质 |
|---|---|
| `handle_uds_request(request)` | 入口包装：注入 0x27 依赖后转交 `uds_server.handle_uds_request` |
| `STATE` | `uds_server.STATE` 的 **re-export / alias**（同一对象，非第二份状态） |
| `reset_all_security_state()` | 测试专用 helper（清全部安全状态） |
| `handle_uds_request_protocol` | 指向协议层 dispatcher 的显式别名 |
| `DEFAULT_SESSION` | `uds_types.SESSION_DEFAULT` 的 **alias**（非重复定义） |
| `SECURITY_LOCKED` | `uds_types.SECURITY_LOCKED` 的 **alias** |
| `SECURITY_LEVEL1` | `uds_types.SECURITY_LEVEL1` 的 **alias** |

### 14.6 Service registry 与 dispatcher

静态 registry 定义在 `uds_server.py`，**含十一个服务**
（0x2E / 0x3E 为 Phase 4X 新增，0x19 / 0x31 / 0x34 / 0x35 / 0x36 / 0x37 为第二轮新增）：

```python
SERVICES = {
    0x10: service_diagnostic_session_control,
    0x11: service_ecu_reset,
    0x14: service_clear_diagnostic_information,
    0x19: service_read_dtc_information,
    0x22: service_read_data_by_identifier,
    0x2E: service_write_data_by_identifier,
    0x31: service_routine_control,
    0x34: service_request_download,
    0x35: service_request_upload,
    0x36: service_transfer_data,
    0x37: service_request_transfer_exit,
    0x38: service_request_file_transfer,
    0x3E: service_tester_present,
}
```

**`0x27` 不在静态 registry 中**。原因是它需要请求时依赖
（`now` + `SECURITY_CONFIG`）：由 `ecu.py` 在每次请求时注入，
与静态表合并后交给 dispatcher：

```python
services = {**uds_server.SERVICES, **ecu._security_services()}
uds_server.handle_uds_request(request, STATE, services=services)
```

dispatcher 的判定顺序（与实现一致）：

```text
request
  ↓
空请求守卫（必须早于 request[0] 与查表）
  ↓  b"" → 7F 00 13
service = request[0]
  ↓
table = SERVICES if services is None else services
  ↓
handler = table.get(service)
  ↓  miss → 7F <service> 11（回显请求 SID，不做长度检查）
  ↓  hit  → handler(request, state)
```

### 14.7 验证基线（实测）

Phase 3 收尾基线：

```text
UDS Unit       104 PASS
Keygen Unit     34 PASS
E2E             65 PASS
Healthcheck     31 PASS / 0 WARN / 0 FAIL
```

Phase 4-P2 之后（新增 ISO-TP 传输参数测试，既有基线**全部保持通过**）：

```text
UDS Unit                 104 PASS
Keygen Unit               34 PASS
E2E                       65 PASS
ISO-TP 参数（在网）        14 PASS   tests/test_isotp_options_vcan.py
can-tools 纯逻辑           34 PASS   can-tools/tests/test_parsers.py
can-tools ISO-TP 参数      50 PASS   can-tools/tests/test_isotp_send_options.py
```

Phase 4-P3 / P3R / P3R2 之后（畸形分帧实验与 transport containment）：

```text
畸形 ISO-TP（在网）        47 PASS   can-tools/tests/test_isotp_malformed_vcan.py
    其中 containment 回归：EILSEQ（跳号/重复）、ECOMM（FC 超时）、
                          EMSGSIZE（FC OVERFLOW）
    0 expectedFailure（P3R 修复后已全部转为普通断言）
```

P9 阶段**连续执行 3 轮完整回归**，三轮结果逐项完全一致
（104 / 34 / 65，healthcheck 31/0/0，退出码均为 0），
并完成 transport / protocol 边界、依赖方向、`EcuState` 字段数、
`STATE` 单一事实源与 rollback point 的静态验证。

工程收口 2026-09-21 基线（Phase 4X 全量，实测）：

```text
UDS Unit       312 PASS   （106 ECU + 34 keygen + 17 services-extra + 13 gateway + 30 dtc + 25 routine + 40 transfer + 47 upload）
E2E（在网）     143 PASS  （65 + 14 isotp-matrix + 8 write-did + 4 multi-ecu + 8 dtc + 11 routine + 17 transfer + 16 upload）
can-tools 离线 183 PASS   （34 parsers + 50 isotp-options + 99 fuzz-harness，
                          含 kernel oracle 三态 10 例）
fuzz kernel oracle 三态 AVAILABLE/UNAVAILABLE/ERROR 如实上报
```

OQ6 合并后基线（main `c112f022`，实测）：

```text
UDS Unit       620 PASS   （116 ECU + 34 keygen + 17 services-extra + 13 gateway + 115 dtc
                           + 39 dtc-clear + 25 routine + 40 transfer + 47 upload
                           + 174 file-transfer）
E2E（在网）     213 PASS  （69 + 14 isotp-matrix + 8 write-did + 4 multi-ecu + 21 dtc
                           + 11 dtc-clear + 11 routine + 18 transfer + 16 upload
                           + 41 file-transfer）
can-tools 离线 303 PASS   （34 parsers + 50 isotp-options + 99 fuzz-harness + 120 uds-fuzz）
qa_fast.sh（ruff + mypy + unit + can-tools offline）= PASS
GitHub CI Fast（main post-merge run #65）= success；Extended 未在 runner 实跑
```

运行方式：

```bash
bash labs/vehicle/uds-basic/tests/run_tests.sh all   # 620 + 213
bash scripts/healthcheck.sh                          # 环境 + 离线测试
```

### 14.8 ISO-TP 传输参数（Phase 4-P2）

在 Phase 4-P2 之前，ECU 的 ISO-TP 行为**完全隐式依赖内核默认值**：
`main()` 里既不调用 `set_opts()` 也不调用 `set_fc_opts()`，
所以 padding / stmin / blocksize / timeout 都无法做对照实验。

现在这些参数全部显式化，并且**默认行为逐字节不变**。

#### 实测：默认帧（vcan0）

单帧交换 `10 01`：

```text
T->ECU  DLC=3  02 10 01
ECU->T  DLC=7  06 50 01 00 32 00 64
```

多帧交换 `22 F1 90 F1 80`（35 字节响应）：

```text
T->ECU  DLC=6  05 22 f1 90 f1 80
ECU->T  DLC=8  10 23 62 f1 90 54 45 53      ← SF/FF 的 PCI 字节
T->ECU  DLC=3  30 00 00                     ← Tester 的 FC：BS=0, STmin=0
ECU->T  DLC=8  21 54 45 43 55 30 30 30
ECU->T  DLC=8  22 30 30 30 30 30 30 31
ECU->T  DLC=8  23 f1 80 42 4f 4f 54 2d
ECU->T  DLC=8  24 53 57 2d 31 2e 30 2e
ECU->T  DLC=2  25 30                        ← 最后一个 CF 只有 2 字节
```

> 方向提醒：**本 ECU 只作为响应发送方，永远不发 FC**。
> 多帧交换中的 FC 只可能由 Tester 发出——所以 `UDS_ISOTP_FC_*`
> 控制的是"ECU 要求对端怎么发"，不是"ECU 自己怎么发"。

#### 环境变量

| 环境变量 | 取值 | 默认（未设置） | 作用 |
|---|---|---|---|
| `UDS_ISOTP_TX_PAD` | `0x00`–`0xFF` | 不填充 | ECU 发出的 CAN 帧补到 8 字节 |
| `UDS_ISOTP_RX_PAD` | `0x00`–`0xFF` | 不启用 | ECU 接收时对填充值的要求 |
| `UDS_ISOTP_FC_STMIN` | `0x00`–`0xFF` | `0x00` | ECU **发出的 FC** 里的 STmin 字节 |
| `UDS_ISOTP_FC_BLOCKSIZE` | `0x00`–`0xFF` | `0`（无限块） | ECU **发出的 FC** 里的 BS |
| `UDS_ISOTP_TX_STMIN` | `500` / `100us` / `2ms` / `1s` | 不强制 | ECU **自己发 CF** 的间隔（纳秒） |
| `UDS_ISOTP_TIMEOUT` | 秒（`0` = 非阻塞） | 阻塞等待 | ECU 的 `recv()` 超时 |

```bash
UDS_ISOTP_TX_PAD=0xAA python3 ecu/ecu.py
UDS_ISOTP_FC_STMIN=0x14 UDS_ISOTP_FC_BLOCKSIZE=1 python3 ecu/ecu.py
UDS_ISOTP_TX_STMIN=2ms python3 ecu/ecu.py
UDS_ISOTP_TIMEOUT=0.3 python3 ecu/ecu.py
```

#### 设计要点（每条都对应一个已踩过的坑）

1. **默认不变**：全部未设置时 `main()` 不调用 `set_opts()`/`set_fc_opts()`，
   内核默认行为与改造前逐字节一致（单帧仍是 `03 10 03`，FC 仍是 `30 00 00`）。
2. **`None` ≠ `0`**：未设置 = `None` = "不改动该选项"；
   `0` 是合法取值（例如 STmin=0 表示无间隔要求），两者必须严格区分。
3. **两个方向的 STmin 不能合并**：
   `UDS_ISOTP_FC_STMIN` 是写进自己 FC 帧的 ISO-TP 字节
   （`0x00–0x7F` = 0–127 ms，`0xF1–0xF9` = 100–900 us）；
   `UDS_ISOTP_TX_STMIN` 走内核 `CAN_ISOTP_TX_STMIN`（`FORCE_TXSTMIN`），
   单位是**纳秒**，且会**覆盖**收到的 FC 里的 STmin。
4. **必须早于 `bind()`**：python-isotp 在 `bind()` 之后调用
   `set_opts()`/`set_fc_opts()` 会直接抛 `RuntimeError`。
5. **非法取值立即失败**：环境变量只接受整数或带单位后缀的时长，
   写错会在**启动时**抛 `ValueError` 并退出，不会静默回退到默认值。
6. **recv 超时是异常**：设置了 `UDS_ISOTP_TIMEOUT` 后，
   python-isotp 的 `recv()` 超时会**抛 `TimeoutError`**（不是返回 `None`），
   所以 `main()` 必须接住它，否则 ECU 空闲一个周期就会退出。
7. **transport error 必须被 containment**（Phase 4-P3R / P3R2）：内核 CAN_ISOTP
   在传输损坏时通过 socket 抛 `OSError`，若不管就会杀死 ECU 进程。
   已实测三条并接住：

   | errno | 名称 | 触发场景 |
   |---|---|---|
   | 84 | `EILSEQ` | 接收侧 CF 序号不匹配（跳号 / 重复 / 回退） |
   | 70 | `ECOMM` | 发送侧 FC 接收超时（`N_Bs` 超时，约 1.2s 后上报） |
   | 90 | `EMSGSIZE` | 发送侧 FC 溢出（收到 `FC OVERFLOW` = `32 00 00`） |

   处理原则（详见第 14.9 节）：

   ```python
   except TimeoutError:      # 必须排在 OSError 之前（它是 OSError 的子类）
       continue
   except OSError as exc:
       if _is_recoverable_isotp_error(exc):
           print(f"ISO-TP RX error: ... — 丢弃当前传输，继续运行")
           continue
       raise                 # 其余 OSError 仍暴露，不被静默吞掉
   ```

   注意 **不能**把 transport error 转换成 `7F xx NRC`：错误发生在
   payload transport 层，不是 UDS service handler 层，
   因此"没有 UDS 响应"才是正确结果。

#### 实测：填充只改 DLC，不改 payload

| 配置 | ECU 应答帧 | `recv()` 重组结果 |
|---|---|---|
| 默认 | `DLC=7  06 50 01 00 32 00 64` | 6 字节 |
| `UDS_ISOTP_TX_PAD=0xAA` | `DLC=8  06 50 01 00 32 00 64 AA` | 6 字节（不变） |

补充实测：多帧响应的**最后一个 CF 也会被填充**（`25 30` → `25 30 aa aa aa aa aa aa`），
但 ISO-TP 按 FF 声明的长度截断，重组结果仍是 35 字节。
另外，带填充的对端**不需要**接收方开启 `RX_PAD` 也能正常重组。

#### 实测：STmin 的两个方向

ECU 返回 35 字节响应时，测量其连续帧（CF）之间的间隔：

| 配置 | ECU 实测 CF 间隔 |
|---|---|
| 默认（FC = `30 00 00`） | 0.06 / 0.12 / 0.08 / 0.07 ms |
| Tester `fc_stmin=0x0A`（FC = `30 00 0a`） | 10.21 / 10.20 / 10.18 / 10.23 ms |
| Tester `fc_stmin=0x14`（FC = `30 00 14`） | 20.06 / 20.39 / 20.20 / 20.23 ms |
| ECU `UDS_ISOTP_TX_STMIN=2ms` + Tester FC 要求 20 ms | **2.19 / 2.20 / 2.38 / 2.25 ms** |

最后一行就是 `FORCE_TXSTMIN` **覆盖**对端 FC 要求的直接证据。

#### 对应测试

```bash
# 在网实验矩阵（14 个用例，需要 vcan0）
/home/lilike/.venvs/network/bin/python tests/test_isotp_options_vcan.py
```

覆盖：默认帧不变（单帧 DLC=3 / FC `30 00 00`）、TX 填充 DLC=8、
RX 填充容错、FC 的 BS 生效、FC 的 STmin 生效（时间量级断言）、
`FORCE_TXSTMIN` 覆盖、以及 `UDS_ISOTP_TIMEOUT` 下"空闲不发帧 + 仍能应答"。

### 14.9 ISO-TP transport error containment（Phase 4-P3R）

**背景**：Phase 4-P3 的畸形分帧实验发现两类确定性输入会让 ECU 进程
直接退出（returncode 1）。这不是 UDS 逻辑问题，而是内核 CAN_ISOTP
在传输损坏时向用户态抛 `OSError`，而 `main()` 当时只捕获 `TimeoutError`。

| 方向 | errno | 名称 | 触发场景 |
|---|---|---|---|
| RX | 84 | `EILSEQ` | CF 序号不匹配（跳号 / 重复 / 回退 / 大跳） |
| TX→RX | 70 | `ECOMM` | FC 接收超时（`N_Bs` 超时，实测约 1.2s 后上报） |
| TX→RX | 90 | `EMSGSIZE` | FC 溢出（收到 `FC OVERFLOW` = `32 00 00`）｜P3R2 补充 |

> 实测细节：`ECOMM` **不是在 `send()` 当场抛出的**，而是在内核
> `N_Bs` 超时后由**后续 socket 操作**（`recv()`）上报。
> `send()` 侧仍保留同等处置，避免内核行为变化时又变成未处理异常。
>
> `EMSGSIZE` 同理：内核在 `isotp_rcv_fc()` 中设置该错误，但实测是在
> **后续 `recv()`** 上被取走（stdout 时序显示 `TX UDS:` 已打印、
> `send()` 正常返回）。**不要**依据"错误来源方向"或"崩溃发生在哪个时段"
> 推断取得者——`sk_err` 是共享 socket 状态，**谁先调用谁取走**（P3K 结论）。

**修复**（只改 `ecu.py`，内核与 `uds_server.py` 均未动）：

```python
def _is_recoverable_isotp_error(exc: OSError) -> bool:
    """当前 transaction 可丢弃的 ISO-TP 传输错误。"""
    return exc.errno in (errno.EILSEQ, errno.ECOMM)
```

```python
try:
    data = sock.recv()
except TimeoutError:                    # 顺序：TimeoutError 是 OSError 子类
    continue
except OSError as exc:
    if _is_recoverable_isotp_error(exc):
        print(f"ISO-TP RX error: {_isotp_error_detail(exc)} — 丢弃当前传输，继续运行")
        continue
    raise                               # 非预期错误仍然暴露
```

```python
try:
    sock.send(response)
except OSError as exc:
    if _is_recoverable_isotp_error(exc):
        print(f"ISO-TP TX error: {_isotp_error_detail(exc)} — 丢弃本次响应发送，继续运行")
        continue
    raise
```

**设计取舍**：

1. **不吞掉所有 `OSError`**：接口被删、socket 真损坏等必须暴露，
   否则会掩盖真实故障。只接住**实测确认**的 errno（当前 84 / 70 / 90）。
2. **不伪造 NRC**：错误发生在 payload transport 层，不是 service handler 层，
   因此"只记录、不回 `7F xx`"是正确行为。
3. **不重建 socket**（第一版）：实测 `catch + continue` 之后 socket 仍然可用
   （后续 `10 01` / `22 F1 80` 均正常），因此不引入 close/recreate 的复杂度。
4. **只按实测扩大 errno 集合**：`EMSGSIZE`(90) 在 P3R2 中被实测触发并纳入；
   `ETIMEDOUT` / `EBADMSG` 仍未实际观测到，因此**没有**加入——避免把真实
   socket / 接口故障误判为"可丢弃传输错误"。

**修复后实测**：

```text
EILSEQ（SN 跳号 / 重复）
  ISO-TP RX error: errno=84 (EILSEQ) ... — 丢弃当前传输，继续运行
  ECU alive = True
  recovery: 10 01 -> 50 01 00 32 00 64

ECOMM（FC 超时最小复现：只回一个 30 01 80）
  ISO-TP RX error: errno=70 (ECOMM) ... — 丢弃当前传输，继续运行
  ECU alive = True
  recovery: 10 01 -> 50 01 00 32 00 64
  （总线上会看到半截 FF 62 f1 90 54 45 53 —— 那是传输被丢弃的表现，
    不是伪造的 UDS 响应）

EMSGSIZE（P3R2：ECU 发送中注入 FC OVERFLOW 32 00 00）
  ISO-TP RX error: errno=90 (EMSGSIZE) ... — 丢弃当前传输，继续运行
  ECU alive = True
  recovery: 10 01 -> 50 01 00 32 00 64
  （3/3 确定性复现）
```

**修复后正常功能验证**（transport 异常处理位于 runtime loop，
不能只验证 malformed 路径）：`10 01` / `10 02` / `10 03` / `22 F1 80` /
`22 F1 90`（多帧）/ `27 01` 全部正常。

**范围声明**：这些修复**只是进程级 containment**，不代表"ISO-TP 已完全加固"。
覆盖范围仅限已**实测**的 Linux CAN_ISOTP transport errors（当前 84 / 70 / 90），
并不意味着所有可能的 ISO-TP errno 都已覆盖。内核仍会如常产生这些传输错误，
只是 ECU 不再因此退出。内核侧为何产生它们（CAN_ISOTP 状态机行为）不在
本阶段范围，属后续 `Phase 4-P3K`（已完成的只读定位）。

**对应测试**（`labs/vehicle/can-tools/tests/test_isotp_malformed_vcan.py`）：

```text
test_wrong_cf_sn_does_not_kill_ecu      # EILSEQ 路径 A-1（跳号）
test_repeated_cf_sn_does_not_kill_ecu   # EILSEQ 路径 A-2（重复）
test_fc_timeout_does_not_kill_ecu       # ECOMM 路径 B
test_fc_overflow_does_not_kill_ecu      # EMSGSIZE 路径（P3R2）
```

三条用例都断言：ECU 存活 + 出现对应 errno 的 containment 日志 + 后续正常
UDS 请求可恢复。P3 阶段的 `@unittest.expectedFailure` 已全部移除。

### 14.10 Phase 3 架构重构说明

Phase 3 完成的是一次**结构性重构**（行为保持不变）：

```text
- protocol constants centralized（uds_types.py）
- response / NRC handling centralized
- explicit EcuState introduced（替代 8 个模块级全局变量）
- service handlers extracted into uds_server.py
- service registry introduced
- dispatcher moved into uds_server.py
- ECU compatibility facade retained（ecu.py）
- transport / protocol boundary verified
```

详细提交历史见 Git log（P1–P8 为独立可回滚提交），本文档不重复记录。

---

## 15. 后续扩展方向

### 15.1 协议能力

- ✅ 已完成：`0x2E` WriteDataByIdentifier（见 4.3）
- ✅ 已完成：`0x3E` TesterPresent（见 4.1）
- ✅ 已完成：`0x19` ReadDTCInformation 的 reportType 01/02/07/08/09/0A
  （其余 reportType 属后续）
- ✅ 已完成：`0x14` ClearDiagnosticInformation（见 4.9；功能组、DTC
  产生/故障注入属后续）
- ✅ 已完成：`0x85` ControlDTCSetting（见 4.11；仅 01/02 两个
  DTCSettingType；optional DTCSettingControlOptionRecord、
  suppressPosRspMsgIndicationBit 全局框架属后续）
- ✅ 已完成：`0x31` RoutineControl（Start/Stop/RequestResults +
  0x2001/0x2002 synthetic routines；routineControlOptionRecord /
  routineStatusRecord 与权限/会话门控属后续）
- ✅ 已完成：`0x34/0x36/0x37` Download Transfer 与 `0x35` RequestUpload
  （synthetic 内存模型，BSC 幂等重传/重发；真实 Flash、签名校验属后续）
- ✅ 已完成：`0x38` RequestFileTransfer（mode 1–5，synthetic in-memory
  文件模型；`0x39` RequestFileTransferExit、`0x06` ResumeFile、
  真实文件系统/Flash、压缩/加密/签名校验属后续）
- `0x28` CommunicationControl
- `0x78` ResponsePending 与 P2* 超时处理
- `0x27` 多安全等级（Level 2：`0x03`/`0x04`，需位掩码式安全等级）
- 功能寻址（`0x7DF`）的**用户态实时网关守护进程**
  （协议层路由与三 ECU 物理寻址实时已通过，见 `ecu/gateway.py`）

### 15.2 状态机与架构

- ✅ 已完成（Phase 3）：`if/elif` 分发 → **服务注册表 + dispatcher**
  （见第 14.6 节）
- ✅ 已完成（Phase 3）：安全状态集中到 `EcuState`，消除模块级全局变量
  （见第 14.4 节）
- ✅ 已完成（Phase 4X）：0x2E/0x3E 入静态注册表；会话级权限前置检查
  在 0x2E 门控链中生效（`0x7F`/`0x33`）
- ✅ 已完成（Phase 4X）：多 ECU 拓扑（多对 CAN ID、`ecu/gateway.py`
  协议层网关，13 单元 + 4 e2e）
- 服务 handler 增加**通用会话级权限前置检查**（0x2E 已内联实现，
  尚未抽象为 registry 级装饰器）
- DID 数据库支持数据类型（ASCII / 数值 / 位域）与缩放

### 15.3 安全测试

- ✅ 已完成（Phase 4X）：ISO-TP 层 Fuzzing（异常分帧、流控滥用、
  序号错乱；`labs/vehicle/can-tools/fuzz/`，含 replay 与 kernel oracle）
- ✅ 已完成：基于本测试套件建立的**回归基线**（run_tests /
  qa_fast / qa_extended / healthcheck）
- UDS Fuzzing：随机 SID / 长度 / DID 组合，监控异常与崩溃
- **SecurityAccess 专项 Fuzzing**：随机 key、越界子功能、超长/超短 key、
  乱序 SendKey，验证 ECU 不会崩溃或意外解锁
- CAN Fuzzing（配合 `labs/vehicle/can-basic`）

### 15.4 工程化

- ✅ 已完成：CI `.github/workflows/ci.yml`：Fast 自动（push/PR），
  Extended 仅 workflow_dispatch；本 PR 的 Fast 已在真实 GitHub runner
  上验证通过（[Historical：工程收口当时曾"尚未在真实 runner 触发
  （无 push 授权）"，该状态已随 push/PR 流程解除。]）
- 用 `cantools` + DBC 描述诊断报文，与 `can-basic` 实验打通
- 输出 UDS 测试报告到 `reports/`

---

## 17. 参考

- ISO 14229-1：UDS 应用层协议
- ISO 15765-2：ISO-TP 传输层协议
- ISO 15765-4：OBD-II 诊断寻址
- `configs/systemd/vcan0.service`：vcan0 自动创建
- `labs/vehicle/can-basic/README.md`：CAN / SocketCAN 基础实验
- 仓库根 `README.md`、`AGENTS.md`：项目总览与工作规范
