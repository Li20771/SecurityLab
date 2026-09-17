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

### ISO-TP 畸形分帧实验（Phase 4-P3）

用 **raw CAN 注入**绕过内核的正常 ISO-TP 封装，研究虚拟 ECU 对异常帧序列
的真实处理。测试文件：

```text
tests/test_isotp_malformed_vcan.py     # 40 个用例，需要 vcan0
```

运行：

```bash
bash tests/run_tests.sh malformed
# 或
/home/lilike/.venvs/network/bin/python tests/test_isotp_malformed_vcan.py
```

每个 case 都输出五段式实验记录（``CASE / INJECT / CAPTURE / ECU / RESULT /
RECOVERY``），不做"裸 PASS/FAIL"。方法论要点：

> **"没有 UDS 响应"不等于失败。** 非法 PCI 很可能在内核 CAN_ISOTP 层就被
> 丢弃，根本没进入 ``handle_uds_request()``——这正是实验价值所在。

另外必须区分**帧字节**与**payload**：CAN 帧首字节是 PCI，
ECU 的 ``10 01`` 响应在总线上是 ``06 50 01 00 32 00 64``。
拿帧字节直接和 ``50 01`` 比较永远不相等。

#### 实测结果汇总

图例：✅ 已实测 ｜ ⚠️ 已实测但结论依赖内核实现 ｜ ❗ 已实测且为**缺陷** ｜
🛡️ 曾为缺陷，已在 P3R 修复并加入回归 ｜ ❓ 未测

| 层次 | Case | 实测结果 |
|---|---|---|
| — | **baseline 合法单帧** `10 01` | ✅ `50 01 00 32 00 64` |
| — | **baseline 合法多帧** FF+CF（22 字节） | ✅ ECU 正确重组，返回 `7f 22 13`（未知 DID 的 NRC） |
| — | **baseline 合法顺序 SN 1,2,3** | ✅ 完整重组并响应 |
| — | **baseline 多帧响应（无注入）** | ✅ 20 字节响应完整，ECU 存活 |
| PCI | 非法 PCI `0x40/0x50/0x60/0x70/0xF0` | ✅ 全部静默丢弃：无 ISO-TP 响应、无 UDS 请求、ECU 存活可恢复 |
| SF | 声明 2 字节、实给 3 字节 | ✅ 按声明长度**截断**：`10 03` 正常交付 → `50 03 00 64 03 e8` |
| SF | 声明 4 字节、实给 2 字节 | ✅ **无任何响应**（2.5s 观察窗口），ECU 存活可恢复 |
| SF | 声明 0 字节 | ✅ **无任何响应**，ECU 存活可恢复 |
| SF | 声明 7 字节、DLC=3 | ✅ **无任何响应**，ECU 存活可恢复 |
| FF | 声明 32 字节但**不发 CF** | 🛡️ 内核 N_Bs 超时（约 1.2s）后抛 `OSError: [Errno 70]`；P3R 后**被接住**，ECU 存活可恢复 |
| FF | 声明 3 字节却继续发 CF | ✅ 多余 CF 被忽略，无响应，ECU 存活可恢复 |
| FF | 声明 16 字节、只补 8 字节 | ✅ 重组不完成 → 无响应，ECU 存活可恢复 |
| CF | 孤立 CF（无 FF），SN=1 / 1,2,3 | ✅ 静默丢弃，无响应，ECU 存活可恢复 |
| SN | 顺序 1,2,3 | ✅ 正常重组并响应（对照组） |
| SN | 跳号 1→3（缺 2） | 🛡️ 内核报 `Errno 84`(EILSEQ)；P3R 后**被接住**，ECU 存活可恢复 |
| SN | 重复 1,1 | 🛡️ 同上（含独立回归用例） |
| SN | 回退 1,0 | 🛡️ 同上 |
| SN | 跳到 0xF | 🛡️ 同上 |
| FC | 无对应传输时的 FC：CTS / WAIT / OVERFLOW | ✅ 全部忽略，无响应，ECU 存活可恢复 |
| FC | ECU 发送中额外注入 WAIT / OVERFLOW | ✅ 内核忽略重复 FC，20 字节响应依然完整，ECU 存活 |
| FC | ECU 发送中额外注入**参数异常** FC（BS/STmin） | ✅ 同上：存活、响应完整 |
| FC | **只用一个** BS=1 + STmin=0x80（保留值）的 CTS | 🛡️ 内核在 N_Bs 超时后报 `Errno 70`(ECOMM)；P3R 后**被接住**，ECU 存活可恢复 |
| FC | **只回一个正常 CTS**（对照） | ✅ 响应完整，ECU 存活 |
| FC | ECU 发送中注入 **FC OVERFLOW（0x32）** | 🛡️ 内核报 `Errno 90`(EMSGSIZE)；P3R2 后**被接住**，ECU 存活可恢复（含独立回归用例） |
| DLC | 声明的长度与实际不符（见 SF 各行） | ✅ 见上 |
| Padding | 单帧带 5 字节 `00` 填充 | ✅ 按 SF 长度截断，正常 `50 01` 响应 |
| Padding | 单帧带 5 字节 `0xAA` 填充 | ✅ 同上（与 P2 的 TX_PAD/RX_PAD 结论一致） |
| Padding | 多帧请求 CF 填充到 DLC=8 | ✅ 请求仍被正确重组 |

