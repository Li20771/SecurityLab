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
| `ecu/ecu.py` | 虚拟 ECU 主程序：ISO-TP 收发 + UDS 服务分发 + 安全状态 |
| `ecu/did_database.py` | DID 数据库（DID → 数据字节）与受保护 DID 标记 |
| `ecu/keygen.py` | 教学用 seed/key 算法（**不依赖 CAN / ISO-TP**） |

`keygen.py` 单独成文件的原因：它没有任何第三方依赖，
因此诊断客户端与测试可以只 `import keygen` 而无需拖入 ISO-TP 依赖，
算法也能脱离 CAN 独立测试。

### 3.2 服务分发逻辑

`ecu/ecu.py` 的核心是纯函数：

```python
def handle_uds_request(request: bytes) -> bytes:
    ...
```

它**不依赖 CAN**，输入请求字节、输出响应字节。
因此可以直接被单元测试调用（见第 8 节），这是本实验可测试性的关键设计。

### 3.3 会话状态与安全状态

ECU 维护两组状态：

```python
# 会话状态
DEFAULT_SESSION = 0x01
CURRENT_SESSION = DEFAULT_SESSION

# 安全访问状态（详见第 6 节）
CURRENT_SECURITY_LEVEL = SECURITY_LOCKED
PENDING_SEED = None
SEED_ISSUED_AT = None
SEED_CONSUMED = False
FAILED_ATTEMPTS = 0
LOCKOUT_UNTIL = None
SEED_COUNTER = 0
```

- `0x10` 切换会话时更新 `CURRENT_SESSION`，并**清除解锁状态与 seed**
- `0x11 01`（hardReset）把会话复位回 `defaultSession`，同样清除解锁状态
- 被拒绝的请求**不会**改变会话状态（已由测试覆盖）
- **失败计数与锁定在会话切换 / 复位后保留**（本教学 ECU 的设计选择，见 6.4）

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

---

## 4. 支持的 SID

| SID | 服务 | 子功能 | 肯定响应 | 状态 |
|---|---|---|---|---|
| `0x10` | DiagnosticSessionControl | `0x01` default<br>`0x02` programming<br>`0x03` extended | `0x50` | 已实现 |
| `0x11` | ECUReset | `0x01` hardReset | `0x51` | 已实现 |
| `0x22` | ReadDataByIdentifier | —（支持多 DID） | `0x62` | 已实现 |
| `0x27` | SecurityAccess | `0x01` RequestSeed<br>`0x02` SendKey | `0x67` | 已实现 |

**尚未实现**（会返回 `NRC 0x11 ServiceNotSupported`）：

`0x14` ClearDiagnosticInformation、`0x19` ReadDTCInformation、
`0x28` CommunicationControl、`0x2E` WriteDataByIdentifier、
`0x31` RoutineControl、`0x3E` TesterPresent、`0x85` ControlDTCSetting 等。

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
实际: 51 01
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

### 4.2 DID 数据库

| DID | 含义 | 数据 | 长度 | 权限 |
|---|---|---|---|---|
| `0xF190` | VIN / 车辆识别号（本实验用测试值） | `TESTECU0000000001` | 17 | 公开 |
| `0xF180` | Boot Software Identification | `BOOT-SW-1.0.0` | 13 | 公开 |
| `0xF187` | Spare Part Number | `SPARE-PART-0001` | 15 | **受保护** |

> 真实车辆中 `0xF190` 是 VIN（17 位 ASCII），`0xF180` 是 Boot SW 版本，
> `0xF187` 是备件号。本实验使用固定测试值，便于断言。
>
> `0xF187` 是本实验的**受保护 DID**：只有在
> `extendedDiagnosticSession` 且通过 SecurityAccess 解锁后才能读取，
> 否则返回 `NRC 0x33 SecurityAccessDenied`。详见第 6 节。

---

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

**为什么会话切换必须清除解锁状态**：否则 Tester 可在
`extendedDiagnosticSession` 解锁后切回 `defaultSession` 并保持解锁，
形成真实的权限绕过漏洞。本实验用测试固化了这一语义
（`test_protected_did_denied_after_session_downgrade`）。

**为什么 `0x11 01` 保留失败计数与锁定**：防止通过复位 ECU
刷掉暴力破解的惩罚状态。

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

**当前实现的权限点**是 DID 级（因为 `0x22` 是当前唯一的读取服务）：

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

**后续扩展**（`0x2E` / `0x31` 等写操作）将沿用同一模型：
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

当前测试规模：

| 测试 | 数量 | 需要 vcan0 |
|---|---|---|
| `tests/test_ecu_unit.py` | 104 | 否 |
| `tests/test_keygen_unit.py` | 34 | 否 |
| `tests/test_e2e_vcan.py` | 65 | 是 |
| **合计** | **203** | — |

### 8.2 离线单元测试（不需要 CAN）

```bash
/home/lilike/.venvs/network/bin/python tests/test_ecu_unit.py
```

直接调用 `handle_uds_request()`，覆盖 **104 个用例**：

