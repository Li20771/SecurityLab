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
from did_database import PROTECTED_DIDS
from uds_server import (
    STATE,
    reset_security_state,
    service_diagnostic_session_control,
    service_ecu_reset,
    service_read_data_by_identifier,
)


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

#: defaultSession 常量（保留供 main() 与外部引用使用）。
#: 会话支持集与 P2/P2* 参数已随 0x10 handler 迁移到 uds_server.py。
DEFAULT_SESSION = 0x01

# 运行时会话状态不再以模块级变量保存。
# 唯一 source of truth 是 uds_server.STATE（EcuState 实例）：
#     STATE.session          当前会话
#     STATE.security_level   当前安全等级
#     STATE.pending_seed     ...
# 这样避免了"模块级变量 + 状态对象"双份状态。


# ======================================================================
# 安全访问（SecurityAccess 0x27）
# ======================================================================

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

#: 安全等级常量（0x27 逻辑与测试断言使用）。
SECURITY_LOCKED = 0x00
SECURITY_LEVEL1 = 0x01


def _now() -> float:
    """
    取当前时间。

    单独抽成函数是为了让测试可以注入时钟
    （unittest.mock.patch("ecu._now", ...)），
    从而在不 sleep 的前提下测试 seed 过期与锁定到期。

    注意：本函数**继续留在 ecu.py**，0x27 逻辑直接从本模块调用它；
    测试的 mock.patch.object(ecu_module, "_now", ...) 因此保持有效。
    """
    return time.monotonic()


def reset_all_security_state() -> None:
    """
    重置全部安全状态（含失败计数与锁定）。

    仅供测试使用，协议处理流程中不会调用。

    "会话相关"部分委托给 uds_server.reset_security_state()，
    保证清理语义只有一处定义。
    """
    reset_security_state(STATE)

    STATE.failed_attempts = 0
    STATE.lockout_until = None
    STATE.seed_counter = 0


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

    if STATE.lockout_until is not None:
        if now < STATE.lockout_until:
            return uds_types.negative_response(
                0x27, uds_types.NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED
            )

        # 锁定到期：清零惩罚状态，允许重新尝试
        STATE.lockout_until = None
        STATE.failed_attempts = 0

    # ---- 5. session / security 条件 ----
    if subfunction == 0x01 and STATE.security_level != SECURITY_LOCKED:
        # 已解锁时再次请求 seed 属于条件不满足
        return uds_types.negative_response(
            0x27, uds_types.NRC_CONDITIONS_NOT_CORRECT
        )

    # ---- RequestSeed ----
    if subfunction == 0x01:
        seed = keygen.generate_seed(STATE.seed_counter, subfunction)
        STATE.seed_counter += 1

        STATE.pending_seed = seed
        STATE.seed_issued_at = now
        STATE.seed_consumed = False

        return uds_types.positive_response(0x27, bytes([0x01]) + seed)

    # ---- SendKey ----
    # ---- 6. sequence ----
    if STATE.pending_seed is None or STATE.seed_consumed:
        # 要么从未请求 seed，要么 seed 已被成功消费（重放）
        return uds_types.negative_response(
            0x27, uds_types.NRC_REQUEST_SEQUENCE_ERROR
        )

    if STATE.seed_issued_at is None or (
        now - STATE.seed_issued_at > SECURITY_CONFIG["seed_expiry_seconds"]
    ):
        # seed 超时作废
        return uds_types.negative_response(
            0x27, uds_types.NRC_REQUEST_SEQUENCE_ERROR
        )

    # ---- 7. key ----
    key = bytes(request[2:])

    if not keygen.verify_key(STATE.pending_seed, key):
        STATE.failed_attempts += 1

        if STATE.failed_attempts >= SECURITY_CONFIG["max_attempts"]:
            # 达到上限：设置锁定，本次返回 0x36
            STATE.lockout_until = now + SECURITY_CONFIG["lockout_seconds"]
            STATE.pending_seed = None
            STATE.seed_issued_at = None
            STATE.seed_consumed = False

            return uds_types.negative_response(
                0x27, uds_types.NRC_EXCEED_NUMBER_OF_ATTEMPTS
            )

        return uds_types.negative_response(0x27, uds_types.NRC_INVALID_KEY)

    # ---- 8. 肯定响应 ----
    STATE.security_level = SECURITY_LEVEL1
    STATE.seed_consumed = True
    STATE.pending_seed = None
    STATE.seed_issued_at = None
    STATE.failed_attempts = 0

    return uds_types.positive_response(0x27, bytes([0x02]))


# ======================================================================
# UDS 服务分发
# ======================================================================

def handle_uds_request(request: bytes) -> bytes:
    """处理一条 UDS 请求，返回响应字节。"""
    if not request:
        return uds_types.negative_response(
            0x00, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    service = request[0]

    # ============================================================
    # DiagnosticSessionControl (0x10)
    # ============================================================
    if service == 0x10:
        return service_diagnostic_session_control(request, STATE)

    # ============================================================
    # ECUReset (0x11)
    # ============================================================
    if service == 0x11:
        return service_ecu_reset(request, STATE)

    # ============================================================
    # ReadDataByIdentifier (0x22)
    # ============================================================
    if service == 0x22:
        return service_read_data_by_identifier(request, STATE)

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
    print(f"Current UDS session: 0x{STATE.session:02X}")
    print(f"Current security level: 0x{STATE.security_level:02X}")
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