#### 关于 STmin 保留值（不要一律叫"非法"）

ISO 15765-2 的 STmin 是**一个字节**，编码分段如下：

```text
0x00–0x7F   →  0–127 ms        （合法）
0xF1–0xF9   →  100–900 us      （合法）
0x80–0xF0   →  保留
0xFA–0xFF   →  保留
```

因此本实验把 ``0xF1`` 当作**合法**档位，只把 ``0x80`` / ``0xF0`` 标注为
保留值。⚠️ 实测：**本机内核对保留值没有特殊处理**——``30 00 80`` 与
``30 00 00`` 观察到的行为相同（响应完整、ECU 存活），并没有出现
"回退到 127 ms"的可观察差异。

#### 关键发现：畸形输入曾杀死 ECU 进程（已在 P3R 修复）

**现象**（Phase 4-P3 首次实测）：两类确定性输入让 ECU 子进程直接退出
（returncode 1）。下面的"现象 / 最小复现 / 机理"保留为缺陷记录：

```text
A. CF 序号异常（跳号 / 重复 / 回退 / 大跳）
   → OSError: [Errno 84] Invalid or incomplete multibyte or wide character

B. 传输未按内核预期完成
   → OSError: [Errno 70] Communication error on send
     触发条件（最小复现）：对 ECU 的多帧响应只回一个
     30 01 80（BS=1, STmin=0x80）的 CTS，不再回后续 FC
```

**最小复现**（固化为用例 ``TestFcParamOnlyCts``）：

```text
1. 注入 SF: 03 22 f1 90          # 触发 ECU 的多帧响应
2. ECU 回 FF: 10 14 62 f1 90 54 45 53
3. 只注入一个 FC: 30 01 80        # 不再回任何后续 FC
4. 约 1.2s 后（内核 N_Bs 超时）ECU 进程退出
```

**机理**：内核在超时/重组失败后通过 socket 向用户态抛错，而
``ecu/ecu.py`` 的接收循环当时只捕获 ``TimeoutError``，``OSError``
不在捕获范围内，于是异常沿 ``main()`` 冒泡并终止进程。

**状态**：🛡️ **已修复**（Phase 4-P3R，commit ``fix(isotp): contain transport socket errors``）。

修复方式是在 transport 边界（``recv()`` / ``send()``）按 errno 分类：

```python
except TimeoutError:          # 必须排在 OSError 之前（TimeoutError 是其子类）
    continue
except OSError as exc:
    if _is_recoverable_isotp_error(exc):   # errno.EILSEQ / errno.ECOMM
        print(f"ISO-TP RX error: ... — 丢弃当前传输，继续运行")
        continue
    raise                     # 其余 OSError 仍然暴露，不被静默吞掉
```

