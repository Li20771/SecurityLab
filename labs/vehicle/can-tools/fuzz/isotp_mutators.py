#!/usr/bin/env python3
"""
ISO-TP stateful fuzz —— mutation 生成（**纯函数模块**）

设计约束（Phase 4X-P1 §8/§9）：

- 所有 mutator 都是**纯函数**：输入 seed + 参数，输出新的 frame sequence。
  不做 send / sleep / capture / subprocess / 任何 I/O。
- **单变量原则**：每次 mutation 只改变一个主要语义字段。
  例如 SN mutation 只改 CF 的 PCI 低半字节，不动 payload / DLC / delay / CAN ID。
- 唯一例外是 padding/DLC 维度本身——它的研究变量就是 DLC。

因此本模块可以完全离线测试，且同样的输入必然产生同样的输出。
"""

from typing import Dict, List, Optional, Tuple

from isotp_corpus import (
    CAN_DATA_MAX_LEN,
    FC_CTS,
    FC_OVERFLOW,
    FC_RESERVED_STATUS,
    FC_WAIT,
    INJECT_ID,
    PCI_CF,
    PCI_FC,
    PCI_FF,
    PCI_SF,
    classify_stmin,
)

# ----------------------------------------------------------------------
# mutation 维度名（稳定标识，写入 testcase JSON）
# ----------------------------------------------------------------------

DIM_PCI = "pci"
DIM_SF_LENGTH = "sf_length"
DIM_FF_LENGTH = "ff_length"
DIM_SN = "sn"
DIM_FC_STATUS = "fc_status"
DIM_BS = "bs"
DIM_STMIN = "stmin"
DIM_PADDING = "padding"

ALL_DIMENSIONS = (
    DIM_PCI,
    DIM_SF_LENGTH,
    DIM_FF_LENGTH,
    DIM_SN,
    DIM_FC_STATUS,
    DIM_BS,
    DIM_STMIN,
    DIM_PADDING,
)

#: padding 变异使用的填充字节
PAD_BYTES = (0x00, 0xAA, 0xFF)

#: DLC 变异使用的目标长度（都是合法经典 CAN DLC）
DLC_TARGETS = (2, 3, 5, 8)


class MutationError(ValueError):
    """mutator 前置条件不满足（例如 seed 里没有可变的 CF）。"""


# ----------------------------------------------------------------------
# 帧定位与改写助手（纯函数）
# ----------------------------------------------------------------------


def _pci_of(action: Dict) -> Optional[int]:
    """取一个 action 的 PCI 高半字节（await_fc 语义上是 CTS：0x30）。"""
    if action.get("type") == "await_fc":
        return PCI_FC

    if action.get("type") == "inject":
        data = action.get("data") or b""

        return data[0] & 0xF0 if data else None

    return None


def _injects(actions: List[Dict]) -> List[int]:
    return [i for i, a in enumerate(actions) if a.get("type") == "inject"]


def _frames_with_pci(actions: List[Dict], pci: int) -> List[int]:
    return [i for i, a in enumerate(actions) if _pci_of(a) == pci]


def _patch(actions: List[Dict], index: int, data: bytes) -> List[Dict]:
    """返回 actions 的浅拷贝，并把第 index 个动作的 data 换成新值。"""
    new_actions = [dict(a) for a in actions]

    if len(data) > CAN_DATA_MAX_LEN:
        raise MutationError(
            f"mutated frame exceeds classic CAN DLC {CAN_DATA_MAX_LEN}: {len(data)}"
        )

    if new_actions[index].get("type") != "inject":
        raise MutationError("_patch 只能用于 inject 动作")

    new_actions[index]["data"] = data

    return new_actions


