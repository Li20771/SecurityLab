#!/usr/bin/env python3
"""
DID（Data Identifier）数据库与读写权限元数据。

- ``DID_DATABASE``：DID 的出厂/默认值（只读基线）。
- ``PROTECTED_DIDS``：读取也需要 SecurityAccess 解锁的敏感 DID。
- ``WRITABLE_DIDS``：允许 0x2E WriteDataByIdentifier 写入的 DID。
- ``WRITABLE_DID_LENGTHS``：每个可写 DID 允许的数据长度区间（闭区间，字节）。

运行时通过 0x2E 写入的值保存在 ECU 状态的 ``did_overrides`` 中
（见 ``uds_server.EcuState``），不修改本模块的出厂默认值；
ECUReset(hardReset) 会清空 overrides，使 DID 恢复出厂值。
"""

#: DID 出厂默认值。
#:
#: 0xF180/0xF190/0xF187 沿用既有实验（bootSoftwareId / VIN / sparePartNumber）。
#: 0xF102 位于厂商自定义区间，作为**可写**教学 DID（"车辆配置名"），
#: 刻意避开 UDS 标准保留的 0xF18x / 0xF19x 段以免语义混淆。
DID_DATABASE: dict[int, bytes] = {
    0xF180: b"BOOT-SW-1.0.0",
    0xF190: b"TESTECU0000000001",
    # 教学用的受保护 DID：
    # 只有 extendedDiagnosticSession + SecurityAccess 解锁后才能读取。
    0xF187: b"SPARE-PART-0001",
    # 教学用的可写 DID（默认会话可读，写入需 extended 会话 + 解锁）。
    0xF102: b"DEFAULT_CONFIG",
}

#: 需要 SecurityAccess 解锁（SECURITY_LEVEL1）才能读取的 DID。
#:
#: 说明：
#: - 0xF187 (sparePartNumber) 在真实车辆中属于较敏感信息，
#:   本实验借此演示 Session → Security → Permission 三层权限模型。
#: - 权限不足时返回 NRC 0x33 SecurityAccessDenied。
#:
#: 另一种可选设计是返回 NRC 0x31 RequestOutOfRange 以隐藏 DID 的存在性
#: （防枚举），本实验刻意选择 0x33 以便教学上语义清晰。
PROTECTED_DIDS: frozenset = frozenset({0xF187})

#: 允许 0x2E WriteDataByIdentifier 写入的 DID 集合。
#:
#: 不在此集合中的已知 DID（如 VIN 0xF190）视为只读，写入返回
#: NRC 0x22 conditionsNotCorrect；未知 DID 返回 0x31 requestOutOfRange。
WRITABLE_DIDS: frozenset = frozenset({0xF102})

#: 可写 DID 的合法数据长度区间 ``{did: (min_len, max_len)}``（闭区间）。
#:
#: 长度越界返回 NRC 0x13 incorrectMessageLength
#: （ISO 也允许 0x31 requestOutOfRange，本实验统一用 0x13 表达"长度不对"）。
WRITABLE_DID_LENGTHS: dict[int, tuple[int, int]] = {
    0xF102: (1, 16),
}
