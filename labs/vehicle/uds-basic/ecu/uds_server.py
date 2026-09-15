#!/usr/bin/env python3
"""
UDS Basic —— ECU 运行时状态与服务处理

本模块包含：

- ``EcuState`` / ``STATE``：ECU 的跨请求运行时状态
- ``reset_security_state()`` / ``required_security_level()``：状态与权限 helper
- 三个服务处理函数：
    * ``service_diagnostic_session_control``  (0x10)
    * ``service_ecu_reset``                   (0x11)
    * ``service_read_data_by_identifier``     (0x22)

刻意**尚未**包含（留待后续阶段）：

- ``service_security_access`` (0x27)
  —— 它依赖 ``ecu.py`` 中的 ``_now()`` 与 ``SECURITY_CONFIG``，
     而 ``uds_server`` 不能反向 import ``ecu``（会形成循环依赖），
     因此 0x27 暂时保留在 ``ecu.py``，留待专门的依赖解耦阶段处理。
- ``SERVICES`` registry
- ``handle_uds_request`` 分发
- Permission 框架 / DID metadata

依赖方向（单向，禁止反向）：

    ecu.py ──> uds_server.py ──> uds_types.py
                             └─> did_database.py

设计目的：把服务实现从传输层剥离，使 handler 只依赖
``(request, state)``，从而可以被独立测试，并支持多个 ECU 状态实例。
"""

from dataclasses import dataclass
from typing import Optional

import uds_types
from did_database import DID_DATABASE, PROTECTED_DIDS

__all__ = [
    "EcuState",
    "STATE",
    "SESSION_TIMING",
    "reset_security_state",
    "required_security_level",
    "service_diagnostic_session_control",
    "service_ecu_reset",
    "service_read_data_by_identifier",
]


# ======================================================================
# 会话参数
# ======================================================================

# 每个会话的 (P2ServerMax, P2*ServerMax)，单位毫秒。
# defaultSession 保持 00 32 / 00 64 不变，以兼容既有行为与测试。
SESSION_TIMING = {
    uds_types.SESSION_DEFAULT: (0x0032, 0x0064),
    uds_types.SESSION_PROGRAMMING: (0x00C8, 0x1388),
    uds_types.SESSION_EXTENDED: (0x0064, 0x03E8),
}


# ======================================================================
# ECU 运行时状态
# ======================================================================


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
    ``SECURITY_CONFIG`` 等可调参数仍留在 ``ecu.py``。
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


# ======================================================================
# 状态 helper
# ======================================================================


def reset_security_state(state: EcuState) -> None:
    """
    重置"会话相关"的安全状态。

    清除安全等级与 seed 生命周期，但**保留**失败计数与锁定状态。

    保留失败计数与锁定是刻意的设计选择：防止 Tester 通过切换会话或
    复位 ECU 来刷掉暴力破解的惩罚状态。注意这**不是所有真实 ECU
    都必然如此**，部分真实 ECU 会在复位时清空失败计数。

    :param state: 目标 ECU 状态
    """
    state.security_level = uds_types.SECURITY_LOCKED
    state.pending_seed = None
    state.seed_issued_at = None
    state.seed_consumed = False


# ======================================================================
# 权限模型
# ======================================================================


def required_security_level(did: int) -> int:
    """返回读取指定 DID 所需的安全等级。"""
    if did in PROTECTED_DIDS:
        return uds_types.SECURITY_LEVEL1

    return uds_types.SECURITY_LOCKED


# ======================================================================
# 0x10 DiagnosticSessionControl
# ======================================================================


def service_diagnostic_session_control(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x10 DiagnosticSessionControl。

    请求：``10 <sessionType>``
    响应：``50 <sessionType> <P2ServerMax:2> <P2*ServerMax:2>``

    会话切换后必须重新解锁：否则 Tester 可在 extended session 解锁后
    切回 default session 而保持解锁状态，形成真实的权限绕过漏洞。
    """
    if len(request) != 2:
        return uds_types.negative_response(
            0x10, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    session_type = request[1]

    if session_type not in uds_types.SUPPORTED_SESSIONS:
        return uds_types.negative_response(
            0x10, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    state.session = session_type

    reset_security_state(state)

    p2_server_max, p2_star_server_max = SESSION_TIMING[session_type]

    payload = bytes([
        session_type,
        (p2_server_max >> 8) & 0xFF, p2_server_max & 0xFF,
        (p2_star_server_max >> 8) & 0xFF, p2_star_server_max & 0xFF,
    ])

    return uds_types.positive_response(0x10, payload)


# ======================================================================
# 0x11 ECUReset
# ======================================================================


def service_ecu_reset(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x11 ECUReset。

    请求：``11 <resetType>``
    响应：``51 <resetType>``

    目前仅实现 hardReset (0x01)：会话复位为 defaultSession，
    并清除解锁状态与 seed 生命周期（保留失败计数与锁定）。
    """
    if len(request) != 2:
        return uds_types.negative_response(
            0x11, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    reset_type = request[1]

    # hardReset
    if reset_type == 0x01:
        state.session = uds_types.SESSION_DEFAULT
        reset_security_state(state)

        return uds_types.positive_response(0x11, bytes([reset_type]))

    # unsupported reset type
    return uds_types.negative_response(
        0x11, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
    )


# ======================================================================
# 0x22 ReadDataByIdentifier
# ======================================================================


def service_read_data_by_identifier(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x22 ReadDataByIdentifier（支持一次读取多个 DID）。

    请求：``22 <DID:2> [<DID:2> ...]``
    响应：``62 <DID:2> <data...> [<DID:2> <data...> ...]``

    关键语义：**all-or-nothing**。

    DID 先在独立缓冲区累积，确认全部 DID 都存在且权限足够后才整体返回。
    任何一项未知（0x31）或权限不足（0x33）都使整个请求失败，
    **不返回部分数据**——避免 Tester 误解析错位数据，也避免泄露部分信息。
    """
    # 至少需要：
    # 0x22 + 2-byte DID
    #
    # 多个 DID：
    # 0x22 + DID + DID + ...
    if len(request) < 3 or (len(request) - 1) % 2 != 0:
        return uds_types.negative_response(
            0x22, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    # 先在独立缓冲区累积，确认全部 DID 合法后再整体返回：
    # 任何一个 DID 未知或权限不足，都整体拒绝，不返回部分数据。
    payload = bytearray()

    for offset in range(1, len(request), 2):
        did = (request[offset] << 8) | request[offset + 1]

        data = DID_DATABASE.get(did)

        if data is None:
            return uds_types.negative_response(
                0x22, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

        if state.security_level < required_security_level(did):
            # 权限不足：返回 SecurityAccessDenied
            #
            # 另一种可选设计是返回 0x31 以隐藏 DID 存在性（防枚举），
            # 本实验刻意选择 0x33 使权限语义更清晰。
            return uds_types.negative_response(
                0x22, uds_types.NRC_SECURITY_ACCESS_DENIED
            )

        payload.extend([request[offset], request[offset + 1]])
        payload.extend(data)

    return uds_types.positive_response(0x22, payload)


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
