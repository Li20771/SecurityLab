#!/usr/bin/env python3
"""
CAN / ISO-TP 实验工具的公共解析与校验

本模块只包含**纯函数**（无 I/O、无 CAN 依赖），因此可以脱离
vcan0 / python-can 环境独立测试。

三个 CLI 工具（``can_capture.py`` / ``can_inject.py`` / ``isotp_send.py``）
共用本模块的输入校验逻辑，避免同一套规则重复实现三遍。

安全边界：

``guard_interface()`` 只允许 ``vcan0``。这是刻意的设计——
这些工具用于隔离实验环境，不应因为参数误写而把流量发到真实 CAN 接口。
"""

import argparse
from typing import Optional, Sequence

#: 本实验工具唯一允许的 CAN 接口。
ALLOWED_INTERFACE = "vcan0"

#: 标准 11-bit CAN ID 范围。
CAN_ID_MIN = 0x000
CAN_ID_MAX = 0x7FF

#: 经典 CAN 单帧最大数据长度。
CAN_DATA_MAX_LEN = 8


class InputError(ValueError):
    """输入校验失败（CLI 层会把它转换成清晰的错误信息）。"""


def parse_can_id(text: str) -> int:
    """
    解析 11-bit CAN ID，接受 ``0x7E0`` 与 ``2016`` 两种写法。

    :raises InputError: 非整数，或超出 0x000–0x7FF
    """
    raw = text.strip()

    if not raw:
        raise InputError("CAN ID 不能为空")

    try:
        value = int(raw, 0)
    except ValueError:
        raise InputError(
            f"无法解析 CAN ID：{text!r}（示例：0x7E0 或 2016）"
        ) from None

    return validate_can_id(value)


