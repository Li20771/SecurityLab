#!/usr/bin/env python3
"""
UDS Basic —— 协议常量与响应构造（零依赖）

本模块只包含**常量**与**两个纯函数**，刻意不包含：

- 任何运行时状态（没有会话状态、安全状态、seed 等）
- 任何服务实现或分发逻辑
- 任何权限系统 / registry / Response 类 / enum 类

设计定位与 ``keygen.py`` 相同：**零第三方依赖**，
因此可以被 ECU、诊断客户端与测试共同 import，
不需要 ISO-TP / SocketCAN 环境。

    import uds_types
    uds_types.negative_response(0x27, 0x35)      # b"\\x7f\\x27\\x35"
    uds_types.positive_response(0x27, b"\\x01")    # b"\\x67\\x01"

参考标准：

- ISO 14229-1：UDS 应用层（服务 SID、NRC）
- ISO 15765-2：ISO-TP 传输层
"""

from collections.abc import Callable

__all__ = [
    # Session
    "SESSION_DEFAULT",
    "SESSION_PROGRAMMING",
    "SESSION_EXTENDED",
    "SUPPORTED_SESSIONS",
    # Security
    "SECURITY_LOCKED",
    "SECURITY_LEVEL1",
    # Reset
    "RESET_HARD",
    "RESET_KEY_OFF_ON",
    "RESET_SOFT",
    "SUPPORTED_RESET_TYPES",
    # NRC
    "NRC_GENERAL_REJECT",
    "NRC_SERVICE_NOT_SUPPORTED",
    "NRC_SUBFUNCTION_NOT_SUPPORTED",
    "NRC_INCORRECT_MESSAGE_LENGTH",
    "NRC_CONDITIONS_NOT_CORRECT",
    "NRC_REQUEST_SEQUENCE_ERROR",
    "NRC_REQUEST_OUT_OF_RANGE",
    "NRC_SECURITY_ACCESS_DENIED",
    "NRC_INVALID_KEY",
    "NRC_EXCEED_NUMBER_OF_ATTEMPTS",
    "NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED",
    "NRC_WRONG_BLOCK_SEQUENCE_COUNTER",
    "NRC_SERVICE_NOT_SUPPORTED_IN_SESSION",
    # 响应构造
    "negative_response",
    "positive_response",
    # 类型
    "ServiceHandler",
]


# ======================================================================
# A. Session 常量（ISO 14229-1 DiagnosticSessionType）
# ======================================================================

SESSION_DEFAULT = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED = 0x03

#: 本实验实现的会话类型集合。
SUPPORTED_SESSIONS: frozenset[int] = frozenset(
    {
        SESSION_DEFAULT,
        SESSION_PROGRAMMING,
        SESSION_EXTENDED,
    }
)


# ======================================================================
# B. Security 常量（0x27 SecurityAccess 的安全等级）
# ======================================================================

SECURITY_LOCKED = 0x00
SECURITY_LEVEL1 = 0x01


# ======================================================================
# B2. Reset 常量（ISO 14229-1 ECUReset resetType）
# ======================================================================

RESET_HARD = 0x01
RESET_KEY_OFF_ON = 0x02
RESET_SOFT = 0x03

#: 本实验实现的 resetType 集合。
#:
#: ``0x04 enableRapidPowerShutDown`` / ``0x05 disableRapidPowerShutDown``
#: 未实现（→ NRC 0x12）；suppressPosRspMsgIndicationBit 亦未实现。
#:
#: **experiment-defined**：本实验模型没有电源域、硬件复位、应用层重启或
#: NVM controller，因此上面三个 resetType 在 :class:`EcuState` 的抽象层级上
#: 采用**完全相同**的状态转换（见 ``_reset_volatile_state``）。
#: 这不是 ISO 14229-1 的强制要求，而是本教学 ECU 的明确选择。
SUPPORTED_RESET_TYPES: frozenset[int] = frozenset(
    {
        RESET_HARD,
        RESET_KEY_OFF_ON,
        RESET_SOFT,
    }
)


# ======================================================================
# C. NRC 常量（ISO 14229-1 Negative Response Codes）
# ======================================================================