- `0x10` 各会话、异常子功能、长度错误、状态不被破坏、各会话 P2/P2*
- `0x11` 正常复位、复位后会话回归、不支持 resetType、长度错误
- `0x22` 单 DID、多 DID、顺序保持、未知 DID、奇数长度
- `0x27` RequestSeed / SendKey 全部路径（见下）
- 未实现服务（`0x19`/`0x2E`/`0x31`/`0x3E`/未知 SID）
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

### 9.5 未实现服务

```bash
$ python client/isotp_client.py 2E F1 90 00
TX UDS: 2e f1 90 00
RX UDS: 7f 2e 11
  → 否定响应：请求 SID 0x2E，NRC 0x11
```

### 9.6 0x27 SecurityAccess

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
│   ├── ecu.py               # 虚拟 ECU（ISO-TP 收发 + UDS 分发 + 安全状态）
│   ├── did_database.py      # DID 数据库（含受保护 DID 标记）
│   └── keygen.py            # 教学用 seed/key 算法（无 CAN 依赖）
└── tests/
    ├── run_tests.sh         # 测试入口（unit / e2e / all）
    ├── test_ecu_unit.py     # 离线单元测试（104 用例，无需 CAN）
    ├── test_keygen_unit.py  # keygen 算法测试（34 用例，无需 CAN）
    └── test_e2e_vcan.py     # vcan0 端到端测试（65 用例）
```

---

## 13. 当前已验证能力

- [x] SocketCAN / vcan0 虚拟总线
- [x] Linux `CAN_ISOTP` 内核模块（`can_isotp`）
- [x] can-isotp 2.0.7 双向收发
- [x] udsoncan 1.26.1 客户端（含 `unlock_security_access()`）
- [x] `0x10` DiagnosticSessionControl（01/02/03 + 各自 P2/P2* + NRC 12/13）
- [x] `0x11` ECUReset（01 + NRC 12/13）
- [x] `0x22` ReadDataByIdentifier（单 DID、多 DID + NRC 31/13）
- [x] `0x27` SecurityAccess（RequestSeed / SendKey 全路径）
- [x] `0x7F` 否定响应与 NRC 判定（0x11/0x12/0x13/0x22/0x24/0x31/0x33/0x35/0x36/0x37）
- [x] 未实现服务统一返回 NRC 0x11
- [x] 会话状态管理与复位回归
- [x] 安全状态管理：失败计数、尝试上限锁定、seed 过期、防重放
- [x] 会话切换 / ECUReset 清除解锁状态（防权限绕过）
- [x] 受保护 DID 的 Session × Security 权限矩阵与整体拒绝
- [x] 教学用 seed/key 算法（公开常数、可复现、雪崩效应）
- [x] ISO-TP 单帧 / 多帧分帧验证
- [x] 离线单元测试 104 PASS（ECU）+ 34 PASS（keygen）
- [x] vcan0 端到端测试 65 PASS
- [x] **三轮连续稳定性验证全部通过**

---

## 14. 后续扩展方向

### 14.1 协议能力

- `0x2E` WriteDataByIdentifier：DID 写入与权限校验（复用第 6 节权限模型）
- `0x19` ReadDTCInformation：DTC 数据库与状态掩码
- `0x31` RoutineControl：例程控制
- `0x3E` TesterPresent：会话保持与 S3 超时
- `0x28` CommunicationControl、`0x85` ControlDTCSetting
- `0x78` ResponsePending 与 P2* 超时处理
- `0x27` 多安全等级（Level 2：`0x03`/`0x04`，需位掩码式安全等级）
- 功能寻址（`0x7DF`）与多 ECU 响应
- 会话相关的服务权限（如非默认会话才允许 `0x2E`）

### 14.2 状态机与架构

- 把 `if/elif` 分发重构为**服务注册表 + 会话状态机**
  （目前 4 个服务仍是 `if/elif` 链，加到第 5–6 个服务时应重构）
- 把安全状态集中到 `EcuState` 对象，消除模块级全局变量
- DID 数据库支持数据类型（ASCII / 数值 / 位域）与缩放
- 多 ECU 拓扑（多对 CAN ID、网关模拟）

### 14.3 安全测试

- UDS Fuzzing：随机 SID / 长度 / DID 组合，监控异常与崩溃
- **SecurityAccess 专项 Fuzzing**：随机 key、越界子功能、超长/超短 key、
  乱序 SendKey，验证 ECU 不会崩溃或意外解锁
- ISO-TP 层 Fuzzing：异常分帧、流控滥用、序号错乱
- CAN Fuzzing（配合 `labs/vehicle/can-basic`）
- 基于本测试套件建立**回归基线**，确保 Fuzzing 前后行为一致

### 14.4 工程化

- 建立 CI（本地 runner）自动跑离线测试（104 + 34 个用例，2 ms 完成）
- 用 `cantools` + DBC 描述诊断报文，与 `can-basic` 实验打通
- 输出 UDS 测试报告到 `reports/`

---

## 15. 参考

- ISO 14229-1：UDS 应用层协议
- ISO 15765-2：ISO-TP 传输层协议
- ISO 15765-4：OBD-II 诊断寻址
- `configs/systemd/vcan0.service`：vcan0 自动创建
- `labs/vehicle/can-basic/README.md`：CAN / SocketCAN 基础实验
- 仓库根 `README.md`、`AGENTS.md`：项目总览与工作规范
