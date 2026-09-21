#!/usr/bin/env python3
"""
DTC 静态数据库（只保存定义，不保存运行时状态）

本模块与 ``did_database.py`` 同一定位：**纯静态数据 + 零依赖**。

- ``DTC_DATABASE``：教学用合成 DTC 的**出厂默认状态**
  （``{DTC(3 字节): statusOfDTC(1 字节)}``）。运行时状态在
  ``EcuState.dtc_records`` 中持有，本模块内容**从不被 handler 修改**。
- ``DTC_STATUS_AVAILABILITY_MASK``：本实验支持的 status 位全可用（0xFF）。
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

__all__ = [
    "DTC_DATABASE",
    "DTC_STATUS_AVAILABILITY_MASK",
    "DTC_FORMAT_IDENTIFIER",
]


#: 教学合成 DTC 出厂状态：{DTC(3 字节 int): statusOfDTC(1 字节)}。
#:
#: 本实验刻意不提供"设置 DTC 状态"的服务（0x85 ControlDTCSetting 未实现），
#: 因此运行时默认即这些值；测试/未来服务通过 ``EcuState.dtc_records``
#: 修改运行时状态。
DTC_DATABASE: dict[int, int] = {
    0x100001: 0x01,   # testFailed
    0x100002: 0x02,   # testFailedThisOperationCycle
    0x100003: 0x04,   # pendingDTC
}

#: 本实验支持的 DTCStatusAvailabilityMask：statusOfDTC 全部 bit 可用。
DTC_STATUS_AVAILABILITY_MASK: int = 0xFF

#: DTCFormatIdentifier：0x01 = ISO 14229-1 DTC format（本实验固定）。
DTC_FORMAT_IDENTIFIER: int = 0x01
