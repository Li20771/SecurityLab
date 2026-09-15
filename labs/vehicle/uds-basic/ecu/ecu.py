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
- transport 参数（Phase 4-P2 显式化，默认值保持内核默认行为）：
  ``ISOTP_TX_PAD`` / ``ISOTP_RX_PAD`` / ``ISOTP_FC_STMIN`` /
  ``ISOTP_FC_BLOCKSIZE`` / ``ISOTP_TX_STMIN`` / ``ISOTP_TIMEOUT`` /
  ``_env_int_opt()`` / ``_env_duration_ns()`` / ``_env_float_opt()`` /
  ``_isotp_socket_options()`` / ``apply_isotp_options()``
- runtime 依赖（归本模块持有，由入口层转交协议层）：
  ``_now()`` / ``SECURITY_CONFIG``
- compatibility facade（不承载 UDS business logic，仅供既有调用方/测试）：
  ``handle_uds_request``（entry wrapper）、``STATE``（单例 re-export）、
  ``reset_all_security_state``（测试 helper）、
  ``DEFAULT_SESSION`` / ``SECURITY_LOCKED`` / ``SECURITY_LEVEL1``（uds_types 别名）
- 0x27 依赖注入：``_security_services()``（每请求现场求值 ``_now()``）
"""

import errno
import os
import time
from typing import Optional

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


def _env_int_opt(name: str, minimum: int, maximum: int) -> Optional[int]:
    """
    读取可选的整数型环境变量，并做范围校验。

    未设置（或空字符串）→ ``None``，语义是"保持内核默认行为"。
    因此调用方必须把 ``None`` 与合法值 ``0`` 区别对待。

    非法取值会**立即抛异常**（在模块导入期即启动时失败），
    不会静默回退到默认值——否则实验参数写错时会产生难以察觉的偏差。
    """
    raw = os.environ.get(name)

    if raw is None or raw.strip() == "":
        return None

    text = raw.strip()

    try:
        value = int(text, 0)
    except ValueError:
        raise ValueError(
            f"环境变量 {name}={raw!r} 不是合法整数（示例：0xAA 或 170）"
        ) from None

    if not minimum <= value <= maximum:
        raise ValueError(
            f"环境变量 {name}={value} 超出允许范围 {minimum}–{maximum}"
        )

    return value


def _env_duration_ns(name: str) -> Optional[int]:
    """
    读取可选的时长型环境变量，统一换算为**纳秒**。

    ``UDS_ISOTP_TX_STMIN`` 对应内核 ``CAN_ISOTP_TX_STMIN``，单位是纳秒
    （不是 ISO-TP 流控帧里那个单字节 STmin），因此这里用独立的后缀表。
    支持 ``100us`` / ``2ms`` / ``1s``，不带单位则按纳秒解释。

    未设置 → ``None``；非法取值立即抛异常。
    """
    raw = os.environ.get(name)

    if raw is None or raw.strip() == "":
        return None

    compact = "".join(raw.split()).lower()

    factor = 1

    for suffix, suffix_ns in (
        ("us", 1_000),
        ("ms", 1_000_000),
        ("s", 1_000_000_000),
    ):
        if compact.endswith(suffix):
            factor = suffix_ns
            compact = compact[: -len(suffix)]
            break

    if not compact:
        raise ValueError(f"环境变量 {name}={raw!r} 缺少数字部分")

    try:
        value = int(compact, 0)
    except ValueError:
        raise ValueError(
            f"环境变量 {name}={raw!r} 不是合法时长（示例：500、100us、2ms、1s）"
        ) from None

    if value < 0:
        raise ValueError(f"环境变量 {name}={raw!r} 不能为负数")

    nanos = value * factor

    if nanos > 0xFFFFFFFF:
        raise ValueError(
            f"环境变量 {name}={raw!r} = {nanos} ns 超过 32-bit 上限 "
            f"(0xFFFFFFFF ns ≈ 4.29 s)"
        )

    return nanos


def _env_float_opt(name: str, minimum: float) -> Optional[float]:
    """
    读取可选的浮点型环境变量，并做下界校验。

    未设置（或空字符串）→ ``None``，语义是"保持内核默认行为"
    （对 socket 超时而言即阻塞等待）。
    """
    raw = os.environ.get(name)

    if raw is None or raw.strip() == "":
        return None

    text = raw.strip()

    try:
        value = float(text)
    except ValueError:
        raise ValueError(
            f"环境变量 {name}={raw!r} 不是合法数值（示例：2.0 或 0）"
        ) from None

    if value < minimum:
        raise ValueError(f"环境变量 {name}={value} 不能小于 {minimum}")

    return value


# 默认值即为标准 11-bit 诊断寻址，保持原行为不变。
# 允许用环境变量覆盖，便于多 ECU / 多通道实验。
INTERFACE = os.environ.get("UDS_INTERFACE", "vcan0")
RX_ID = _env_int("UDS_RX_ID", 0x7E0)
TX_ID = _env_int("UDS_TX_ID", 0x7E8)


# ======================================================================
# ISO-TP 传输参数（Phase 4-P2：从"隐式依赖内核默认"变为"显式可配置"）
# ======================================================================
#
# 设计要点（每一条都对应一个坑）：
#
# 1. **默认不变**：四个参数全部未设置时，``main()`` 不会调用
#    set_opts()/set_fc_opts()，内核默认行为与改造前逐字节一致
#    （单帧仍是 ``03 10 03``，不填充；FC 仍是 ``30 00 00``）。
#
# 2. **None ≠ 0**：``None`` 表示"不改动该选项"，``0`` 是合法取值
#    （例如 STmin=0 表示无间隔要求）。因此下面只读取"是否显式设置"。
#
# 3. **两个方向的 STmin 不能合并**（这是最容易搞错的概念）：
#
#      UDS_ISOTP_FC_STMIN   本 ECU 在**自己发出的流控帧**里填的 STmin 字节，
#                           是"要求对方放慢"的限制，ISO-TP 字节编码
#                           （0x00–0x7F = 0–127 ms，0xF1–0xF9 = 100–900 us）。
#
#      UDS_ISOTP_TX_STMIN   内核 CAN_ISOTP_TX_STMIN：强制**本 ECU 自己**
#                           发送连续帧的间隔，会**覆盖**收到的 FC 里的 STmin；
#                           单位是**纳秒**，与上面的字节编码无关。
#
# 4. **必须早于 bind()**：python-isotp 的 set_opts/set_fc_opts 在 bind 之后
#    会直接抛 RuntimeError，所以 apply_isotp_options() 只能在 bind 前调用。

#: 发送帧填充字节（0x00–0xFF）。None = 不填充（内核默认）。
ISOTP_TX_PAD = _env_int_opt("UDS_ISOTP_TX_PAD", 0x00, 0xFF)

#: 接收帧填充字节（0x00–0xFF）。None = 不启用接收填充容忍（内核默认）。
ISOTP_RX_PAD = _env_int_opt("UDS_ISOTP_RX_PAD", 0x00, 0xFF)

#: 本 ECU 流控帧里的 STmin 字节（0x00–0xFF）。None = 内核默认 0x00。
ISOTP_FC_STMIN = _env_int_opt("UDS_ISOTP_FC_STMIN", 0x00, 0xFF)

#: 本 ECU 流控帧里的块大小 BS（0x00–0xFF）。None = 内核默认 0（无限块）。
ISOTP_FC_BLOCKSIZE = _env_int_opt("UDS_ISOTP_FC_BLOCKSIZE", 0x00, 0xFF)

#: 本 ECU 自身连续帧发送间隔，单位**纳秒**。None = 不强制（内核默认）。
ISOTP_TX_STMIN = _env_duration_ns("UDS_ISOTP_TX_STMIN")

#: 接收超时（秒，0 表示非阻塞）。None = 阻塞等待（内核默认）。
#:
#: 注意方向性：这控制的是**本 ECU 的 recv() 是否阻塞**，
#: 与 client 的 ``--timeout``（"等响应等多久"）不是同一个概念。
ISOTP_TIMEOUT = _env_float_opt("UDS_ISOTP_TIMEOUT", 0.0)


def _isotp_socket_options() -> dict:
    """
    把已解析的显式 ISO-TP 参数整理成待下发的选项。

    只包含**显式设置过**的项；全部未设置时返回空 dict，
    从而保证"默认行为不变"。
    """
    return {
        "tx_pad": ISOTP_TX_PAD,
        "rx_pad": ISOTP_RX_PAD,
        "fc_stmin": ISOTP_FC_STMIN,
        "fc_blocksize": ISOTP_FC_BLOCKSIZE,
        "tx_stmin": ISOTP_TX_STMIN,
    }


def apply_isotp_options(sock) -> None:
    """
    在 ``bind()`` **之前**把显式 ISO-TP 参数下发给 socket。

    全部未设置时本函数不做任何系统调用，因此不改变既有默认行为。
    """
    general = {}

    if ISOTP_TX_PAD is not None:
        general["txpad"] = ISOTP_TX_PAD

    if ISOTP_RX_PAD is not None:
        general["rxpad"] = ISOTP_RX_PAD

    if ISOTP_TX_STMIN is not None:
        # 单位纳秒；python-isotp 会自动置 FORCE_TXSTMIN 标志
        general["tx_stmin"] = ISOTP_TX_STMIN

    if general:
        sock.set_opts(**general)

    flow_control = {}

    if ISOTP_FC_BLOCKSIZE is not None:
        flow_control["bs"] = ISOTP_FC_BLOCKSIZE

    if ISOTP_FC_STMIN is not None:
        flow_control["stmin"] = ISOTP_FC_STMIN

    if flow_control:
        sock.set_fc_opts(**flow_control)


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


def _is_recoverable_isotp_error(exc: OSError) -> bool:
    """
    判断一个 ``OSError`` 是否属于**当前 transaction 可丢弃**的
    ISO-TP 传输错误（内核 CAN_ISOTP 的 error contract）。

    已实测确认的两条（Phase 4-P3 / P3R）::

        EILSEQ (84)  接收侧序号不匹配 —— wrong CF sequence number
        ECOMM  (70)  发送侧流控接收超时 —— FC reception timeout

    语义都是"这一次 ISO-TP 传输坏了"，而不是"这个 socket / 接口坏了"。
    因此正确的处置是**丢弃当前传输**并继续服务下一个请求。

    刻意**不**把 ``ETIMEDOUT`` / ``EBADMSG`` / ``EMSGSIZE`` 一并加入：
    内核文档列出它们属于 ISO-TP 传输错误类别，但本项目尚未实际观测到。
    在没有实测证据前扩大捕获范围，会掩盖真正的 socket / 接口故障。
    如果后续实验真的观察到，再按实测语义逐个加入（见 README）。
    """
    return exc.errno in (errno.EILSEQ, errno.ECOMM)


def _isotp_error_detail(exc: OSError) -> str:
    """格式化 transport error 细节（errno + 助记符 + 异常文本）。"""
    name = errno.errorcode.get(exc.errno, "?")

    return f"errno={exc.errno} ({name}) {exc}"


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

    try:
        apply_isotp_options(sock)

        if ISOTP_TIMEOUT is not None:
            sock.settimeout(ISOTP_TIMEOUT)

        sock.bind(INTERFACE, address)
    except Exception:
        # 参数下发失败时不要把 socket 泄漏出去
        sock.close()
        raise

    print("ISO-TP socket bound successfully.")
    print("ISO-TP options (unset = kernel default):")
    print(f"  tx_pad       : {ISOTP_TX_PAD if ISOTP_TX_PAD is not None else 'unset'}")
    print(f"  rx_pad       : {ISOTP_RX_PAD if ISOTP_RX_PAD is not None else 'unset'}")
    print(
        f"  fc_stmin     : "
        f"{f'0x{ISOTP_FC_STMIN:02X}' if ISOTP_FC_STMIN is not None else 'unset'}"
    )
    print(
        f"  fc_blocksize : "
        f"{ISOTP_FC_BLOCKSIZE if ISOTP_FC_BLOCKSIZE is not None else 'unset'}"
    )
    print(
        f"  tx_stmin     : "
        f"{f'{ISOTP_TX_STMIN} ns' if ISOTP_TX_STMIN is not None else 'unset'}"
    )
    print(
        f"  timeout      : "
        f"{f'{ISOTP_TIMEOUT}s' if ISOTP_TIMEOUT is not None else 'unset (blocking)'}"
    )
    print(f"Current UDS session: 0x{STATE.session:02X}")
    print(f"Current security level: 0x{STATE.security_level:02X}")
    print(f"Protected DIDs: {', '.join(f'0x{d:04X}' for d in sorted(PROTECTED_DIDS))}")
    print("Virtual ECU is waiting for UDS requests...")

    try:
        while True:
            try:
                data = sock.recv()
            except TimeoutError:
                # 设置了 UDS_ISOTP_TIMEOUT 后，recv() 超时会**抛异常**
                # （而不是返回 None），必须在这里接住，否则空闲一个周期
                # 就会让整个 ECU 退出。这也是 `data is None` 分支之外
                # 真正需要处理的"没收到请求"路径。
                #
                # 顺序很重要：TimeoutError 是 OSError 的子类，
                # 必须排在 OSError 之前，否则永远不可达。
                continue
            except OSError as exc:
                if _is_recoverable_isotp_error(exc):
                    # 本次传输被内核判定为损坏。实测两条都已走到这里：
                    #   EILSEQ(84) CF 序号不匹配
                    #   ECOMM(70)  发送侧 FC 超时（内核在后续操作上报）
                    # 丢弃当前 transaction 并继续服务，**不**伪造 UDS NRC：
                    # 错误发生在 payload transport 层，不是 service handler 层，
                    # 因此"没有 UDS 响应"才是正确结果。
                    print(
                        f"ISO-TP RX error: {_isotp_error_detail(exc)} "
                        f"— 丢弃当前传输，继续运行"
                    )
                    continue

                # 非预期错误（接口被删、socket 真损坏等）必须暴露出来，
                # 不能被静默吞掉。
                raise

            if data is None:
                continue

            print(f"RX UDS: {data.hex(' ')}")

            response = handle_uds_request(data)

            print(f"TX UDS: {response.hex(' ')}")

            try:
                sock.send(response)
            except OSError as exc:
                if _is_recoverable_isotp_error(exc):
                    # UDS 响应已生成，失败发生在 UDS → ISO-TP 发送阶段，
                    # 因此只记录发送失败，不重新调用 uds_server。
                    #
                    # 说明：实测 ECOMM 是在**后续 recv()** 上浮现的（内核在
                    # 下一次 socket 操作上报 FC 超时），send() 本身未抛出。
                    # 这里保留同等处置，避免内核行为变化时又变成未处理异常。
                    print(
                        f"ISO-TP TX error: {_isotp_error_detail(exc)} "
                        f"— 丢弃本次响应发送，继续运行"
                    )
                    continue

                raise

    except KeyboardInterrupt:
        print("\nStopping virtual ECU...")

    finally:
        sock.close()


if __name__ == "__main__":
    main()
