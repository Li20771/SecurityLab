#!/usr/bin/env python3
"""
DTC **运行时状态**操作原语（runtime DTC state mutation）

与 ``dtc_database.py`` 的分工（这是本模块存在的全部理由）：

    dtc_database.py        DTC **定义**：出厂默认 statusOfDTC + severity /
                           functionalUnit —— 纯静态数据，运行时**从不**被修改
    EcuState.dtc_records   DTC **运行时状态**：当前 statusOfDTC
                           —— 由本模块提供的原语修改

本模块**不持有**任何状态：每个函数都只作用于调用方传入的 ``state``，
因此天然支持多个 ``EcuState`` 实例互不干扰。

**为什么需要它**：在 0x14 之外，原先没有任何"运行时产生 / 更新 DTC"的
入口，调用方只能直接写 ``state.dtc_records[dtc] = status`` —— 既没有注册
校验，也没有 status 范围校验，未注册的 DTC 会被**静默**写进去。本模块把
这一写入收敛为**受校验的单一入口**，为后续（OQ6-B 的 0x85 / 故障产生）
提供明确控制点。

``metadata`` 与 ``runtime status`` 保持**正交**：本模块只碰 status，
severity / functionalUnit 始终只从 ``dtc_database.DTC_METADATA`` 读取。

两个写入入口的**契约差异是刻意的，不要合并**：

    set_dtc_status()     受校验（已注册 DTC + status 单字节范围）
                         —— 用于"产生 / 更新"
    clear_dtc_status()   **不做注册校验** —— 与 0x14 的既有语义一致
                         （0x14 接受 ``dtc_records`` 中实际存在的任何 key，
                          含运行时注入的 DTC；加校验会改变 0x14 行为）

本模块**不新增任何 UDS 服务**：fault generation 只经 Python API / test
seam 完成，不把它做成协议能力。

依赖方向：本模块只读 import ``dtc_database``，**不 import** ``uds_server``
（用 ``Protocol`` 结构化描述所需字段，避免与 0x14 的接线形成循环依赖）。
"""

from typing import Protocol

from dtc_database import DTC_DATABASE

__all__ = [
    "DTC_STATUS_MIN",
    "DTC_STATUS_MAX",
    "DtcStateError",
    "UnknownDtcError",
    "is_registered_dtc",
    "set_dtc_status",
    "clear_dtc_status",
    "get_dtc_status",
]


#: statusOfDTC 在 wire 上是单字节，合法闭区间。
DTC_STATUS_MIN = 0x00
DTC_STATUS_MAX = 0xFF


class DtcStateError(ValueError):
    """DTC 运行时状态写入被拒绝。"""


class UnknownDtcError(DtcStateError):
    """目标 DTC 未注册（不在 ``DTC_DATABASE`` 中）。"""


class _HasDtcRecords(Protocol):
    """
    本模块只需要一个可变的 ``dtc_records`` 映射。

    刻意用 ``Protocol`` 而非 ``from uds_server import EcuState``：
    ``uds_server`` 会 import 本模块（0x14 的写入落点），反向 import 会
    形成循环依赖。
    """

    dtc_records: dict[int, int]


def is_registered_dtc(dtc: int) -> bool:
    """DTC 是否是本实验已注册的定义（``DTC_DATABASE`` 的 key）。"""
    return dtc in DTC_DATABASE


def set_dtc_status(state: _HasDtcRecords, dtc: int, status: int) -> None:
    """
    把**已注册** DTC 的运行时 status 设为 ``status``。

    语义：``status == 0x00`` 表示该 DTC 当前**不活动 / 无故障**；
    非零表示**活动**（各位的含义见 ``dtc_database`` 的 status 约定）。
    因此 "inactive → active" 就是本函数写非零值，
    "active → cleared / inactive" 走 :func:`clear_dtc_status`（即 0x14 语义）。

    校验（全部发生在**任何写入之前**，失败时不触碰 state）：

    - ``dtc`` 必须已注册 → 否则 :class:`UnknownDtcError`
    - ``DTC_STATUS_MIN <= status <= DTC_STATUS_MAX`` → 否则 :class:`DtcStateError`

    **刻意不静默创建新 DTC**：未注册的 DTC 不会被写进 ``dtc_records``。
    否则会凭空多出一个没有 metadata 的永久 DTC —— 那会让 0x19/07、08
    （按 ``DTC_METADATA`` 做 severity 过滤）与 0x19/09 的语义变得难以解释。

    重复调用**幂等**：同一 ``(dtc, status)`` 写多次结果相同。
    """
    if not is_registered_dtc(dtc):
        raise UnknownDtcError(
            f"DTC 0x{dtc:06X} 未注册（不在 DTC_DATABASE 中）；"
            f"本 API 不会静默创建新 DTC"
        )

    if not DTC_STATUS_MIN <= status <= DTC_STATUS_MAX:
        raise DtcStateError(
            f"status 0x{status:X} 超出 statusOfDTC 的单字节范围 "
            f"[0x{DTC_STATUS_MIN:02X}, 0x{DTC_STATUS_MAX:02X}]"
        )

    state.dtc_records[dtc] = status


def clear_dtc_status(state: _HasDtcRecords, dtc: int) -> None:
    """
    把 DTC 的运行时 status 置 ``0x00``（清除故障）。

    **刻意不做注册校验** —— 与 0x14 的既有语义保持一致：0x14 接受
    ``state.dtc_records`` 中**实际存在**的任何 key（含运行时 / 测试注入的
    DTC），给它加注册校验会改变 0x14 的行为。调用方自行负责先做成员判定。

    本函数是 0x14 的**写入落点**；:func:`set_dtc_status` 则是"产生 / 更新"
    的受校验入口。两者契约不同，见模块 docstring。
    """
    state.dtc_records[dtc] = 0x00


def get_dtc_status(state: _HasDtcRecords, dtc: int) -> int:
    """
    读取**已注册** DTC 的当前运行时 status。

    未注册 DTC → :class:`UnknownDtcError`（与 :func:`set_dtc_status` 对称）。

    若该 DTC 已注册但当前不在运行时存储中（例如测试把 ``dtc_records``
    清空以模拟空库），返回 ``0x00``（不活动）而不是抛 KeyError ——
    "不在存储中"与"存在且不活动"在读取语义上等价。
    """
    if not is_registered_dtc(dtc):
        raise UnknownDtcError(f"DTC 0x{dtc:06X} 未注册（不在 DTC_DATABASE 中）")

    return state.dtc_records.get(dtc, DTC_STATUS_MIN)