修复后实测：

```text
Errno 84 (EILSEQ)  → ECU 存活，当前传输被丢弃，后续 10 01 正常
Errno 70 (ECOMM)   → ECU 存活，本次响应发送被丢弃，后续 10 01 正常
Errno 90 (EMSGSIZE)→ ECU 存活，后续 10 01 正常（P3R2 补充，见下）
```

三条路径各自有独立回归用例（``test_wrong_cf_sn_does_not_kill_ecu`` /
``test_repeated_cf_sn_does_not_kill_ecu`` / ``test_fc_timeout_does_not_kill_ecu`` /
``test_fc_overflow_does_not_kill_ecu``），
且 ``expectedFailure`` 已全部移除（P3 的缺陷记录机制不再需要）。

#### EMSGSIZE 补充说明（Phase 4-P3R2）

```text
触发方式：ECU 正在发送多帧响应时，注入 FC OVERFLOW (FS=0x2, 即 32 00 00)
errno   ：EMSGSIZE / 90
内核动作：isotp_rcv_fc() 置 sk_err = EMSGSIZE，tx.state 复位为 ISOTP_IDLE
处理范围：userspace process-level containment（仅 ecu.py 白名单新增 errno 90）
```

实测取得位置：内核在 ``isotp_rcv_fc()`` 里设置该错误，但它在**后续
``recv()``** 上被取走 —— stdout 时序显示 ``TX UDS:`` 已打印、``send()``
正常返回之后才出现 containment 日志。**不要**因为"它来自 FC 接收"或
"崩溃发生在 send 时段"就断定取得者；按 P3K 结论，``sk_err`` 是共享状态，
谁先调用谁取走。

> 边界声明：本修复**只是进程级 containment**，不是"ISO-TP 已完全加固"。
> 仅覆盖已**实测**确认的三条 errno 路径（84 / 70 / 90）；内核仍会如常报告
> 这些传输错误，只是 ECU 不再因此退出。``ETIMEDOUT`` / ``EBADMSG``
> 尚未实测，未纳入白名单。内核侧行为未做任何修改。

> 观察到的**非**问题：所有 malformed 输入都**没有**造成状态污染——
> ECU 存活时后续正常 UDS 请求全部成功（每个 case 都做了恢复检查）。
> 修复前唯一的失败模式是进程直接死亡。

#### ❓ 尚未测试（不要当成结论）

- 随机 / 覆盖引导 / 有状态 fuzzing 与 mutation engine
- 并发注入、多 ECU、CAN-FD、DoIP
- 其他内核版本 / 其他 ISO-TP 实现（本次结论绑定当前 WSL2 内核
  ``6.18.33.2-microsoft-standard-WSL2`` + python-isotp 2.0.7）
- ``ETIMEDOUT`` / ``EBADMSG`` 等其余 ISO-TP 传输错误：内核文档列出它们属于
  同一错误类别，但本项目**尚未实际观测到**，因此**没有**加入 recoverable
  分类（避免掩盖真实 socket / 接口故障）。``EMSGSIZE`` 已在 P3R2 实测并
  纳入 containment，不再属于本列表。
- 内核侧行为本身（CAN_ISOTP 状态机如何产生这些 errno）—— 属后续
  ``Phase 4-P3K`` 的范围，P3R/P3R2 只做 userspace containment

> CAN ID 错误（0x7E1 / 0x7DF / 0x000）已作为独立分类
> ``TestCanAddressing`` 纳入测试（实测：均无响应、ECU 存活），
> 但不与 ISO-TP malformed 混为一谈。

### 不做 UDS 解释

本工具**不解析** SID、NRC、DID 或 SecurityAccess，只原样打印响应字节。
这样它不会与 `uds_server.py` 形成业务耦合。要理解语义请对照
`../uds-basic/README.md`（第 4/5 节：支持的 SID 与 NRC）。

---

## 3.5 Stateful ISO-TP fuzzing harness（Phase 4X-P1）

