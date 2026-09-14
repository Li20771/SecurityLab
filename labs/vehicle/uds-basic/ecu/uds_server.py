#!/usr/bin/env python3
"""
UDS Basic —— ECU 运行时状态

本模块目前**只包含** ``EcuState`` 与默认单例 ``STATE``。

刻意不包含（留待后续阶段）：

- Service Registry / service handler
- ``handle_uds_request`` 分发
- Permission / DID 逻辑
- 传输层

设计目的：把原先散落在 ``ecu.py`` 模块级的 8 个可变全局变量
收拢为一个显式状态对象，使"跨请求状态"有唯一的 source of truth，
并且可以被测试独立实例化（不再依赖模块级全局）。

    from uds_server import EcuState, STATE

    STATE.session            # 当前会话
    STATE.security_level     # 当前安全等级
    STATE.failed_attempts    # 连续失败次数
    ...

本模块依赖 ``uds_types`` 提供协议常量，不依赖 CAN / ISO-TP。
"""

from dataclasses import dataclass
from typing import Optional

import uds_types

__all__ = ["EcuState", "STATE"]


@dataclass
class EcuState:
    """
    虚拟 ECU 的全部跨请求运行时状态。

    字段与迁移前 ``ecu.py`` 中的 8 个模块级变量一一对应：

        CURRENT_SESSION        -> session
        CURRENT_SECURITY_LEVEL -> security_level
        PENDING_SEED           -> pending_seed
        SEED_ISSUED_AT         -> seed_issued_at
        SEED_CONSUMED          -> seed_consumed
        FAILED_ATTEMPTS        -> failed_attempts
        LOCKOUT_UNTIL          -> lockout_until
        SEED_COUNTER           -> seed_counter

    默认值必须与迁移前 ECU 的初始状态**完全一致**。

    注意：本对象只承载"状态"，不承载"配置"。
    ``SECURITY_CONFIG``、``SESSION_TIMING`` 等可调参数仍留在 ``ecu.py``。
    """

    #: 当前诊断会话（SESSION_DEFAULT / SESSION_PROGRAMMING / SESSION_EXTENDED）
    session: int = uds_types.SESSION_DEFAULT

    #: 当前安全等级（SECURITY_LOCKED / SECURITY_LEVEL1）
    security_level: int = uds_types.SECURITY_LOCKED

    #: 已下发但尚未被使用的 seed（RequestSeed 写入，SendKey 消费）
    pending_seed: Optional[bytes] = None

    #: seed 下发时刻（time.monotonic() 基准，用于判断 seed 是否过期）
    seed_issued_at: Optional[float] = None

    #: seed 是否已被成功消费（用于检测重放）
    seed_consumed: bool = False

    #: 连续失败次数（错误 key 递增，解锁成功或锁定到期归零）
    failed_attempts: int = 0

    #: 锁定解除时刻（None 表示未处于锁定）
    lockout_until: Optional[float] = None

    #: seed 序号（每次 RequestSeed 递增，使 seed 每次不同）
    seed_counter: int = 0


#: 默认状态单例：ECU 进程使用的唯一运行时状态实例。
#:
#: 单例本身不承载业务逻辑；``ecu.py`` 通过 ``STATE.<field>`` 读写字段，
#: 不会重新绑定 ``STATE`` 名字。
STATE = EcuState()


if __name__ == "__main__":
    # 手动观察用：打印默认状态，便于确认初始值与迁移前一致
    print("EcuState 默认值：")
    print(f"  session         = 0x{STATE.session:02X}")
    print(f"  security_level  = 0x{STATE.security_level:02X}")
    print(f"  pending_seed    = {STATE.pending_seed!r}")
    print(f"  seed_issued_at  = {STATE.seed_issued_at!r}")
    print(f"  seed_consumed   = {STATE.seed_consumed!r}")
    print(f"  failed_attempts = {STATE.failed_attempts!r}")
    print(f"  lockout_until   = {STATE.lockout_until!r}")
    print(f"  seed_counter    = {STATE.seed_counter!r}")
