# CAN / ISO-TP 实验工具

三个用于车载诊断链路实验的最小工具，配合 `labs/vehicle/uds-basic/` 的虚拟 ECU 使用。

本目录是**实验工具层**，不是 ECU runtime。依赖方向单向：

```text
can-tools  ──>  python-can / python-isotp  ──>  vcan0
```

它**不**被 `ecu.py` 或 `uds_server.py` 依赖。

---

## 安全边界（重要）

三个工具**只允许 `vcan0`**。传入任何其它接口都会被明确拒绝：

```text
$ python3 can_inject.py --interface can0 0x7E0 01
ERROR: only vcan0 is allowed by this lab tool (收到 'can0')。
```

这是刻意的设计：避免因为参数误写，把实验流量发到真实 CAN 接口或真实车辆。
本目录所有工具都只用于**隔离的虚拟 CAN 实验环境**。

---

## 三个工具的分工

| 工具 | 层次 | 发送/接收的内容 |
|---|---|---|
| `can_capture.py` | 链路层 | 观察 **raw CAN 帧**（时间戳 / ID / DLC / 数据） |
| `can_inject.py` | 链路层 | 发送 **1 个原始 CAN 帧**（PCI 字节由你自己写） |
| `isotp_send.py` | 传输层 | 发送 **1 段完整 ISO-TP payload**（由协议栈分帧） |

### `can_inject.py` != `isotp_send.py`

这是理解 ISO-TP 的关键概念，务必区分：

```text
can_inject.py   你给出        CAN ID + 数据字节（含 PCI）
                → 总线上一帧就是你所写的内容

isotp_send.py   你给出        完整 payload（不含 PCI）
                → python-isotp / 内核 CAN_ISOTP 决定
                  用单帧还是「首帧 + 连续帧」，并处理流控
```

举例：同样是"发 `10 03`"

```text
can_inject.py 0x7E0 02 10 03      → 帧数据 = 02 10 03（你写的 PCI 0x02 = 2 字节）
isotp_send.py 10 03               → 帧数据 = 02 10 03（协议栈替你加的同一 PCI）

但 can_inject.py 10 03            → 帧数据 = 10 03（错误！PCI=0x10 表示首帧）
```

也就是说：`can_inject.py` 允许你构造**协议栈不会产生**的帧——
这正是后续阶段做异常分帧实验的基础。`isotp_send.py` 则总是产生**合法**流量。

---

## 共同的前置条件

| 项目 | 要求 |
|---|---|
| Python | `/home/lilike/.venvs/network/bin/python`（Python 3.12） |
| 依赖 | `python-can==4.6.1`、`can-isotp==2.0.7` |
| 接口 | `vcan0` 已存在且为 UP（工具**不会**创建它） |

确认 vcan0：

```bash
ip -details link show vcan0
```

若不存在（需要一次 root）：

```bash
sudo systemctl start vcan0.service    # 仓库自带 configs/systemd/vcan0.service
```

---

## 1. can_capture.py — raw CAN 观察

持续接收并打印 CAN 帧，格式近似 `candump`：

```bash
python3 can_capture.py                       # 持续打印所有帧
python3 can_capture.py --count 10            # 收满 10 帧后退出
python3 can_capture.py --id 0x7E8            # 只看某个仲裁 ID
python3 can_capture.py --timeout 0.5         # 每帧接收超时（秒）
```

输出示例：

```text
# timestamp  ID   [DLC]  DATA
1789443383.627738  555  [3]  01 02 03
1789443384.075875  7E8  [2]  AA BB
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--interface` | `vcan0` | 只允许 vcan0 |
| `--id` | 不过滤 | 只显示该 11-bit CAN ID |
| `--timeout` | `1.0` | 每帧等待超时；超时后继续等待，不退出 |
| `--count` | `0` | 收到多少帧后退出；`0` = 持续运行 |

Ctrl+C 可随时停止，会打印已捕获帧数。

---

## 2. can_inject.py — raw CAN 帧注入

发送原始 CAN 帧（**不使用 ISO-TP**）：

