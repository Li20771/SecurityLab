#!/usr/bin/env python3
"""
UDS Basic —— 虚拟 ECU

在 vcan0 上模拟一个支持以下服务的诊断 ECU：

- 0x10 DiagnosticSessionControl
- 0x11 ECUReset
- 0x22 ReadDataByIdentifier（含受保护 DID 的权限检查）
- 0x27 SecurityAccess（RequestSeed / SendKey）

安全边界：

本程序只应绑定到 vcan0（虚拟 CAN）。不要指向真实 CAN 接口、
真实 ECU 或真实车辆。默认接口为 vcan0，可用环境变量覆盖。
"""

import os
import time

import isotp

import keygen
import uds_types
from did_database import DID_DATABASE, PROTECTED_DIDS


def _env_int(name: str, default: int) -> int:
    """读取整数型环境变量，支持 0x 十六进制写法。"""
    raw = os.environ.get(name)

    if raw is None or raw == "":
        return default

    return int(raw, 0)


# 默认值即为标准 11-bit 诊断寻址，保持原行为不变。
# 允许用环境变量覆盖，便于多 ECU / 多通道实验。
INTERFACE = os.environ.get("UDS_INTERFACE", "vcan0")
RX_ID = _env_int("UDS_RX_ID", 0x7E0)
TX_ID = _env_int("UDS_TX_ID", 0x7E8)


# ======================================================================
# 会话
# ======================================================================

DEFAULT_SESSION = 0x01

SESSION_DEFAULT = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED = 0x03

SUPPORTED_SESSIONS = {
    SESSION_DEFAULT,
    SESSION_PROGRAMMING,
    SESSION_EXTENDED,
}

# 每个会话的 (P2ServerMax, P2*ServerMax)，单位毫秒。
# defaultSession 保持 00 32 / 00 64 不变，以兼容既有行为与测试。
SESSION_TIMING = {
    SESSION_DEFAULT: (0x0032, 0x0064),
    SESSION_PROGRAMMING: (0x00C8, 0x1388),
    SESSION_EXTENDED: (0x0064, 0x03E8),
}

CURRENT_SESSION = DEFAULT_SESSION


# ======================================================================
# 安全访问（SecurityAccess 0x27）
# ======================================================================

SECURITY_LOCKED = 0x00
SECURITY_LEVEL1 = 0x01

CURRENT_SECURITY_LEVEL = SECURITY_LOCKED

#: 已下发但尚未被使用的 seed（bytes 或 None）
PENDING_SEED = None

#: seed 下发时刻（time.monotonic() 基准，float 或 None）
SEED_ISSUED_AT = None

#: seed 是否已被成功消费（用于检测重放）
SEED_CONSUMED = False

#: 连续失败次数
FAILED_ATTEMPTS = 0

#: 锁定解除时刻（float 或 None）
LOCKOUT_UNTIL = None

#: seed 序号，每次 RequestSeed 递增，使 seed 每次不同
SEED_COUNTER = 0

#: 安全访问参数。测试可直接改写本 dict 以构造边界场景。
#:
#: 锁定窗口可通过环境变量 UDS_LOCKOUT_SECONDS 覆盖：
#: 自动化测试需要比整个测试套件运行时间更长的锁定窗口，
#: 否则锁定会在测试过程中自然到期，使断言变得不确定。
SECURITY_CONFIG = {
    "max_attempts": 3,          # 连续失败上限，达到即锁定
    "lockout_seconds": _env_int("UDS_LOCKOUT_SECONDS", 10),   # 锁定时长（秒）
    "seed_expiry_seconds": 5,   # seed 有效期（秒）
}


def _now() -> float:
    """
    取当前时间。

    单独抽成函数是为了让测试可以注入时钟
    （unittest.mock.patch("ecu._now", ...)），
    从而在不 sleep 的前提下测试 seed 过期与锁定到期。
    """
    return time.monotonic()


