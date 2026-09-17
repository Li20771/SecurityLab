#!/usr/bin/env python3
"""
ISO-TP stateful fuzz —— seed corpus 与状态模型（**纯数据模块**）

本模块只包含：

- fuzzer 侧的最小 ISO-TP 状态常量
- 由 Phase 4-P3 已验证的 47 个 malformed case 整理而来的**结构化 seed**

刻意保持**纯数据**：不 import python-can / python-isotp，不做 subprocess、
不做 socket、不 sleep、不做任何 I/O。因此可以在任何环境离线导入与测试。

.. warning::

   本模块的状态常量只是 **fuzzer/test 侧的模型**，**不是 Linux kernel ABI**，
   也不代表 ``struct isotp_sock`` 的字段。与内核源码的对应关系见下方注释；
   内核侧真实状态名来自 ``net/can/isotp.c`` 的 ``enum``
   （``ISOTP_IDLE`` / ``ISOTP_WAIT_FIRST_FC`` / ``ISOTP_WAIT_FC`` /
   ``ISOTP_WAIT_DATA`` / ``ISOTP_SENDING``）。

seed 结构
---------

每个 seed 是"一串动作"，不是单纯的帧列表——因为部分实验需要**有序等待**
（例如等 ECU 的多帧响应发出首帧后再注入 FC）::

    {
        "seed_id":   "SN_SKIP_1_3",
        "category":  "sequence_number",
        "summary":   "FF + 合法 CF1 + 跳号 CF3（缺 SN=2）",
        "verified":  True,      # 是否来自 P3 已实测通过的 case
        "actions": [
            {"type": "inject", "id": 0x7E0, "data": bytes(...), "delay_ms": 100},
            {"type": "await_fc", "timeout_ms": 1000},   # 等 ECU 的 FF 后回 FC
            {"type": "wait",   "delay_ms": 300},
        ],
    }

动作类型::

    inject    注入一帧 raw CAN
    wait      固定延时（不依赖对端）
    await_fc  等待 ECU 发出 FF，然后回一个正常 CTS（保持其传输继续）

``await_fc`` 是把 P3 测试里"先等 FF 再注入"的时序固化成数据，
使 mutator 仍然可以是纯函数（只改字节，不改时序）。
"""

from typing import Dict, List, Optional

# ----------------------------------------------------------------------
# 固定实验边界（P1 §3：永不变化）
# ----------------------------------------------------------------------

INTERFACE = "vcan0"

#: Tester → ECU（注入方向）
INJECT_ID = 0x7E0

#: ECU → Tester（观察方向）
OBSERVE_ID = 0x7E8

#: 功能寻址广播地址——仅作 addressing 参考实验，**不是** mutation 维度
FUNCTIONAL_ID = 0x7DF

#: 经典 CAN 单帧数据上限
CAN_DATA_MAX_LEN = 8

#: P4X-P1 使用的固定帧间隔（全部 seed 一致；timing 变异留给后续阶段）
DEFAULT_DELAY_MS = 100

#: 等待"ECU 发出 FF"的上限
AWAIT_FC_TIMEOUT_MS = 1000

# ----------------------------------------------------------------------
# 状态模型（fuzzer 侧；非内核 ABI）
# ----------------------------------------------------------------------
#
# 命名刻意与内核不同，避免让人误以为可以直接套用：
# 内核 enum isotp_state 的取值见 net/can/isotp.c:129-135。
#
# 对应关系（P3K 已确认）：
#   RX_IDLE            ≈ ISOTP_IDLE
#   RX_WAIT_DATA       ≈ ISOTP_WAIT_DATA     （收到 FF 后等 CF）
#   TX_IDLE            ≈ ISOTP_IDLE
#   TX_WAIT_FIRST_FC   ≈ ISOTP_WAIT_FIRST_FC （发出 FF 后等首个 FC）
#   TX_SENDING         ≈ ISOTP_SENDING       （收到 CTS 后连续发 CF）
#   TX_WAIT_FC         ≈ ISOTP_WAIT_FC       （块边界处等下一个 FC）

RX_IDLE = "RX_IDLE"
RX_WAIT_DATA = "RX_WAIT_DATA"

