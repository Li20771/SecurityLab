#!/usr/bin/env python3
"""
Routine 静态数据库（只保存定义，不保存运行时状态）

本模块与 ``did_database.py`` / ``dtc_database.py`` 同一定位：
**纯静态数据 + 零依赖 + 无副作用**。

- ``ROUTINE_NAMES``：0x31 RoutineControl 支持的 synthetic routine
  标识符 → 名称。运行时状态（是否 active、start 计数）在
  ``EcuState.active_routines`` / ``EcuState.routine_start_counts``，
  本模块内容**从不被 handler 修改**。
- ``MAGIC_ECHO_RESULT``：magic-echo routine 的固定 4 字节结果
  （与 Start 次数无关，用于教学确定性断言）。

Routine 刻意选在厂商/实验自定义区间（0x200x），不依赖真实硬件、
可离线 unit 测试、可 vCAN E2E 验证：

    ROUTINE_START_COUNTER = 0x2001
        Start 递增 ``EcuState.routine_start_counts``；
        Results 返回计数的 4 字节 big-endian。

    ROUTINE_MAGIC_ECHO = 0x2002
        Results 返回固定魔数 ``MAGIC_ECHO_RESULT``（纯查询）。

依赖方向：本模块是零依赖叶子模块（与 uds_types.py 同级）。
"""

__all__ = [
    "ROUTINE_START_COUNTER",
    "ROUTINE_MAGIC_ECHO",
    "ROUTINE_NAMES",
    "ROUTINE_RESULT_LENGTH",
    "MAGIC_ECHO_RESULT",
]


#: start-counter routine：Results 返回 Start 次数（4 字节 big-endian）。
ROUTINE_START_COUNTER = 0x2001

#: magic-echo routine：Results 返回固定魔数（与 Start 次数无关）。
ROUTINE_MAGIC_ECHO = 0x2002

#: 本实验支持的 routine 标识符 → 名称（教学可读性）。
ROUTINE_NAMES: dict[int, str] = {
    ROUTINE_START_COUNTER: "start-counter",
    ROUTINE_MAGIC_ECHO: "magic-echo",
}

#: 每个 routine 的 Results 长度（字节），本 feature 统一 4 字节。
ROUTINE_RESULT_LENGTH: int = 4

#: magic-echo 的固定结果字节。
MAGIC_ECHO_RESULT: bytes = b"\x31\x41\x59\x26"
