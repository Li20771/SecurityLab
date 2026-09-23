#!/usr/bin/env python3
"""
UDS fuzz —— UDS 层变异（**恰好 4 个维度**，v1 + OQ3 Phase 1）

维度（Frozen Contract：不得新增第五维）：

    uds_len / uds_sid / uds_sub / uds_payload
    **没有 uds_seq** —— 跨请求序列属下一阶段 Stateful Design Review

三个硬约束（两条路径共用）：

1. **deterministic**：``same seed + same mutation spec`` → same frames
2. **no-op rejected**：变异后 frames 的 ``frames_fingerprint`` 与 seed 相同 → 拒绝
3. **final len <= envelope**：超出**本 seed 自己 envelope**（R5-b：v1 seed = 7、
   MF seed = 128）的变异**不生成**（生成期过滤）或在 apply 期拒绝，
   **绝不截断**

两条变异路径（OQ3）：

    payload 级 : 改 UDS payload 字节 → 重新编码（uds_corpus.encode_uds_request）
    mf_* 帧级  : 改**规范分帧**的 FF/CF PCI 字节或删除末帧，**payload 字节不变**

mf_* 是唯一允许**故意破坏 transport framing** 的变异（Frozen Contract Layer B），
共七个：M1..M7，全部位于 ``uds_len`` 维度，全部带 ``mf_`` 保留前缀。

本模块**不 import** ``isotp_fuzz``（引擎）：变异逻辑保持纯函数，可完全离线单测。
帧指纹复用 ``isotp_mutators.frames_fingerprint``（不复制）。

层边界：本模块只作用于**一个请求**（及其分帧）；编码与去重在别处
（``uds_corpus`` / ``uds_fuzz``），跨请求状态一律 OUT OF SCOPE。
"""

import os
import sys
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)