TX_IDLE = "TX_IDLE"
TX_WAIT_FIRST_FC = "TX_WAIT_FIRST_FC"
TX_SENDING = "TX_SENDING"
TX_WAIT_FC = "TX_WAIT_FC"

#: 全部状态的只读集合
ALL_STATES = (
    RX_IDLE,
    RX_WAIT_DATA,
    TX_IDLE,
    TX_WAIT_FIRST_FC,
    TX_SENDING,
    TX_WAIT_FC,
)

# ----------------------------------------------------------------------
# ISO-TP PCI 常量
# ----------------------------------------------------------------------

PCI_SF = 0x00  # Single Frame
PCI_FF = 0x10  # First Frame
PCI_CF = 0x20  # Consecutive Frame
PCI_FC = 0x30  # Flow Control

PCI_NAMES = {
    PCI_SF: "SF",
    PCI_FF: "FF",
    PCI_CF: "CF",
    PCI_FC: "FC",
}

#: P3 已实测的四类基础 PCI 之外的高半字节（非法 PCI）
INVALID_PCI_NIBBLES = (0x4, 0x5, 0x6, 0x7, 0xF)

#: FC 流状态（FS）
FC_CTS = 0x0
FC_WAIT = 0x1
FC_OVERFLOW = 0x2

#: 保留/未定义的 FS 取值（P1 §14：单独归类，不与 OVERFLOW 混淆）
FC_RESERVED_STATUS = tuple(range(0x3, 0x10))

#: STmin 编码分段（ISO 15765-2）
STMIN_MS_RANGE = (0x00, 0x7F)        # 0–127 ms，合法
STMIN_US_RANGE = (0xF1, 0xF9)        # 100–900 us，合法
STMIN_RESERVED = tuple(range(0x80, 0xF1)) + tuple(range(0xFA, 0x100))


def classify_stmin(value: int) -> str:
    """
    按 ISO 15765-2 编码语义分类 STmin 字节。

    刻意**不**把 ``0x80+`` 一律叫 "invalid"：
    ``0xF1–0xF9`` 是合法的微秒档位；只有其余才是保留值。
    """
    if 0x00 <= value <= 0x7F:
        return "legal_ms"

    if 0xF1 <= value <= 0xF9:
        return "legal_us"

    return "reserved"


def is_invalid_pci(byte: int) -> bool:
    """PCI 高半字节是否落在四类基础帧之外。"""
    return (byte & 0xF0) not in PCI_NAMES


# ----------------------------------------------------------------------
# 已实测的 payload（与 P3 测试保持一致，不重新发明）
# ----------------------------------------------------------------------

#: ReadDataByIdentifier 0xF1 80 → 15 字节响应
DID_F180_REQUEST = bytes([0x22, 0xF1, 0x80])

#: ReadDataByIdentifier 0xF1 90 → 20 字节多帧响应（FF + 2 CF）
MULTIFRAME_REQUEST_20B = bytes([0x22, 0xF1, 0x90])

#: 22 字节请求 → FF + 3 CF（序号实验需要足够长的 payload）
MULTIFRAME_REQUEST_22B = bytes(
    [0x22, 0xF1, 0x90, 0xF1, 0x80, 0xF1, 0x87, 0xF1, 0x86, 0xF1, 0x85, 0xF1,
     0x84, 0xF1, 0x83, 0xF1, 0x82, 0xF1, 0x81, 0xF1, 0x80, 0xF1]
)

#: 单帧合法请求
SESSION_DEFAULT_REQUEST = bytes([0x10, 0x01])

#: 恢复探针（硬 oracle）
RECOVERY_REQUEST = bytes([0x10, 0x01])
RECOVERY_EXPECTED = bytes([0x50, 0x01, 0x00, 0x32, 0x00, 0x64])

#: 备选恢复探针
RECOVERY_REQUEST_READ = bytes([0x22, 0xF1, 0x80])
RECOVERY_EXPECTED_READ_PREFIX = bytes([0x62, 0xF1, 0x80])

#: ECU 对 22 F1 90 的 20 字节响应（P3 实测）
ECU_RESPONSE_20B = bytes(
    [0x62, 0xF1, 0x90, 0x54, 0x45, 0x53, 0x54, 0x45, 0x43, 0x55,
     0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x31]
)