在 `fuzz/` 下实现的**有状态、模型驱动**变异框架。它与上面三个 CLI 工具
互补：`can_inject.py` 是"手工发一帧"，fuzzer 是"按状态模型系统变异"。

```text
fuzz/
├── isotp_corpus.py    纯数据：seed corpus、状态模型常量、payload 常量
├── isotp_mutators.py  纯函数：各维度 mutator（不发帧、不等待、不抓包）
├── case_schema.py     testcase schema、校验、oracle 分类、replay identity
├── isotp_fuzz.py      执行引擎：ECU 进程隔离、raw CAN 注入/观察、oracle
└── replay.py          精确重放已保存的 testcase
```

### 固定实验边界

```text
interface  = vcan0（永远）
inject_id  = 0x7E0
observe_id = 0x7E8
```

第一版**不做 CAN ID fuzzing**（`addressing` 类别仅作参考，标记
`reference_only`），不接触真实 CAN 接口，所有输入经
`parsers.guard_interface` / CAN ID / DLC / 字节校验。

### corpus：来自已验证的 malformed 实验

seed 不是凭空发明的，而是把 Phase 4-P3/P3R/P3R2 中**已实测**的
47 个 case 整理成结构化定义（原始测试文件保持不动）：

| 类别 | seed 数 | 内容 |
|---|---|---|
| `baseline` | 4 | 合法单帧、合法多帧、多帧响应、顺序 SN（对照） |
| `pci` | 5 | 非法 PCI `0x4X/0x5X/0x6X/0x7X/0xFX` |
| `single_frame` | 4 | 声明 4 给 2、声明 2 给 3、声明 0、声明 7 DLC=3 |
| `first_frame` | 3 | FF 无 CF、声明 3 多发 CF、声明 16 只给 8 |
| `consecutive_frame` | 2 | 孤立 CF、孤立 CF 序列 |
| `sequence_number` | 5 | 跳号 1→3、重复、回退 1→0、跳 0xF、中途跳号 |
| `flow_control` | 12 | 无传输的 CTS/WAIT/OVERFLOW；发送中 OVERFLOW/WAIT；BS/STmin 各档 |
| `padding` | 3 | SF `00`/`0xAA` 填充、多帧 CF 填充 |
| `recovery` | 1 | 恢复基线 |
| `addressing` | 3 | `0x7E1`/`0x7DF`/`0x000`（**参考，不参与变异**） |

seed 的**时序也是数据**：动作有 `inject` / `wait` / `await_fc` 三种。
`await_fc` 表示"等 ECU 的 FF 再回一个 CTS"——它在语义上就是流控帧，
因此 BS/STmin mutator 可以同时作用于显式 FC 帧和 `await_fc`。

`await_fc` 还带一个 `response_len`（ECU 响应的声明总长度），只用于计算
"剩余 CF 数"：FC 类 seed 的多帧传输是 **ECU 发出的响应**，我们只回 FC，
因此没有自己的 FF，而 BS 的块边界恰恰针对那条传输
（20 字节响应 → 剩余 2 个 CF → 边界 `bs=2`）。

### mutation 维度（单变量原则）

| 维度 | 取值 |
|---|---|
| `pci` | 高半字节 `0x4–0x7`、`0xF`（保持 DLC 合法，不做随机帧） |
| `sf_length` | 声明长度 0–7 |
| `ff_length` | 0 / 1 / 3 / 8 / 0xFFF（覆盖声明<实际、=、>） |
| `sn` | 0 / 1 / 2 / 3 / 0xF（默认改最后一帧 CF） |
| `fc_status` | CTS / WAIT / OVERFLOW，**保留值 `0x3–0xF` 单独标 `FC_RESERVED_STATUS`** |
| `bs` | 0 / 1 / 2 / 255，**外加"恰好等于剩余 CF 数"的块边界**（单独打 `boundary` 标记） |
| `stmin` | `0x00/0x01/0x7F`（ms）、`0xF1/0xF9`（us）、`0x80/0xF0/0xFA/0xFF`（保留） |
| `padding` | DLC ∈ {2,3,5,8} × 填充字节 {0x00,0xAA,0xFF} |

