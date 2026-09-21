#!/usr/bin/env python3
"""
UDS Basic —— ECU 运行时状态与服务处理

本模块包含：

- ``EcuState`` / ``STATE``：ECU 的跨请求运行时状态
- ``reset_security_state()`` / ``required_security_level()``：状态与权限 helper
- 八个服务处理函数：
    * ``service_diagnostic_session_control``  (0x10)
    * ``service_ecu_reset``                   (0x11)
    * ``service_read_data_by_identifier``     (0x22)
    * ``service_security_access``             (0x27)
    * ``service_write_data_by_identifier``    (0x2E)
    * ``service_tester_present``              (0x3E)
    * ``service_read_dtc_information``        (0x19)
    * ``service_routine_control``             (0x31)
- ``SERVICES``：静态服务注册表（**不含 0x27**）
- ``_make_security_access_handler()``：为 0x27 绑定请求时依赖的工厂
- ``handle_uds_request()``：协议层 dispatcher

0x27 的时间与配置依赖不进入静态注册表，而由 transport / 入口层在每次请求时
通过 ``handle_uds_request(..., services=...)`` 注入；handler 内部
**不调用任何时钟、不读取全局配置**，因此本模块可以完全不依赖 ``ecu.py``。

刻意**尚未**包含（留待后续阶段）：

- Permission 框架 / DID metadata

依赖方向（单向，禁止反向）：

    ecu.py ──> uds_server.py ──> uds_types.py
                             ├─> did_database.py
                             └─> keygen.py

设计目的：把 UDS 协议层（状态、服务、registry、dispatch）从传输层剥离，
使服务实现只依赖 ``(request, state[, 显式依赖])``，从而可以被独立测试，
并支持多个 ECU 状态实例。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import keygen
import uds_types
from did_database import (
    DID_DATABASE,
    PROTECTED_DIDS,
    WRITABLE_DID_LENGTHS,
    WRITABLE_DIDS,
)
from dtc_database import (
    DTC_DATABASE,
    DTC_FORMAT_IDENTIFIER,
    DTC_STATUS_AVAILABILITY_MASK,
)
from routine_database import (
    MAGIC_ECHO_RESULT,
    ROUTINE_MAGIC_ECHO,
    ROUTINE_NAMES,
    ROUTINE_RESULT_LENGTH,
    ROUTINE_START_COUNTER,
)

__all__ = [
    "EcuState",
    "STATE",
    "SESSION_TIMING",
    "SERVICES",
    "reset_security_state",
    "required_security_level",
    "service_diagnostic_session_control",
    "service_ecu_reset",
    "service_read_data_by_identifier",
    "service_security_access",
    "service_write_data_by_identifier",
    "service_tester_present",
    "service_read_dtc_information",
    "service_routine_control",
    "handle_uds_request",
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


def _default_dtc_records() -> dict[int, int]:
    """出厂默认 DTC 状态：复制静态 ``dtc_database.DTC_DATABASE``。

    独立工厂函数（而非 lambda）便于读文档、避免共享可变默认值，
    也让 dataclass 字段定义保持整洁。
    """
    return dict(DTC_DATABASE)


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
    pending_seed: bytes | None = None

    #: seed 下发时刻（time.monotonic() 基准，用于判断 seed 是否过期）
    seed_issued_at: float | None = None

    #: seed 是否已被成功消费（用于检测重放）
    seed_consumed: bool = False

    #: 连续失败次数（错误 key 递增，解锁成功或锁定到期归零）
    failed_attempts: int = 0

    #: 锁定解除时刻（None 表示未处于锁定）
    lockout_until: float | None = None

    #: seed 序号（每次 RequestSeed 递增，使 seed 每次不同）
    seed_counter: int = 0

    #: 0x2E WriteDataByIdentifier 写入的 DID 运行时值覆盖。
    #:
    #: 只存放被显式写过的 DID；读取时优先于 ``DID_DATABASE`` 的出厂值。
    #: ECUReset(hardReset) 清空本字典，使 DID 恢复出厂值。
    #: 用 field(default_factory=dict) 避免 dataclass 共享可变默认值。
    did_overrides: dict[int, bytes] = field(default_factory=dict)

    #: 0x19 ReadDTCInformation 的运行时 DTC 状态：{DTC(3 字节): statusOfDTC(1 字节)}。
    #:
    #: 默认复制 ``dtc_database.DTC_DATABASE`` 的出厂值（见
    #: ``_default_dtc_records()``）；运行时读取一律从本字段进行。
    #: ECUReset(hardReset) **不清除**本字典——DTC 是故障历史记忆，
    #: 与 ``did_overrides`` 的"复位即恢复出厂"语义刻意不同（测试固化）。
    dtc_records: dict[int, int] = field(default_factory=_default_dtc_records)

    #: 0x31 RoutineControl：当前处于 active 状态的 routine 标识符集合。
    #:
    #: StartRoutine 加入、StopRoutine 移除；重复 Start / 未 active 的
    #: Stop 都由本集合判定（NRC 0x22）。ECUReset(hardReset) 清空。
    #: session 切换**不影响**本集合（0x31 无会话门控，见设计文档）。
    active_routines: set[int] = field(default_factory=set)

    #: 0x31 RoutineControl：start-counter routine 的 Start 累计次数。
    #:
    #: 只被 StartRoutine 递增；RequestRoutineResults 是纯查询，
    #: 结果由本字典即时推导（不缓存 routine_results）。
    #: ECUReset(hardReset) 清空；session 切换不影响。
    routine_start_counts: dict[int, int] = field(default_factory=dict)


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
    清除解锁状态与 seed 生命周期（保留失败计数与锁定）、清空
    DID override 与 routine 状态（保留 DTC 故障记忆）。
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
        # 复位使通过 0x2E 写入的 DID 恢复出厂默认值
        state.did_overrides.clear()
        # routine 状态是易失瞬态：复位清空（与 dtc_records 的故障
        # 记忆语义刻意相反——DTC 保留、routine 清空，测试成对固化）
        state.active_routines.clear()
        state.routine_start_counts.clear()

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

        # 优先读取 0x2E 写入的运行时值，否则回落到出厂默认值
        data: bytes | None
        if did in state.did_overrides:
            data = state.did_overrides[did]
        else:
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

    return uds_types.positive_response(0x22, bytes(payload))


# ======================================================================
# 0x27 SecurityAccess
# ======================================================================


def service_security_access(
    request: bytes,
    state: EcuState,
    *,
    now: float,
    security_config: Mapping[str, int],
) -> bytes:
    """
    处理 0x27 SecurityAccess。

    NRC 判定顺序（严格遵守）：

        1. 基础格式        → 0x13
        2. 子功能          → 0x12
        3. 子功能对应长度   → 0x13
        4. lockout         → 0x37
        5. session/security 条件 → 0x22
        6. sequence        → 0x24
        7. key             → 0x35 / 0x36
        8. 肯定响应

    本函数是**纯函数**：不读取任何模块级状态、不调用时钟、不读取全局配置。
    两项外部依赖由调用方显式传入：

    :param now: 本次请求开始处理时的时间快照（float 值，**不是** callable）。
                由 ``ecu.py`` 在分派时现场求值，使测试可以注入时钟。
    :param security_config: 安全访问参数（``seed_expiry_seconds`` /
                ``max_attempts`` / ``lockout_seconds``）。
                与 ECU 使用**同一个 dict 对象**，以便配置改动即时可见。
    """
    # ---- 1. 基础格式：必须能取到子功能字节 ----
    if len(request) < 2:
        return uds_types.negative_response(
            0x27, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    subfunction = request[1]

    # ---- 2. 子功能：本实验只实现 Level 1（0x01 / 0x02）----
    if subfunction not in (0x01, 0x02):
        return uds_types.negative_response(
            0x27, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    # ---- 3. 子功能对应长度 ----
    if subfunction == 0x01:
        # RequestSeed: 27 <subfunction>
        if len(request) != 2:
            return uds_types.negative_response(
                0x27, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )
    else:
        # SendKey: 27 <subfunction> <key:4>
        if len(request) != 2 + keygen.KEY_LENGTH:
            return uds_types.negative_response(
                0x27, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

    # ---- 4. lockout：锁定期间任何 0x27 请求都返回 0x37 ----
    if state.lockout_until is not None:
        if now < state.lockout_until:
            return uds_types.negative_response(
                0x27, uds_types.NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED
            )

        # 锁定到期：清零惩罚状态，允许重新尝试
        state.lockout_until = None
        state.failed_attempts = 0

    # ---- 5. session / security 条件 ----
    if subfunction == 0x01 and state.security_level != uds_types.SECURITY_LOCKED:
        # 已解锁时再次请求 seed 属于条件不满足
        return uds_types.negative_response(
            0x27, uds_types.NRC_CONDITIONS_NOT_CORRECT
        )

    # ---- RequestSeed ----
    if subfunction == 0x01:
        seed = keygen.generate_seed(state.seed_counter, subfunction)
        state.seed_counter += 1

        state.pending_seed = seed
        state.seed_issued_at = now
        state.seed_consumed = False

        return uds_types.positive_response(0x27, bytes([0x01]) + seed)

    # ---- SendKey ----
    # ---- 6. sequence ----
    if state.pending_seed is None or state.seed_consumed:
        # 要么从未请求 seed，要么 seed 已被成功消费（重放）
        return uds_types.negative_response(
            0x27, uds_types.NRC_REQUEST_SEQUENCE_ERROR
        )

    if state.seed_issued_at is None or (
        now - state.seed_issued_at > security_config["seed_expiry_seconds"]
    ):
        # seed 超时作废
        return uds_types.negative_response(
            0x27, uds_types.NRC_REQUEST_SEQUENCE_ERROR
        )

    # ---- 7. key ----
    key = bytes(request[2:])

    if not keygen.verify_key(state.pending_seed, key):
        state.failed_attempts += 1

        if state.failed_attempts >= security_config["max_attempts"]:
            # 达到上限：设置锁定，本次返回 0x36
            state.lockout_until = now + security_config["lockout_seconds"]
            state.pending_seed = None
            state.seed_issued_at = None
            state.seed_consumed = False

            return uds_types.negative_response(
                0x27, uds_types.NRC_EXCEED_NUMBER_OF_ATTEMPTS
            )

        return uds_types.negative_response(0x27, uds_types.NRC_INVALID_KEY)

    # ---- 8. 肯定响应 ----
    state.security_level = uds_types.SECURITY_LEVEL1
    state.seed_consumed = True
    state.pending_seed = None
    state.seed_issued_at = None
    state.failed_attempts = 0

    return uds_types.positive_response(0x27, bytes([0x02]))


# ======================================================================
# 0x2E WriteDataByIdentifier
# ======================================================================


def service_write_data_by_identifier(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x2E WriteDataByIdentifier。

    请求：``2E <DID:2> <data...>``
    响应：``6E <DID:2>``

    门控顺序（刻意把"只读 DID"放在会话/安全检查之后，语义最清晰）：

        1. 长度         → 0x13（至少 2E + DID:2 + 1 字节数据）
        2. DID 未知     → 0x31 requestOutOfRange
        3. 会话         → defaultSession 返回 0x7F
                          serviceNotSupportedInActiveSession
        4. 安全         → 未解锁返回 0x33 securityAccessDenied
        5. DID 只读     → 0x22 conditionsNotCorrect
        6. 数据长度越界 → 0x13
        7. 写入并返回 6E <DID:2>

    写入值进入 ``state.did_overrides``，随后 0x22 会读到新值；
    ECUReset(hardReset) 清空覆盖，恢复出厂默认值。
    """
    if len(request) < 4:
        return uds_types.negative_response(
            0x2E, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    did = (request[1] << 8) | request[2]
    data = bytes(request[3:])

    if did not in DID_DATABASE:
        return uds_types.negative_response(
            0x2E, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    if state.session == uds_types.SESSION_DEFAULT:
        return uds_types.negative_response(
            0x2E, uds_types.NRC_SERVICE_NOT_SUPPORTED_IN_SESSION
        )

    if state.security_level < uds_types.SECURITY_LEVEL1:
        return uds_types.negative_response(
            0x2E, uds_types.NRC_SECURITY_ACCESS_DENIED
        )

    if did not in WRITABLE_DIDS:
        # 已知但只读的 DID（如 VIN 0xF190）
        return uds_types.negative_response(
            0x2E, uds_types.NRC_CONDITIONS_NOT_CORRECT
        )

    min_len, max_len = WRITABLE_DID_LENGTHS[did]
    if not min_len <= len(data) <= max_len:
        return uds_types.negative_response(
            0x2E, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    state.did_overrides[did] = data

    return uds_types.positive_response(0x2E, bytes([request[1], request[2]]))


# ======================================================================
# 0x3E TesterPresent
# ======================================================================

#: TesterPresent 唯一支持的子功能（zeroSubFunction）。
_TESTER_PRESENT_ZERO_SUBFUNCTION = 0x00

#: suppressPosRspMsgIndicationBit（子功能字节最高位）。
_SUPPRESS_POS_RSP_BIT = 0x80


def service_tester_present(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x3E TesterPresent。

    请求：``3E <subFunction>``

    - ``3E 00``：响应 ``7E 00``。
    - ``3E 80``：子功能为 0 且置 suppressPosRspMsgIndicationBit，
      返回空字节 ``b""``，入口层（``ecu.py``）据此**抑制**任何响应。
    - 其它子功能（剥离 bit7 后非 0）：NRC 0x12 subFunctionNotSupported。
    - 长度不为 2：NRC 0x13。

    TesterPresent 在任意会话可用、不改变任何状态（真实 ECU 用它维持
    非默认会话的 S3* 定时器；本实验 ECU 不实现 S3* 自动退会话，故无副作用）。
    """
    if len(request) != 2:
        return uds_types.negative_response(
            0x3E, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    subfunction = request[1]

    if (subfunction & 0x7F) != _TESTER_PRESENT_ZERO_SUBFUNCTION:
        return uds_types.negative_response(
            0x3E, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    if subfunction & _SUPPRESS_POS_RSP_BIT:
        # 抑制正响应：dispatcher 回传 b""，ecu.py 主循环不得发送空帧。
        return b""

    return uds_types.positive_response(0x3E, bytes([0x00]))


# ======================================================================
# 0x19 ReadDTCInformation
# ======================================================================

#: 本 feature 实现的 0x19 reportType 子功能。
REPORT_NUMBER_OF_DTC = 0x01
REPORT_DTC_BY_STATUS_MASK = 0x02
REPORT_SUPPORTED_DTC = 0x0A


def _matching_dtcs(state: EcuState, status_mask: int) -> list[tuple[int, int]]:
    """返回 ``(statusOfDTC & status_mask) != 0`` 的 (DTC, status) 对。

    按 DTC 升序输出，保证响应字节顺序稳定（0x19/02 教学要求）。
    """
    return sorted(
        (dtc, status) for dtc, status in state.dtc_records.items()
        if (status & status_mask) != 0
    )


def service_read_dtc_information(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x19 ReadDTCInformation。

    本 feature 只实现三个 reportType（其余 → NRC 0x12）：

    - ``19 01 <DTCStatusMask>`` → ``59 01 <DTCStatusAvailabilityMask>
      <DTCFormatIdentifier> <DTCCount_H> <DTCCount_L>``，
      计数 = ``(statusOfDTC & mask) != 0`` 的 DTC 数（16-bit big-endian）。
    - ``19 02 <DTCStatusMask>`` → ``59 02 <DTCStatusAvailabilityMask>
      [(DTC_H DTC_M DTC_L) <statusOfDTC>]*``，只含匹配项、**不含
      DTCCount**，按 DTC 升序。
    - ``19 0A`` → ``59 0A <DTCStatusAvailabilityMask>
      [(DTC_H DTC_M DTC_L) <statusOfDTC>]*``，报告全部支持 DTC
      （不受 status 过滤），按 DTC 升序。

    长度规则：01/02 严格 3 字节，0A 严格 2 字节，否则 NRC 0x13。
    运行时 DTC 状态只从 ``state.dtc_records`` 读取；本 handler
    **不修改任何状态**（本 PR 不提供"设置 DTC 状态"的服务）。
    suppressPosRspMsgIndicationBit 不在本 feature 范围（后续统一处理）。
    """
    if len(request) < 2:
        return uds_types.negative_response(
            0x19, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    report_type = request[1]

    if report_type == REPORT_NUMBER_OF_DTC:
        if len(request) != 3:
            return uds_types.negative_response(
                0x19, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        status_mask = request[2]
        count = len(_matching_dtcs(state, status_mask))

        payload = bytes([
            DTC_STATUS_AVAILABILITY_MASK,
            DTC_FORMAT_IDENTIFIER,
            (count >> 8) & 0xFF,
            count & 0xFF,
        ])

        return uds_types.positive_response(0x19, payload)

    if report_type == REPORT_DTC_BY_STATUS_MASK:
        if len(request) != 3:
            return uds_types.negative_response(
                0x19, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        status_mask = request[2]
        payload = bytes([DTC_STATUS_AVAILABILITY_MASK])

        for dtc, status in _matching_dtcs(state, status_mask):
            payload += bytes([
                (dtc >> 16) & 0xFF,
                (dtc >> 8) & 0xFF,
                dtc & 0xFF,
                status,
            ])

        return uds_types.positive_response(0x19, payload)

    if report_type == REPORT_SUPPORTED_DTC:
        if len(request) != 2:
            return uds_types.negative_response(
                0x19, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        payload = bytes([DTC_STATUS_AVAILABILITY_MASK])

        # 0x0A 报告支持的全部 DTC（不按 status 过滤），稳定排序。
        for dtc, status in sorted(state.dtc_records.items()):
            payload += bytes([
                (dtc >> 16) & 0xFF,
                (dtc >> 8) & 0xFF,
                dtc & 0xFF,
                status,
            ])

        return uds_types.positive_response(0x19, payload)

    return uds_types.negative_response(
        0x19, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
    )


# ======================================================================
# 0x31 RoutineControl
# ======================================================================

#: 本 feature 实现的 0x31 子功能。
ROUTINE_START = 0x01
ROUTINE_STOP = 0x02
ROUTINE_REQUEST_RESULTS = 0x03


def _routine_result(state: EcuState, routine_id: int) -> bytes:
    """
    计算指定 routine 的 Results 内容（纯查询，不修改任何状态）。

    - start-counter：``routine_start_counts`` 的 4 字节 big-endian；
    - magic-echo：固定魔数 ``MAGIC_ECHO_RESULT``。
    """
    if routine_id == ROUTINE_START_COUNTER:
        count = state.routine_start_counts.get(routine_id, 0)
        return count.to_bytes(ROUTINE_RESULT_LENGTH, "big")

    if routine_id == ROUTINE_MAGIC_ECHO:
        return MAGIC_ECHO_RESULT

    # 不可达：调用方已做过 RID 存在性检查，此处仅作防御。
    raise ValueError(f"未知 routine identifier：0x{routine_id:04X}")


def service_routine_control(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x31 RoutineControl。

    本 feature 固定请求形态（不实现 routineControlOptionRecord）：

        ``31 <subFunction> <routineIdentifier:2>``，严格 4 字节。

    - ``01`` StartRoutine：routine 未 active → 标记 active、递增
      start-counter（仅 0x2001 有计数语义）、返回 ``71 01 <RID:2>``；
      已 active → 7F 31 22（状态不变）。
    - ``02`` StopRoutine：active → 取消标记、返回 ``71 02 <RID:2>``；
      未 active → 7F 31 22（状态不变）。
    - ``03`` RequestRoutineResults：任意时刻可查（纯读，不要求 active）、
      返回 ``71 03 <RID:2> <result:4>``。

    NRC：subFunction 非 01/02/03 → 0x12；长度 ≠ 4 → 0x13；
    未知 RID → 0x31。不实现 0x33 / 0x7E / 0x7F（权限/会话门控属
    未来 framework）；正响应刻意不携带 routineStatusRecord（最小模型，
    文档化于 README）。
    """
    if len(request) != 4:
        return uds_types.negative_response(
            0x31, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    sub_function = request[1]
    routine_id = (request[2] << 8) | request[3]

    if sub_function not in (ROUTINE_START, ROUTINE_STOP, ROUTINE_REQUEST_RESULTS):
        return uds_types.negative_response(
            0x31, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    if routine_id not in ROUTINE_NAMES:
        return uds_types.negative_response(
            0x31, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    if sub_function == ROUTINE_START:
        if routine_id in state.active_routines:
            # 重复 Start：状态机不允许，且不得改变任何状态。
            return uds_types.negative_response(
                0x31, uds_types.NRC_CONDITIONS_NOT_CORRECT
            )

        state.active_routines.add(routine_id)

        if routine_id == ROUTINE_START_COUNTER:
            state.routine_start_counts[routine_id] = (
                state.routine_start_counts.get(routine_id, 0) + 1
            )

        return uds_types.positive_response(0x31, bytes([ROUTINE_START, request[2], request[3]]))

    if sub_function == ROUTINE_STOP:
        if routine_id not in state.active_routines:
            # 未 active 的 Stop：状态机不允许，且不得改变任何状态。
            return uds_types.negative_response(
                0x31, uds_types.NRC_CONDITIONS_NOT_CORRECT
            )

        state.active_routines.remove(routine_id)

        return uds_types.positive_response(0x31, bytes([ROUTINE_STOP, request[2], request[3]]))

    # ROUTINE_REQUEST_RESULTS：纯查询，不改变任何状态。
    result = _routine_result(state, routine_id)

    return uds_types.positive_response(
        0x31, bytes([ROUTINE_REQUEST_RESULTS, request[2], request[3]]) + result
    )


# ======================================================================
# UDS 服务分发
# ======================================================================


def _make_security_access_handler(
    now: float,
    security_config: Mapping[str, int],
) -> Callable[[bytes, EcuState], bytes]:
    """
    为 0x27 SecurityAccess 构造一个已绑定运行时依赖的 handler。

    0x27 需要两项请求时依赖：时间快照 ``now`` 与安全配置 ``security_config``。
    本工厂把它们绑定进一个标准形态 ``(request, state) -> bytes`` 的 handler，
    使它可以与其它服务一样放进 registry。

    约束：

    - ``now`` 必须是**已经求值好的 float**，不是 callable、不是时钟函数。
      传入 ``_now`` 或 ``time.monotonic`` 会在测试 patch 时产生陈旧引用，
      使时间注入静默失效。
    - ``security_config`` 必须是**原对象引用**，不得 copy/snapshot，
      以保持"配置改动即时可见"的语义。
    - 本工厂不调用任何时钟，也不读取任何模块级运行时状态。
    """
    def handler(request: bytes, state: EcuState) -> bytes:
        return service_security_access(
            request,
            state,
            now=now,
            security_config=security_config,
        )

    return handler


#: UDS 静态服务注册表：服务标识符 → 处理函数。
#:
#: 所有条目的调用形态统一为 ``handler(request, state) -> bytes``。
#:
#: 0x27 SecurityAccess **不在本表内**：它需要请求时依赖
#: （now + SECURITY_CONFIG），由 transport / 入口层在每次请求时通过
#: ``handle_uds_request(..., services=...)`` 注入
#: （见 ``_make_security_access_handler``）。
SERVICES = {
    0x10: service_diagnostic_session_control,
    0x11: service_ecu_reset,
    0x19: service_read_dtc_information,
    0x22: service_read_data_by_identifier,
    0x2E: service_write_data_by_identifier,
    0x31: service_routine_control,
    0x3E: service_tester_present,
}


def handle_uds_request(
    request: bytes,
    state: EcuState,
    *,
    services: Mapping[int, Callable[[bytes, EcuState], bytes]] | None = None,
) -> bytes:
    """
    处理一条 UDS 请求，返回响应字节。

    :param request: 原始 UDS 请求字节
    :param state:   目标 ECU 状态（显式传入，不使用隐式全局状态）
    :param services: 可选的服务表覆盖。为 None 时使用静态 ``SERVICES``；
                     入口层借助它注入需要请求时依赖的服务条目（0x27）。
    """
    # 空请求必须在查表之前处理：空请求没有 SID 可取，
    # 若先执行 request[0] 会 IndexError。此处固定回显 SID 0x00 + NRC 0x13。
    if not request:
        return uds_types.negative_response(
            0x00, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    service = request[0]

    table = SERVICES if services is None else services

    handler = table.get(service)

    if handler is None:
        # 未注册的 SID：回显请求 SID + NRC 0x11。
        # 不额外做长度检查；尾随负载一并忽略。
        return uds_types.negative_response(
            service, uds_types.NRC_SERVICE_NOT_SUPPORTED
        )

    return handler(request, state)


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