for _p in (_TOOLS_DIR, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import isotp_corpus  # noqa: E402  （只取 PCI_FF / PCI_CF 常量）
import isotp_mutators  # noqa: E402  （只取 frames_fingerprint）
import uds_corpus  # noqa: E402
from uds_corpus import (  # noqa: E402
    CONTROL_BYTE_SIDS,
    MAX_UDS_REQUEST_LEN,
    SUPPRESS_SIDS,
    V1_IN_SIDS,
)

__all__ = [
    "DIM_LEN",
    "DIM_SID",
    "DIM_SUB",
    "DIM_PAYLOAD",
    "ALL_DIMENSIONS",
    "UNIMPL_SIDS",
    "SUPPRESS_BIT",
    "MF_OP_FF_DECLARED_ZERO",
    "MF_OP_FF_DECLARED_MINUS_ONE",
    "MF_OP_FF_DECLARED_PLUS_ONE",
    "MF_OP_FF_DECLARED_MAX",
    "MF_OP_CF_DROP_LAST",
    "MF_OP_CF_SN_ZERO",
    "MF_OP_CF_SN_OFF_BY_ONE",
    "MF_OPS",
    "FF_DECLARED_MAX",
    "MutationError",
    "mutation_specs_for_seed",
    "apply_mutation",
    "build_mutated_frames",
]


#: 四个维度（顺序即 spec 生成顺序，参与确定性排序的基准）。
DIM_LEN = "uds_len"
DIM_SID = "uds_sid"
DIM_SUB = "uds_sub"
DIM_PAYLOAD = "uds_payload"

ALL_DIMENSIONS: Tuple[str, ...] = (DIM_LEN, DIM_SID, DIM_SUB, DIM_PAYLOAD)

#: ``uds_sid`` 的固定取值（Frozen Contract §6）。
SID_FIXED_VALUES: Tuple[int, ...] = (0x00, 0x7F, 0xFF)

#: ``uds_sid.unimpl`` 的**固定**集合 —— 取自 README 已文档化的"尚未实现"清单，
#: 不自行发明。
UNIMPL_SIDS: Tuple[int, ...] = (0x06, 0x28, 0x39, 0x85)

#: ``uds_sub`` 的固定取值。
CONTROL_BYTE_FIXED_VALUES: Tuple[int, ...] = (0x00, 0x7F, 0xFF)

#: suppressPositiveResponseBit。
SUPPRESS_BIT = 0x80

#: ``uds_payload.field_boundary`` 中 0x2E 的数据长度取值。
DID_WRITE_DATA_LENGTHS: Tuple[int, ...] = (1, 4)


# ----------------------------------------------------------------------
# OQ3 multi-frame 传输变异（M1..M7）
# ----------------------------------------------------------------------
#
# 全部位于 ``uds_len`` 维度（attribution rule 4：它们改变"声明/实际长度关系"
# 或"预期 payload 的交付"）。它们修改的是**传输帧字节**（FF/CF 的 PCI），
# **不修改 UDS payload 字节** —— 这正是用保留前缀 ``mf_`` 与 v1 的
# payload 级 ``decl_*`` 区分开的原因。
#
# 禁止新增第八个 mf_* op，禁止任何 wildcard family（Frozen Contract）。

MF_OP_FF_DECLARED_ZERO = "mf_ff_declared_zero"
MF_OP_FF_DECLARED_MINUS_ONE = "mf_ff_declared_minus_one"
MF_OP_FF_DECLARED_PLUS_ONE = "mf_ff_declared_plus_one"
MF_OP_FF_DECLARED_MAX = "mf_ff_declared_max"
MF_OP_CF_DROP_LAST = "mf_cf_drop_last"
MF_OP_CF_SN_ZERO = "mf_cf_sn_zero"
MF_OP_CF_SN_OFF_BY_ONE = "mf_cf_sn_off_by_one"

#: 冻结的七个 mf_* op（顺序即 spec 生成顺序 M1..M7）。
MF_OPS: Tuple[str, ...] = (
    MF_OP_FF_DECLARED_ZERO,        # M1
    MF_OP_FF_DECLARED_MINUS_ONE,   # M2
    MF_OP_FF_DECLARED_PLUS_ONE,    # M3
    MF_OP_FF_DECLARED_MAX,         # M4
    MF_OP_CF_DROP_LAST,            # M5
    MF_OP_CF_SN_ZERO,              # M6
    MF_OP_CF_SN_OFF_BY_ONE,        # M7
)

#: FF 12-bit 声明长度的上限（M4 用）。
FF_DECLARED_MAX = 0x0FFF

#: 只改 FF PCI 的两个字节、不改帧数的 op。
_MF_DECLARED_OPS: Dict[str, str] = {
    MF_OP_FF_DECLARED_ZERO: "zero",
    MF_OP_FF_DECLARED_MINUS_ONE: "minus_one",
    MF_OP_FF_DECLARED_PLUS_ONE: "plus_one",
    MF_OP_FF_DECLARED_MAX: "max",
}

#: 只改单个 CF 的 PCI 低半字节（SN）的 op。
_MF_SN_OPS: Tuple[str, ...] = (MF_OP_CF_SN_ZERO, MF_OP_CF_SN_OFF_BY_ONE)


class MutationError(ValueError):
    """该 spec 不适用于该 seed（不适用 ≠ 失败）。"""


# ----------------------------------------------------------------------
# spec 构造
# ----------------------------------------------------------------------


def _spec(dimension: str, params: Dict, label: str) -> Dict:
    return {"dimension": dimension, "params": params, "label": label}


def _field_boundary_specs(raw: bytes) -> List[Dict]:
    """
    逐服务的 ``field_boundary`` spec（Frozen Contract §6 的精确集合）。

    只对**该 seed 确实具备对应字段**的服务生成；字段不存在 → 不生成。
    """
    service = raw[0]
    length = len(raw)
    out: List[Dict] = []

    def add(params: Dict, label: str) -> None:
        out.append(_spec(DIM_PAYLOAD, params, label))

    if service == 0x22 and length >= 3:
        for value in (0xFFFF, 0x0000):
            add({"op": "field", "target": "did", "value": value},
                f"field=did(0x{value:04X})")

    elif service == 0x14 and length == 4:
        for value in (0x000000, 0xFFFFFE):
            add({"op": "field", "target": "group_of_dtc", "value": value},
                f"field=groupOfDTC(0x{value:06X})")

    elif service == 0x19 and length >= 3:
        for value in (0x00, 0xFF):
            add({"op": "field", "target": "mask", "value": value},
                f"field=mask(0x{value:02X})")

    elif service == 0x31 and length >= 4:
        for value in (0x0000, 0xFFFF):
            add({"op": "field", "target": "rid", "value": value},
                f"field=rid(0x{value:04X})")

    elif service == 0x2E and length >= 4:
        for value in (0xFFFF, 0x0000):
            add({"op": "field", "target": "did", "value": value},
                f"field=did(0x{value:04X})")

        for data_length in DID_WRITE_DATA_LENGTHS:
            add({"op": "field", "target": "data_len", "value": data_length},
                f"field=dataLen({data_length})")

    elif service == 0x27 and length == 6:
        for value in (0x00000000, 0xFFFFFFFF):
            add({"op": "field", "target": "key", "value": value},
                f"field=key(0x{value:08X})")

    elif service == 0x38 and length >= 5:
        for value in (0x0000, 0x0001, 0xFFFF):
            add({"op": "field", "target": "path_len", "value": value},
                f"field=pathLen(0x{value:04X})")

        add({"op": "field", "target": "path_first", "value": 0x41},
            "field=path[0](0x41)")

        path_len = int.from_bytes(raw[2:4], "big")
        path = raw[4:4 + path_len]

        # 只有 ReadDir（mode 0x05）且路径**非根**且以 '/' 结尾时才成立。
        if raw[1] == 0x05 and len(path) > 1 and path.endswith(b"/"):
            add({"op": "field", "target": "strip_trailing_slash", "value": 1},
                "field=readDir-trailing-slash")

    return out


def _mf_specs(raw: bytes) -> List[Dict]:
    """
    MF seeds 专属的 M1..M7 spec（Frozen Contract "Complete mf_* enumeration"）。

    只在**规范分帧确实为 MF**（L ≥ 8）时生成。因为 v1 seed 的 envelope 固定为 7
    （R5-b），它们永远进不到这里 —— 所以 v1 seed 不会获得任何 mf_* spec。

    顺序固定为 M1..M5 然后 M6（CF index 升序）然后 M7（CF index 升序）。
    """
    if len(raw) <= MAX_UDS_REQUEST_LEN:
        return []

    specs: List[Dict] = [
        _spec(DIM_LEN, {"op": MF_OP_FF_DECLARED_ZERO}, "mf-ff-declared-zero"),
        _spec(DIM_LEN, {"op": MF_OP_FF_DECLARED_MINUS_ONE}, "mf-ff-declared-minus-one"),
        _spec(DIM_LEN, {"op": MF_OP_FF_DECLARED_PLUS_ONE}, "mf-ff-declared-plus-one"),
        _spec(DIM_LEN, {"op": MF_OP_FF_DECLARED_MAX}, "mf-ff-declared-max"),
        _spec(DIM_LEN, {"op": MF_OP_CF_DROP_LAST}, "mf-cf-drop-last"),
    ]

    cf_count = uds_corpus.framing_cf_count(len(raw))

    # ---- M6：SN := 0，但排除 SN 本来就是 0 的 CF（否则是 no-op）----
    for index in range(1, cf_count + 1):
        if uds_corpus.cf_sequence_number(index) == 0:
            continue

        specs.append(_spec(
            DIM_LEN, {"op": MF_OP_CF_SN_ZERO, "cf": index},
            f"mf-cf-sn-zero@cf{index}",
        ))

    # ---- M7：SN := (SN+1) & 0x0F，对**每一个** CF index 各一条 ----
    #       mod-16 自增没有不动点，所以不存在 no-op。
    for index in range(1, cf_count + 1):
        specs.append(_spec(
            DIM_LEN, {"op": MF_OP_CF_SN_OFF_BY_ONE, "cf": index},
            f"mf-cf-sn-off-by-one@cf{index}",
        ))

    return specs


def mutation_specs_for_seed(seed: Dict) -> List[Dict]:
    """
    返回某个 seed 上"有意义"的变异列表（**稳定顺序**）。

    生成期即过滤掉不适用 / 会超长的 spec —— 因此"最终请求 ≤ envelope"
    由构造保证。**envelope 取自该 seed 自身**（R5-b：v1 seed = 7，
    MF seed = 128），不是全局常数。
    """
    raw = bytes(seed["uds_request"])
    length = len(raw)
    service = raw[0]
    envelope = seed.get("envelope", MAX_UDS_REQUEST_LEN)

    if seed.get("reference_only"):
        return []

    specs: List[Dict] = []

    # ---- uds_len（长度门限相对**本 seed 的** envelope）----
    if length >= 2:
        specs.append(_spec(DIM_LEN, {"op": "sid_only"}, "len=sid-only"))
        specs.append(_spec(DIM_LEN, {"op": "drop1"}, "len=drop1"))

    if length <= envelope - 1:
        specs.append(_spec(DIM_LEN, {"op": "add1_00"}, "len=+1(00)"))

    if length <= envelope - 2:
        specs.append(_spec(DIM_LEN, {"op": "add2_00"}, "len=+2(00)"))
        specs.append(_spec(DIM_LEN, {"op": "add2_ff"}, "len=+2(FF)"))

    # 声明长度错配只对 0x38 的 pathLen 有意义（改声明、不改实际 path 字节）
    if service == 0x38 and length >= 5:
        specs.append(_spec(DIM_LEN, {"op": "decl_plus"}, "decl=pathLen+1"))
        specs.append(_spec(DIM_LEN, {"op": "decl_minus"}, "decl=pathLen-1"))

    # ---- uds_sid ----
    for value in SID_FIXED_VALUES:
        specs.append(_spec(DIM_SID, {"value": value}, f"sid=0x{value:02X}"))

    for value in UNIMPL_SIDS:
        specs.append(_spec(DIM_SID, {"value": value}, f"sid=unimpl-0x{value:02X}"))

    for value in V1_IN_SIDS:
        if value != service:
            specs.append(_spec(DIM_SID, {"value": value}, f"sid=cross-0x{value:02X}"))

    # ---- uds_sub（服务控制字节）----
    if service in CONTROL_BYTE_SIDS and length >= 2:
        for value in CONTROL_BYTE_FIXED_VALUES:
            specs.append(_spec(DIM_SUB, {"value": value}, f"ctl=0x{value:02X}"))

        # suppress **只对真正实现该位的服务**生成（Frozen Contract §8）
        if service in SUPPRESS_SIDS:
            specs.append(_spec(DIM_SUB, {"op": "suppress"}, "ctl=suppress"))

    # ---- uds_payload ----
    if length >= 2:
        specs.append(_spec(DIM_PAYLOAD, {"op": "all_zero"}, "pay=all-00"))
        specs.append(_spec(DIM_PAYLOAD, {"op": "all_ff"}, "pay=all-FF"))

        # bitflip：**恰 8 * (L-1)** 条（不含 SID 所在字节）
        for index in range(1, length):
            for bit in range(8):
                specs.append(_spec(
                    DIM_PAYLOAD, {"op": "bitflip", "index": index, "bit": bit},
                    f"pay=flip[{index}].{bit}",
                ))

        for index in range(1, length):
            specs.append(_spec(DIM_PAYLOAD, {"op": "del", "index": index},
                               f"pay=del[{index}]"))
            specs.append(_spec(
                DIM_PAYLOAD, {"op": "rep", "index": index, "value": 0x00},
                f"pay=rep[{index}]=00"))
            specs.append(_spec(
                DIM_PAYLOAD, {"op": "rep", "index": index, "value": 0xFF},
                f"pay=rep[{index}]=FF"))

    specs.extend(_field_boundary_specs(raw))

    # ---- OQ3：MF 传输变异（只对 MF seed 生成；v1 seed 永远进不来）----
    specs.extend(_mf_specs(raw))

    return specs


# ----------------------------------------------------------------------
# 变异应用（字节级，纯函数）
# ----------------------------------------------------------------------


def _apply_len(raw: bytes, params: Dict) -> bytes:
    op = params["op"]

    if op == "sid_only":
        return raw[:1]

    if op == "drop1":
        return raw[:-1]

    if op == "add1_00":
        return raw + b"\x00"

    if op == "add2_00":
        return raw + b"\x00\x00"

    if op == "add2_ff":
        return raw + b"\xff\xff"

    if op in ("decl_plus", "decl_minus"):
        declared = int.from_bytes(raw[2:4], "big")
        delta = 1 if op == "decl_plus" else -1
        new_declared = declared + delta

        if not 0 <= new_declared <= 0xFFFF:
            raise MutationError(f"声明长度越界：{new_declared}")

        # 只改声明值，**不动实际 path 字节**
        return raw[:2] + new_declared.to_bytes(2, "big") + raw[4:]

    raise MutationError(f"未知 uds_len op {op!r}")


def _apply_sid(raw: bytes, params: Dict) -> bytes:
    return bytes([params["value"] & 0xFF]) + raw[1:]


def _apply_sub(raw: bytes, params: Dict) -> bytes:
    service = raw[0]

    if len(raw) < 2:
        raise MutationError("请求没有控制字节")

    if service not in CONTROL_BYTE_SIDS:
        raise MutationError(f"0x{service:02X} 没有服务控制字节")

    if params.get("op") == "suppress":
        # 应用期再守一次：这是"0x38 绝不可能获得 suppress"的兜底
        if service not in SUPPRESS_SIDS:
            raise MutationError(f"0x{service:02X} 未实现 suppressPositiveResponseBit")

        value = raw[1] | SUPPRESS_BIT
    else:
        value = params["value"] & 0xFF

    return bytes([raw[0], value]) + raw[2:]


def _apply_payload(raw: bytes, params: Dict) -> bytes:
    op = params["op"]
    length = len(raw)

    if op == "all_zero":
        return raw[:1] + bytes(length - 1)

    if op == "all_ff":
        return raw[:1] + b"\xff" * (length - 1)

    if op == "bitflip":
        index = params["index"]
        bit = params["bit"]

        if not 1 <= index < length:
            raise MutationError(f"bitflip 下标 {index} 越界（长度 {length}）")

        mutated = bytearray(raw)
        mutated[index] ^= 1 << bit
        return bytes(mutated)

    if op == "del":
        index = params["index"]

        if not 1 <= index < length:
            raise MutationError(f"del 下标 {index} 越界（长度 {length}）")

        return raw[:index] + raw[index + 1:]

    if op == "rep":
        index = params["index"]

        if not 1 <= index < length:
            raise MutationError(f"rep 下标 {index} 越界（长度 {length}）")

        return raw[:index] + bytes([params["value"] & 0xFF]) + raw[index + 1:]

    if op == "field":
        return _apply_field(raw, params)

    raise MutationError(f"未知 uds_payload op {op!r}")


def _apply_field(raw: bytes, params: Dict) -> bytes:
    target = params["target"]
    value = params["value"]

    if target == "did":
        return raw[:1] + (value & 0xFFFF).to_bytes(2, "big") + raw[3:]

    if target == "group_of_dtc":
        return raw[:1] + (value & 0xFFFFFF).to_bytes(3, "big")

    if target == "mask":
        return raw[:2] + bytes([value & 0xFF]) + raw[3:]

    if target == "rid":
        return raw[:2] + (value & 0xFFFF).to_bytes(2, "big") + raw[4:]

    if target == "key":
        return raw[:2] + (value & 0xFFFFFFFF).to_bytes(4, "big")

    if target == "data_len":
        data = raw[3:]
        new_data = (data + bytes(value))[:value]
        return raw[:3] + new_data

    if target == "path_len":
        return raw[:2] + (value & 0xFFFF).to_bytes(2, "big") + raw[4:]

    if target == "path_first":
        if len(raw) < 5:
            raise MutationError("请求没有 path 字节")
        return raw[:4] + bytes([value & 0xFF]) + raw[5:]

    if target == "strip_trailing_slash":
        declared = int.from_bytes(raw[2:4], "big")
        path = raw[4:4 + declared]

        if not path.endswith(b"/") or len(path) <= 1:
            raise MutationError("路径不是非根目录形态，无法去尾斜杠")

        # 同时把声明减 1，保持 pathLen 与实际 path 字节自洽
        return raw[:2] + (declared - 1).to_bytes(2, "big") + path[:-1]

    raise MutationError(f"未知 field target {target!r}")


def apply_mutation(seed: Dict, spec: Dict) -> Tuple[Optional[bytes], Optional[str]]:
    """
    对 seed 施加一条**payload 级** mutation，返回 ``(mutated_uds_bytes, reason)``。

    - 成功：``(bytes, None)``，长度已确保 ≤ **该 seed 的 envelope**（R5-b）
    - 不适用：``(None, reason)`` —— 不是失败，是"该 spec 对该 seed 无意义"

    OQ3 的 mf_* op 是**帧级**变异（改 FF/CF 的 PCI，不改 payload 字节），
    因此本函数对它们一律返回 ``(None, reason)``，要求调用方改用
    :func:`build_mutated_frames` —— 避免"payload 没变"被误读成 no-op。
    """
    raw = bytes(seed["uds_request"])
    dimension = spec["dimension"]
    params = spec["params"]
    envelope = seed.get("envelope", MAX_UDS_REQUEST_LEN)

    if params.get("op") in MF_OPS:
        return None, (
            f"{params['op']} 是帧级（transport）变异，payload 字节不变；"
            f"请使用 build_mutated_frames()"
        )

    try:
        if dimension == DIM_LEN:
            mutated = _apply_len(raw, params)
        elif dimension == DIM_SID:
            mutated = _apply_sid(raw, params)
        elif dimension == DIM_SUB:
            mutated = _apply_sub(raw, params)
        elif dimension == DIM_PAYLOAD:
            mutated = _apply_payload(raw, params)
        else:
            return None, f"未知维度 {dimension!r}"
    except MutationError as exc:
        return None, str(exc)

    if not mutated:
        return None, "变异产生空请求"

    if len(mutated) > envelope:
        return None, (
            f"变异产生 {len(mutated)} 字节 > 本 seed envelope {envelope}"
            f"（超长不生成、不截断）"
        )

    return mutated, None


def _apply_mf_op(frames: List[Dict], raw: bytes,
                 spec: Dict) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """
    在**规范分帧**上施加一个 mf_* 传输变异（M1..M7）。

    只修改 FF/CF 的 PCI 字节（M5 除外，它删除帧），**绝不修改 payload 字节**。
    返回 ``(frames, reason)``。
    """
    op = spec["params"]["op"]
    out = [dict(action) for action in frames]

    # ---- M1..M4：只改 FF 的 12-bit 声明长度，帧数与 payload 均不变 ----
    if op in _MF_DECLARED_OPS:
        declared = {
            "zero": 0,
            "minus_one": len(raw) - 1,
            "plus_one": len(raw) + 1,
            "max": FF_DECLARED_MAX,
        }[_MF_DECLARED_OPS[op]]

        if not 0 <= declared <= FF_DECLARED_MAX:
            return None, f"声明长度 {declared} 超出 12-bit 范围"

        if declared == len(raw):
            return None, f"声明长度与规范值相同（{declared}）—— no-op"

        first = bytes(out[0]["data"])
        out[0] = dict(out[0], data=bytes([
            (isotp_corpus.PCI_FF | ((declared >> 8) & 0x0F)) & 0xFF,
            declared & 0xFF,
        ]) + first[2:])

        return out, None

    # ---- M5：删除最后一个 CF（FF 与其余 CF 逐字节不变）----
    if op == MF_OP_CF_DROP_LAST:
        if len(out) < 2:
            return None, "没有 CF 可删（该 case 不是 MF 分帧）"

        return out[:-1], None

    # ---- M6 / M7：只改某一个 CF 的 PCI 低半字节（SN）----
    if op in _MF_SN_OPS:
        index = int(spec["params"]["cf"])

        if not 1 <= index <= len(out) - 1:
            return None, f"CF index {index} 越界（本 case 有 {len(out) - 1} 个 CF）"

        action = out[index]
        data = bytes(action["data"])
        current = data[0] & 0x0F

        new_sn = 0 if op == MF_OP_CF_SN_ZERO else (current + 1) & 0x0F

        if new_sn == current:
            return None, f"CF #{index} 的 SN 已经是 {current} —— no-op"

        out[index] = dict(action, data=bytes([(data[0] & 0xF0) | new_sn]) + data[1:])

        return out, None

    return None, f"未知 mf_* op {op!r}"


def build_mutated_frames(seed: Dict, spec: Dict) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """
    完整变异流水（到 ``frames[]`` 为止，不含去重）：

        payload 级 op : spec → mutated UDS bytes → encoding → frames[]
        mf_* 帧级 op  : spec → canonical frames → 帧级 PCI/帧数变换
        然后两者都 → 与 seed 的 frames 比较指纹 → no-op 拒绝

    返回 ``(frames, skip_reason)``；形状与既有引擎的
    ``isotp_mutators.apply_mutation`` 对称。

    no-op 判据对两条路径**统一**：任何生成出来的 spec 都必须改变
    ``frames_fingerprint``（Frozen Contract §8），no-op 在这里就被拒，
    绝不依赖 campaign 去重兜底。
    """
    params = spec.get("params") or {}
    canonical_frames = uds_corpus.encode_uds_request(seed["uds_request"])

    if params.get("op") in MF_OPS:
        frames, reason = _apply_mf_op(
            canonical_frames, bytes(seed["uds_request"]), spec)

        if frames is None:
            return None, reason
    else:
        mutated, reason = apply_mutation(seed, spec)

        if mutated is None:
            return None, reason

        try:
            frames = uds_corpus.encode_uds_request(mutated)
        except uds_corpus.UdsEncodeError as exc:
            return None, f"编码失败：{exc}"

    if isotp_mutators.frames_fingerprint(frames) == \
            isotp_mutators.frames_fingerprint(canonical_frames):
        return None, (
            f"no-op mutation（{spec.get('label', spec['dimension'])} 与 seed 帧序列相同）"
        )

    return frames, None


if __name__ == "__main__":
    # 手动观察用：打印每个 seed 的 spec 数量与 no-op 拒绝数
    total = 0
    skipped = 0

    for seed in uds_corpus.iter_seeds():
        specs = mutation_specs_for_seed(seed)
        built = 0

        for spec in specs:
            frames, reason = build_mutated_frames(seed, spec)

            if frames is None:
                skipped += 1
            else:
                built += 1

        total += len(specs)
        print(f"  {seed['seed_id']:<28} specs={len(specs):>4}  built={built:>4}")

    print()
    print(f"spec 总数 = {total}  实际可生成 = {total - skipped}  拒绝 = {skipped}")