每次变异**只改一个语义字段**；`padding` 维度本身的研究变量就是 DLC，
属于规格允许的例外。STmin 按 ISO-TP 编码分类，**不**把 `0x80+` 一律叫
"invalid"（`0xF1–0xF9` 是合法微秒档）。

### oracle 与结果分类

```text
process   子进程 poll() → ALIVE / DEAD
response  UDS response / No response / Partial ISO-TP
recovery  硬 oracle：10 01 → 50 01 00 32 00 64
runtime   normal / timeout / hang
kernel    dmesg 前后快照 + 过滤差分（绝不 dmesg -c）
```

固定判定顺序：`KERNEL_ANOMALY > ECU_CRASH > STATE_CORRUPTION > HANG >
TRANSPORT_ERROR > UDS_RESPONSE > DISCARD > NORMAL`。

`STATE_CORRUPTION` 专门表示"进程存活但下一个正常请求失败"——它**不会**
被 `process_alive=true` 掩盖。`recovery` 是独立字段（PASS/FAIL），
因此 `class=ECU_CRASH` 与 `recovery=FAIL` 可以同时表达。

`errno` 只记录已 containment 的 84/70/90 之外的值会被标记 `new_errno`，
但 fuzzer **不会**自动把它加入 `_is_recoverable_isotp_error()`
（需要单独定位与人工确认）。

### 可复现性

- `--seed` 记录 `master_seed`；P1 全部变异为确定性，同参数必得同序列。
- 每 case 可落盘 JSON（`case_id` / `seed` / `mutation` / `frames` / `delay` /
  `can_ids` / `result`）。
- **canonical replay identity 只依赖 `case_id`/`seed`/`mutation`/`frames`**，
  刻意排除 timestamp / kernel logs / observed response / process state ——
  它们是执行结果，不能改变 testcase 本身。
- `replay.py` 读取 `frames[]` **原样重发**（不是重新生成变异），
  因此是 exact reproduction；重放后同样执行 recovery probe。

```bash
# 列出 corpus 与可生成 case 数（不执行）
python3 fuzz/isotp_fuzz.py --list

# 小规模 smoke（<=30 case，每 case 一个全新 ECU 进程）
python3 fuzz/isotp_fuzz.py --smoke --seed 12345 --out ../fuzz-out

# 精确重放
python3 fuzz/replay.py ../fuzz-out/<case>.json --repeat 3 --show-frames
```

### 预算与执行模型

- P1 只做 **≤30 case** 的 smoke，不扩大到 1000（`--max-cases` 为安全阀）。
- **每个 case 一个全新 ECU 进程**（严格隔离），case 结束后终止。
- 不做 coverage-guided fuzzing（无 AFL/libFuzzer/coverage/instrumentation），
  不做并发、不多 ECU。

> 边界声明：本 harness 只做**确定性、有界、可复现**的模型驱动变异，
> 不声称"fuzzing 完成"或"coverage 完整"。发现 `ECU_CRASH` /
> `STATE_CORRUPTION` / `KERNEL_ANOMALY` / `NEW_ERRNO` 时按规则**保存最小
> 复现并停止该 mutation family**，不在同一阶段顺手修 ECU 或内核。

> 落盘提示：在只读文件沙箱下子进程写盘会被拒绝（`PermissionError`），
> 此时用 `--no-artifacts` 运行 smoke；`fuzz-out/` 已在 `.gitignore` 中。

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
├── fuzz/                 # Stateful ISO-TP fuzzing（Phase 4X-P1）
│   ├── isotp_corpus.py       # 纯数据：seed corpus + 状态模型常量
│   ├── isotp_mutators.py     # 纯函数：各维度 mutator
│   ├── case_schema.py        # testcase schema / 校验 / oracle 分类 / replay identity
│   ├── isotp_fuzz.py         # 执行引擎：进程隔离 + raw CAN + oracle
│   └── replay.py             # 精确重放已保存的 testcase
└── tests/
    ├── run_tests.sh                   # 测试入口（all / offline / malformed）
    ├── test_parsers.py                # 34 个离线单元测试（不依赖 vcan0）
    ├── test_isotp_send_options.py     # 50 个 ISO-TP 参数测试（不需 vcan0）
    ├── test_fuzz_offline.py           # 74 个 fuzz 离线测试（不需 vcan0）
    └── test_isotp_malformed_vcan.py   # 47 个畸形分帧实验（需要 vcan0）