```bash
python3 can_inject.py 0x7E0 02 10 03 00 00 00 00 00   # 8 字节
python3 can_inject.py 0x555 01 02 03                  # 3 字节
python3 can_inject.py 0x7E0 --data 02 10 03           # 等价写法
python3 can_inject.py 0x555 01 --repeat 5 --interval 0.1
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `can_id` | 必填 | 11-bit CAN ID（`0x000`–`0x7FF`），接受 `0x7E0` 或 `2016` |
| `data` | 可省略 | 0–8 字节十六进制；支持 `02 10 03` 或 `021003` |
| `--data` | — | 数据字节的等价写法（不可与位置参数同时使用） |
| `--interface` | `vcan0` | 只允许 vcan0 |
| `--repeat` | `1` | 重复发送次数；`0` = 持续发送直到 Ctrl+C |
| `--interval` | `0.0` | 重复发送间隔（秒） |

输入校验（全部在 `parsers.py`，可离线测试）：

- CAN ID 必须在 `0x000`–`0x7FF`，否则拒绝
- 数据必须 0–8 字节，且为合法十六进制，否则拒绝
- 超过 8 字节会被拒绝并提示经典 CAN 单帧上限

---

## 3. isotp_send.py — ISO-TP payload 发送

发送一段完整 payload，由协议栈负责分帧：

```bash
python3 isotp_send.py 10 03
python3 isotp_send.py 22 F1 90
python3 isotp_send.py 27 01
python3 isotp_send.py --txid 0x7E0 --rxid 0x7E8 22 F1 90
python3 isotp_send.py --no-wait 10 03          # 只发不等
```

输出示例：

```text
# interface: vcan0
# txid:      0x7E0  (Tester 发送 = ECU 接收)
# rxid:      0x7E8  (Tester 接收 = ECU 发送)
# addressing: Normal_11bits
# payload:    22 f1 90  (3 bytes)
# framing:   single frame 预计

TX: 22 f1 90
RX: 62 f1 90 54 45 53 54 45 43 55 30 30 30 30 30 30 30 30 30 31  (20 bytes)
```

### ID 方向语义（容易搞混，务必看清）

```text
--txid  = Tester 的发送 ID = ECU 的接收 ID   （默认 0x7E0）
--rxid  = Tester 的接收 ID = ECU 的发送 ID   （默认 0x7E8）
```

> `--txid` 指的是 **Tester 发出去的 ID**，不是"ECU 的发送 ID"。
> 与 `labs/vehicle/uds-basic/client/isotp_client.py`、`uds_client.py`
> 以及 E2E 测试中的方向约定完全一致。

| 参数 | 默认 | 说明 |
|---|---|---|
| `payload` | 必填 | payload 字节（十六进制），支持 `10 03` 或 `1003` |
| `--txid` | `0x7E0` | Tester 发送 ID |
| `--rxid` | `0x7E8` | Tester 接收 ID |
| `--interface` | `vcan0` | 只允许 vcan0 |
| `--timeout` | `2.0` | 等待响应超时（沿用既有 client 的 `RECV_TIMEOUT` 取值） |
| `--no-wait` | 关闭 | 只发送，不等待响应 |

### ISO-TP 传输参数（Phase 4-P2 起可显式配置）

在 P2 之前，padding / stmin / blocksize / timeout **全部隐式依赖内核默认值**，
无法做对照实验。现在它们都是显式参数，且**默认行为逐字节不变**。

| 参数 | 取值 | 默认 | 作用方向 |
|---|---|---|---|
| `--tx-pad` | `0x00`–`0xFF` | 不填充 | 本端发出的 CAN 帧补到 8 字节 |
| `--rx-pad` | `0x00`–`0xFF` | 不启用 | 本端接收时对填充值的要求 |
| `--fc-stmin` | `0x00`–`0xFF` / `5ms` / `500us` | `0x00` | 本端**发出的 FC** 里填的 STmin |
| `--fc-blocksize` | `0x00`–`0xFF` | `0`（无限块） | 本端**发出的 FC** 里的块大小 BS |
| `--tx-stmin` | 纳秒：`500` / `100us` / `2ms` / `1s` | 不强制 | 本端**自己发 CF** 的间隔 |
| `--timeout` | 秒 | `2.0` | 等待响应超时 |

```bash
python3 isotp_send.py --tx-pad 0xAA 10 03
python3 isotp_send.py --fc-stmin 5ms --fc-blocksize 1 22 F1 90 --txid 0x7E0
python3 isotp_send.py --tx-stmin 2ms --no-wait 22 F1 90 F1 80
python3 isotp_send.py --timeout 0.5 22 F1 90        # 没有 ECU 时 0.5s 干净退出
```

#### 两个 STmin 不是一个东西（最容易搞错的概念）

```text
--fc-stmin   写进「我们发出的 FC 帧」→ 要求**对方**放慢
             单位是 ISO-TP 的 STmin 字节：0x00–0x7F = 0–127 ms，
             0xF1–0xF9 = 100–900 us，其它取值保留

