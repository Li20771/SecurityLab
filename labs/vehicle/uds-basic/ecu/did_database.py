#!/usr/bin/env python3

DID_DATABASE: dict[int, bytes] = {
    0xF180: b"BOOT-SW-1.0.0",
    0xF190: b"TESTECU0000000001",
    # 教学用的受保护 DID：
    # 只有 extendedDiagnosticSession + SecurityAccess 解锁后才能读取。
    0xF187: b"SPARE-PART-0001",
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