```

`parsers.py` 是三个 CLI 共用的输入校验层。它刻意保持**纯函数、无 I/O、
无 CAN 依赖**，因此可以脱离 vcan0 独立测试——这也让三个工具的输入规则
只有一处定义，不会各自漂移。

---

## 6. 运行测试

工具自身的离线单元测试（**不需要 vcan0**）：

```bash
bash tests/run_tests.sh offline
# 或分别运行
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

需要 vcan0 的在网实验（Phase 4-P3，会自动启停虚拟 ECU 子进程）：

```bash
bash tests/run_tests.sh malformed
# 或
/home/lilike/.venvs/network/bin/python tests/test_isotp_malformed_vcan.py
```

- `test_isotp_malformed_vcan.py`（46）：畸形 PCI、SF 长度不一致、
  FF 不完整 / 长度异常、孤立 CF、SN 跳号 / 重复 / 回退、无对应传输的 FC、
  发送中注入 WAIT / OVERFLOW / 异常 BS / 保留 STmin、padding 与 DLC 异常、
  CAN addressing 对照，以及 P3R 新增的 **EILSEQ / ECOMM containment 回归**。
  结果见上一节"ISO-TP 畸形分帧实验"。

> 注意：该套件在 P3R 之后**不含任何 `expectedFailure`**（P3 阶段的缺陷
> 记录机制已随修复移除）。若未来重新出现进程级崩溃，用例会直接失败。

UDS 实验的完整回归（与本目录相互独立）：

```bash
bash ../uds-basic/tests/run_tests.sh all
```

---

## 7. 本目录不包含（后续阶段）

已完成：

```text
Phase 4-P1   观察（capture） · 注入（inject） · ISO-TP 发送（isotp_send）
Phase 4-P2   ISO-TP 传输参数显式化（padding / stmin / blocksize / timeout）
Phase 4-P3   确定性畸形分帧实验（PCI / SF / FF / CF-SN / FC / padding-DLC）
Phase 4-P3R  transport error containment（EILSEQ / ECOMM 不再杀死 ECU）
Phase 4-P3K  内核侧只读定位（EILSEQ/ECOMM 生成点、sk_err 发布路径）
Phase 4-P3R2 EMSGSIZE containment（FC OVERFLOW 不再杀死 ECU）
Phase 4X-P0  stateful ISO-TP fuzzing 设计勘察（corpus/状态模型/oracle/replay）
Phase 4X-P1  fuzz harness 实现（fuzz/ + 离线测试 + smoke，未扩大 campaign）
```

明确尚未实现：

- 随机 / 覆盖引导 / 有状态 fuzzing 与 mutation engine（Phase 4-P3 明确不做）
- CAN replay、PCAP / BLF 日志
- 传输层抽象（`transport.py`）—— 当前无此必要
- 对 P3 发现的进程崩溃做**修复**（属协议层改动，需单独阶段 + 单独提交）

`can_inject.py` 已经提供了后续这些实验所需的**链路层注入能力**，
但它本身只负责“把一帧按你写的字节发出去”，不判断内容是否合法。

> 说明：`--rx-pad` 在 Linux 内核实现下**不改变接收结果**——
> 超长（带填充）的单帧与多帧都能被正常重组，无论本端是否启用 RX_PAD。
> 它更接近"接受哪种填充值的宽容度"开关，因此本工具保留该参数以便
> 做对照实验，但不要指望它能用来"拒绝带填充的对端"。