# ----------------------------------------------------------------------
# 帧构造助手（纯函数，仅产生 bytes）
# ----------------------------------------------------------------------


def sf(payload: bytes) -> bytes:
    """合法单帧：PCI = 0x0N。"""
    return bytes([(PCI_SF | len(payload)) & 0xFF]) + payload


def sd(raw_pci: int, payload: bytes) -> bytes:
    """单帧但**显式指定** PCI 字节（用于声明长度与实际不符的实验）。"""
    return bytes([raw_pci & 0xFF]) + payload


def ff(total_length: int, first_data: bytes) -> bytes:
    """合法首帧：PCI = 0x1 + 12-bit 总长度。"""
    return bytes([
        PCI_FF | ((total_length >> 8) & 0x0F),
        total_length & 0xFF,
    ]) + first_data


def cf(sequence_number: int, data: bytes) -> bytes:
    """连续帧：PCI = 0x2 + SN。"""
    return bytes([PCI_CF | (sequence_number & 0x0F)]) + data


def fc(status: int, block_size: int = 0, stmin: int = 0) -> bytes:
    """流控帧：PCI = 0x3 + FS, BS, STmin。"""
    return bytes([PCI_FC | (status & 0x0F), block_size & 0xFF, stmin & 0xFF])


def padding(data: bytes, pad_byte: int = 0x00, dlc: int = CAN_DATA_MAX_LEN) -> bytes:
    """把帧填充到指定 DLC（默认 8）。"""
    if len(data) > dlc:
        raise ValueError(f"frame already longer than DLC {dlc}: {len(data)}")

    return data + bytes([pad_byte & 0xFF]) * (dlc - len(data))


# ----------------------------------------------------------------------
# seed 构造助手
# ----------------------------------------------------------------------


def inject(data: bytes, delay_ms: int = DEFAULT_DELAY_MS,
           arbitration_id: int = INJECT_ID) -> Dict:
    return {
        "type": "inject",
        "id": arbitration_id,
        "data": data,
        "delay_ms": delay_ms,
    }


def wait(delay_ms: int = DEFAULT_DELAY_MS) -> Dict:
    return {"type": "wait", "delay_ms": delay_ms}


def await_fc(timeout_ms: int = AWAIT_FC_TIMEOUT_MS,
             block_size: int = 0, stmin: int = 0,
             response_len: Optional[int] = None) -> Dict:
    """
    等 ECU 的 FF，然后回一个正常 CTS（让它的多帧传输得以继续）。

    ``response_len`` 记录**ECU 响应**的声明总长度。它不参与执行，
    只用于计算"剩余 CF 数"——因为这类 seed 里我们没有自己的 FF，
    而 BS 的"恰好覆盖剩余 CF"边界恰恰是针对 ECU 那条传输的。
    """
    action = {
        "type": "await_fc",
        "timeout_ms": timeout_ms,
        "block_size": block_size,
        "stmin": stmin,
    }

    if response_len is not None:
        action["response_len"] = response_len

    return action


def seed(seed_id: str, category: str, summary: str, actions: List[Dict],
         verified: bool = True, reference_only: bool = False) -> Dict:
    return {
        "seed_id": seed_id,
        "category": category,
        "summary": summary,
        "verified": verified,
        "reference_only": reference_only,
        "actions": actions,
    }


# ----------------------------------------------------------------------
# Corpus
# ----------------------------------------------------------------------

#: 22 字节请求的分片（FF 6 字节 + CF1 7 + CF2 7 + CF3 2）
_P22 = MULTIFRAME_REQUEST_22B
_P22_FF = ff(len(_P22), _P22[0:6])
_P22_CF1 = cf(1, _P22[6:13])
_P22_CF2 = cf(2, _P22[13:20])
_P22_CF3 = cf(3, _P22[20:22])

#: 8 字节请求（FF + CF1 即可完整）
_P8 = bytes([0x22, 0xF1, 0x90, 0xF1, 0x80, 0xF1, 0x87, 0xF1])
_P8_FF = ff(len(_P8), _P8[0:6])
_P8_CF1 = cf(1, _P8[6:])