def validate_can_id(value: int) -> int:
    """
    校验一个已经是整数的 CAN ID。

    :raises InputError: 超出标准 11-bit 范围
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise InputError(f"CAN ID 必须是整数，收到 {type(value).__name__}")

    if not CAN_ID_MIN <= value <= CAN_ID_MAX:
        raise InputError(
            f"CAN ID 0x{value:X} 超出标准 11-bit 范围 "
            f"(0x{CAN_ID_MIN:03X}–0x{CAN_ID_MAX:03X})"
        )

    return value


def parse_hex_bytes(tokens: Sequence[str]) -> bytes:
    """
    把十六进制字节序列解析为 ``bytes``。

    支持两种写法（以及它们的混合）：

        ["02", "10", "03"]
        ["021003"]

    空输入返回空 ``bytes``（合法：0 字节 payload 由调用方决定是否允许）。

    :raises InputError: 非十六进制，或字节数超过 8
    """
    joined = "".join(str(token).strip() for token in tokens)

    if joined == "":
        return b""

    # 允许用冒号/逗号分隔
    for separator in (":", ",", "-"):
        joined = joined.replace(separator, "")

    if len(joined) % 2 != 0:
        raise InputError(
            f"十六进制字节串长度必须为偶数（每个字节 2 个字符），收到 {len(joined)} 个字符"
        )

    try:
        data = bytes.fromhex(joined)
    except ValueError:
        raise InputError(
            f"无法解析十六进制字节：{joined!r}（示例：02 10 03）"
        ) from None

    if len(data) > CAN_DATA_MAX_LEN:
        raise InputError(
            f"数据长度 {len(data)} 字节超过经典 CAN 单帧上限 {CAN_DATA_MAX_LEN} 字节"
        )

    return data


def guard_interface(interface: str) -> str:
    """
    只允许实验用虚拟 CAN 接口。

    :raises InputError: 接口不是 vcan0
    """
    name = (interface or "").strip()

    if name != ALLOWED_INTERFACE:
        raise InputError(
            f"only {ALLOWED_INTERFACE} is allowed by this lab tool "
            f"(收到 {name!r})。"
            f"这些工具只用于隔离实验环境，不接受真实 CAN 接口。"
        )

    return name


#: ISO-TP 分离时间（separation time）后缀 → 毫秒系数。
#:
#: 用于 ``--fc-stmin``：ISO 15765-2 用**一个字节**编码分离时间，
#: 单位由分段决定（见 README）。这里只负责把人类写法换算成毫秒，
#: 再交给 :func:`encode_stmin_ms` 编码，避免调用方自己算。
STMIN_MS_SUFFIXES = (
    ("us", 0.001),
    ("ms", 1.0),
)

#: 时长后缀 → 纳秒系数。
#:
#: 用于 ``--tx-stmin``：内核 ``CAN_ISOTP_TX_STMIN`` 的单位是**纳秒**
#: （python-isotp 文档亦为 nanoseconds），与 ISO-TP FC 字节编码无关，
#: 因此必须有独立的后缀表，不能被 ``--fc-stmin`` 的语义污染。
DURATION_NS_SUFFIXES = (
    ("us", 1_000),
    ("ms", 1_000_000),
    ("s", 1_000_000_000),
)


def parse_optional_byte(text: Optional[str], what: str = "值") -> Optional[int]:
    """
    解析可选的单字节参数（0x00–0xFF），接受 ``0xAA`` 与 ``170`` 两种写法。

    ``None`` / 空字符串表示**未指定**，返回 ``None``。
    返回 ``None`` 的语义是"不改动该选项"，即保持内核默认行为，
    因此调用方必须把 ``None`` 与 ``0`` 区别对待（``0`` 是合法值）。

    :raises InputError: 非整数，或超出 0x00–0xFF
    """
    if text is None:
        return None

    raw = str(text).strip()

    if not raw:
        return None

    try:
        value = int(raw, 0)
    except ValueError:
        raise InputError(
            f"无法解析{what}：{text!r}（示例：0xAA 或 170）"
        ) from None

    if not 0 <= value <= 0xFF:
        raise InputError(f"{what} {value} 超出单字节范围 (0x00–0xFF)")

    return value


def parse_duration_ns(text: Optional[str], what: str = "时长") -> Optional[int]:
    """
    解析可选的时长参数，统一换算为**纳秒**（32-bit 无符号上限）。

    用于 ``--tx-stmin``，因为内核 ``CAN_ISOTP_TX_STMIN`` 的单位是纳秒。
    支持带单位的写法，也支持不带单位（视为纳秒）::

        100us → 100_000
        2ms   → 2_000_000
        1s    → 1_000_000_000
        500   → 500

    ``None`` / 空字符串表示**未指定**，返回 ``None``（不改动该选项）。

    :raises InputError: 非整数、负数、单位无法识别，或超过 0xFFFFFFFF
    """
    if text is None:
        return None

    # 统一小写并去掉内部空白，使 "2 MS" / "2 ms" / "2MS" 等价
    raw = "".join(str(text).split()).lower()

    if not raw:
        return None

    factor = 1

    for suffix, suffix_ns in DURATION_NS_SUFFIXES:
        if raw.endswith(suffix):
            factor = suffix_ns
            raw = raw[: -len(suffix)]
            break

    if not raw:
        raise InputError(f"无法解析{what}：{text!r}（缺少数字部分）")

    try:
        value = int(raw, 0)
    except ValueError:
        raise InputError(
            f"无法解析{what}：{text!r}（示例：500、100us、2ms、1s）"
        ) from None

    if value < 0:
        raise InputError(f"{what}不能为负数：{text!r}")

    nanos = value * factor

    if nanos > 0xFFFFFFFF:
        raise InputError(
            f"{what} {text!r} = {nanos} ns 超过 32-bit 无符号上限 "
            f"(0xFFFFFFFF ns ≈ 4.29 s)"
        )

    return nanos


def encode_stmin_ms(milliseconds: float) -> int:
    """
    把毫秒值编码为 ISO 15765-2 流控帧 STmin 字节。

    ISO 15765-2 规定::

        0x00–0x7F  →  0–127 ms
        0xF1–0xF9  →  100–900 us（即 0.1–0.9 ms）
        其它取值    →  保留（本函数拒绝）

    两个编码区间不连续（0.1–0.9 ms 与 1–127 ms），因此编码必然是
    **量化的**：小于 1 ms 的值截断到 0.1 ms 档位，位于 (0.9, 1) 的值
    则上取到 1 ms。本函数只为**人类可读的 CLI 用法**服务
    （如 ``--fc-stmin 5ms``、``--fc-stmin 500us``）。
    需要精确控制字节值时直接传整数即可（如 ``--fc-stmin 0xF1``）。

    :raises InputError: 负数，或超过 0x7F ms
    """
    if milliseconds < 0:
        raise InputError(f"STmin 不能为负数：{milliseconds} ms")

    if milliseconds > 0x7F:
        # 超过 127 ms 无法表示：宁可报错，也不要悄悄取一个不同的值。
        raise InputError(
            f"STmin {milliseconds} ms 超出 ISO-TP 可编码范围 "
            f"(0–0x7F ms 或 0xF1–0xF9 = 100–900 us)"
        )

    if milliseconds == 0:
        return 0x00

    if milliseconds < 1.0:
        # 0.1 ms 粒度（10 表示 1.0 ms，被上面的分支接住，不会越界）
        return 0xF0 + max(1, round(milliseconds * 10))

    return int(milliseconds)


def stmin_actual_ms(stmin_byte: int) -> float:
    """
    把 STmin 字节解码为**实际生效的毫秒值**（用于报告/验证）。

    保留值（0x80–0xF0、0xFA–0xFF）按 ISO 15765-2 视为 127 ms
    （接收方应回退到最大分离时间），本函数同样如此处理。
    """
    if 0x00 <= stmin_byte <= 0x7F:
        return float(stmin_byte)

    if 0xF1 <= stmin_byte <= 0xF9:
        return (stmin_byte - 0xF0) / 10.0

    return 127.0


def parse_stmin_argument(
    text: Optional[str], what: str = "STmin"
) -> Optional[int]:
    """
    解析**流控帧 STmin**（本端在自身 FC 帧里发出的分离时间请求）。

    两种写法都支持：

    - 字节写法（精确，推荐用于实验对照）：``0x05`` 或 ``5`` → 直接作为 STmin 字节
    - 时长写法（直观）：``5ms`` 或 ``500us`` → 经 :func:`encode_stmin_ms` 量化

    裸数字的歧义说明：ISO-TP 中 STmin 字节 ``0x05`` 的语义**就是** 5 ms，
    因此"5"无论解释为字节还是毫秒，结果都是同一个值，无需区分。

    ``None`` / 空字符串表示未指定，返回 ``None``（使用内核默认值）。

    :raises InputError: 无法解析，或超出单字节 / 可编码范围
    """
    if text is None:
        return None

    raw = text.strip()

    if not raw:
        return None

    compact = "".join(raw.split()).lower()

    # 先看单位后缀：只有带单位时才走"时长 → 编码"路径。
    factor_ms = None

    for suffix, suffix_factor in STMIN_MS_SUFFIXES:
        if compact.endswith(suffix):
            factor_ms = suffix_factor
            compact = compact[: -len(suffix)]
            break

    if factor_ms is None:
        # 裸数字：ISO-TP 中 STmin 字节 0x05 的语义**就是** 5 ms，
        # 所以"字节写法"与"毫秒写法"数值一致。但这里必须按**整数**解析，
        # 否则 0xF1 会被当成 241 ms 而拒绝（它其实是合法的微秒档位）。
        try:
            value = int(compact, 0)
        except ValueError:
            raise InputError(
                f"无法解析{what}：{text!r}（示例：5、0x05、5ms、500us）"
            ) from None

        return parse_optional_byte(str(value), what=what)

    try:
        milliseconds = float(compact) * factor_ms
    except ValueError:
        raise InputError(
            f"无法解析{what}：{text!r}（示例：5、0x05、5ms、500us）"
        ) from None

    return encode_stmin_ms(milliseconds)


class ArgumentParser(argparse.ArgumentParser):
    """
    把校验失败转换为友好的 CLI 错误（退出码 2），而不是 Python traceback。
    """

    def error(self, message: str):  # type: ignore[override]
        self.print_usage()
        self.exit(2, f"{self.prog}: error: {message}\n")


def add_interface_argument(parser: argparse.ArgumentParser) -> None:
    """为 parser 增加统一的 --interface 参数（默认 vcan0，仅允许 vcan0）。"""
    parser.add_argument(
        "--interface",
        default=ALLOWED_INTERFACE,
        help=f"CAN 接口（本实验工具只允许 {ALLOWED_INTERFACE}，默认 {ALLOWED_INTERFACE}）",
    )


def format_can_frame(timestamp: Optional[float], arbitration_id: int, data: bytes) -> str:
    """
    把一帧 CAN 报文格式化为近似 ``candump`` 风格的一行文本。

    例::

        1757912345.123456  7E0  [8]  02 10 03 00 00 00 00 00
    """
    stamp = f"{timestamp:.6f}" if timestamp is not None else "-"
    payload = data.hex(" ").upper()

    return f"{stamp}  {arbitration_id:03X}  [{len(data)}]  {payload}"