def _patch_fc_fields(actions: List[Dict], index: int, *,
                     status: Optional[int] = None,
                     bs: Optional[int] = None,
                     stmin: Optional[int] = None) -> List[Dict]:
    """
    改写一个"FC 语义"动作。

    FC 在 corpus 里有两种表示：

    - ``{"type": "inject", "data": bytes([0x30|FS, BS, STmin])}``
    - ``{"type": "await_fc", "block_size": BS, "stmin": STmin}``
      —— 它在语义上就是"等 ECU 的 FF 然后回一个 CTS"，
      因此 FS 固定为 CTS，只有 BS/STmin 可变。
    """
    new_actions = [dict(a) for a in actions]
    target = new_actions[index]

    if target.get("type") == "await_fc":
        if status is not None and status != FC_CTS:
            raise MutationError(
                "await_fc 语义固定为 CTS，不能改 FS —— "
                "若要注入 WAIT/OVERFLOW，请针对显式 FC 帧（inject 30/31/32）"
            )

        if bs is not None:
            target["block_size"] = bs

        if stmin is not None:
            target["stmin"] = stmin

        return new_actions

    data = target["data"]

    if len(data) < 3:
        raise MutationError("FC 至少需要 3 字节（PCI + BS + STmin）")

    first = data[0]

    if status is not None:
        first = (first & 0xF0) | (status & 0x0F)

    return _patch(
        actions,
        index,
        bytes([
            first,
            data[1] if bs is None else (bs & 0xFF),
            data[2] if stmin is None else (stmin & 0xFF),
        ]),
    )


def _resolve_target(actions: List[Dict], pci: int, which: str) -> int:
    """
    按 ``which`` 选择目标帧下标。

    ``first`` / ``last`` 用于 CF（跳号通常只改一个 CF）。
    """
    indices = _frames_with_pci(actions, pci)

    if not indices:
        raise MutationError(f"seed 中没有 PCI=0x{pci:02X} 的帧，无法变异")

    if which == "first":
        return indices[0]

    if which == "last":
        return indices[-1]

    if which.startswith("index:"):
        wanted = int(which.split(":", 1)[1])

        if wanted not in indices:
            raise MutationError(f"指定的帧下标 {wanted} 不是 PCI=0x{pci:02X}")

        return wanted

    raise MutationError(f"未知的 target 选择器：{which!r}")


# ----------------------------------------------------------------------
# 各维度 mutator
# ----------------------------------------------------------------------


def mutate_pci(actions: List[Dict], value: int, which: str = "first") -> List[Dict]:
    """
    PCI 高半字节变异（保留低半字节，只有 SF 的 SF_DL 语义依赖它）。

    保持 DLC 与 payload 不变——不做 "random CAN frame" fuzzing。
    若 seed 里找不到 SF/FF/CF/FC 四类基础帧之一，抛 MutationError。
    """
    candidates = _injects(actions)

    if not candidates:
        raise MutationError("seed 中没有 inject 帧")

    index = candidates[0]
    data = actions[index]["data"]

    if not data:
        raise MutationError("目标帧为空")

    high = (value & 0x0F) << 4
    new_first = high | (data[0] & 0x0F)

    return _patch(actions, index, bytes([new_first]) + data[1:])


def mutate_sf_length(actions: List[Dict], declared: int,
                     which: str = "first") -> List[Dict]:
    """只改 SF 的声明长度（PCI 低半字节），payload 字节原样保留。"""
    if not 0 <= declared <= 0x0F:
        raise MutationError(f"SF 声明长度必须在 0..15：{declared}")

    index = _resolve_target(actions, PCI_SF, which)
    data = actions[index]["data"]
    new_first = (data[0] & 0xF0) | (declared & 0x0F)

    return _patch(actions, index, bytes([new_first]) + data[1:])


