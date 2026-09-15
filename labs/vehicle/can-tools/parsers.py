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
