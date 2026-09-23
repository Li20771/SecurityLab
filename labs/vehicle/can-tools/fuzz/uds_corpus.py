#!/usr/bin/env python3
"""
UDS fuzz v1 —— 合法单帧 UDS 种子 + UDS→ISO-TP SF 编码器

定位（Frozen Contract v3）：

    本模块 = UDS 层的**数据源**：合法单帧 UDS 请求种子 + 一个极小的编码器。

它**不是**第二套 fuzz 框架：

- 帧构造复用 ``isotp_corpus.sf()``（不复制 SF 编码逻辑）
- 种子集合复用既有 UDS 测试已固化的请求字节（不复制整个测试套件）
- 本模块完全不 import python-can / python-isotp

**v1 层边界（不得逾越）**：

- 只做 **单请求、单帧**：``len(uds_request) <= 7``
- 绝不做 multi-frame（FF/CF）、绝不做跨请求序列
- 超长请求一律 **reject**，不截断

**TRANSPORT INVARIANT**（冻结）：编码结果必须是自洽 SF ——
``PCI SF 长度字节 == 实际 UDS payload 长度``。任何不满足的请求在这里就被拒绝，
不允许生成"看起来像 UDS 但其实是 ISO-TP 畸形"的 case（那属于已被覆盖的
ISO-TP 层，会污染 UDS 层的信号）。

依赖方向：``uds_corpus`` ──> ``isotp_corpus``（只读复用帧原语）。
"""

import os
import sys
from typing import Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)