--tx-stmin   约束「我们自己发 CF」的节奏 → **我们自己**放慢
             走内核 CAN_ISOTP_TX_STMIN（FORCE_TXSTMIN 标志），
             单位是**纳秒**，并且会**覆盖**收到的 FC 里的 STmin
```

因此**不能**把它俩合并成一个 `--stmin`：方向相反，单位也不同
（一个是单字节编码，一个是纳秒）。

#### 实测数据（vcan0，35 字节多帧响应）

让 ECU 返回 35 字节响应，测量 ECU 连续帧（CF）之间的间隔：

| 配置 | ECU 实测 CF 间隔 |
|---|---|
| 默认（FC = `30 00 00`） | 0.06 / 0.12 / 0.08 / 0.07 ms |
| 本端 `--fc-stmin 10ms`（FC = `30 00 0a`） | 10.21 / 10.20 / 10.18 / 10.23 ms |
| 本端 `--fc-stmin 20ms`（FC = `30 00 14`） | 20.06 / 20.39 / 20.20 / 20.23 ms |
| ECU 侧 `UDS_ISOTP_TX_STMIN=2ms` + 本端 FC 要求 20 ms | **2.19 / 2.20 / 2.38 / 2.25 ms** |

最后一行就是"覆盖"的直接证据：对端 FC 要求 20 ms，但本端强制 2 ms，
实际节拍是 2 ms。默认情况下则是微秒级（无间隔要求）。

### 不做 UDS 解释

本工具**不解析** SID、NRC、DID 或 SecurityAccess，只原样打印响应字节。
这样它不会与 `uds_server.py` 形成业务耦合。要理解语义请对照
`../uds-basic/README.md`（第 4/5 节：支持的 SID 与 NRC）。

---

## 4. 实测示例

### 4.1 raw CAN 观察 + 注入

一个终端：

```bash
python3 can_capture.py --count 1 --timeout 5
```

另一个终端：

```bash
python3 can_inject.py 0x555 01 02 03
```

capture 侧应看到：

```text
1789443383.627738  555  [3]  01 02 03
```

### 4.2 ISO-TP 单帧（需要 ECU 运行）

先启动虚拟 ECU（另一个终端，保持运行）：

```bash
cd ../uds-basic
/home/lilike/.venvs/network/bin/python -u ecu/ecu.py
```

然后：

```bash
python3 isotp_send.py 10 03
# RX: 50 03 00 64 03 e8  (6 bytes)

python3 isotp_send.py 22 F1 80
# RX: 62 f1 80 42 4f 4f 54 2d 53 57 2d 31 2e 30 2e 30  (16 bytes)
```

### 4.3 ISO-TP 多帧（FF + FC + CF）

`22 F1 90` 的响应为 20 字节，超过单帧容量，会触发多帧序列。
用 capture 观察完整过程：

```bash
# 终端 1：抓帧
python3 can_capture.py --count 6 --timeout 4

