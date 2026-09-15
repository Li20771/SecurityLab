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

模块职责（Phase 3 重构后的边界）：

本模块是 **ECU 入口层（transport + entry + compatibility facade）**，
不承载 UDS 业务逻辑。全部协议实现（状态机、服务 handler、registry、
dispatcher、NRC/response 组装）位于 ``uds_server.py``。

- transport / entry：``INTERFACE`` / ``RX_ID`` / ``TX_ID`` / ``main()``
- runtime 依赖（归本模块持有，由入口层转交协议层）：
  ``_now()`` / ``SECURITY_CONFIG``
- compatibility facade（不承载 UDS business logic，仅供既有调用方/测试）：
  ``handle_uds_request``（entry wrapper）、``STATE``（单例 re-export）、
  ``reset_all_security_state``（测试 helper）、
  ``DEFAULT_SESSION`` / ``SECURITY_LOCKED`` / ``SECURITY_LEVEL1``（uds_types 别名）
- 0x27 依赖注入：``_security_services()``（每请求现场求值 ``_now()``）
"""

import os
import time

import isotp

import uds_server
import uds_types
from did_database import PROTECTED_DIDS
from uds_server import (
    STATE,
    EcuState,
    reset_security_state,
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

#: defaultSession 别名（保持 ecu.DEFAULT_SESSION 可用）。
#: 会话支持集与 P2/P2* 参数已随 0x10 handler 迁移到 uds_server.py。
DEFAULT_SESSION = uds_types.SESSION_DEFAULT

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

#: 安全等级别名（保持 ecu.SECURITY_LOCKED / ecu.SECURITY_LEVEL1 可用）。
#: 规范定义在 uds_types.py，此处不再重复数值。
SECURITY_LOCKED = uds_types.SECURITY_LOCKED
SECURITY_LEVEL1 = uds_types.SECURITY_LEVEL1


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
# UDS 服务入口（0x27 运行时依赖注入边界）
# ======================================================================


def _security_services() -> dict:
    """
    构造需要请求时依赖的服务条目。

    目前只有 0x27 SecurityAccess：它需要时间快照与安全配置。

    约束（P5B/P6/P7 设计要求，请勿"优化"）：

    - ``_now()`` 必须**每次调用现场求值**，不得缓存结果或缓存 callable。
      测试通过 ``mock.patch.object(ecu, "_now", ...)`` 注入时钟；
      只有本函数在每次请求时、于 ``ecu`` 命名空间内按名字解析才能让 patch 生效。
    - ``SECURITY_CONFIG`` 必须**原对象引用**传入，不得 copy 或重建，
      以保持"配置改动即时可见"的语义。
    """
    return {
        0x27: uds_server._make_security_access_handler(
            now=_now(),
            security_config=SECURITY_CONFIG,
        )
    }


def handle_uds_request(request: bytes) -> bytes:  # noqa: F811
    """
    UDS 请求入口（保持既有单参数签名）—— ECU 进程使用的默认入口。

    本函数是 transport 层与协议层之间的**唯一依赖注入边界**：
    它在每次请求时把 0x27 所需的运行时依赖（时间快照 + 安全配置）绑定好，
    与 ``uds_server.SERVICES`` 静态表合并后交给协议层 dispatcher。

    之所以需要这一层：0x27 依赖 ``_now()`` 与 ``SECURITY_CONFIG``，
    而这两者按设计仍归属 ``ecu.py``（且测试通过 patch ``ecu._now`` 注入时钟），
    因此注入必须发生在 ``ecu`` 命名空间内、且每次请求现场求值。
    """
    services = {
        **uds_server.SERVICES,
        **_security_services(),
    }

    return uds_server.handle_uds_request(
        request,
        STATE,
        services=services,
    )


#: 协议层 dispatcher 的显式别名（不含运行时依赖注入）。
#:
#: 与上面的 ``handle_uds_request`` 区分：
#:   - ``handle_uds_request``        —— ECU 默认入口，注入 0x27 依赖
#:   - ``handle_uds_request_protocol`` —— 协议层原始 dispatcher
handle_uds_request_protocol = uds_server.handle_uds_request


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
