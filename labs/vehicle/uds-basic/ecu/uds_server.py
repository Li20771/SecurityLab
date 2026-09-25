#!/usr/bin/env python3
"""
UDS Basic —— ECU 运行时状态与服务处理

本模块包含：

- ``EcuState`` / ``STATE``：ECU 的跨请求运行时状态
- ``reset_security_state()`` / ``required_security_level()``：状态与权限 helper
- 十四个服务处理函数：
    * ``service_diagnostic_session_control``  (0x10)
    * ``service_ecu_reset``                   (0x11)
    * ``service_clear_diagnostic_information`` (0x14)
    * ``service_read_data_by_identifier``     (0x22)
    * ``service_security_access``             (0x27)
    * ``service_write_data_by_identifier``    (0x2E)
    * ``service_tester_present``              (0x3E)
    * ``service_read_dtc_information``        (0x19)
    * ``service_routine_control``             (0x31)
    * ``service_request_download``            (0x34)
    * ``service_request_upload``              (0x35)
    * ``service_transfer_data``               (0x36)
    * ``service_request_transfer_exit``       (0x37)
    * ``service_request_file_transfer``       (0x38)
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
from dataclasses import dataclass, field, replace

import dtc_runtime
import keygen
import service_spec
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
    DTC_METADATA,
    DTC_SEVERITY_NO_SEVERITY_AVAILABLE,
    DTC_STATUS_AVAILABILITY_MASK,
    DtcMetadata,
)
from file_database import (
    FILE_MODE_ADD_FILE,
    FILE_MODE_DELETE_FILE,
    FILE_MODE_IDLE,
    FILE_MODE_READ_DIR,
    FILE_MODE_READ_FILE,
    FILE_MODE_REPLACE_FILE,
    MAX_DIR_INFO_LENGTH,
    MAX_FILE_SIZE,
    _ancestor_is_file,
    _build_dir_info,
    _default_files,
    _dir_exists,
    _path_exists,
)
from routine_database import (
    MAGIC_ECHO_RESULT,
    ROUTINE_MAGIC_ECHO,
    ROUTINE_NAMES,
    ROUTINE_RESULT_LENGTH,
    ROUTINE_START_COUNTER,
)
from transfer_database import (
    DOWNLOAD_ADDRESS_LENGTH,
    DOWNLOAD_SIZE_LENGTH,
    MAX_BLOCK_LENGTH_BYTES,
    MAX_DOWNLOAD_SIZE,
    MAX_TRANSFER_REQUEST_LENGTH,
    MAX_UPLOAD_SIZE,
    REQUEST_DOWNLOAD_ALFID,
    REQUEST_DOWNLOAD_DFI,
    _upload_chunk,
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
    "service_clear_diagnostic_information",
    "service_read_data_by_identifier",
    "service_security_access",
    "service_write_data_by_identifier",
    "service_tester_present",
    "service_read_dtc_information",
    "service_control_dtc_setting",
    "service_routine_control",
    "service_request_download",
    "service_request_upload",
    "service_transfer_data",
    "service_request_transfer_exit",
    "service_request_file_transfer",
    "resolve_service_spec",
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

    # ---- 0x34/0x36/0x37 download transfer 运行时状态 ----
    #
    # 全部是 synthetic in-memory 下载目标的协议层状态（不是真实 Flash、
    # 不接触文件系统）；transport 层不保存任何 transfer 状态。
    # received 一律由 ``len(download_buffer)`` 推导，不设独立计数器。

    #: 是否有进行中的 download session（IDLE / ACTIVE 判定源）。
    download_active: bool = False

    #: 0x34 解析出的 memoryAddress（仅 metadata，教学保存，不访问真实地址）。
    download_address: int = 0

    #: 0x34 声明的下载总长度（0x36 越界保护与 0x37 完整性判定的依据）。
    download_size: int = 0

    #: 期望的下一个 blockSequenceCounter（0x34 后为 1；成功 +1 并 &0xFF
    #: 回绕；0 表示无进行中会话）。
    download_expected_block: int = 0

    #: 已接收数据的合成内存缓冲（synthetic download target）。
    #: 0x34 时清空；0x36 追加；大小恒 ≤ download_size。
    download_buffer: bytearray = field(default_factory=bytearray)

    #: 上一条被接受的**完整** TransferData 请求（BSC+data）。
    #: 全字节相等的重传是幂等操作（不追加、不推进 counter、仍正响应）。
    download_last_block_request: bytes | None = None

    # ---- 0x35 upload transfer 运行时状态 ----
    #
    # 与 download 共享同一个"transfer 会话槽"：download_active 与
    # upload_active 互斥（0x34/0x35 建立会话时互相检查对方）。
    # 数据源是 synthetic 循环模式（transfer_database.UPLOAD_SOURCE），
    # 不保存 upload buffer——sent 即已发送字节数，块内容按
    # 固定块契约（min(126, remaining)）从源即时生成。

    #: 是否有进行中的 upload session（IDLE / ACTIVE 判定源）。
    upload_active: bool = False

    #: 0x35 解析出的 memoryAddress（仅 metadata，教学保存）。
    upload_address: int = 0

    #: 0x35 声明的上传总长度（1..MAX_UPLOAD_SIZE=32130）。
    upload_size: int = 0

    #: 期望的下一个 BSC（0x35 后为 1；成功 +1 并 &0xFF；0 = 无会话）。
    upload_expected_block: int = 0

    #: 已发送字节数（**唯一发送进度**）：duplicate 判定前置
    #: （sent==0 表示尚无上一块）、块定位基准、0x37 完成判定依据。
    upload_sent: int = 0

    # ---- 0x38 RequestFileTransfer 运行时状态 ----
    #
    # 本 PR **只**新增这三个字段。刻意不引入任何 file 专用 transfer 计数器 /
    # 缓冲：读文件走既有 upload 5 字段（upload_size/sent/expected_block），
    # 写文件走既有 download 6 字段（download_size/buffer/last_block_request），
    # 因此没有 dir_buffer、没有 upload_last_block、没有 DirectoryTransferState、
    # 也没有通用 Transfer framework。

    #: 当前 file transfer 的 modeOfOperation（0 = IDLE）。
    #:
    #: 取值见 ``file_database.FILE_MODE_*``。它是 0x36/0x37 的**分流开关**：
    #: 4/5 让 upload 分支改用文件 / dirInfo 作数据源，1/3 让 download 分支
    #: 在 0x37 完成时把 buffer 提交进 ``files``，0 则完全走既有 0x34/0x35 路径。
    file_mode: int = 0

    #: 当前 file transfer 的目标路径（``file_mode == 0`` 时为 ""）。
    #:
    #: 只保存**字符串**：内容从不缓存，ReadDir 甚至不缓存 dirInfo
    #: （每次都经 ``_build_dir_info`` 确定性重新生成）。
    file_path: str = ""

    #: synthetic in-memory 文件存储：``路径 → 内容``。
    #:
    #: 默认复制 ``file_database.FILE_DATABASE``（见 ``_default_files()``）。
    #: 运行期读写**只**针对本字段，静态 FILE_DATABASE 永不被修改。
    #: ECUReset(hardReset) **不清除**本字典——它是"非易失存储"的教学替身，
    #: 与 download_buffer / upload_sent 等瞬态 transfer 状态语义刻意相反。
    files: dict[str, bytes] = field(default_factory=_default_files)

    #: 0x85 ControlDTCSetting 的开关：DTC status bits 是否允许继续被
    #: ``dtc_runtime.set_dtc_status()`` 更新。
    #:
    #: - ``True``（默认 = DTC setting **ON**）：runtime mutation 正常生效
    #: - ``False``（= DTC setting **OFF**）：mutation 被冻结，
    #:   已有 status **保持不变**
    #:
    #: **不影响 0x14**：ClearDiagnosticInformation 是独立语义 —— OFF 冻结的是
    #: "故障产生 / 更新"这条路径，不是"清除"这条路径，故 OFF 时 0x14 仍可清除。
    #: ECUReset(01/02/03) **恢复为 True**（见 ``_reset_volatile_state``）。
    dtc_setting_enabled: bool = True


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


def _reset_volatile_state(state: EcuState) -> None:
    """
    应用本实验定义的 **reset-sensitive diagnostic state** 状态转换。

    **这不是完整硬件 volatile-memory 模型**：本实验模型没有电源域、没有
    硬件复位、没有应用层重启、也没有 NVM controller。本函数表达的是本 ECU
    在 ECUReset 下**可观测到的**诊断状态变化——凡在本模型内部被视为
    "掉电即失"的状态一律复位，被视为"故障记忆 / 非易失"的一律保留。

    复位（22 字段）::

        session / security_level / pending_seed / seed_issued_at /
        seed_consumed / did_overrides / active_routines /
        routine_start_counts / download_* (6) / upload_* (5) /
        file_mode / file_path / dtc_setting_enabled

    刻意保留（5 字段）::

        failed_attempts / lockout_until / seed_counter
            —— 防止 Tester 通过复位刷掉暴力破解惩罚状态
        dtc_records
            —— 故障历史记忆（与 did_overrides 的"复位即恢复出厂"刻意相反）
        files
            —— "非易失存储"替身（复位中止的是传输，不是已提交数据）

    **experiment-defined**：``RESET_HARD`` / ``RESET_KEY_OFF_ON`` /
    ``RESET_SOFT`` 三个 resetType 共用本转换，无任何类型专属差异。
    本模型不具备区分三者所需的硬件/分层语义。
    """
    state.session = uds_types.SESSION_DEFAULT
    reset_security_state(state)
    # 复位使通过 0x2E 写入的 DID 恢复出厂默认值
    state.did_overrides.clear()
    # routine 状态是易失瞬态：复位清空（与 dtc_records 的故障
    # 记忆语义刻意相反——DTC 保留、routine 清空，测试成对固化）
    state.active_routines.clear()
    state.routine_start_counts.clear()
    # download transfer 同样是进行中的瞬态操作：复位必然中止
    # （buffer/counter/last_block 全清，回到 IDLE）
    state.download_active = False
    state.download_address = 0
    state.download_size = 0
    state.download_expected_block = 0
    state.download_buffer.clear()
    state.download_last_block_request = None
    # upload transfer 同属瞬态操作：复位中止（5 字段全清）
    state.upload_active = False
    state.upload_address = 0
    state.upload_size = 0
    state.upload_expected_block = 0
    state.upload_sent = 0
    # 0x38 file transfer 的会话标识同样是瞬态：复位中止进行中的
    # AddFile / ReplaceFile（**不提交** download_buffer 进 files）、
    # 中止 ReadFile / ReadDir（本来就只读，不影响 files）。
    state.file_mode = FILE_MODE_IDLE
    state.file_path = ""
    # 刻意**不**清 state.files：它是本实验的"非易失存储"替身，
    # 复位中止的是**传输**而不是已提交的数据（与 dtc_records 同侧的
    # 保留语义，与 did_overrides / download_buffer 的恢复出厂语义相反）。
    #
    # 0x85 DTC setting 是易失的**控制模式**（不是故障记忆），因此与
    # session / did_overrides 同侧，复位恢复为上电默认 ON。
    # 若复位保留 OFF，Tester 可让故障检测被无限期停用 —— 一个任何标准
    # 复位都清不掉的"安全相关"状态，与 0x11 幂等复位的语义冲突。
    state.dtc_setting_enabled = True


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

    本实验实现三个 resetType：

        0x01 hardReset        ┐
        0x02 keyOffOnReset    ├─ 本模型下三者状态转换**完全相同**
        0x03 softReset        ┘

    未实现（→ NRC 0x12）：``0x04 enableRapidPowerShutDown``、
    ``0x05 disableRapidPowerShutDown``，以及带 suppressPosRspMsgIndicationBit
    的请求（本实验不实现该位）。

    **三个 resetType 状态等价（experiment-defined）**：区分 hardReset /
    keyOffOnReset / softReset 需要电源域、硬件复位深度、应用层与固件层之分、
    NVM controller 等语义，本教学模型一概不具备。因此它们共用
    :func:`_reset_volatile_state`，**不制造人为差异**。这是本实验的明确选择，
    不是 ISO 14229-1 的强制要求。
    """
    if len(request) != 2:
        return uds_types.negative_response(
            0x11, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    reset_type = request[1]

    if reset_type not in uds_types.SUPPORTED_RESET_TYPES:
        # 未实现的 resetType（0x04 / 0x05 / 0xFF 等）
        return uds_types.negative_response(
            0x11, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    _reset_volatile_state(state)

    return uds_types.positive_response(0x11, bytes([reset_type]))


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
# 0x14 ClearDiagnosticInformation
# ======================================================================

#: 0x14 的 groupOfDTC 通配值：清除**全部** DTC 的 status。
#:
#: 本实验**不实现 ISO 功能组**（0xFFFF00–0xFFFFFE 之类）：现有模型
#: ``{DTC: statusOfDTC}`` 不含任何分组元数据，表达功能组需要新增
#: 映射结构 —— 那是新概念，属本 PR OUT OF SCOPE。因此功能组取值
#: 自然落入"未支持单值 → 0x31"，无需特判。
#: **本约定是本项目 profile，不声称是 ISO 功能组语义的完整实现。**
GROUP_OF_DTC_ALL: int = 0xFFFFFF


def service_clear_diagnostic_information(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x14 ClearDiagnosticInformation。

    请求：``14 <groupOfDTC:3>``（严格 4 字节；groupOfDTC 为 24-bit
    big-endian，与 0x19 输出 DTC 的 ``(dtc>>16, dtc>>8, dtc)`` 编码一致）
    响应：``54``（严格 1 字节，**不回显** groupOfDTC）

    响应为何无参数：ISO 14229-1 对该服务的正响应定义即无参数的 ``0x54``。
    本项目只在确有需要处声明 profile 偏离（如 0x38 的 LFID），此处无需。

    NRC 判定顺序（严格遵守）::

        1. 长度 ≠ 4                        → 0x13
        2. groupOfDTC 不在支持集合          → 0x31
        3. 肯定响应                         → 0x54

    **本服务不设 session / security 门控**（项目 profile 决定）：0x11
    hardReset 是更彻底的破坏性操作（清 DID override 与 routine 状态）
    且当前零门控，若给 0x14 加门控而 0x11 不加则语义不自洽；0x19 读
    DTC 亦零门控。门控抽象属路线图中的 Permission / session framework，
    不在本 PR 范围。

    **支持集合（本项目 profile）**：``0xFFFFFF``（全部）或
    ``state.dtc_records`` 中**实际存在**的单个 DTC。其余一律 0x31
    （含 ISO 功能组取值与 ``0x000000``）。清除一个不存在/未支持的 DTC
    返回 0x31 而**不是** no-op 成功 —— 与"未知 DID → 0x31""未知 RID →
    0x31"的项目惯例一致；这是本项目刻意选择的语义。

    **清除语义：status 置 0x00，绝不删除 key。**

    - key 集合即"支持的 DTC"集合，``0x19/0A`` 报告的正是它。删 key 会让
      已清除的 DTC 从 0x0A 消失，与"supported ≠ 当前故障"矛盾，也会让
      第二次清除退化为 0x31（破坏幂等）。
    - 因此 ``0xFFFFFF`` 是"遍历并置零"，**不是** ``dict.clear()``；
      空库执行 all-clear 仍成功返回 ``54``。
    - 静态 ``DTC_DATABASE`` 永不被写入，``dtc_records`` 也不重新绑定。

    任何 NRC 分支都在**任何 state 写入之前**返回。
    """
    if len(request) != 4:
        return uds_types.negative_response(
            0x14, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    group_of_dtc = (request[1] << 16) | (request[2] << 8) | request[3]

    if group_of_dtc == GROUP_OF_DTC_ALL:
        # 就地置零：key 集合保持不变，且不重新绑定 dtc_records。
        # 取 list() 快照以避免"边遍历边写"的歧义读法（值写回既有 key，
        # dict 大小不变，但显式快照让意图无歧义）。
        for dtc in list(state.dtc_records):
            dtc_runtime.clear_dtc_status(state, dtc)

        return uds_types.positive_response(0x14)

    if group_of_dtc not in state.dtc_records:
        return uds_types.negative_response(
            0x14, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    dtc_runtime.clear_dtc_status(state, group_of_dtc)

    return uds_types.positive_response(0x14)


# ======================================================================
# 0x85 ControlDTCSetting
# ======================================================================

#: 本 feature 实现的 0x85 DTCSettingType 子功能。
DTC_SETTING_ON = 0x01
DTC_SETTING_OFF = 0x02

#: 本实验支持的 DTCSettingType 集合（其余 → NRC 0x12）。
SUPPORTED_DTC_SETTING_TYPES: frozenset[int] = frozenset(
    {
        DTC_SETTING_ON,
        DTC_SETTING_OFF,
    }
)


def service_control_dtc_setting(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x85 ControlDTCSetting。

    请求：``85 <DTCSettingType>``（严格 2 字节）
    响应：``C5 <DTCSettingType>``（**回显** DTCSettingType）

    本实验只实现两个 DTCSettingType：

        ``0x01`` DTC setting **ON**  —— 允许 ``dtc_runtime.set_dtc_status()``
                                       继续更新 DTC status bits
        ``0x02`` DTC setting **OFF** —— **冻结** DTC status bits 的更新

    **本服务不"清 DTC"**：它控制的是 DTC status bits *是否允许继续被 runtime
    mutation 更新*，本身不改变任何已有 status。OFF 之后已有 status 原样冻结。

    **与 0x14 的关系（本项目 profile）**：两者语义**独立** ——
    ``0x85 02``（OFF）**不**阻止 ``0x14`` 清除 DTC。OFF 冻结的是
    "故障产生 / 更新"这条路径，不是"清除"这条路径；因此
    ``0x85 02`` 之后 ``0x14`` 仍能清除，且 ``0x19`` 能看到清除结果。

    NRC 判定顺序（与 0x11 等保持一致：**先长度、后子功能**）::

        1. 长度 != 2                → 0x13
        2. DTCSettingType 未实现     → 0x12
        3. 肯定响应                  → C5 <type>

    **本服务不设 session / security 门控** —— 与本项目 0x11 / 0x14 / 0x19 的
    既有 zero-gated profile 一致；门控抽象属路线图中的
    Permission / session framework，不在本 feature 范围。

    suppressPosRspMsgIndicationBit 不在本 feature 范围（本项目尚未统一处理
    该位，此处刻意不建立全局 suppress framework）。
    """
    if len(request) != 2:
        return uds_types.negative_response(
            0x85, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    setting_type = request[1]

    if setting_type not in SUPPORTED_DTC_SETTING_TYPES:
        return uds_types.negative_response(
            0x85, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    state.dtc_setting_enabled = setting_type == DTC_SETTING_ON

    return uds_types.positive_response(0x85, bytes([setting_type]))


# ======================================================================
# 0x19 ReadDTCInformation
# ======================================================================

#: 本 feature 实现的 0x19 reportType 子功能。
REPORT_NUMBER_OF_DTC = 0x01
REPORT_DTC_BY_STATUS_MASK = 0x02
REPORT_NUMBER_OF_DTC_BY_SEVERITY = 0x07
REPORT_DTC_BY_SEVERITY = 0x08
REPORT_SEVERITY_INFORMATION = 0x09
REPORT_SUPPORTED_DTC = 0x0A

#: 运行时出现**未声明 metadata** 的 DTC 时的回退值。
#:
#: 正常路径下 ``DTC_METADATA`` 覆盖 ``DTC_DATABASE`` 全部条目（导入期护栏
#: 保证），但 ``EcuState.dtc_records`` 允许被运行时/测试注入额外 DTC
#: （如 ``test_hard_reset_preserves_dtc_records`` 注入 0x100009）。
#: 此时按 ISO 语义回退为 ``noSeverityAvailable``：severity=0x00 不匹配
#: 任何非零 severityMask（因此不会出现在 0x07/0x08 结果里），而 0x09
#: 仍能为该 DTC 返回确定记录而不是抛 KeyError。
_NO_METADATA = DtcMetadata(DTC_SEVERITY_NO_SEVERITY_AVAILABLE, 0x00)


def _matching_dtcs(state: EcuState, status_mask: int) -> list[tuple[int, int]]:
    """返回 ``(statusOfDTC & status_mask) != 0`` 的 (DTC, status) 对。

    按 DTC 升序输出，保证响应字节顺序稳定（0x19/02 教学要求）。
    """
    return sorted(
        (dtc, status) for dtc, status in state.dtc_records.items()
        if (status & status_mask) != 0
    )


def _matching_dtcs_by_severity(
    state: EcuState, severity_mask: int, status_mask: int
) -> list[tuple[int, int, int, int]]:
    """
    返回**同时**满足两个 mask 的 ``(DTC, status, severity, functional_unit)``。

    匹配条件（两个条件为**逻辑与**）::

        (severity & severity_mask) != 0
        AND
        (status   & status_mask)   != 0

    按 DTC 升序输出，保证响应字节顺序稳定（0x19/08 教学要求）。

    severity / functional_unit 一律取自静态 ``DTC_METADATA`` —— 它们是
    **DTC 定义属性**，与 ``state.dtc_records`` 的运行时 status 正交：
    0x14 清零 status 会改变本函数的**匹配结果**，但不会改变 severity
    取值本身。
    """
    matched = []

    for dtc, status in state.dtc_records.items():
        metadata = DTC_METADATA.get(dtc, _NO_METADATA)

        if (metadata.severity & severity_mask) == 0:
            continue

        if (status & status_mask) == 0:
            continue

        matched.append(
            (dtc, status, metadata.severity, metadata.functional_unit)
        )

    return sorted(matched)


def service_read_dtc_information(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x19 ReadDTCInformation。

    本 feature 实现六个 reportType（其余 → NRC 0x12）。

    **status 家族**（OQ3 之前即在 main，wire format 冻结不变）：

    - ``19 01 <DTCStatusMask>`` → ``59 <availMask> <DTCFormatIdentifier>
      <DTCCount_H> <DTCCount_L>``；计数 = ``(statusOfDTC & mask) != 0``。
    - ``19 02 <DTCStatusMask>`` → ``59 <availMask> [(DTC:3)(status:1)]*``，
      只含匹配项、**不含 DTCCount**，按 DTC 升序。
    - ``19 0A`` → ``59 <availMask> [(DTC:3)(status:1)]*``，报告全部支持
      DTC（不受 status 过滤），按 DTC 升序。

    **severity 家族**（OQ5 新增，沿用同一 profile）：

    - ``19 07 <DTCSeverityMask> <DTCStatusMask>`` → ``59 <availMask>
      <DTCFormatIdentifier> <DTCCount_H> <DTCCount_L>``；两个 mask 为
      **逻辑与**。与 ``0x19/01`` **同形** —— 本实验的 count 型正响应统一为
      ``59 <availMask> <format> <count>``，**不输出 DTCSeverityAvailabilityMask**。
    - ``19 08 <DTCSeverityMask> <DTCStatusMask>`` → ``59 <availMask>
      [(severity:1)(funcUnit:1)(DTC:3)(status:1)]*``。
    - ``19 09 <DTC:3>`` → ``59 <availMask>
      (severity:1)(funcUnit:1)(DTC:3)(status:1)``；未知 DTC → NRC 0x31。

    **本实验的 wire profile（刻意区别于 ISO 14229-1）**：正响应第 2 字节
    **不是 reportType 回显**，而是 ``DTCStatusAvailabilityMask``::

        ISO 14229-1 : 59 <reportType> <availMask> ...
        SecurityLab : 59 <availMask> ...

    这是本实验**既有的 wire contract**（01/02/0A 自 OQ3 之前即如此，且被
    unit + e2e 逐字节固化）。OQ5 的 07/08/09 **沿用同一 profile**：既不因
    "标准如此"回头改动既有三个类型，也不在新类型上单独引入回显——否则
    同一个服务会出现两套响应解析约定。

    **注意**：去掉 reportType 只意味着**少一个字段**，不代表可以改变其余
    字段的存在性或顺序。本实验的 count 型正响应不带
    ``DTCSeverityAvailabilityMask``；记录型正响应的记录字段顺序为
    ``severity → functionalUnit → DTC → status``（见上）。

    长度规则（含 reportType 字节）：01/02 为 3，07/08 为 4，09 为 5，
    0A 为 2；不符 → NRC 0x13。

    数据来源：运行时 status 只从 ``state.dtc_records`` 读取；
    severity / functional_unit 只从静态 ``dtc_database.DTC_METADATA``
    读取（**不复制**进运行时状态）。本 handler **不修改任何状态**。
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

    if report_type == REPORT_NUMBER_OF_DTC_BY_SEVERITY:
        if len(request) != 4:
            return uds_types.negative_response(
                0x19, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        severity_mask = request[2]
        status_mask = request[3]
        count = len(
            _matching_dtcs_by_severity(state, severity_mask, status_mask)
        )

        # SecurityLab profile：与 0x19/01 同形 —— 只有 SID + availability +
        # format + count，**不输出 DTCSeverityAvailabilityMask**。
        payload = bytes([
            DTC_STATUS_AVAILABILITY_MASK,
            DTC_FORMAT_IDENTIFIER,
            (count >> 8) & 0xFF,
            count & 0xFF,
        ])

        return uds_types.positive_response(0x19, payload)

    if report_type == REPORT_DTC_BY_SEVERITY:
        if len(request) != 4:
            return uds_types.negative_response(
                0x19, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        severity_mask = request[2]
        status_mask = request[3]

        # SecurityLab profile：不输出 DTCSeverityAvailabilityMask。
        payload = bytes([DTC_STATUS_AVAILABILITY_MASK])

        matched = _matching_dtcs_by_severity(
            state, severity_mask, status_mask
        )

        # 每条记录 6 字节，字段顺序为
        # **severity(1) + functionalUnit(1) + DTC(3) + status(1)**。
        for dtc, status, severity, functional_unit in matched:
            payload += bytes([
                severity,
                functional_unit,
                (dtc >> 16) & 0xFF,
                (dtc >> 8) & 0xFF,
                dtc & 0xFF,
                status,
            ])

        return uds_types.positive_response(0x19, payload)

    if report_type == REPORT_SEVERITY_INFORMATION:
        if len(request) != 5:
            return uds_types.negative_response(
                0x19, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        dtc = (request[2] << 16) | (request[3] << 8) | request[4]

        # 与项目既有惯例一致："未知 DID / 未知 RID / 未知 DTC → 0x31"
        if dtc not in state.dtc_records:
            return uds_types.negative_response(
                0x19, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

        metadata = DTC_METADATA.get(dtc, _NO_METADATA)

        # status 取自运行时；severity / functionalUnit 取自静态 metadata，
        # 二者**不合并存储**（0x14 只动前者）。
        # 字段顺序与 0x19/08 的记录一致：
        # **severity(1) + functionalUnit(1) + DTC(3) + status(1)**。
        payload = bytes([
            DTC_STATUS_AVAILABILITY_MASK,
            metadata.severity,
            metadata.functional_unit,
            (dtc >> 16) & 0xFF,
            (dtc >> 8) & 0xFF,
            dtc & 0xFF,
            state.dtc_records[dtc],
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
# 0x34 / 0x36 / 0x37 download transfer（atomic feature）
# ======================================================================


def _validate_download_size(size: int) -> bool:
    """
    校验下载大小是否在本实验支持范围内。

    独立纯函数：wire 上 2 字节 size 字段最大可编码 0xFFFF（= MAX_DOWNLOAD_SIZE），
    因此超限请求无法通过合法 wire 请求构造；本护栏仍保留并单独单测
    （defense-in-depth，直接以 65536 等值调用本函数验证拒绝路径）。
    """
    return 0 < size <= MAX_DOWNLOAD_SIZE


def _parse_request_download(request: bytes) -> tuple[int, int] | None:
    """解析 0x34 请求，返回 (address, size)；结构非法返回 None。

    结构非法（长度 ≠ 8）与字段值不支持（DFI/ALFID/size）分开处理：
    本函数只负责结构；值校验在 handler 中映射到 0x31。
    """
    if len(request) != 1 + 1 + 1 + DOWNLOAD_ADDRESS_LENGTH + DOWNLOAD_SIZE_LENGTH:
        return None

    address = (request[3] << 16) | (request[4] << 8) | request[5]
    size = (request[6] << 8) | request[7]

    return address, size


def service_request_download(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x34 RequestDownload。

    本 feature 只支持一种格式（8 字节）::

        34 00 23 <memoryAddress:3> <memorySize:2>

    - DFI=0x00、ALFID=0x23（sizeLength=2 高半字节 + addressLength=3
      低半字节）为唯一支持值，其它值 → 7F 34 31；
    - size 必须 1..MAX_DOWNLOAD_SIZE（0 或超限 → 7F 34 31；
      超限护栏见 ``_validate_download_size``）；
    - 已有 active download 时 → 7F 34 22，状态不变。

    成功：建立全新 download session（expected=1、buffer 清空、
    last_block=None——不保留上一 session 的任何状态），返回
    ``74 20 00 80``（maxNumberOfBlockLength=128 = 完整 TransferData
    请求长度上限，数据部分上限 126）。
    """
    parsed = _parse_request_download(request)

    if parsed is None:
        return uds_types.negative_response(
            0x34, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    if request[1] != REQUEST_DOWNLOAD_DFI:
        return uds_types.negative_response(
            0x34, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    if request[2] != REQUEST_DOWNLOAD_ALFID:
        return uds_types.negative_response(
            0x34, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    address, size = parsed

    if not _validate_download_size(size):
        return uds_types.negative_response(
            0x34, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    # 方向互斥：download 与 upload 共享 transfer 会话槽，
    # 任一方向 active 时新的 0x34 都拒绝（状态不变）。
    if state.download_active or state.upload_active:
        return uds_types.negative_response(
            0x34, uds_types.NRC_CONDITIONS_NOT_CORRECT
        )

    state.download_active = True
    state.download_address = address
    state.download_size = size
    state.download_expected_block = 1
    state.download_buffer.clear()
    state.download_last_block_request = None

    # lengthFormatIdentifier：高半字节 = maxNumberOfBlockLength 的字节数（2），
    # 低半字节固定 0 → 0x20。
    return uds_types.positive_response(
        0x34, bytes([
            MAX_BLOCK_LENGTH_BYTES << 4,
            (MAX_TRANSFER_REQUEST_LENGTH >> 8) & 0xFF,
            MAX_TRANSFER_REQUEST_LENGTH & 0xFF,
        ])
    )


def _validate_upload_size(size: int) -> bool:
    """
    校验上传大小是否在本实验支持范围内。

    与 ``_validate_download_size`` 同一思路：纯函数、独立单测。
    上限 = MAX_UPLOAD_SIZE（255 块 × 126 字节）——上传方向的 BSC
    是单字节，超过 255 块会回绕造成 duplicate/新块歧义。
    """
    return 0 < size <= MAX_UPLOAD_SIZE


def service_request_upload(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x35 RequestUpload。

    与 0x34 共享同一 8 字节请求结构（方向相反）::

        35 00 23 <memoryAddress:3> <memorySize:2>

    - DFI=0x00、ALFID=0x23 为唯一支持值（其它 → 7F 35 31）；
    - size 必须 1..MAX_UPLOAD_SIZE(32130)（0 或超限 → 7F 35 31）；
    - 任一方向已有 active transfer（download 或 upload）→ 7F 35 22，
      状态不变；
    - 长度 ≠ 8 → 7F 35 13。

    成功：建立全新 upload session（expected=1、sent=0——sent==0
    同时表示"尚不存在上一块"，是 duplicate 判定的前置条件），
    返回 ``75 20 00 80``（maxNumberOfBlockLength=128 指 ECU 每条
    TransferData **响应**的最大长度）。
    """
    parsed = _parse_request_download(request)  # shared 8-byte format parser

    if parsed is None:
        return uds_types.negative_response(
            0x35, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    if request[1] != REQUEST_DOWNLOAD_DFI:
        return uds_types.negative_response(
            0x35, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    if request[2] != REQUEST_DOWNLOAD_ALFID:
        return uds_types.negative_response(
            0x35, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    address, size = parsed

    if not _validate_upload_size(size):
        return uds_types.negative_response(
            0x35, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    if state.download_active or state.upload_active:
        return uds_types.negative_response(
            0x35, uds_types.NRC_CONDITIONS_NOT_CORRECT
        )

    state.upload_active = True
    state.upload_address = address
    state.upload_size = size
    state.upload_expected_block = 1
    state.upload_sent = 0

    # lengthFormatIdentifier：高半字节 = maxNumberOfBlockLength 的字节数（2），
    # 低半字节固定 0 → 0x20（与 0x34 正响应同构）。
    return uds_types.positive_response(
        0x35, bytes([
            MAX_BLOCK_LENGTH_BYTES << 4,
            (MAX_TRANSFER_REQUEST_LENGTH >> 8) & 0xFF,
            MAX_TRANSFER_REQUEST_LENGTH & 0xFF,
        ])
    )


def _upload_source_chunk(state: EcuState, position: int, length: int) -> bytes:
    """
    0x36 upload 分支的**数据源分流**（三路，Design Review v4 冻结）。

    - ``file_mode == FILE_MODE_READ_FILE``：源是 ``state.files[file_path]``
      的字节切片；
    - ``file_mode == FILE_MODE_READ_DIR``：源是 ``_build_dir_info()`` 的
      **即时重算**结果——ReadDir **不缓存** dirInfo，因此 duplicate 重发
      与首发送必然逐字节一致；
    - 其它（``file_mode == 0``，即既有 0x35 RequestUpload）：源是
      ``transfer_database._upload_chunk`` 的 256 字节循环模式，
      行为与本 PR 之前**逐字节一致**。

    三种源都是 ``(position, length)`` 的纯函数，所以既有 upload BSC
    状态机（含 duplicate 重发与尾块重建）不需要任何改动即可复用。

    路径索引是安全的：ReadFile/ReadDir 只在文件/目录**存在**时建立会话
    （否则 0x38 返回 0x31），而传输进行中 DeleteFile 无法执行（0x38 的
    transfer slot 互斥），hardReset 会先把 ``file_mode`` 清回 IDLE。
    """
    if state.file_mode == FILE_MODE_READ_FILE:
        return state.files[state.file_path][position:position + length]

    if state.file_mode == FILE_MODE_READ_DIR:
        return _build_dir_info(state.files, state.file_path)[position:position + length]

    return _upload_chunk(position, length)


def service_transfer_data(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x36 TransferData。

    **顶层方向分流先于任何长度检查**（upload 的合法请求
    ``36 <BSC>`` 是 2 字节，不能先被 download 的 3..128 规则误杀）：

    - upload active → upload 分支（见下）；
    - download active → 既有 download 分支（逻辑逐字节不变）；
    - 都 inactive → 任意形态 → 7F 36 24（无方向，长度规则不适用）。

    Upload 分支（请求严格 ``36 <BSC>``，len != 2 → 7F 36 13）：

    1. ``upload_sent > 0`` 且 BSC == (expected−1)&0xFF → **幂等重发**
       上一块（固定块契约确定性重建，不保存块数据），76 <BSC> <块>，
       sent/expected 均不变（连续重复亦然）；
    2. BSC == expected 且 remaining == 0 → 7F 36 22（无剩余数据，先 0x37）；
    3. BSC == expected → 76 <BSC> <_upload_chunk(sent, min(126, remaining))>，
       sent += chunk_len，expected=(expected+1)&0xFF；
    4. 其它 BSC → 7F 36 73（状态不变）。
    """
    if state.upload_active:
        if len(request) != 2:
            return uds_types.negative_response(
                0x36, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        block_counter = request[1]

        if (
            state.upload_sent > 0
            and block_counter == (state.upload_expected_block - 1) & 0xFF
        ):
            # 幂等重发上一块：按固定块契约确定性重建，不保存块数据。
            if state.upload_sent < state.upload_size:
                prev_len = MAX_TRANSFER_REQUEST_LENGTH - 2
            else:
                prev_len = state.upload_size % (MAX_TRANSFER_REQUEST_LENGTH - 2) or (
                    MAX_TRANSFER_REQUEST_LENGTH - 2
                )

            prev_start = state.upload_sent - prev_len
            chunk = _upload_source_chunk(state, prev_start, prev_len)

            return uds_types.positive_response(
                0x36, bytes([block_counter]) + chunk
            )

        if block_counter != state.upload_expected_block:
            return uds_types.negative_response(
                0x36, uds_types.NRC_WRONG_BLOCK_SEQUENCE_COUNTER
            )

        remaining = state.upload_size - state.upload_sent

        if remaining == 0:
            return uds_types.negative_response(
                0x36, uds_types.NRC_CONDITIONS_NOT_CORRECT
            )

        chunk_len = min(MAX_TRANSFER_REQUEST_LENGTH - 2, remaining)
        chunk = _upload_source_chunk(state, state.upload_sent, chunk_len)

        state.upload_sent += chunk_len
        state.upload_expected_block = (state.upload_expected_block + 1) & 0xFF

        return uds_types.positive_response(0x36, bytes([block_counter]) + chunk)

    if state.download_active:
        # ---- 既有 download 分支：逻辑逐字节不变 ----
        if len(request) < 3 or len(request) > MAX_TRANSFER_REQUEST_LENGTH:
            return uds_types.negative_response(
                0x36, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        block_counter = request[1]
        data = bytes(request[2:])

        if not state.download_active:
            return uds_types.negative_response(
                0x36, uds_types.NRC_REQUEST_SEQUENCE_ERROR
            )

        if request == state.download_last_block_request:
            # 幂等重传：上一条完整请求的完全重复（含 BSC 相同）。
            # 刻意放在越界检查**之前**：buffer 已满时重发仍须正响应。
            return uds_types.positive_response(0x36, bytes([block_counter]))

        if block_counter != state.download_expected_block:
            return uds_types.negative_response(
                0x36, uds_types.NRC_WRONG_BLOCK_SEQUENCE_COUNTER
            )

        if len(data) > MAX_TRANSFER_REQUEST_LENGTH - 2:
            return uds_types.negative_response(
                0x36, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        if len(state.download_buffer) + len(data) > state.download_size:
            return uds_types.negative_response(
                0x36, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        state.download_buffer.extend(data)
        state.download_expected_block = (state.download_expected_block + 1) & 0xFF
        state.download_last_block_request = request

        return uds_types.positive_response(0x36, bytes([block_counter]))

    return uds_types.negative_response(
        0x36, uds_types.NRC_REQUEST_SEQUENCE_ERROR
    )


def service_request_transfer_exit(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x37 RequestTransferExit。

    请求严格 1 字节（本 feature 不实现 requestTransferExitRecord）。
    该长度规则**方向无关**（两方向一致），可安全置于分流之前。

    顶层方向分流（upload 分支必须先于 download 检查，
    避免旧结构 ``if not download_active → 24`` 吞掉 upload 会话）：

    - upload active：sent < size → 7F 37 22（状态不变，可继续 0x36）；
      sent == size → 77 + 清理全部 5 个 upload 字段 + ``file_mode`` /
      ``file_path`` → IDLE。本分支对 0x35 与 0x38 ReadFile/ReadDir 同构，
      因为三者共用同一套 upload 字段。
    - download active：既有 download 完成分支——
      received < requested → 7F 37 22；complete → 77 + 清理 download
      字段 + ``file_mode`` / ``file_path`` → IDLE。
      ``file_mode`` 为 AddFile / ReplaceFile 时，**在清理之前**先把
      ``download_buffer`` 提交进 ``state.files``（见下）。
    - 都 inactive → 7F 37 24。

    Add/Replace 的原子性完全落在这里：``state.files`` 只在"buffer == size
    且 0x37 正常收尾"这一个时刻被写入。提前 abort、传输不完整、hardReset
    都到不了该写入点，因此既不会新建也不会覆盖目标路径。
    """
    if len(request) != 1:
        return uds_types.negative_response(
            0x37, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    if state.upload_active:
        if state.upload_sent < state.upload_size:
            return uds_types.negative_response(
                0x37, uds_types.NRC_CONDITIONS_NOT_CORRECT
            )

        state.upload_active = False
        state.upload_address = 0
        state.upload_size = 0
        state.upload_expected_block = 0
        state.upload_sent = 0
        # 0x38 ReadFile / ReadDir 的会话标识随之清回 IDLE。对既有 0x35
        # 而言这两个字段本就是 0 / ""（file_mode 只由 0x38 设置，而 0x38
        # 要求无 active transfer），因此该清理对 0x35 语义完全无影响。
        # ReadFile / ReadDir 全程**不修改** files。
        state.file_mode = FILE_MODE_IDLE
        state.file_path = ""

        return uds_types.positive_response(0x37)

    if state.download_active:
        if len(state.download_buffer) < state.download_size:
            return uds_types.negative_response(
                0x37, uds_types.NRC_CONDITIONS_NOT_CORRECT
            )

        # 0x38 AddFile / ReplaceFile 的提交点：**只有**传输完整走完并由
        # 0x37 正常收尾时才写入 files。中途 abort（0x37 提前 / 0x11 复位）
        # 永远到不了这里，因此既不会新建也不会覆盖目标路径。
        # 对既有 0x34 内存下载（file_mode == 0）本分支不提交任何东西。
        if state.file_mode in (FILE_MODE_ADD_FILE, FILE_MODE_REPLACE_FILE):
            state.files[state.file_path] = bytes(state.download_buffer)

        state.download_active = False
        state.download_address = 0
        state.download_size = 0
        state.download_expected_block = 0
        state.download_buffer.clear()
        state.download_last_block_request = None
        state.file_mode = FILE_MODE_IDLE
        state.file_path = ""

        return uds_types.positive_response(0x37)

    return uds_types.negative_response(
        0x37, uds_types.NRC_REQUEST_SEQUENCE_ERROR
    )


# ======================================================================
# 0x38 RequestFileTransfer（synthetic in-memory 文件模型）
# ======================================================================
#
# 本节实现 Design Review v4 冻结的**实验 profile**，不是 ISO 14229-1 的
# 一比一实现。三处刻意的 profile 差异（README 4.8 有完整说明）：
#
# 1. 正响应里的 lengthFormatIdentifier 固定 **0x02**，表示
#    maxNumberOfBlockLength 用 2 bytes 编码。这与 0x34/0x35 的
#    "高半字节 = 该字段的字节数"编码（0x20）**不是**同一套，不要混用。
# 2. ReadDir 正响应恰好 10 字节：
#    ``78 05 02 00 80 00 00 02 <dirInfoLength:2>``，
#    其中 fileSizeOrDirInfoParameterLength = 0x0002 表示紧跟其后的
#    dirInfoLength 用 2 bytes 编码。响应**绝不携带 dirInfo 本体**——
#    真正的目录数据只能经 0x36/0x37 送出。
# 3. ReadFile 正响应恰好 16 字节：
#    ``78 04 02 00 80 00 00 04 <sizeUncompressed:4> <sizeCompressed:4>``，
#    其中 fileSizeOrDirInfoParameterLength = 0x0004 表示后面每个 size
#    字段用 4 bytes 编码。
#
# modeOfOperation **不是** UDS 子功能字节：非法 mode 一律 NRC 0x31，
# 不使用子功能专用的 0x12。

#: 0x38 唯一支持的 dataFormatIdentifier（0x00 = 无压缩 / 无加密）。
FILE_DFI: int = 0x00

#: 0x38 请求中 fileSizeParameterLength 唯一支持值（每个 size 字段 4 字节）。
FILE_SIZE_PARAMETER_LENGTH: int = 0x04

#: 0x38 正响应的 lengthFormatIdentifier（本实验 profile，见上）。
FILE_LENGTH_FORMAT_IDENTIFIER: int = 0x02

#: ReadDir 正响应中 fileSizeOrDirInfoParameterLength 的取值（2 字节编码）。
DIR_INFO_PARAMETER_LENGTH: int = 0x0002

#: 本 feature 支持的 modeOfOperation 集合。
#: 0（IDLE）是 ``EcuState.file_mode`` 的内部取值，不是合法请求 mode。
SUPPORTED_FILE_MODES: frozenset[int] = frozenset({
    FILE_MODE_ADD_FILE,
    FILE_MODE_DELETE_FILE,
    FILE_MODE_REPLACE_FILE,
    FILE_MODE_READ_FILE,
    FILE_MODE_READ_DIR,
})

#: filePathAndName 的字节长度上限（本实验模型约束）。
MAX_FILE_PATH_LENGTH: int = 64

#: filePathAndName 允许的字节范围：可打印 ASCII（含端点）。
_PATH_MIN_BYTE: int = 0x20
_PATH_MAX_BYTE: int = 0x7E

#: 各 mode 的请求总长度与 pathLen 的差值
#: （SID:1 + mode:1 + pathLen:2 + mode 专属尾部字段）。
_FILE_REQUEST_OVERHEAD: dict[int, int] = {
    FILE_MODE_ADD_FILE: 14,      # DFI:1 + fspLen:1 + sizeUncompressed:4 + sizeCompressed:4
    FILE_MODE_DELETE_FILE: 4,    # 无尾部字段
    FILE_MODE_REPLACE_FILE: 14,
    FILE_MODE_READ_FILE: 5,      # DFI:1
    FILE_MODE_READ_DIR: 4,       # 无尾部字段
}


def _parse_file_path(
    request: bytes,
    offset: int,
    length: int,
    mode: int,
) -> str | None:
    """
    解析并校验 ``filePathAndName``；非法返回 ``None``（调用方映射 0x31）。

    本实验模型约束（**不**声称都是 ISO 强制限制）：

    - 非空、≤ 64 字节；
    - 全部为可打印 ASCII（0x20–0x7E）——因此 ``decode("ascii")`` 必然安全；
    - 以 ``/`` 开头；
    - 文件路径（mode 1/2/3/4）不得以 ``/`` 结尾；
    - ReadDir（mode 5）必须为 ``/`` 或以 ``/`` 结尾。
    """
    if length == 0 or length > MAX_FILE_PATH_LENGTH:
        return None

    raw = bytes(request[offset:offset + length])

    if not raw.startswith(b"/"):
        return None

    if not all(_PATH_MIN_BYTE <= byte <= _PATH_MAX_BYTE for byte in raw):
        return None

    if mode == FILE_MODE_READ_DIR:
        if not (raw == b"/" or raw.endswith(b"/")):
            return None
    elif raw.endswith(b"/"):
        return None

    return raw.decode("ascii")


def service_request_file_transfer(request: bytes, state: EcuState) -> bytes:
    """
    处理 0x38 RequestFileTransfer（synthetic in-memory 文件模型）。

    请求形态由 mode 决定::

        AddFile     38 01 <pathLen:2> <path> <DFI:1> <fspLen:1> <sizeU:4> <sizeC:4>
        DeleteFile  38 02 <pathLen:2> <path>
        ReplaceFile 38 03 <pathLen:2> <path> <DFI:1> <fspLen:1> <sizeU:4> <sizeC:4>
        ReadFile    38 04 <pathLen:2> <path> <DFI:1>
        ReadDir     38 05 <pathLen:2> <path>

    NRC 判定顺序（严格遵守，Design Review v4 冻结）::

        1. 长度不足 / 取不到 pathLen:2         → 0x13
        2. mode ∉ {1,2,3,4,5}                  → 0x31（**不是** 0x12）
        3. pathLen 超过实际 payload            → 0x13
        4. path 非法                           → 0x31
        5. mode 专属结构错（字段多 / 缺）       → 0x13
        6. 不支持的值（DFI / fspLen /
           size == 0 / size > MAX_FILE_SIZE /
           compressed != uncompressed）        → 0x31
        7. transfer slot 忙                    → 0x22
        8. 文件状态（存在性 / 是否目录 /
           dirInfo 超长）                      → 0x22 / 0x31
        9. 肯定响应

    任何 NRC 路径都**不修改任何状态**（判定全部前置于建立会话）。

    建立会话时 mode 1/3 占用既有 download 6 字段、mode 4/5 占用既有
    upload 5 字段，并同时写入 ``file_mode`` / ``file_path`` 供 0x36/0x37
    分流。因为复用的是既有 active 标志，0x34/0x35 无需任何改动即获得
    与 0x38 的完整双向互斥。

    mode 2 DeleteFile 是 **one-shot**：立即删除并返回 ``78 02``，既不建立
    transfer 也不保留 ``file_mode`` / ``file_path``；因此完成后 0x36/0x37
    直接是 0x24，而 0x34/0x35 立刻可用。
    """
    # ---- 1. 长度不足 / 取不到 pathLen:2 → 0x13 ----
    if len(request) < 4:
        return uds_types.negative_response(
            0x38, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    mode = request[1]

    # ---- 2. mode 非法 → 0x31 ----
    if mode not in SUPPORTED_FILE_MODES:
        return uds_types.negative_response(
            0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    path_length = (request[2] << 8) | request[3]

    # ---- 3. pathLen 声明的长度超过实际 payload → 0x13 ----
    if len(request) < 4 + path_length:
        return uds_types.negative_response(
            0x38, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    # ---- 4. path 非法 → 0x31 ----
    path = _parse_file_path(request, 4, path_length, mode)

    if path is None:
        return uds_types.negative_response(
            0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
        )

    # ---- 5. mode 专属结构错（合法长度固定为 pathLen + overhead）→ 0x13 ----
    if len(request) != path_length + _FILE_REQUEST_OVERHEAD[mode]:
        return uds_types.negative_response(
            0x38, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    # ---- 6. 不支持的值 → 0x31 ----
    file_size = 0

    if mode in (FILE_MODE_ADD_FILE, FILE_MODE_REPLACE_FILE):
        if request[4 + path_length] != FILE_DFI:
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

        if request[5 + path_length] != FILE_SIZE_PARAMETER_LENGTH:
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

        size_uncompressed = int.from_bytes(
            request[6 + path_length:10 + path_length], "big")
        size_compressed = int.from_bytes(
            request[10 + path_length:14 + path_length], "big")

        if size_uncompressed == 0 or size_uncompressed > MAX_FILE_SIZE:
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

        # 无压缩模型：compressed 必须与 uncompressed 相等，且 0 一律拒绝。
        if size_compressed == 0 or size_compressed != size_uncompressed:
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

        file_size = size_uncompressed

    elif mode == FILE_MODE_READ_FILE:
        if request[4 + path_length] != FILE_DFI:
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

    # ---- 7. transfer slot 忙（任一方向 active 都拒绝）→ 0x22 ----
    if state.download_active or state.upload_active:
        return uds_types.negative_response(
            0x38, uds_types.NRC_CONDITIONS_NOT_CORRECT
        )

    # ---- 8. 文件状态 → 0x22 / 0x31 ----
    #
    # 文件名与目录名共享同一命名空间（本项目的模型约束，非 ISO 规定），
    # AddFile 有两类必须拒绝的冲突，任一漏掉都会让目录视图无法唯一编码：
    #
    #   1. 目标名字已被占用（已存在的文件 **或** 已存在的隐式目录）；
    #   2. 目标路径的某个**祖先已是文件**——否则该祖先会同时是文件与
    #      隐式目录，dirInfo 里一个名字只能有一个 size，两种语义
    #      无法同时表示（见 file_database._ancestor_is_file）。
    if mode == FILE_MODE_ADD_FILE:
        if _path_exists(state.files, path) or _ancestor_is_file(state.files, path):
            return uds_types.negative_response(
                0x38, uds_types.NRC_CONDITIONS_NOT_CORRECT
            )

    elif mode == FILE_MODE_REPLACE_FILE:
        if path not in state.files:
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

    elif mode == FILE_MODE_READ_FILE:
        if path not in state.files:
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

    elif mode == FILE_MODE_DELETE_FILE:
        # DeleteFile 只删文件：本 feature 没有 rmdir，目录路径一律 0x31。
        if path not in state.files:
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

    elif mode == FILE_MODE_READ_DIR:
        if not _dir_exists(state.files, path):
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

    # ---- 9. 肯定响应 ----
    #
    # DeleteFile：one-shot，删完即 IDLE —— 不建立 transfer、不动任何
    # counter、不保留 file_mode / file_path。
    if mode == FILE_MODE_DELETE_FILE:
        del state.files[path]

        return uds_types.positive_response(0x38, bytes([FILE_MODE_DELETE_FILE]))

    # ---- AddFile / ReplaceFile：建立 download transfer ----
    if mode in (FILE_MODE_ADD_FILE, FILE_MODE_REPLACE_FILE):
        state.file_mode = mode
        state.file_path = path
        state.download_active = True
        state.download_address = 0
        state.download_size = file_size
        state.download_expected_block = 1
        state.download_buffer.clear()
        state.download_last_block_request = None

        return uds_types.positive_response(0x38, bytes([
            mode,
            FILE_LENGTH_FORMAT_IDENTIFIER,
            (MAX_TRANSFER_REQUEST_LENGTH >> 8) & 0xFF,
            MAX_TRANSFER_REQUEST_LENGTH & 0xFF,
            FILE_DFI,
        ]))

    # ---- ReadFile / ReadDir：建立 upload transfer ----
    #
    # 两者共用既有 upload 5 字段；数据源由 0x36 按 file_mode 分流，
    # 因此这里**不**保存任何数据副本——ReadDir 连 dirInfo 都不缓存，
    # 只保存它的长度作为 upload_size。
    if mode == FILE_MODE_READ_FILE:
        file_size = len(state.files[path])
        payload = bytes([
            FILE_MODE_READ_FILE,
            FILE_LENGTH_FORMAT_IDENTIFIER,
            (MAX_TRANSFER_REQUEST_LENGTH >> 8) & 0xFF,
            MAX_TRANSFER_REQUEST_LENGTH & 0xFF,
            FILE_DFI,
            (FILE_SIZE_PARAMETER_LENGTH >> 8) & 0xFF,
            FILE_SIZE_PARAMETER_LENGTH & 0xFF,
        ]) + file_size.to_bytes(4, "big") + file_size.to_bytes(4, "big")

    else:
        dir_info_length = len(_build_dir_info(state.files, path))

        if dir_info_length > MAX_DIR_INFO_LENGTH:
            return uds_types.negative_response(
                0x38, uds_types.NRC_REQUEST_OUT_OF_RANGE
            )

        file_size = dir_info_length
        payload = bytes([
            FILE_MODE_READ_DIR,
            FILE_LENGTH_FORMAT_IDENTIFIER,
            (MAX_TRANSFER_REQUEST_LENGTH >> 8) & 0xFF,
            MAX_TRANSFER_REQUEST_LENGTH & 0xFF,
            FILE_DFI,
            (DIR_INFO_PARAMETER_LENGTH >> 8) & 0xFF,
            DIR_INFO_PARAMETER_LENGTH & 0xFF,
            (dir_info_length >> 8) & 0xFF,
            dir_info_length & 0xFF,
        ])

    state.file_mode = mode
    state.file_path = path
    state.upload_active = True
    state.upload_address = 0
    state.upload_size = file_size
    state.upload_expected_block = 1
    state.upload_sent = 0

    return uds_types.positive_response(0x38, payload)


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
    0x14: service_clear_diagnostic_information,
    0x19: service_read_dtc_information,
    0x22: service_read_data_by_identifier,
    0x2E: service_write_data_by_identifier,
    0x31: service_routine_control,
    0x34: service_request_download,
    0x35: service_request_upload,
    0x36: service_transfer_data,
    0x37: service_request_transfer_exit,
    0x38: service_request_file_transfer,
    0x3E: service_tester_present,
    0x85: service_control_dtc_setting,
}


# ======================================================================
# C1 Phase 1 —— 服务授权规格模型（ServiceSpec / guards）
# ======================================================================
#
# **Phase 1 定位（历史）：只建模，不执行。**
#
# 下面的 ``SERVICE_SPECS`` 是 ``SERVICES`` 的**平行模型**。Phase 1 **当时**，
# dispatcher（``handle_uds_request``）仍走 ``SERVICES[sid] -> handler``，
# **不执行**任何 ``guards`` —— 因此本节的引入**不改变任何可观测行为**，
# 只使真实授权语义显式、可校验、可对拍。
#
# **当前状态**：Phase 2 Step 1 起 dispatcher 改为经 ``resolve_service_spec()``
# 进入执行框架；**执行哪些 guard 由源码常量 ``_SPEC_EXECUTION`` 控制** ——
# 已列入该白名单的 SID 执行其声明式 guard 链，未列入的 SID 跳过 guards。
# Migration A 已启用 0x22。
#
# guards 的语义（见 ``service_spec.Guard``）：返回 NRC 表示拒绝，``None`` 表示通过；
# **顺序即语义**，必须与下列 handler 内的既有 inline 顺序**逐级一致**。


def _guard_read_did(request: bytes, state: EcuState) -> int | None:
    """
    ``0x22`` 的**单个** guard：完整复刻 ``service_read_data_by_identifier`` 的
    判定语义（``uds_server.py`` 内该 handler 的 ① 格式 + ② 逐 DID 循环）。

    顺序与短路规则**必须与 inline 实现一致**：

        ① 格式：``len>=3`` 且 ``(len-1) % 2 == 0``      → ``0x13``
        ② 逐 DID 循环（**按请求顺序**）：
             未知 DID                                   → ``0x31``
             ``security_level < required_security_level(did)`` → ``0x33``

    **首个失败的 DID 决定 NRC**（all-or-nothing）—— 因此 NRC **依赖 DID 在请求中的
    顺序**（``22 F1 87 FF FF`` → ``33``，而 ``22 FF FF F1 87`` → ``31``）。
    任何"先解析全部 DID 再统一判定"的写法都会改变该行为。
    """
    # ① 格式（至少 0x22 + 2 字节 DID；DID 列表长度必须是偶数）
    if len(request) < 3 or (len(request) - 1) % 2 != 0:
        return uds_types.NRC_INCORRECT_MESSAGE_LENGTH

    # ② 逐 DID 循环，短路于**第一个**失败的 DID
    for offset in range(1, len(request), 2):
        did = (request[offset] << 8) | request[offset + 1]

        # 与 inline 实现等价：override 优先，其次出厂库；两者都没有 → 未知
        if did not in state.did_overrides and did not in DID_DATABASE:
            return uds_types.NRC_REQUEST_OUT_OF_RANGE

        if state.security_level < required_security_level(did):
            return uds_types.NRC_SECURITY_ACCESS_DENIED

    return None


def _guard_write_did_length_format(request: bytes, state: EcuState) -> int | None:
    """``0x2E`` ①：``len >= 4``（``2E <DID:2> <data...>``）→ 否则 ``0x13``。"""
    if len(request) < 4:
        return uds_types.NRC_INCORRECT_MESSAGE_LENGTH
    return None


def _guard_write_did_known(request: bytes, state: EcuState) -> int | None:
    """``0x2E`` ②：DID 必须在 ``DID_DATABASE`` 中 → 否则 ``0x31``（**先于**会话门控）。"""
    did = (request[1] << 8) | request[2]
    if did not in DID_DATABASE:
        return uds_types.NRC_REQUEST_OUT_OF_RANGE
    return None


def _guard_write_did_session(request: bytes, state: EcuState) -> int | None:
    """
    ``0x2E`` ③：``session != DEFAULT`` → 否则 ``0x7F``。

    实现**刻意保留** inline 的 ``== SESSION_DEFAULT`` 写法（而非
    ``not in allowed``），以避免引入新语义（设计文档 §18.1 OD-5）。
    """
    if state.session == uds_types.SESSION_DEFAULT:
        return uds_types.NRC_SERVICE_NOT_SUPPORTED_IN_SESSION
    return None


def _guard_write_did_security(request: bytes, state: EcuState) -> int | None:
    """``0x2E`` ④：``security_level >= LEVEL1`` → 否则 ``0x33``。"""
    if state.security_level < uds_types.SECURITY_LEVEL1:
        return uds_types.NRC_SECURITY_ACCESS_DENIED
    return None


def _guard_write_did_writable(request: bytes, state: EcuState) -> int | None:
    """``0x2E`` ⑤：DID 必须可写 → 否则 ``0x22``（已知但只读，如 VIN ``0xF190``）。"""
    did = (request[1] << 8) | request[2]
    if did not in WRITABLE_DIDS:
        return uds_types.NRC_CONDITIONS_NOT_CORRECT
    return None


def _guard_write_did_data_length(request: bytes, state: EcuState) -> int | None:
    """
    ``0x2E`` ⑥：数据长度必须在 ``WRITABLE_DID_LENGTHS[did]`` 区间内 → 否则 ``0x13``。

    前置条件：⑤ 已保证 DID 可写，因此这里必然能取到长度区间（链式短路）。
    """
    did = (request[1] << 8) | request[2]
    data = bytes(request[3:])
    min_len, max_len = WRITABLE_DID_LENGTHS[did]
    if not min_len <= len(data) <= max_len:
        return uds_types.NRC_INCORRECT_MESSAGE_LENGTH
    return None


def _build_service_specs() -> dict[int, service_spec.ServiceSpec[EcuState]]:
    """
    构造静态 ``SERVICES`` 中 **14 个**服务的完整 spec。

    ``0x27`` SecurityAccess 属**动态注入**服务（不在静态表内），由
    ``SPECS_EXCLUDED`` 显式记录，本函数**不**为它构造 spec（见该常量处说明）。

    授权语义**完全来自当前 inline 实现**：

    - ``0x22`` / ``0x2E``：**有**授权 guard（见上），且 ``payload_scoped=True``。
    - 其余 12 个：**没有**授权 guard（其 NRC 全部为格式 / 范围 / 序列类），
      ``guards=()``。
      **刻意不因为它们"看起来应该有"而新增 session/security 限制** ——
      那会是行为变更（设计文档 §8.3 明确禁止）。
    """
    specs: dict[int, service_spec.ServiceSpec[EcuState]] = {}

    for sid, handler in SERVICES.items():
        specs[sid] = service_spec.ServiceSpec[EcuState](sid=sid, handler=handler)

    # 0x22：per-DID 权限 → 无法用单一 required_security 概括；NRC 依赖 DID 顺序
    specs[0x22] = service_spec.ServiceSpec[EcuState](
        sid=0x22,
        handler=SERVICES[0x22],
        guards=(_guard_read_did,),
        allowed_sessions=None,
        required_security=None,          # per-DID：F187 → LEVEL1，F190 → LOCKED
        payload_scoped=True,
        note="per-DID 安全等级 + NRC 依赖 DID 请求顺序（all-or-nothing）",
    )

    # 0x2E：整体要求 LEVEL1（合法声明），但完整链还依赖 payload 与**顺序**
    specs[0x2E] = service_spec.ServiceSpec[EcuState](
        sid=0x2E,
        handler=SERVICES[0x2E],
        guards=(
            _guard_write_did_length_format,
            _guard_write_did_known,
            _guard_write_did_session,
            _guard_write_did_security,
            _guard_write_did_writable,
            _guard_write_did_data_length,
        ),
        allowed_sessions=None,           # 真实约束 = "非 DEFAULT"，由 guard ③ 表达
        required_security=uds_types.SECURITY_LEVEL1,
        payload_scoped=True,
        note="六级链 13→31→7F→33→22→13；顺序依赖 payload（未知 DID 先于会话）",
    )

    return specs


#: C1 Phase 1 的服务规格模型：``sid -> ServiceSpec``。
#:
#: **Phase 1 不执行本表**（dispatcher 仍走 ``SERVICES``）；它用于
#: 模型校验 / capability matrix / 审计 / Phase 2 的迁移对拍基准。
SERVICE_SPECS: dict[int, service_spec.ServiceSpec[EcuState]] = _build_service_specs()

#: 导入期护栏（fail-fast，与 ``dtc_database`` 的做法一致）：
#: spec 违反结构约束时**模块直接无法导入**，而不是等到运行时才发现。
#: 规则见 ``service_spec.validate_specs``（含 OD-2 的
#: ``payload_scoped=True → guards 非空``）。
service_spec.validate_specs(SERVICE_SPECS)

#: 规格模型必须与静态注册表**覆盖同一组服务**（防止一边增删、另一边漏改）。
if set(SERVICE_SPECS) != set(SERVICES):
    raise ValueError(
        "SERVICE_SPECS 与 SERVICES 的服务集合不一致："
        f"缺 spec={sorted(set(SERVICES) - set(SERVICE_SPECS))}，"
        f"多余 spec={sorted(set(SERVICE_SPECS) - set(SERVICES))}"
    )


#: **未纳入** Phase 1 规格模型的服务 → 原因（capability description，非可执行 spec）。
#:
#: ``0x27 SecurityAccess`` 刻意**不**放进 ``SERVICE_SPECS``：
#:
#: 1. 它**不在静态 ``SERVICES`` 表内**（见上），由 ``ecu.py`` 每请求注入
#:    （``now`` + ``SECURITY_CONFIG``）—— 这是既有的分层边界；
#: 2. 它的 authorization-like 行为是**其自身状态机**（seed/key/锁定/过期），
#:    而**不是**"某服务被授权门控"（设计文档 §2.4 的两个口径）；
#: 3. 若要把它做成可执行 spec（即补上一个 ``handler``），就必须在这里
#:    import / 构造那个注入 handler → **迫使 ``handle_uds_request(..., services=...)``
#:    的对外契约发生变化** —— 那正是设计文档 §18.1 **OD-4** 尚未冻结的接口变更，
#:    也是本 Phase 明确禁止的动作（"不要擅自改接口"）。
#:
#: 因此 Phase 1 对 ``0x27`` 的表达**仅限本说明**：不建 spec、不动注入路径、
#: 不改状态机。是否纳入属 **OD-6**（依赖 OD-4），留待后续阶段决定。
SPECS_EXCLUDED: dict[int, str] = {
    0x27: (
        "SecurityAccess 不在静态 SERVICES 内，由 ecu.py 每请求注入；"
        "其行为属自身状态机而非服务门控；纳入 spec 需先解决 OD-4/OD-6"
        "（services= 契约与注入路径），Phase 1 刻意不改变注入执行路径"
    ),
}

#: 覆盖性护栏（fail-fast）：使"某个服务被静默漏建模"成为一个**可执行的**事实。
if set(SERVICE_SPECS) & set(SPECS_EXCLUDED):
    raise ValueError(
        "同一服务不得既建模又被排除："
        f"{sorted(hex(s) for s in set(SERVICE_SPECS) & set(SPECS_EXCLUDED))}"
    )

if 0x27 not in SPECS_EXCLUDED:
    # 0x27 是**已知的注入型服务**（不在静态表、由入口层注入）。
    # 它必须显式出现在排除清单里，否则就是被静默漏掉了。
    raise ValueError("0x27 SecurityAccess 必须显式出现在 SPECS_EXCLUDED 中")


# ======================================================================
# C1 Phase 2 —— ServiceSpec 执行框架
# ======================================================================
#
# Phase 2 的目标：把 dispatcher 从 ``SERVICES[sid] -> handler`` 切到
# ``resolve spec -> ordered guards -> handler``，但**可观测响应字节不变**。
#
# 迁移按**服务逐个**进行（先 0x22，再 0x2E），因此执行路径的切换必须是
# **per-SID 可开关**的 —— 见 ``_SPEC_EXECUTION``。
#
# 设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md``
# （OD-4 = A′、OD-5、OD-6、§9 dispatcher 架构、§15 rollback、§20.2 实施顺序）。


#: 已切换到 ``ServiceSpec`` 执行路径（即 guards 会被真正求值）的 SID 集合。
#:
#: **源码常量，不是 feature flag**（设计文档 §15.3）：
#: 回滚必须是一次**可审计的代码变更**（留下 git 历史与 review 记录），
#: 而不是一次环境配置变更 —— 后者会让同一 commit 在不同环境行为不同。
#:
#: 迁移进度（设计文档 §11.1 / §12.1，**严格 A → B 顺序**）：
#:
#: - Phase 2 Step 1（框架引入时）：``frozenset()`` —— 不迁移任何服务
#: - **Migration A1（当前）：``{0x22}``** —— 仅 0x22 走 spec 执行路径
#: - Migration B（0x2E）：``{0x22, 0x2E}``
#:
#: 未列入的 SID 走 ``()``，即 ``_guards_for()`` 恒返回空、guards 不执行，
#: dispatcher 行为与其 legacy 路径完全一致。
_SPEC_EXECUTION: frozenset[int] = frozenset({0x22})


def _guards_for(
    spec: service_spec.ServiceSpec[EcuState],
) -> tuple[service_spec.Guard[EcuState], ...]:
    """
    返回**当前应当执行**的 guard 链。

    SID 已在 ``_SPEC_EXECUTION`` 中 → 返回 ``spec.guards``（顺序即语义）；
    否则返回 ``()``（该服务仍走 legacy 逐字路径，guards **不执行**）。

    这使"迁移某个服务"与"引入执行框架"成为两件可分离的事：
    框架先落地并全量回归，再逐 SID 打开。
    """
    return spec.guards if spec.sid in _SPEC_EXECUTION else ()


def resolve_service_spec(
    sid: int,
    services: Mapping[int, Callable[[bytes, EcuState], bytes]] | None,
) -> service_spec.ServiceSpec[EcuState] | None:
    """
    把 ``(sid, services)`` 解析为一个 ``ServiceSpec``；``None`` 表示未注册。

    **纯函数**：不读全局可变状态、无副作用、**不调用任何 guard**。
    解析与执行分离，使本函数可被独立单测。

    契约（设计文档 §6.5，OD-4 = A′）：

    ``services is None``
        直接取 ``SERVICE_SPECS``。

    ``services`` 非空
        override 表**取代而非合并**（既有契约，N6）：
        因此 **SID 的存在性由 override 决定** —— 未列出的 SID → ``None``
        → dispatcher 返回 ``7F <sid> 11``。

        - SID **不在** ``SERVICE_SPECS``（典型：0x27 动态注入、测试自定义 SID）
          → 构造 ``ServiceSpec(guards=())``。
        - SID **在** ``SERVICE_SPECS``（已建模）
          → ``replace(base, handler=override_handler)``：
            **handler 可被覆盖，guards 保留自 ``SERVICE_SPECS``。**

    最后一条即 §6.5.1 的**有意收紧**：``services=`` 覆盖的是**实现**，
    **不是授权** —— 调用方无法通过传入自定义 handler 来绕过该 SID 的 guards。
    （Phase 1 无此语义，因为 Phase 1 根本不执行 guards。）
    """
    if services is None:
        return SERVICE_SPECS.get(sid)

    handler = services.get(sid)

    if handler is None:
        # 覆盖表取代而非合并：未列出的 SID 视为未注册。
        return None

    base = SERVICE_SPECS.get(sid)

    if base is None:
        # 静态表未建模的 SID：无声明式授权，guards 为空。
        return service_spec.ServiceSpec[EcuState](
            sid=sid,
            handler=handler,
            guards=(),
        )

    # 已建模 SID：保留授权链，仅替换 handler（OD-4 A′）。
    return replace(base, handler=handler)


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
    :param services: 可选的服务表覆盖。为 None 时使用静态服务规格表
                     （两者均经 ``resolve_service_spec`` 解析）；
                     入口层借助它注入需要请求时依赖的服务条目（0x27）。
                     覆盖**取代**而非合并；对已建模 SID 仅替换 handler、
                     **不**替换授权 —— 详见 ``resolve_service_spec``。

    Phase 2：本函数经 ``resolve_service_spec()`` 进入 ``ServiceSpec`` 执行框架。
    **执行哪些 guard 由源码常量 ``_SPEC_EXECUTION`` 决定**：已列入的 SID 执行其
    声明式 guard 链，未列入的 SID 跳过 guards。当前 Migration A 已启用 0x22。

    未迁移的 SID 因此仍走其 handler 的既有路径；guard 链与 handler 内检查
    逐字节等价，故已迁移 SID 的可观测响应不变。
    """
    # 空请求必须在查表之前处理：空请求没有 SID 可取，
    # 若先执行 request[0] 会 IndexError。此处固定回显 SID 0x00 + NRC 0x13。
    if not request:
        return uds_types.negative_response(
            0x00, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    service = request[0]

    spec = resolve_service_spec(service, services)

    if spec is None:
        # 未注册的 SID：回显请求 SID + NRC 0x11。
        # 不额外做长度检查；尾随负载一并忽略。
        return uds_types.negative_response(
            service, uds_types.NRC_SERVICE_NOT_SUPPORTED
        )

    # 按序求值授权 guard 链（**顺序即语义**），首个失败即短路。
    # guard 只判定、不产生响应；NRC 由这里统一组装成 7F <sid> <nrc>。
    # 执行哪些 guard 由 ``_guards_for()`` 按 ``_SPEC_EXECUTION`` 白名单决定：
    # 已迁移的 SID 执行其 guard 链，未迁移的 SID 返回空链（跳过本循环）。
    for guard in _guards_for(spec):
        nrc = guard(request, state)
        if nrc is not None:
            return uds_types.negative_response(service, nrc)

    return spec.handler(request, state)


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