# 终端 2：发送
python3 isotp_send.py 22 F1 90
```

实测抓到的帧序列（`vcan0` 上真实流量）：

```text
7E0  [4]  03 22 F1 90            ← Tester 单帧（PCI 0x03 = 3 字节）
7E8  [8]  10 14 62 F1 90 54 45 53 ← ECU 首帧 FF（PCI 0x1014 = 总长 20 字节）
7E0  [3]  30 00 00               ← Tester 流控 FC（CTS，BS=0，STmin=0）
7E8  [8]  21 54 45 43 55 30 30 30 ← ECU 连续帧 CF（序号 1）
7E8  [8]  22 30 30 30 30 30 30 31 ← ECU 连续帧 CF（序号 2）
```

要点：

- **CAN 帧 ≠ ISO-TP payload**。你在 `isotp_send.py` 只给了一段 payload，
  总线上出现的是上面 5 帧。
- 流控帧（`30 00 00`）由**内核 CAN_ISOTP**自动产生，不是本工具构造的。
- 这些参数（padding / STmin / blocksize / 流控）当前均为**内核默认值**，
  项目未显式配置——这正是 Phase 4-P2 的待办方向。

---

## 5. 文件结构

```text
can-tools/
├── README.md
├── parsers.py            # 纯函数：CAN ID / 十六进制字节 / 接口守卫 / 帧格式化
│                         #          + ISO-TP 参数解析（单字节 / 时长 / STmin）
├── can_capture.py        # raw CAN 观察
├── can_inject.py         # raw CAN 帧注入
├── isotp_send.py         # ISO-TP payload 发送（含显式传输参数）
└── tests/
    ├── test_parsers.py              # 34 个离线单元测试（不依赖 vcan0）
    └── test_isotp_send_options.py   # 50 个 ISO-TP 参数测试（不需 vcan0）
```

`parsers.py` 是三个 CLI 共用的输入校验层。它刻意保持**纯函数、无 I/O、
无 CAN 依赖**，因此可以脱离 vcan0 独立测试——这也让三个工具的输入规则
只有一处定义，不会各自漂移。

---

## 6. 运行测试

工具自身的离线单元测试（**不需要 vcan0**）：

```bash
/home/lilike/.venvs/network/bin/python tests/test_parsers.py
/home/lilike/.venvs/network/bin/python tests/test_isotp_send_options.py
```

覆盖：

- `test_parsers.py`（34）：CAN ID 解析与边界、十六进制字节解析（多种写法）、
  数据长度上限、非法输入、接口守卫、帧格式化。
- `test_isotp_send_options.py`（50）：ISO-TP 参数解析（单字节范围、
  `us`/`ms`/`s` 单位换算、STmin 的字节与时长两种写法、超范围拒绝）、
  "参数 → `set_opts`/`set_fc_opts`"的映射方向、
  **全部未指定时不下发任何 socket 选项**（默认行为不变的回归点）、
  以及 `--timeout` 在没有 ECU 时干净退出。

> `test_isotp_send_options.py` 需要 python-isotp（会 import `isotp_send`），
> 但不打开任何 socket，仍然不需要 vcan0。
> 需要 vcan0 的**在网实验**见
> `../uds-basic/tests/test_isotp_options_vcan.py`（14 个用例）。

UDS 实验的完整回归（与本目录相互独立）：

```bash
bash ../uds-basic/tests/run_tests.sh all
```

---

## 7. 本目录不包含（后续阶段）

已完成：

```text
Phase 4-P1  观察（capture） · 注入（inject） · ISO-TP 发送（isotp_send）
Phase 4-P2  ISO-TP 传输参数显式化（padding / stmin / blocksize / timeout）
```

明确尚未实现：

- 异常 / 畸形 ISO-TP 实验（非法 PCI、错误长度、序号错乱、流控滥用）
- CAN replay、PCAP / BLF 日志
- UDS 层与 raw CAN 层 fuzzing
- 传输层抽象（`transport.py`）—— 当前无此必要

`can_inject.py` 已经提供了后续这些实验所需的**链路层注入能力**，
但它本身只负责“把一帧按你写的字节发出去”，不判断内容是否合法。

> 说明：`--rx-pad` 在 Linux 内核实现下**不改变接收结果**——
> 超长（带填充）的单帧与多帧都能被正常重组，无论本端是否启用 RX_PAD。
> 它更接近"接受哪种填充值的宽容度"开关，因此本工具保留该参数以便
> 做对照实验，但不要指望它能用来"拒绝带填充的对端"。