def reset_security_state() -> None:
    """
    重置"会话相关"的安全状态。

    清除安全等级与 seed 生命周期，但**保留**失败计数与锁定状态。

    保留失败计数与锁定是刻意的设计选择：防止 Tester 通过切换会话或
    复位 ECU 来刷掉暴力破解的惩罚状态。注意这**不是所有真实 ECU
    都必然如此**，部分真实 ECU 会在复位时清空失败计数。
    """
    global CURRENT_SECURITY_LEVEL
    global PENDING_SEED
    global SEED_ISSUED_AT
    global SEED_CONSUMED

    CURRENT_SECURITY_LEVEL = SECURITY_LOCKED
    PENDING_SEED = None
    SEED_ISSUED_AT = None
    SEED_CONSUMED = False


def reset_all_security_state() -> None:
    """
    重置全部安全状态（含失败计数与锁定）。

    仅供测试使用，协议处理流程中不会调用。
    """
    global FAILED_ATTEMPTS
    global LOCKOUT_UNTIL
    global SEED_COUNTER

    reset_security_state()

    FAILED_ATTEMPTS = 0
    LOCKOUT_UNTIL = None
    SEED_COUNTER = 0


# ======================================================================
# 权限模型
# ======================================================================

def required_security_level(did: int) -> int:
    """返回读取指定 DID 所需的安全等级。"""
    if did in PROTECTED_DIDS:
        return SECURITY_LEVEL1

    return SECURITY_LOCKED


# ======================================================================
# 0x27 SecurityAccess
# ======================================================================