for _p in (_TOOLS_DIR, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import isotp_corpus  # noqa: E402

__all__ = [
    "MAX_UDS_REQUEST_LEN",
    "MAX_MF_UDS_REQUEST_LEN",
    "MAX_FRAMES_PER_CASE",
    "V1_IN_SIDS",
    "CONTROL_BYTE_SIDS",
    "SUPPRESS_SIDS",
    "CORPUS",
    "UdsEncodeError",
    "encode_uds_request",
    "sf_invariant_ok",
    "framing_cf_count",
    "cf_sequence_number",
    "framing_invariant_ok",
    "iter_seeds",
    "seed_by_id",
]


#: v1 单请求的 UDS 长度上限（Frozen Contract §3：超出**不生成**，不是截断）。
#: 同时是 **v1 seed 的 envelope**（R5-b：envelope 在 genesis 固定）。
MAX_UDS_REQUEST_LEN = 7

#: OQ3 Phase 1 的 multi-frame **传输信封**（Frozen Contract §3）。
#:
#: 注意：这是 fuzzing 的信封，**不是**"所有 UDS 服务的合法最大请求长度"。
#: 选它的原因：(a) 覆盖本工程可达的每个服务最大合法请求（0x38 addFile 用
#: 64 字节 path = 78 B；0x2E 用 16 字节 data = 19 B；0x34/0x35 = 8 B）；
#: (b) 与本工程既有的 128 字节请求长度约定一致； (c) 恰好给出有界的 case
#: 规模（见 MAX_FRAMES_PER_CASE）。超过本值的请求**不生成**。
MAX_MF_UDS_REQUEST_LEN = 128

#: 单个 case 允许的最大帧数（Frozen Contract §2/§3）。
#:
#: 128 字节 payload 经 FF(6) + CF(7) 分帧恰为 1 + ceil(122/7) = **19** 帧，
#: 注入耗时 19 × DEFAULT_DELAY_MS(100ms) = 1.90s，加观察窗口 0.35s = 2.25s，
#: 相对 CASE_TIMEOUT = 20s 有 8.9 倍余量。它同时约束规范序列与**任何**畸形
#: framing 变异（Layer C 准入条件之一）。
MAX_FRAMES_PER_CASE = 19

#: FF / CF 各自可携带的 payload 字节数（经典 CAN 8 字节减去 PCI 字节）。
_FF_PAYLOAD_LEN = isotp_corpus.CAN_DATA_MAX_LEN - 2   # 6
_CF_PAYLOAD_LEN = isotp_corpus.CAN_DATA_MAX_LEN - 1   # 7

#: v1 覆盖的 SID（Frozen Contract §2）。
#:
#: 0x34 / 0x35 的最小合法请求就是 **8 字节**（>7）→ OUT；
#: 0x36 在无 active transfer 时**任何形状**都先返回 0x24（分流前无长度检查，
#: 已核实）→ 变异零信号 → OUT；
#: 0x38 仅 mode 02/04/05 可能 ≤7（mode 01/03 至少 15 字节）→ 部分 IN。
V1_IN_SIDS = (0x10, 0x11, 0x14, 0x19, 0x22, 0x27, 0x2E, 0x31, 0x37, 0x38, 0x3E)

#: 请求第 2 字节是"服务控制字节"的 SID（Frozen Contract §7）。
#:
#: 注意该字节的**性质按服务不同**：0x10/0x11/0x31/0x3E 是 sub-function，
#: 0x19 是 reportType，0x27 是 securityAccessType，0x38 是 **modeOfOperation**
#: （**不是** sub-function）。这也是本维度命名为 "control byte" 而非
#: "sub-function" 的原因。
CONTROL_BYTE_SIDS = (0x10, 0x11, 0x19, 0x27, 0x31, 0x38, 0x3E)

#: 真正实现了 suppressPositiveResponseBit 的 SID（Frozen Contract §8）。
#:
#: 代码核实：``_SUPPRESS_POS_RSP_BIT`` 仅出现在 ``service_tester_present``
#: 内（uds_server.py:727/752/757），其余 10 个 handler 均不引用该位。
SUPPRESS_SIDS = (0x3E,)


class UdsEncodeError(ValueError):
    """UDS 请求无法编码为合法单帧。"""


def sf_invariant_ok(frame: bytes) -> bool:
    """
    TRANSPORT INVARIANT 检查（Frozen Contract §4）—— 判定一帧是否是
    v1 认可的**自洽单帧**：

    1. 必须是 ISO-TP SF（PCI 高半字节 = 0x0）
    2. **PCI 声明长度 == 实际 payload 长度**（帧长 = payload + 1 PCI 字节）
    3. 声明长度 ≤ :data:`MAX_UDS_REQUEST_LEN`（v1 只做单帧）

    本函数是编码器与测试**共用的唯一判据**：编码器用它守门，
    测试用它做 mutation guard（故意破坏 PCI 必须被它抓到）。
    """
    if not frame:
        return False

    if (frame[0] & 0xF0) != 0x00:
        return False

    declared = frame[0] & 0x0F

    if declared != len(frame) - 1:
        return False

    return declared <= MAX_UDS_REQUEST_LEN


def encode_uds_request(uds_request: bytes) -> List[Dict]:
    """
    把一个 UDS 请求编码成 ``frames[]``（**size-class aware**，OQ3）。

        L ≤ 7          → 恰好一个 SF（**与原 v1 路径逐字节相同**）
        8 ≤ L ≤ 128    → FF(6 payload bytes) + CF(7 payload bytes each)
        L > 128        → reject（**不生成、不截断**）

    帧原语一律复用 ``isotp_corpus.sf()`` / ``ff()`` / ``cf()`` ——
    本模块**不重新实现**任何 ISO-TP 分帧逻辑。

    编码器本身**与 seed 的 envelope 无关**：它按长度选择 size class。
    "某个 seed 只能长到多少"由 ``uds_mutators`` 按该 seed 自己的 envelope
    守门（R5-b），二者职责不同。

    :raises UdsEncodeError: 空请求、L > MAX_MF_UDS_REQUEST_LEN、帧数超过
        MAX_FRAMES_PER_CASE，或结果不满足 Layer A canonical 判据。
    """
    raw = bytes(uds_request)

    if not raw:
        raise UdsEncodeError("UDS 请求不能为空")

    if len(raw) > MAX_MF_UDS_REQUEST_LEN:
        raise UdsEncodeError(
            f"UDS 请求 {len(raw)} 字节 > OQ3 envelope {MAX_MF_UDS_REQUEST_LEN}"
            f"（超出不生成、不截断）：{raw.hex(' ')}"
        )

    # ---- SF 分支：v1 路径，字面未变（R5-c）----
    if len(raw) <= MAX_UDS_REQUEST_LEN:
        frame = isotp_corpus.sf(raw)

        if not sf_invariant_ok(frame):
            raise UdsEncodeError(
                f"TRANSPORT INVARIANT 违反：frame={frame.hex(' ')} "
                f"payload={len(raw)} 字节"
            )

        return [isotp_corpus.inject(frame)]

    # ---- MF 分支：FF + CF，全部复用既有原语 ----
    frames = [isotp_corpus.inject(isotp_corpus.ff(len(raw), raw[0:_FF_PAYLOAD_LEN]))]

    sequence = 1
    offset = _FF_PAYLOAD_LEN

    while offset < len(raw):
        frames.append(isotp_corpus.inject(
            isotp_corpus.cf(sequence, raw[offset:offset + _CF_PAYLOAD_LEN])
        ))
        sequence += 1
        offset += _CF_PAYLOAD_LEN

    if len(frames) > MAX_FRAMES_PER_CASE:
        raise UdsEncodeError(
            f"分帧产生 {len(frames)} 帧 > MAX_FRAMES_PER_CASE "
            f"{MAX_FRAMES_PER_CASE}"
        )

    if not framing_invariant_ok(raw, frames):
        raise UdsEncodeError(
            f"Layer A canonical 判据违反：payload={len(raw)} 字节 "
            f"frames={len(frames)}"
        )

    return frames


def framing_cf_count(payload_len: int) -> int:
    """
    规范分帧需要多少个 CF（SF 返回 0）。

    与 :func:`framing_invariant_ok` 共用同一算式，避免两处各自推导。
    """
    if payload_len <= MAX_UDS_REQUEST_LEN:
        return 0

    return (payload_len - _FF_PAYLOAD_LEN + _CF_PAYLOAD_LEN - 1) // _CF_PAYLOAD_LEN


def cf_sequence_number(cf_index: int) -> int:
    """
    CF #``cf_index`` 的规范序号（1-based）：``SN(j) = j mod 16``。

    即 CF #1..#15 携带 SN 1..15，CF #16 携带 **SN 0**，CF #17 携带 SN 1……
    本函数是 SN 相关 mf_* 变异判断"是否 no-op"的唯一依据。
    """
    return cf_index % 16


def framing_invariant_ok(uds_request: bytes, frames: List[Dict]) -> bool:
    """
    **Layer A canonical framing 判据**（Frozen Contract §4）——独立于编码器
    重新推导一遍分帧，判定 ``frames`` 是否是 ``uds_request`` 的**规范分帧**：

    - L ≤ 7：恰好 1 个 SF，PCI 长度 == L，payload 逐字节相同
    - 8 ≤ L ≤ 128：FF 声明 12-bit 总长 == L 且携带前 6 字节；随后
      C = ceil((L−6)/7) 个 CF，SN(j) = j mod 16 从 1 连续递增，除末块外
      每块 7 字节 payload，且拼接结果逐字节等于原 payload
    - 帧数 ≤ :data:`MAX_FRAMES_PER_CASE`

    刻意**只**用于 Layer A：Layer B 的 mf_* 变异是**故意**破坏 framing 的，
    对它们调用本函数必然为 False，因此**不得**把它当作"所有 case 的准入条件"
    （那是 Layer C 的职责，见 uds_fuzz）。
    """
    raw = bytes(uds_request)

    if not raw or len(raw) > MAX_MF_UDS_REQUEST_LEN:
        return False

    if len(frames) > MAX_FRAMES_PER_CASE:
        return False

    def frame_data(action: Dict) -> bytes | None:
        if not isinstance(action, Dict) or action.get("type") != "inject":
            return None

        if action.get("id") != isotp_corpus.INJECT_ID:
            return None

        return bytes(action.get("data", b""))

    # ---- SF ----
    if len(raw) <= MAX_UDS_REQUEST_LEN:
        if len(frames) != 1:
            return False

        data = frame_data(frames[0])

        if data is None or not sf_invariant_ok(data):
            return False

        return data[1:1 + len(raw)] == raw

    # ---- MF ----
    expected_cf = (len(raw) - _FF_PAYLOAD_LEN + _CF_PAYLOAD_LEN - 1) // _CF_PAYLOAD_LEN

    if len(frames) != 1 + expected_cf:
        return False

    first = frame_data(frames[0])

    if first is None or len(first) != 2 + _FF_PAYLOAD_LEN:
        return False

    if (first[0] & 0xF0) != isotp_corpus.PCI_FF:
        return False

    declared = ((first[0] & 0x0F) << 8) | first[1]

    if declared != len(raw):
        return False

    if first[2:] != raw[:_FF_PAYLOAD_LEN]:
        return False

    offset = _FF_PAYLOAD_LEN
    sequence = 1

    for action in frames[1:]:
        data = frame_data(action)

        if data is None or len(data) < 1:
            return False

        if (data[0] & 0xF0) != isotp_corpus.PCI_CF:
            return False

        if (data[0] & 0x0F) != (sequence & 0x0F):
            return False

        chunk = raw[offset:offset + _CF_PAYLOAD_LEN]

        if data[1:] != chunk:
            return False

        # 按**实际**块长推进：末块可能短于 7 字节
        offset += len(chunk)
        sequence += 1

    return offset == len(raw)


def _seed(seed_id: str, uds_request: bytes, service: int, category: str,
          control_byte: int = None, note: str = "",
          envelope: int = MAX_UDS_REQUEST_LEN) -> Dict:
    """
    构造一个 seed（构造期做长度与编码自洽校验）。

    ``envelope`` 是**本 seed 自己的长度信封**，在 genesis 固定（R5-b）：
    v1 seed 用默认的 7，MF seed 用 128。它决定 ``uds_mutators`` 对该 seed
    生成哪些长度相关 spec 的上限——**不会**被全局常数覆盖。
    """
    raw = bytes(uds_request)

    if len(raw) > envelope:
        raise UdsEncodeError(
            f"seed {seed_id} 长度 {len(raw)} 超过其 envelope {envelope}"
        )

    if raw[0] != service:
        raise UdsEncodeError(
            f"seed {seed_id} 声明 service 0x{service:02X} 与请求首字节 "
            f"0x{raw[0]:02X} 不符"
        )

    # 提前验证可编码，避免把一个坏 seed 带进 campaign
    encode_uds_request(raw)

    return {
        "seed_id": seed_id,
        "uds_request": raw,
        "service": service,
        "control_byte": control_byte,
        "category": category,
        "note": note,
        "envelope": envelope,
        "reference_only": False,
    }


#: v1 种子集合（**稳定顺序**，顺序即 campaign 候选顺序的基准）。
#:
#: 来源：既有 721 个 UDS 测试中已固化的请求字节，按"每服务/每 mode 一条代表"
#: 抽取，**刻意不复制整个测试套件**。每一条都 ≤7 字节且编码为自洽 SF。
CORPUS: List[Dict] = [
    _seed("uds-10-default-session", bytes([0x10, 0x01]), 0x10, "session",
          0x01, "10 01 肯定路径"),
    _seed("uds-11-hard-reset", bytes([0x11, 0x01]), 0x11, "reset",
          0x01, "11 01 肯定路径（单请求改状态，靠每 case fresh ECU 隔离）"),
    _seed("uds-14-clear-all", bytes([0x14, 0xFF, 0xFF, 0xFF]), 0x14, "dtc",
          None, "14 FFFFFF 肯定路径"),
    _seed("uds-19-count-by-mask", bytes([0x19, 0x01, 0xFF]), 0x19, "dtc",
          0x01, "19/01 肯定路径"),
    _seed("uds-19-supported", bytes([0x19, 0x0A]), 0x19, "dtc",
          0x0A, "19/0A 肯定路径（另一 reportType）"),
    _seed("uds-22-single-did", bytes([0x22, 0xF1, 0x80]), 0x22, "did",
          None, "22 单 DID 肯定路径"),
    _seed("uds-27-request-seed", bytes([0x27, 0x01]), 0x27, "security",
          0x01, "27 01 肯定路径"),
    _seed("uds-27-send-key-no-seed", bytes([0x27, 0x02, 0x00, 0x00, 0x00, 0x00]),
          0x27, "security", 0x02,
          "27 02 无前置 seed → 0x24（正向 seed→key 流属 NEXT DESIGN）"),
    _seed("uds-2e-short-write", bytes([0x2E, 0xF1, 0x02, 0x41]), 0x2E, "did",
          None, "2E 短数据（default session → 0x7F 门控）"),
    _seed("uds-31-start-counter", bytes([0x31, 0x01, 0x20, 0x01]), 0x31,
          "routine", 0x01, "31 StartRoutine 肯定路径"),
    _seed("uds-37-no-transfer", bytes([0x37]), 0x37, "transfer",
          None, "37 无 active transfer → 0x24（长度规则先于分流）"),
    _seed("uds-38-readdir-root", bytes([0x38, 0x05, 0x00, 0x01, 0x2F]), 0x38,
          "file", 0x05, "38/05 ReadDir '/' 肯定路径"),
    _seed("uds-38-readdir-subdir", bytes([0x38, 0x05, 0x00, 0x03, 0x2F, 0x61, 0x2F]),
          0x38, "file", 0x05,
          "38/05 ReadDir '/a/' → 0x31；提供『尾斜杠』变异目标（路径非根）"),
    _seed("uds-38-readfile-missing", bytes([0x38, 0x04, 0x00, 0x02, 0x2F, 0x61, 0x00]),
          0x38, "file", 0x04,
          "38/04 ReadFile '/a' → 0x31（出厂无 ≤3 字节路径，故为负面代表）"),
    _seed("uds-38-deletefile-missing", bytes([0x38, 0x02, 0x00, 0x02, 0x2F, 0x61]),
          0x38, "file", 0x02, "38/02 DeleteFile '/a' → 0x31"),
    _seed("uds-3e-tester-present", bytes([0x3E, 0x00]), 0x3E, "session",
          0x00, "3E 00 肯定路径"),
    _seed("uds-3e-suppress", bytes([0x3E, 0x80]), 0x3E, "session",
          0x80, "3E 80 **合法抑制路径**（本 ECU 唯一实现 suppress 的服务）"),

    # ---------------- OQ3 multi-frame seeds（envelope = 128）----------------
    #
    # 覆盖 Frozen Contract §13 的 MF scope：0x22（>2 DID）、0x2E（data >4）、
    # 0x34（8B）、0x35（8B）、0x38 mode 01/03 与长路径（pathLen ≤ 64）。
    # 刻意**不含** 0x36 / 0x37 standalone、不含 stateful 序列、不含 0x27 seed→key。
    _seed("uds-mf-22-four-dids",
          bytes([0x22, 0xF1, 0x80, 0xF1, 0x90, 0xF1, 0x87, 0xF1, 0x86]),
          0x22, "did", None,
          "22 四个 DID = 9 字节 → FF + CF1（v1 只覆盖 ≤2 DID）",
          envelope=MAX_MF_UDS_REQUEST_LEN),
    _seed("uds-mf-2e-16b-data",
          bytes([0x2E, 0xF1, 0x02]) + b"CONFIG-16-BYTES!",
          0x2E, "did", None,
          "2E 写入 16 字节（可写 DID 0xF102 的上限）= 19 字节",
          envelope=MAX_MF_UDS_REQUEST_LEN),
    _seed("uds-mf-34-download",
          bytes([0x34, 0x00, 0x23, 0x11, 0x22, 0x33, 0x00, 0x08]),
          0x34, "transfer", None,
          "34 RequestDownload 最小合法请求 = 8 字节（MF 的**下边界**）",
          envelope=MAX_MF_UDS_REQUEST_LEN),
    _seed("uds-mf-35-upload",
          bytes([0x35, 0x00, 0x23, 0x11, 0x22, 0x33, 0x00, 0x08]),
          0x35, "transfer", None,
          "35 RequestUpload 最小合法请求 = 8 字节",
          envelope=MAX_MF_UDS_REQUEST_LEN),
    _seed("uds-mf-38-addfile-64b-path",
          bytes([0x38, 0x01, 0x00, 0x40]) + b"/" + b"a" * 63
          + bytes([0x00, 0x04]) + (4).to_bytes(4, "big") * 2,
          0x38, "file", 0x01,
          "38/01 AddFile pathLen=64（模型上限）= 78 字节；v1 因 ≥15 字节而 OUT",
          envelope=MAX_MF_UDS_REQUEST_LEN),
    _seed("uds-mf-38-replacefile",
          bytes([0x38, 0x03, 0x00, 0x11]) + b"/boot/version.txt"
          + bytes([0x00, 0x04]) + (4).to_bytes(4, "big") * 2,
          0x38, "file", 0x03,
          "38/03 ReplaceFile 指向真实出厂文件 = 31 字节",
          envelope=MAX_MF_UDS_REQUEST_LEN),
]


def iter_seeds():
    """按稳定顺序遍历种子。"""
    return iter(CORPUS)


def seed_by_id(seed_id: str) -> Dict:
    """按 seed_id 取种子；不存在则 KeyError。"""
    for seed in CORPUS:
        if seed["seed_id"] == seed_id:
            return seed

    raise KeyError(seed_id)


if __name__ == "__main__":
    # 手动观察用：打印种子与其编码结果
    print(f"v1 单请求上限 = {MAX_UDS_REQUEST_LEN} 字节")
    print(f"v1 IN SID      = {[f'0x{s:02X}' for s in V1_IN_SIDS]}")
    print(f"控制字节服务   = {[f'0x{s:02X}' for s in CONTROL_BYTE_SIDS]}")
    print(f"suppress 服务  = {[f'0x{s:02X}' for s in SUPPRESS_SIDS]}")
    print()

    for s in CORPUS:
        frames = encode_uds_request(s["uds_request"])
        wire = frames[0]["data"].hex(" ").upper()

        print(f"  {s['seed_id']:<28} {s['uds_request'].hex(' '):<20}"
              f" ({len(s['uds_request'])}B) -> {wire}")