#: 一般拒绝。当前生产代码尚未使用，保留供后续服务使用。
NRC_GENERAL_REJECT = 0x10

NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13

NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24

NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33

NRC_INVALID_KEY = 0x35
NRC_EXCEED_NUMBER_OF_ATTEMPTS = 0x36
NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED = 0x37

#: 0x36 TransferData 的 blockSequenceCounter 不匹配（真正的 sequence error；
#: 完全重复的上一条请求按幂等重传处理，不落本 NRC）。
NRC_WRONG_BLOCK_SEQUENCE_COUNTER = 0x73

#: 服务在当前会话不可用。
#: 当前 4 个服务在任意会话都可用，因此本常量暂未被门控逻辑使用，
#: 保留给后续需要按会话限制的服务（如 0x2E 写入）。
NRC_SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F


# ======================================================================
# D. 响应构造
# ======================================================================

#: UDS 肯定响应 SID 与请求 SID 的固定偏移（ISO 14229-1）。
_POSITIVE_RESPONSE_OFFSET = 0x40

#: 否定响应固定以 0x7F 开头。
_NEGATIVE_RESPONSE_PREFIX = 0x7F

#: 单字节字段的取值范围。
_BYTE_MIN = 0x00
_BYTE_MAX = 0xFF


def _check_byte(value: int, name: str) -> None:
    """校验一个单字节整数字段（最基本的类型/范围检查）。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} 必须是 int，实际为 {type(value).__name__}")

    if not _BYTE_MIN <= value <= _BYTE_MAX:
        raise ValueError(f"{name} 必须在 0x00–0xFF 范围内，实际为 0x{value:X}")


def negative_response(sid: int, nrc: int) -> bytes:
    """
    返回否定响应 ``7F <SID> <NRC>``。

    :param sid: 请求的服务标识符（会被原样回显）
    :param nrc: 否定响应码
    :return: 3 字节响应。

    >>> negative_response(0x27, 0x35) == bytes([0x7F, 0x27, 0x35])
    True
    """
    _check_byte(sid, "sid")
    _check_byte(nrc, "nrc")

    return bytes([_NEGATIVE_RESPONSE_PREFIX, sid, nrc])


def positive_response(sid: int, payload: bytes = b"") -> bytes:
    """
    返回肯定响应 ``<SID + 0x40> + payload``。

    :param sid: 请求的服务标识符（正响应 SID = SID + 0x40）
    :param payload: 响应数据（不含 SID），默认空
    :return: 由响应 SID 与 payload 拼接而成的字节串。

    >>> positive_response(0x27, b"\\x01\\x02") == bytes([0x67, 0x01, 0x02])
    True
    >>> positive_response(0x10) == bytes([0x50])
    True
    """
    _check_byte(sid, "sid")

    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError(
            f"payload 必须是 bytes 或 bytearray，实际为 {type(payload).__name__}"
        )

    response_sid = sid + _POSITIVE_RESPONSE_OFFSET

    if response_sid > _BYTE_MAX:
        raise ValueError(
            f"SID 0x{sid:02X} 加 0x{_POSITIVE_RESPONSE_OFFSET:02X} 后超出单字节范围"
        )

    return bytes([response_sid]) + bytes(payload)


# ======================================================================
# E. 类型定义
# ======================================================================

#: 服务处理函数签名：接收原始请求与 ECU 状态，返回完整响应字节。
#:
#: 第二个参数目前标注为 object，因为状态对象（EcuState）尚未引入；
#: 待引入后再收窄为具体类型，避免本阶段为类型系统增加复杂度。
ServiceHandler = Callable[[bytes, object], bytes]


if __name__ == "__main__":
    # 手动观察用：打印常量与响应构造示例
    print("Sessions:", [f"0x{s:02X}" for s in sorted(SUPPORTED_SESSIONS)])
    print(f"Security: locked=0x{SECURITY_LOCKED:02X} level1=0x{SECURITY_LEVEL1:02X}")
    print()
    print("negative_response(0x27, 0x35) =", negative_response(0x27, 0x35).hex(" "))
    print("positive_response(0x27, 0102) =", positive_response(0x27, b"\x01\x02").hex(" "))
    print("positive_response(0x10)       =", positive_response(0x10).hex(" "))
