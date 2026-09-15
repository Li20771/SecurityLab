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

import uds_types
from did_database import PROTECTED_DIDS
from uds_server import (
    STATE,
    EcuState,
    reset_security_state,
    service_diagnostic_session_control,
    service_ecu_reset,
    service_read_data_by_identifier,
    service_security_access,
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
# UDS 服务分发
# ======================================================================


def _dispatch_security_access(request: bytes, state: EcuState) -> bytes:
    """
    0x27 SecurityAccess 的 dispatch adapter。

    ``service_security_access`` 是纯函数，需要两项运行时依赖：
    时间快照 ``now`` 与安全配置 ``security_config``。本 adapter 是**唯一的
    依赖注入点**，把它们补上，使 registry 中所有条目都统一为
    ``(request, state) -> bytes``。

    约束（P5B/P6 设计要求，请勿"优化"）：

    - ``_now()`` 必须**每次调用现场求值**，不得缓存结果，也不得缓存 callable。
      测试通过 ``mock.patch.object(ecu, "_now", ...)`` 注入时钟，
      只有现场按名字解析才能让 patch 生效。
    - ``SECURITY_CONFIG`` 必须**原对象传入**，不得 copy 或重建，
      以保持"配置改动即时可见"的语义。
    """
    return service_security_access(
        request,
        state,
        now=_now(),
        security_config=SECURITY_CONFIG,
    )


#: UDS 服务注册表：服务标识符 → 处理函数。
#:
#: 所有条目的调用形态统一为 ``handler(request, state) -> bytes``。
#: 0x27 因为需要运行时依赖，经 ``_dispatch_security_access`` 适配后注册。
#:
#: SID 暂时使用字面量；常量统一（SID_*）留待后续独立阶段处理。
SERVICES = {
    0x10: service_diagnostic_session_control,
    0x11: service_ecu_reset,
    0x22: service_read_data_by_identifier,
    0x27: _dispatch_security_access,
}


def handle_uds_request(request: bytes) -> bytes:
    """处理一条 UDS 请求，返回响应字节。"""
    # 空请求必须在查表之前处理：空请求没有 SID 可取，
    # 若先执行 request[0] 会 IndexError。此处固定回显 SID 0x00 + NRC 0x13。
    if not request:
        return uds_types.negative_response(
            0x00, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
        )

    service = request[0]

    handler = SERVICES.get(service)

    if handler is None:
        # 未注册的 SID：回显请求 SID + NRC 0x11。
        # 不额外做长度检查；尾随负载一并忽略。
        return uds_types.negative_response(
            service, uds_types.NRC_SERVICE_NOT_SUPPORTED
        )

    return handler(request, STATE)


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
