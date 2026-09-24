#!/usr/bin/env python3
"""
DTC 静态数据库（只保存定义，不保存运行时状态）

本模块与 ``did_database.py`` 同一定位：**纯静态数据 + 零依赖**。

- ``DTC_DATABASE``：教学用合成 DTC 的**出厂默认状态**
  （``{DTC(3 字节): statusOfDTC(1 字节)}``）。运行时状态在
  ``EcuState.dtc_records`` 中持有，本模块内容**从不被 handler 修改**。
- ``DTC_METADATA``：**DTC 定义属性**（``{DTC: DtcMetadata(severity,
  functional_unit)}``）。与 ``DTC_DATABASE`` 同属静态定义，与运行时
  status **完全正交** —— 0x14 清除故障时 severity 不变（见下）。
- ``DTC_STATUS_AVAILABILITY_MASK``：本实验支持的 status 位全可用（0xFF）。
- ``DTC_SEVERITY_AVAILABILITY_MASK``：本实验支持的 severity 位（0xE0）。
- ``DTC_FORMAT_IDENTIFIER``：固定 0x01，表示 ISO 14229-1 DTC format
  （0x19/01 正响应携带）。

DTC 值刻意选在厂商/实验自定义区间，避免与真实车辆 DTC 语义混淆；
statusOfDTC 使用不同 bit 便于 status mask 过滤测试：

    bit0 testFailed
    bit1 testFailedThisOperationCycle
    bit2 pendingDTC
    ...

    DTC_DATABASE 中的默认状态刻意**各不相同**，让 0x19/01、02
    的 mask 过滤与计数具备可断言的教学意义。

依赖方向：本模块是零依赖叶子模块（与 uds_types.py 同级）。
"""

from typing import NamedTuple

__all__ = [
    "DTC_DATABASE",
    "DTC_STATUS_AVAILABILITY_MASK",
    "DTC_FORMAT_IDENTIFIER",
    "DtcMetadata",
    "DTC_METADATA",
    "DTC_SEVERITY_CHECK_IMMEDIATELY",
    "DTC_SEVERITY_CHECK_AT_NEXT_HALT",
    "DTC_SEVERITY_MAINTENANCE_ONLY",
    "DTC_SEVERITY_NO_SEVERITY_AVAILABLE",
    "DTC_SEVERITY_AVAILABILITY_MASK",
]


#: 教学合成 DTC 出厂状态：{DTC(3 字节 int): statusOfDTC(1 字节)}。
#:
#: 本实验**没有任何 UDS 服务会置位 DTC status**：``0x85`` ControlDTCSetting
#: 只开关"是否允许继续更新"，本身不产生故障。因此运行时默认即这些值；
#: 产生 / 更新只经进程内的 ``dtc_runtime.set_dtc_status()``
#: （fault generation 不是协议能力），``0x14`` 则把 status 置 ``0x00``。
DTC_DATABASE: dict[int, int] = {
    0x100001: 0x01,   # testFailed
    0x100002: 0x02,   # testFailedThisOperationCycle
    0x100003: 0x04,   # pendingDTC
}

#: 本实验支持的 DTCStatusAvailabilityMask：statusOfDTC 全部 bit 可用。
DTC_STATUS_AVAILABILITY_MASK: int = 0xFF

#: DTCFormatIdentifier：0x01 = ISO 14229-1 DTC format（本实验固定）。
DTC_FORMAT_IDENTIFIER: int = 0x01


# ======================================================================
# DTC 定义属性（severity / functional unit）
# ======================================================================

#: ISO 14229-1 DTCSeverity 位定义（本实验采用的三个有效位）。
DTC_SEVERITY_CHECK_IMMEDIATELY = 0x80
DTC_SEVERITY_CHECK_AT_NEXT_HALT = 0x40
DTC_SEVERITY_MAINTENANCE_ONLY = 0x20

#: 无 severity 可用。作为**客户端 mask 取值**有意义；本实验的 DTC
#: 定义不使用它（每个 DTC 都有明确 severity）。
DTC_SEVERITY_NO_SEVERITY_AVAILABLE = 0x00

#: 本实验支持的 DTCSeverityAvailabilityMask。
#:
#: ``0xE0`` = bit7|bit6|bit5，即上面三个 severity 位。低 5 bit 不参与
#: 本项目的 severity 语义，恒为 0。
DTC_SEVERITY_AVAILABILITY_MASK: int = 0xE0


class DtcMetadata(NamedTuple):
    """
    单个 DTC 的**定义属性**（静态、只读）。

    用 ``NamedTuple`` 而非 dict：本项目的 lint/type 门禁（ruff + mypy）
    要求可静态检查的属性访问，且 ``NamedTuple`` 天然不可变 ——
    防止运行时被误改成"会变的元数据"。
    """

    severity: int
    functional_unit: int


#: DTC 定义属性表：``{DTC(3 字节): DtcMetadata(severity, functional_unit)}``。
#:
#: 与 status 的**职责边界**（这是本模块最重要的约定）：
#:
#:     DTC_DATABASE           出厂默认 statusOfDTC  ┐ 运行时状态
#:     EcuState.dtc_records   当前 statusOfDTC      ┘ 0x14 会置 0x00
#:     DTC_METADATA           severity / functionalUnit
#:                            —— **定义属性**：0x14 清除故障时**不变**，
#:                            ECUReset(01/02/03) 也**不变**
#:
#: severity 刻意三个各不相同（0x80 / 0x40 / 0x20），使 0x19/07、08、09
#: 的 severity mask 过滤具备可断言的教学意义。
#: functionalUnit 为教学合成值（ISO 14229-1 规定其取值由车厂定义）。
DTC_METADATA: dict[int, DtcMetadata] = {
    0x100001: DtcMetadata(DTC_SEVERITY_CHECK_IMMEDIATELY, 0x01),
    0x100002: DtcMetadata(DTC_SEVERITY_CHECK_AT_NEXT_HALT, 0x02),
    0x100003: DtcMetadata(DTC_SEVERITY_MAINTENANCE_ONLY, 0x03),
}

#: 导入期完整性护栏：每个支持的 DTC 都必须有 metadata，且不得有多余条目。
#:
#: fail-fast —— 数据不自洽时模块直接无法导入，而不是等到运行时 KeyError
#: 才发现某个 DTC 没有 severity。
if set(DTC_DATABASE) != set(DTC_METADATA):
    raise ValueError(
        "DTC_DATABASE 与 DTC_METADATA 的 DTC 集合不一致："
        f"缺 metadata={sorted(set(DTC_DATABASE) - set(DTC_METADATA))}，"
        f"多余 metadata={sorted(set(DTC_METADATA) - set(DTC_DATABASE))}"
    )
