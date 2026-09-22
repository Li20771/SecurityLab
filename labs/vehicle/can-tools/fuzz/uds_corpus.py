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
    "V1_IN_SIDS",
    "CONTROL_BYTE_SIDS",
    "SUPPRESS_SIDS",
    "CORPUS",
    "UdsEncodeError",
    "encode_uds_request",
    "sf_invariant_ok",
    "iter_seeds",
    "seed_by_id",
]


#: v1 单请求的 UDS 长度上限（Frozen Contract §3：超出**不生成**，不是截断）。
MAX_UDS_REQUEST_LEN = 7

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
    把**一个** UDS 请求编码成 ``frames[]``（恰好一个 CAN 单帧）。

    :raises UdsEncodeError: 空请求、超过 :data:`MAX_UDS_REQUEST_LEN`、
        或编码结果不满足 TRANSPORT INVARIANT。

    刻意只支持 1 请求 → 1 帧：v1 的层边界就是单帧，任何需要 FF/CF 的
    路径都属下一轮 Design Review（OQ3），在这里被**明确拒绝**而不是静默降级。
    """
    raw = bytes(uds_request)

    if not raw:
        raise UdsEncodeError("UDS 请求不能为空")

    if len(raw) > MAX_UDS_REQUEST_LEN:
        raise UdsEncodeError(
            f"UDS 请求 {len(raw)} 字节 > v1 上限 {MAX_UDS_REQUEST_LEN}"
            f"（multi-frame 属 OUT OF SCOPE）：{raw.hex(' ')}"
        )

    frame = isotp_corpus.sf(raw)

    # TRANSPORT INVARIANT：由唯一判据守门（编码器与测试共用）
    if not sf_invariant_ok(frame):
        raise UdsEncodeError(
            f"TRANSPORT INVARIANT 违反：frame={frame.hex(' ')} "
            f"payload={len(raw)} 字节"
        )

    return [isotp_corpus.inject(frame)]


def _seed(seed_id: str, uds_request: bytes, service: int, category: str,
          control_byte: int = None, note: str = "") -> Dict:
    """构造一个 seed（构造期做长度与编码自洽校验）。"""
    raw = bytes(uds_request)

    if len(raw) > MAX_UDS_REQUEST_LEN:
        raise UdsEncodeError(f"seed {seed_id} 超过 {MAX_UDS_REQUEST_LEN} 字节")

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