def mutate_ff_length(actions: List[Dict], declared: int,
                     which: str = "first") -> List[Dict]:
    """只改 FF 的 12-bit 声明总长度，携带的数据字节原样保留。"""
    if not 0 <= declared <= 0xFFF:
        raise MutationError(f"FF 声明长度必须在 0..4095：{declared}")

    index = _resolve_target(actions, PCI_FF, which)
    data = actions[index]["data"]

    if len(data) < 2:
        raise MutationError("FF 至少需要 2 字节（PCI + 长度）")

    new = bytes([
        (data[0] & 0xF0) | ((declared >> 8) & 0x0F),
        declared & 0xFF,
    ]) + data[2:]

    return _patch(actions, index, new)


def mutate_sn(actions: List[Dict], value: int, which: str = "last") -> List[Dict]:
    """
    只改 CF 的序号（PCI 低半字节）。

    默认改**最后一个** CF——因为中途跳号才能观察内核重组行为；
    对单 CF seed 则自然退化为改那一个。
    """
    if not 0 <= value <= 0x0F:
        raise MutationError(f"SN 必须在 0..15：{value}")

    index = _resolve_target(actions, PCI_CF, which)
    data = actions[index]["data"]
    new_first = (data[0] & 0xF0) | (value & 0x0F)

    return _patch(actions, index, bytes([new_first]) + data[1:])


def mutate_fc_status(actions: List[Dict], status: int,
                     which: str = "last") -> List[Dict]:
    """只改 FC 的流状态（FS）。保留/未定义的 FS 也允许，由调用方分类。"""
    if not 0 <= status <= 0x0F:
        raise MutationError(f"FS 必须在 0..15：{status}")

    index = _resolve_target(actions, PCI_FC, which)

    return _patch_fc_fields(actions, index, status=status)


def mutate_bs(actions: List[Dict], value: int, which: str = "last") -> List[Dict]:
    """只改 FC 的块大小 BS。"""
    if not 0 <= value <= 0xFF:
        raise MutationError(f"BS 必须在 0..255：{value}")

    index = _resolve_target(actions, PCI_FC, which)

    return _patch_fc_fields(actions, index, bs=value)


def mutate_stmin(actions: List[Dict], value: int, which: str = "last") -> List[Dict]:
    """只改 FC 的 STmin。分类见 isotp_corpus.classify_stmin。"""
    if not 0 <= value <= 0xFF:
        raise MutationError(f"STmin 必须在 0..255：{value}")

    index = _resolve_target(actions, PCI_FC, which)

    return _patch_fc_fields(actions, index, stmin=value)


def mutate_padding(actions: List[Dict], pad_byte: int = 0xAA,
                   dlc: int = CAN_DATA_MAX_LEN) -> List[Dict]:
    """
    DLC / 填充变异：**payload 不变**，只改 DLC 与尾部填充。

    ``dlc`` 可以大于或小于当前帧长：
      - 大于：用 ``pad_byte`` 填充到 ``dlc``
      - 小于：截断到 ``dlc``（研究"声明与实际不符"的 DLC 维度）
    """
    if not 0 <= dlc <= CAN_DATA_MAX_LEN:
        raise MutationError(f"经典 CAN DLC 必须在 0..8：{dlc}")

    if not 0 <= pad_byte <= 0xFF:
        raise MutationError(f"填充字节必须在 0..255：{pad_byte}")

    # 选最长的 inject 帧作为填充目标（通常是 FF）
    indices = _injects(actions)

    if not indices:
        raise MutationError("seed 中没有 inject 帧")

    index = max(indices, key=lambda i: len(actions[i]["data"]))
    data = actions[index]["data"]

    if dlc >= len(data):
        new = data + bytes([pad_byte & 0xFF]) * (dlc - len(data))
    else:
        new = data[:dlc]

    return _patch(actions, index, new)


# ----------------------------------------------------------------------
# 变异计划：从 corpus 生成有界、可复现的 case 集合
# ----------------------------------------------------------------------