def _security_access(request: bytes) -> bytes:
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
    """
    global CURRENT_SECURITY_LEVEL
    global PENDING_SEED
    global SEED_ISSUED_AT
    global SEED_CONSUMED
    global FAILED_ATTEMPTS
    global LOCKOUT_UNTIL
    global SEED_COUNTER

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
    now = _now()

    if LOCKOUT_UNTIL is not None:
        if now < LOCKOUT_UNTIL:
            return uds_types.negative_response(
                0x27, uds_types.NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED
            )

        # 锁定到期：清零惩罚状态，允许重新尝试
        LOCKOUT_UNTIL = None
        FAILED_ATTEMPTS = 0

    # ---- 5. session / security 条件 ----
    if subfunction == 0x01 and CURRENT_SECURITY_LEVEL != SECURITY_LOCKED:
        # 已解锁时再次请求 seed 属于条件不满足
        return uds_types.negative_response(
            0x27, uds_types.NRC_CONDITIONS_NOT_CORRECT
        )

    # ---- RequestSeed ----
    if subfunction == 0x01:
        seed = keygen.generate_seed(SEED_COUNTER, subfunction)
        SEED_COUNTER += 1

        PENDING_SEED = seed
        SEED_ISSUED_AT = now
        SEED_CONSUMED = False

        return uds_types.positive_response(0x27, bytes([0x01]) + seed)

    # ---- SendKey ----
    # ---- 6. sequence ----
    if PENDING_SEED is None or SEED_CONSUMED:
        # 要么从未请求 seed，要么 seed 已被成功消费（重放）
        return uds_types.negative_response(
            0x27, uds_types.NRC_REQUEST_SEQUENCE_ERROR
        )

    if SEED_ISSUED_AT is None or (
        now - SEED_ISSUED_AT > SECURITY_CONFIG["seed_expiry_seconds"]
    ):
        # seed 超时作废
        return uds_types.negative_response(
            0x27, uds_types.NRC_REQUEST_SEQUENCE_ERROR
        )

    # ---- 7. key ----
    key = bytes(request[2:])

    if not keygen.verify_key(PENDING_SEED, key):
        FAILED_ATTEMPTS += 1

        if FAILED_ATTEMPTS >= SECURITY_CONFIG["max_attempts"]:
            # 达到上限：设置锁定，本次返回 0x36
            LOCKOUT_UNTIL = now + SECURITY_CONFIG["lockout_seconds"]
            PENDING_SEED = None
            SEED_ISSUED_AT = None
            SEED_CONSUMED = False

            return uds_types.negative_response(
                0x27, uds_types.NRC_EXCEED_NUMBER_OF_ATTEMPTS
            )

        return uds_types.negative_response(0x27, uds_types.NRC_INVALID_KEY)

    # ---- 8. 肯定响应 ----
    CURRENT_SECURITY_LEVEL = SECURITY_LEVEL1
    SEED_CONSUMED = True
    PENDING_SEED = None
    SEED_ISSUED_AT = None
    FAILED_ATTEMPTS = 0

    return uds_types.positive_response(0x27, bytes([0x02]))


# ======================================================================
# UDS 服务分发
# ======================================================================

def handle_uds_request(request: bytes) -> bytes:
    """处理一条 UDS 请求，返回响应字节。"""
    global CURRENT_SESSION

    if not request:
        return uds_types.negative_response(
            0x00, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    service = request[0]

    # ============================================================
    # DiagnosticSessionControl (0x10)
    # ============================================================
    if service == 0x10:
        if len(request) != 2:
            return uds_types.negative_response(
                0x10, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        session_type = request[1]

        if session_type not in SUPPORTED_SESSIONS:
            return uds_types.negative_response(
                0x10, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
            )

        CURRENT_SESSION = session_type

        # 会话切换后必须重新解锁：
        # 否则 Tester 可在 extended session 解锁后切回 default session
        # 而保持解锁状态，形成真实的权限绕过漏洞。
        reset_security_state()

        p2_server_max, p2_star_server_max = SESSION_TIMING[session_type]

        payload = bytes([
            session_type,
            (p2_server_max >> 8) & 0xFF, p2_server_max & 0xFF,
            (p2_star_server_max >> 8) & 0xFF, p2_star_server_max & 0xFF,
        ])

        return uds_types.positive_response(0x10, payload)

    # ============================================================
    # ECUReset (0x11)
    # ============================================================
    if service == 0x11:
        if len(request) != 2:
            return uds_types.negative_response(
                0x11, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
            )

        reset_type = request[1]

        # hardReset
        if reset_type == 0x01:
            CURRENT_SESSION = DEFAULT_SESSION
            reset_security_state()

            return uds_types.positive_response(0x11, bytes([reset_type]))

        # unsupported reset type
        return uds_types.negative_response(
            0x11, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    # ============================================================
    # ReadDataByIdentifier (0x22)
    # ============================================================
    if service == 0x22:
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

            if CURRENT_SECURITY_LEVEL < required_security_level(did):
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

    # ============================================================
    # SecurityAccess (0x27)
    # ============================================================
    if service == 0x27:
        return _security_access(request)

    # ============================================================
    # Service not supported
    # ============================================================
    return uds_types.negative_response(service, uds_types.NRC_SERVICE_NOT_SUPPORTED)


def main() -> None:
    print(f"ISO-TP version: {isotp.__version__}")
    print(f"CAN interface: {INTERFACE}")
    print(f"RX ID: 0x{RX_ID:03X}")
    print(f"TX ID: 0x{TX_ID:03X}")

    address = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        rxid=RX_ID,
        txid=TX_ID,
    )

    sock = isotp.socket()
    sock.bind(INTERFACE, address)

    print("ISO-TP socket bound successfully.")
    print(f"Current UDS session: 0x{CURRENT_SESSION:02X}")
    print(f"Current security level: 0x{CURRENT_SECURITY_LEVEL:02X}")
    print(f"Protected DIDs: {', '.join(f'0x{d:04X}' for d in sorted(PROTECTED_DIDS))}")
    print("Virtual ECU is waiting for UDS requests...")

    try:
        while True:
            data = sock.recv()

            if data is None:
                continue

            print(f"RX UDS: {data.hex(' ')}")

            response = handle_uds_request(data)

            print(f"TX UDS: {response.hex(' ')}")

            sock.send(response)

    except KeyboardInterrupt:
        print("\nStopping virtual ECU...")

    finally:
        sock.close()


if __name__ == "__main__":
    main()