def build_corpus() -> List[Dict]:
    """
    返回全部 seed。

    每个 seed 都对应 Phase 4-P3/P3R/P3R2 中**已实测**的一个 case
    （``verified=True``），或明确标注为参考实验（``reference_only=True``）。
    """
    seeds: List[Dict] = []

    # ---------------- baseline ----------------
    seeds.append(seed(
        "BASELINE_SF_1001", "baseline", "合法单帧 10 01（对照）",
        [inject(sf(SESSION_DEFAULT_REQUEST)), wait(500)],
    ))
    seeds.append(seed(
        "BASELINE_MF_22B", "baseline", "合法多帧请求 FF+CF1..CF3（对照）",
        [inject(_P22_FF), inject(_P22_CF1), inject(_P22_CF2), inject(_P22_CF3),
         wait(800)],
    ))
    seeds.append(seed(
        "BASELINE_MF_RESPONSE_20B", "baseline",
        "合法单帧请求触发 ECU 20 字节多帧响应（正常流控）",
        [inject(sf(MULTIFRAME_REQUEST_20B)), await_fc(response_len=20), wait(1500)],
    ))
    seeds.append(seed(
        "BASELINE_SN_SEQUENTIAL", "baseline", "顺序 SN 1,2,3（对照）",
        [inject(_P22_FF), inject(_P22_CF1), inject(_P22_CF2), inject(_P22_CF3),
         wait(800)],
    ))

    # ---------------- pci ----------------
    for nibble in INVALID_PCI_NIBBLES:
        byte = (nibble << 4) | 0x00
        seeds.append(seed(
            f"PCI_INVALID_{nibble:X}X", "pci",
            f"非法 PCI 高半字节 0x{nibble:X}（不属于 SF/FF/CF/FC）",
            [inject(bytes([byte, 0x10, 0x03, 0x00])), wait(600)],
        ))

    # ---------------- single_frame ----------------
    seeds.append(seed(
        "SF_DECLARES_MORE_THAN_PROVIDED", "single_frame",
        "声明 4 字节但只给 2 字节（04 10 03）",
        [inject(sd(0x04, bytes([0x10, 0x03]))), wait(600)],
    ))
    seeds.append(seed(
        "SF_DECLARES_LESS_THAN_PROVIDED", "single_frame",
        "声明 2 字节但给 3 字节（02 10 03 99）→ 按声明截断后正常交付",
        [inject(sd(0x02, bytes([0x10, 0x03, 0x99]))), wait(600)],
    ))
    seeds.append(seed(
        "SF_DECLARES_ZERO", "single_frame", "声明 0 字节 payload（00）",
        [inject(sd(0x00, b"")), wait(600)],
    ))
    seeds.append(seed(
        "SF_DECLARES_7_DLC_3", "single_frame", "声明 7 字节但 DLC=3",
        [inject(sd(0x07, bytes([0x22, 0xF1]))), wait(600)],
    ))

    # ---------------- first_frame ----------------
    seeds.append(seed(
        "FF_INCOMPLETE_NO_CF", "first_frame",
        "FF 声明 32 字节但之后不发任何 CF",
        [inject(ff(0x20, bytes([0x22, 0xF1, 0x90, 0xF1, 0x80, 0xF1]))),
         wait(1500)],
    ))
    seeds.append(seed(
        "FF_DECLARES_SMALL_EXTRA_CF", "first_frame",
        "FF 声明 3 字节却继续发 CF（多余 CF 应被忽略）",
        [inject(ff(3, bytes([0x10, 0x03, 0x00, 0x00, 0x00, 0x00]))),
         inject(cf(1, bytes([0xAA, 0xBB]))), wait(600)],
    ))
    seeds.append(seed(
        "FF_DECLARES_LARGE_SHORT_CF", "first_frame",
        "FF 声明 16 字节但 CF 只补到 8 字节（重组不完成）",
        [inject(ff(0x10, _P22[0:6])), inject(cf(1, bytes([0x87, 0xF1]))),
         wait(600)],
    ))

    # ---------------- consecutive_frame ----------------
    seeds.append(seed(
        "CF_WITHOUT_FF_SINGLE", "consecutive_frame", "孤立 CF（SN=1，无 FF）",
        [inject(cf(1, bytes([0x10, 0x03, 0x00, 0x00]))), wait(600)],
    ))
    seeds.append(seed(
        "CF_WITHOUT_FF_SEQUENCE", "consecutive_frame", "孤立 CF 序列 SN=1,2,3",
        [inject(cf(1, bytes([0x11, 0x22]))), inject(cf(2, bytes([0x33, 0x44]))),
         inject(cf(3, bytes([0x55, 0x66]))), wait(600)],
    ))

    # ---------------- sequence_number ----------------
    seeds.append(seed(
        "SN_SKIP_1_3", "sequence_number", "FF + CF1 + CF3（缺 SN=2）→ EILSEQ",
        [inject(_P22_FF), inject(_P22_CF1), inject(_P22_CF3), wait(800)],
    ))
    seeds.append(seed(
        "SN_REPEAT_1_1", "sequence_number", "FF + CF1 + CF1（重复）→ EILSEQ",
        [inject(_P22_FF), inject(_P22_CF1), inject(_P22_CF1), wait(800)],
    ))
    seeds.append(seed(
        "SN_BACKWARD_1_0", "sequence_number", "FF + CF1 + CF0（回退）→ EILSEQ",
        [inject(_P22_FF), inject(_P22_CF1), inject(cf(0, _P22[13:20])),
         wait(800)],
    ))
    seeds.append(seed(
        "SN_JUMP_1_F", "sequence_number", "FF + CF1 + CF15（大跳）→ EILSEQ",
        [inject(_P22_FF), inject(_P22_CF1), inject(cf(0xF, _P22[13:20])),
         wait(800)],
    ))
    seeds.append(seed(
        "SN_SKIP_2_3_MIDWAY", "sequence_number",
        "FF + CF1 + CF2 + CF3 中途把 CF2 改成跳过（SN 序列 1,3,3）",
        [inject(_P22_FF), inject(_P22_CF1), inject(cf(3, _P22[13:20])),
         inject(_P22_CF3), wait(800)],
    ))

    # ---------------- flow_control ----------------
    seeds.append(seed(
        "FC_UNSOLICITED_CTS", "flow_control", "无对应传输时的 CTS",
        [inject(fc(FC_CTS)), wait(600)],
    ))
    seeds.append(seed(
        "FC_UNSOLICITED_WAIT", "flow_control", "无对应传输时的 WAIT",
        [inject(fc(FC_WAIT)), wait(600)],
    ))
    seeds.append(seed(
        "FC_UNSOLICITED_OVERFLOW", "flow_control", "无对应传输时的 OVERFLOW",
        [inject(fc(FC_OVERFLOW)), wait(600)],
    ))
    seeds.append(seed(
        "FC_OVERFLOW_DURING_SEND", "flow_control",
        "ECU 发送多帧响应中注入 OVERFLOW（→ EMSGSIZE）",
        [inject(sf(MULTIFRAME_REQUEST_20B)), await_fc(response_len=20),
         inject(fc(FC_OVERFLOW)), wait(1500)],
    ))
    seeds.append(seed(
        "FC_WAIT_DURING_SEND", "flow_control",
        "ECU 发送多帧响应中注入 WAIT",
        [inject(sf(MULTIFRAME_REQUEST_20B)), await_fc(response_len=20),
         inject(fc(FC_WAIT)), wait(1500)],
    ))
    seeds.append(seed(
        "FC_ONLY_RESTRICTED_CTS", "flow_control",
        "只回一个受限 CTS（BS=1, STmin=0x80）→ ECOMM",
        [inject(sf(MULTIFRAME_REQUEST_20B)), wait(150),
         inject(fc(FC_CTS, block_size=1, stmin=0x80)), wait(2500)],
    ))
    seeds.append(seed(
        "FC_BS_1", "flow_control", "额外注入 BS=1 的 CTS",
        [inject(sf(MULTIFRAME_REQUEST_20B)), await_fc(response_len=20),
         inject(fc(FC_CTS, block_size=1)), wait(1500)],
    ))
    seeds.append(seed(
        "FC_BS_255", "flow_control", "额外注入 BS=255 的 CTS",
        [inject(sf(MULTIFRAME_REQUEST_20B)), await_fc(response_len=20),
         inject(fc(FC_CTS, block_size=255)), wait(1500)],
    ))
    seeds.append(seed(
        "FC_STMIN_100US", "flow_control", "额外注入 STmin=0xF1（合法 100us）",
        [inject(sf(MULTIFRAME_REQUEST_20B)), await_fc(response_len=20),
         inject(fc(FC_CTS, stmin=0xF1)), wait(1500)],
    ))
    seeds.append(seed(
        "FC_STMIN_RESERVED_80", "flow_control", "额外注入 STmin=0x80（保留值）",
        [inject(sf(MULTIFRAME_REQUEST_20B)), await_fc(response_len=20),
         inject(fc(FC_CTS, stmin=0x80)), wait(1500)],
    ))
    seeds.append(seed(
        "FC_STMIN_RESERVED_F0", "flow_control", "额外注入 STmin=0xF0（保留值）",
        [inject(sf(MULTIFRAME_REQUEST_20B)), await_fc(response_len=20),
         inject(fc(FC_CTS, stmin=0xF0)), wait(1500)],
    ))
    seeds.append(seed(
        "FC_STMIN_127MS", "flow_control", "额外注入 STmin=0x7F（合法上限）",
        [inject(sf(MULTIFRAME_REQUEST_20B)), await_fc(response_len=20),
         inject(fc(FC_CTS, stmin=0x7F)), wait(1500)],
    ))

    # ---------------- padding ----------------
    seeds.append(seed(
        "PAD_SF_ZERO_PADDING", "padding", "单帧声明 2 字节，尾部 5 个 0x00 填充",
        [inject(padding(sf(SESSION_DEFAULT_REQUEST), 0x00)), wait(600)],
    ))
    seeds.append(seed(
        "PAD_SF_AA_PADDING", "padding", "单帧声明 2 字节，尾部 5 个 0xAA 填充",
        [inject(padding(sf(SESSION_DEFAULT_REQUEST), 0xAA)), wait(600)],
    ))
    seeds.append(seed(
        "PAD_MF_CF_PADDED", "padding", "多帧请求的 CF 填充到 DLC=8",
        [inject(_P8_FF), inject(padding(_P8_CF1, 0xAA)), wait(800)],
    ))

    # ---------------- recovery ----------------
    seeds.append(seed(
        "RECOVERY_PLAIN", "recovery", "仅正常请求（恢复基线）",
        [inject(sf(SESSION_DEFAULT_REQUEST)), wait(500)],
    ))

    # ---------------- addressing（reference-only） ----------------
    for aid, label in ((0x7E1, "相邻物理地址"), (FUNCTIONAL_ID, "功能寻址广播"),
                       (0x000, "无关 ID")):
        seeds.append(seed(
            f"ADDR_REF_{aid:03X}", "addressing", f"{label}（参考实验，不做变异）",
            [inject(sf(SESSION_DEFAULT_REQUEST), arbitration_id=aid), wait(600)],
            reference_only=True,
        ))

    return seeds


#: 模块级只读 corpus
CORPUS: List[Dict] = build_corpus()

#: 类别 → seed 数
CATEGORIES = (
    "baseline",
    "pci",
    "single_frame",
    "first_frame",
    "consecutive_frame",
    "sequence_number",
    "flow_control",
    "padding",
    "recovery",
    "addressing",
)

#: 第一版允许变异的类别（addressing 仅参考）
MUTABLE_CATEGORIES = tuple(c for c in CATEGORIES if c != "addressing")


def seeds_by_category(category: str) -> List[Dict]:
    return [s for s in CORPUS if s["category"] == category]


def get_seed(seed_id: str) -> Optional[Dict]:
    for s in CORPUS:
        if s["seed_id"] == seed_id:
            return s

    return None


def corpus_stats() -> Dict[str, int]:
    stats: Dict[str, int] = {}

    for s in CORPUS:
        stats[s["category"]] = stats.get(s["category"], 0) + 1

    return stats