def remaining_cf_count(actions: List[Dict]) -> Optional[int]:
    """
    计算"剩余连续帧数"——即首帧之后的 CF 个数。

    BS 恰好等于该值是一个有意义的边界：它表示"**一次**块授权正好覆盖
    剩余全部 CF，因此不会再发第二个 FC"，与该值不匹配时行为不同。

    两种来源：

    1. 帧序列里**我们自己**的 FF → 用它的声明长度
    2. 序列里没有 FF，但有 ``await_fc(response_len=N)``
       → 用**对端（ECU）响应**的声明长度。FC 类 seed 属于这种：
       多帧传输是 ECU 发出的响应，BS 边界针对的正是它。

    :returns: CF 个数；无法判定时返回 ``None``
    """
    ff_indices = _frames_with_pci(actions, PCI_FF)
    declared: Optional[int] = None
    first_payload = 0

    if ff_indices:
        data = actions[ff_indices[0]].get("data") or b""

        if len(data) >= 2:
            declared = ((data[0] & 0x0F) << 8) | data[1]
            first_payload = max(0, len(data) - 2)
    else:
        for action in actions:
            if action.get("type") == "await_fc" and action.get("response_len"):
                declared = int(action["response_len"])
                first_payload = 6  # FF 携带的数据字节（经典 CAN）
                break

    if declared is None:
        return None

    if declared <= first_payload:
        return 0

    # 经典 CAN：每个 CF 携带 ll_dl - 1 = 7 字节
    return -(-(declared - first_payload) // 7)


def mutation_specs_for_seed(seed: Dict) -> List[Dict]:
    """
    返回某个 seed 上"有意义"的变异列表。

    只生成与 seed 结构匹配的变异（P1 §15：payload 不足以形成对应边界时，
    不创建无意义 mutation）。每条 spec 形如::

        {"dimension": "sn", "params": {"value": 3, "which": "last"},
         "label": "sn=3"}
    """
    specs: List[Dict] = []

    if seed.get("reference_only"):
        return specs

    actions = seed["actions"]
    has = {
        PCI_SF: bool(_frames_with_pci(actions, PCI_SF)),
        PCI_FF: bool(_frames_with_pci(actions, PCI_FF)),
        PCI_CF: bool(_frames_with_pci(actions, PCI_CF)),
        PCI_FC: bool(_frames_with_pci(actions, PCI_FC)),
    }

    # ---- PCI ----
    for nibble in (0x4, 0x5, 0x6, 0x7, 0xF):
        specs.append({
            "dimension": DIM_PCI,
            "params": {"value": nibble},
            "label": f"pci=0x{nibble:X}X",
        })

    # ---- SF length ----
    if has[PCI_SF]:
        for declared in (0, 1, 2, 3, 4, 5, 6, 7):
            specs.append({
                "dimension": DIM_SF_LENGTH,
                "params": {"declared": declared},
                "label": f"sf_len={declared}",
            })

    # ---- FF length ----
    if has[PCI_FF]:
        for declared in (0, 1, 3, 8, 0xFFF):
            specs.append({
                "dimension": DIM_FF_LENGTH,
                "params": {"declared": declared},
                "label": f"ff_len={declared}",
            })

    # ---- SN ----
    if has[PCI_CF]:
        for value in (0, 1, 2, 3, 0xF):
            specs.append({
                "dimension": DIM_SN,
                "params": {"value": value, "which": "last"},
                "label": f"sn={value}",
            })

    # ---- FC status ----
    if has[PCI_FC]:
        for status in (FC_CTS, FC_WAIT, FC_OVERFLOW):
            specs.append({
                "dimension": DIM_FC_STATUS,
                "params": {"status": status},
                "label": f"fs={status}",
            })

        # 保留 FS 单独归类（P1 §14：不与 OVERFLOW 混为一类）
        for status in (0x3, 0x4, 0xF):
            specs.append({
                "dimension": DIM_FC_STATUS,
                "params": {"status": status},
                "label": f"fs_reserved={status}",
                "class_hint": "FC_RESERVED_STATUS",
            })

        # ---- BS ----
        for bs in (0, 1, 2, 255):
            specs.append({
                "dimension": DIM_BS,
                "params": {"value": bs},
                "label": f"bs={bs}",
            })

        # "恰好等于剩余 CF 数"是一个有意义的块边界：
        # 它表示一次块授权正好覆盖剩余全部 CF（不会再发第二个 FC）。
        # 即使该数值与上面的取值重合，也单独标出来——
        # 否则这个边界在结果里就无法被识别。
        remaining = remaining_cf_count(actions)

        if remaining is not None and remaining > 0:
            specs.append({
                "dimension": DIM_BS,
                "params": {"value": remaining},
                "label": f"bs={remaining}-remaining",
                "boundary": "remaining_cf_count",
            })

        # ---- STmin（按 ISO-TP 编码语义分类）----
        for stmin in (0x00, 0x01, 0x7F, 0xF1, 0xF9, 0x80, 0xF0, 0xFA, 0xFF):
            specs.append({
                "dimension": DIM_STMIN,
                "params": {"value": stmin},
                "label": f"stmin=0x{stmin:02X}",
                "stmin_class": classify_stmin(stmin),
            })

    # ---- padding / DLC ----
    for dlc in DLC_TARGETS:
        for pad in PAD_BYTES:
            specs.append({
                "dimension": DIM_PADDING,
                "params": {"dlc": dlc, "pad_byte": pad},
                "label": f"dlc={dlc},pad=0x{pad:02X}",
            })

    return specs


#: 维度 → mutator 函数
MUTATORS = {
    DIM_PCI: mutate_pci,
    DIM_SF_LENGTH: mutate_sf_length,
    DIM_FF_LENGTH: mutate_ff_length,
    DIM_SN: mutate_sn,
    DIM_FC_STATUS: mutate_fc_status,
    DIM_BS: mutate_bs,
    DIM_STMIN: mutate_stmin,
    DIM_PADDING: mutate_padding,
}


def apply_mutation(seed: Dict, spec: Dict) -> Tuple[List[Dict], Optional[str]]:
    """
    对 seed 施加一条 mutation。

    返回 ``(new_actions, skip_reason)``：

    - 成功且**确实改变**了帧序列：``(actions, None)``
    - 不适用：``(None, reason)``——例如该 seed 没有对应帧类型
    - 不改变任何东西：``(None, "mutation 不改变帧序列…")``
      —— 例如把 SN 改成它原本就是的值。这不是失败，
      而是"无意义 mutation"（P1 §15：不创建无意义 mutation）。
    """
    mutator = MUTATORS.get(spec["dimension"])

    if mutator is None:
        return None, f"未知维度 {spec['dimension']!r}"

    try:
        mutated = mutator([dict(a) for a in seed["actions"]], **spec["params"])
    except MutationError as exc:
        return None, str(exc)

    if frames_fingerprint(mutated) == frames_fingerprint(seed["actions"]):
        return None, (
            f"mutation 不改变帧序列（{spec.get('label', spec['dimension'])} 与 seed 相同）"
        )

    return mutated, None


def frames_fingerprint(actions: List[Dict]) -> str:
    """
    生成 frame sequence 的稳定指纹（用于 determinism 与 replay 一致性检查）。

    只包含**注入语义**：CAN ID + 字节 + 延时类型；不含时间戳等执行结果。
    """
    parts = []

    for a in actions:
        kind = a.get("type")

        if kind == "inject":
            parts.append(
                f"I:{a['id']:03X}:{bytes(a['data']).hex()}:{a.get('delay_ms', 0)}"
            )
        elif kind == "wait":
            parts.append(f"W:{a.get('delay_ms', 0)}")
        elif kind == "await_fc":
            parts.append(
                f"A:{a.get('timeout_ms', 0)}:{a.get('block_size', 0)}:{a.get('stmin', 0)}"
            )
        else:
            parts.append(f"?{kind}")

    return "|".join(parts)
