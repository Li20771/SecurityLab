#!/usr/bin/env python3
"""
UDS Basic —— Gateway Runtime v1（SocketCAN / ISO-TP transport adapter 层）

本模块把已有的**纯协议层路由核心**接到真实 vcan0 上：

    vcan0 ──ISO-TP 重组──► GatewayRuntime ──► DiagnosticGateway.route()
                                                        │
                            ◄──ISO-TP 分帧── [(response_id, payload)]

架构边界（OQ7 的硬约束，请勿把两层重新揉成一个模块）：

    ecu/gateway.py          纯协议层：CAN ID + UDS payload → 路由决策。
                            **不** import isotp / socket，不启动进程，因此可以
                            在没有 vcan0 的机器上完整离线单测
                            （tests/test_gateway_unit.py，13 用例）。
    ecu/gateway_runtime.py  实时 transport adapter（本模块）：socket 生命周期、
                            select 循环、信号处理、ISO-TP 错误遏制。

本模块**不重新实现任何寻址规则** —— 物理单播、0x7DF 广播不收集响应、未知
CAN ID 丢弃、空响应不发帧，全部来自 ``DiagnosticGateway.route()``。
Runtime 只负责"搬运 + 生命周期 + 错误遏制"。

传输实现的选择（实测依据，不是推测）：

每个请求 ID 一个 ``isotp.socket()``，与 ``ecu.py`` 使用的是**同一种** ISO-TP
用法（``isotp.Address(Normal_11bits, rxid=..., txid=...)`` + ``bind(interface)``），
OQ7 没有引入第二种 ISO-TP 用法。之所以选"一地址一 socket"而不是"单 socket
收所有帧"，是因为内核 CAN_ISOTP 的接收过滤本来就按 rxid 进行 —— 实测：

    rxid=0x7E0 只收到 0x7E0 的帧；rxid=0x7E1 只收到 0x7E1 的帧；
    rxid=0x7DF 只收到 0x7DF 的帧；未绑定的 0x7E5 谁也收不到。

于是"按 CAN ID 分发"由内核完成，用户态只需要 ``select()`` 出哪个 socket 可读。
ISO-TP 的重组 / 分帧（单帧、首帧、连续帧、流控帧）全部由内核处理，本模块
既不做分帧也不解析 PCI。

进程模型（v1 刻意保持简单）：三个 ``VirtualEcu`` 实例化在**同一个进程**内，
但各自持有独立的 ``EcuState``。不引入多进程 / IPC / supervisor / Docker ——
OQ7 的价值是"既有协议模型 → 真实 vCAN runtime"，不是重做 ECU 仿真框架。

用法：

    python3 ecu/gateway_runtime.py --interface vcan0

模块入口说明：``ecu/`` 目录**没有** ``__init__.py``（它不是 package，而是被
各测试用 ``sys.path.insert`` 直接加入搜索路径的脚本目录），因此入口是
"直接执行本文件"，而不是 ``python -m ecu.gateway_runtime``。这与 ``ecu.py``
的既有启动方式（``subprocess.Popen([sys.executable, "-u", "ecu/ecu.py"])``）
保持一致。

安全边界：只使用 vcan0 + 虚拟 ECU，不接触真实 CAN / ECU / 车辆。
"""

import argparse
import select
import signal
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from types import FrameType
from typing import Protocol

import isotp
from ecu import _is_recoverable_isotp_error, _isotp_error_detail
from gateway import (
    FUNCTIONAL_ADDRESS,
    DiagnosticGateway,
    RouteResult,
    build_default_gateway,
)

#: 默认 CAN 接口：**虚拟** CAN。刻意不是 can0 —— 网关守护进程绝不应把
#: 默认值指向真实总线。
DEFAULT_INTERFACE = "vcan0"

#: 单次总线等待上限（秒），同时用作 select() 超时与 socket 超时。
#:
#: 这个值同时决定两件事，因此既不能取 0，也不宜过大：
#:
#:   1. **退出延迟**：serve_forever() 每轮最多阻塞这么久，因此收到
#:      SIGTERM/SIGINT 后最迟下一轮就退出 —— "必须 kill -9 才能结束"
#:      正是本层要排除的行为。
#:   2. **防止挂死**：给 socket 设超时后，即使 select() 误报可读，
#:      recv() 也只会抛 TimeoutError 而不会永久阻塞。
#:
#: 1.0s 与内核 ISO-TP 默认 FC 超时同量级。
DEFAULT_WAIT_TIMEOUT = 1.0

#: 功能寻址 socket 的 txid 占位值。
#:
#: 内核 ISO-TP 在 bind 时要求 rxid != txid，而按本项目定义 0x7DF 是
#: "广播且不回帧"（``DiagnosticGateway.route()`` 对功能寻址恒定返回 ``[]``），
#: 因此这个 txid **永远不会被使用**。取值刻意避开网段内任何响应 ID
#: （0x7E8/0x7E9/0x7EA），以免被误读成某个真实应答地址。
FUNCTIONAL_PLACEHOLDER_TX_ID = 0x7FF


def _close_quietly(sock: object) -> None:
    """关闭 socket，且不让关闭失败掩盖真正的原始异常。"""
    try:
        sock.close()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 关闭失败不应掩盖原始错误
        pass


class Transport(Protocol):
    """
    网关运行时需要的**最小**传输接口。

    存在的唯一理由是让派发 / 生命周期 / 错误遏制可以在没有 vcan0 的机器上
    离线单测（与 ``VirtualEcu.handler`` 属于同一类测试接缝）。真实实现只有
    :class:`IsotpTransport` 一个，测试注入一个小 fake，没有插件注册机制。
    """

    def open(self) -> None: ...

    def close(self) -> None: ...

    def receive(self, timeout: float) -> tuple[int, bytes] | None: ...

    def send(self, can_id: int, payload: bytes) -> None: ...


class IsotpTransport:
    """
    真实 SocketCAN / ISO-TP transport：每个请求 ID 一个 socket。

    传输参数刻意保持内核默认（不启用 tx_pad / rx_pad / STmin / BS），与
    ``ecu.py`` 的默认行为一致 —— ``ecu.py`` 也只在对应环境变量被显式设置时
    才下发 ISO-TP 选项。
    """

    def __init__(
        self,
        interface: str,
        addresses: Iterable[tuple[int, int]],
        *,
        functional_address: int = FUNCTIONAL_ADDRESS,
        wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    ) -> None:
        """
        :param interface: CAN 接口名（默认使用方传入 ``vcan0``）。
        :param addresses: 物理寻址对 ``(request_id, response_id)`` 组成的可迭代对象。
        :param functional_address: 功能寻址（广播）请求 ID。
        :param wait_timeout: socket 超时（秒），见 ``DEFAULT_WAIT_TIMEOUT``。
        """
        self.interface = interface
        self.functional_address = functional_address
        self.wait_timeout = wait_timeout

        self._addresses: list[tuple[int, int]] = list(addresses)

        if not self._addresses:
            raise ValueError("transport 至少需要一个物理寻址对")

        # 计划绑定的全部 (request_id, txid)：物理寻址对 + 功能寻址占位。
        # 与 DiagnosticGateway.__init__ 一样，校验在**构造期**完成（fail fast），
        # 不拖到 open() 才暴露装配错误。
        self._bindings: list[tuple[int, int]] = [
            *self._addresses,
            (functional_address, FUNCTIONAL_PLACEHOLDER_TX_ID),
        ]

        tx_ids = [tx for _, tx in self._bindings]
        if len(set(tx_ids)) != len(tx_ids):
            raise ValueError(
                "response_id 重复（功能寻址的 txid 占位值 "
                f"0x{FUNCTIONAL_PLACEHOLDER_TX_ID:03X} 也计入），"
                "发送映射会指向错误的 socket"
            )

        self._sockets: list[isotp.socket] = []
        self._request_id_by_socket: dict[isotp.socket, int] = {}
        self._socket_by_response_id: dict[int, isotp.socket] = {}
        self._opened = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def open(self) -> None:
        """
        绑定全部 socket（物理寻址 + 功能寻址）。

        绑定中途失败（接口不存在 / 权限不足 / ID 冲突）时，已经建立的 socket
        会被全部关闭后再抛出，不留下泄漏的 fd。
        """
        if self._opened:
            raise RuntimeError("IsotpTransport 已经 open()")

        bindings = self._bindings
        created: list[isotp.socket] = []

        try:
            for request_id, response_id in bindings:
                created.append(self._bind_one(request_id, response_id))
        except Exception:
            for sock in created:
                _close_quietly(sock)
            raise

        self._sockets = created
        self._request_id_by_socket = {
            sock: request_id
            for sock, (request_id, _) in zip(created, bindings, strict=True)
        }
        # 功能寻址 socket 不进入发送映射：它没有应答地址，也永远不发送。
        self._socket_by_response_id = {
            response_id: sock
            for sock, (request_id, response_id) in zip(created, bindings, strict=True)
            if request_id != self.functional_address
        }
        self._opened = True

    def close(self) -> None:
        """关闭全部 socket。**幂等**：重复调用是安全的。"""
        for sock in self._sockets:
            _close_quietly(sock)

        self._sockets = []
        self._request_id_by_socket = {}
        self._socket_by_response_id = {}
        self._opened = False

    # ------------------------------------------------------------------
    # 收发
    # ------------------------------------------------------------------

    def receive(self, timeout: float) -> tuple[int, bytes] | None:
        """
        等待并取出一条**已重组**的请求。

        :returns: ``(request_id, payload)``；超时或暂无完整 PDU 时返回 ``None``。
        :raises OSError: 内核 ISO-TP 报出的传输错误（是否可恢复由调用方判定）。
        """
        if not self._opened:
            raise RuntimeError("IsotpTransport 尚未 open()")

        readable, _, _ = select.select(self._sockets, [], [], timeout)

        for sock in readable:
            request_id = self._request_id_by_socket[sock]

            try:
                payload: bytes | None = sock.recv()
            except TimeoutError:
                # 罕见竞态：select 报可读但 PDU 尚未就绪。这不是传输错误，
                # 换下一个可读 socket（或留到下一轮）即可。
                continue

            if payload is None:
                # 防御性分支：本版本 python-isotp 的 recv() 不返回 None，
                # 但其它实现/版本可能用它表示"暂无数据"（ecu.py 亦保留此判断）。
                continue

            return request_id, payload

        return None

    def send(self, can_id: int, payload: bytes) -> None:
        """
        在 ``can_id`` 上发送一条响应。

        :raises KeyError: ``can_id`` 不属于本网段的任何应答地址。
            路由层不会产生这种 ID（它只回自己有应答地址的 ECU），因此这是
            **装配错误**而不是"异常请求"：按项目既有风格暴露出来，不静默丢弃。
        """
        if not self._opened:
            raise RuntimeError("IsotpTransport 尚未 open()")

        sock = self._socket_by_response_id.get(can_id)

        if sock is None:
            raise KeyError(
                f"没有可在 0x{can_id:03X} 上发送的 socket（不在本网段应答地址内）"
            )

        sock.send(payload)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _bind_one(self, request_id: int, response_id: int) -> isotp.socket:
        """绑定单个 ISO-TP socket；失败时自行清理。"""
        sock = isotp.socket()

        try:
            sock.bind(
                self.interface,
                isotp.Address(
                    isotp.AddressingMode.Normal_11bits,
                    rxid=request_id,
                    txid=response_id,
                ),
            )
            # 超时是**守护进程存活**的必要条件，不是性能调优：没有超时的话，
            # 一旦 select() 误报可读，recv() 会永久阻塞，此时进程收到 SIGTERM
            # 只会执行信号处理器并重启该系统调用 —— 结果就是"只能 kill -9"。
            sock.settimeout(self.wait_timeout)
        except Exception:
            _close_quietly(sock)
            raise

        return sock


class GatewayRuntime:
    """
    网关守护进程：把 transport 上收到的请求交给协议层路由，并把响应发回总线。

    本类只做搬运 + 生命周期 + 错误遏制，**不含任何寻址判断**；因此它的
    行为完全由 ``DiagnosticGateway`` 决定，而后者可以脱离 SocketCAN 单测。

    错误遏制策略（沿用 ``ecu.py``，不另立一套）：

    - 可恢复的 ISO-TP 传输错误（``EILSEQ`` / ``ECOMM`` / ``EMSGSIZE``）
      → 丢弃当前传输，守护进程继续运行；
    - 其它 ``OSError``（接口被删 / socket 真损坏）→ 向上抛出，**不静默吞掉**；
    - 超时（空闲，或本层 socket 超时）→ 不作为错误。
    """

    def __init__(
        self,
        gateway: DiagnosticGateway,
        transport: Transport,
        *,
        wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
        log: Callable[[str], None] = print,
    ) -> None:
        self.gateway = gateway
        self.wait_timeout = wait_timeout
        self._transport = transport
        self._log = log
        self._stopping = threading.Event()
        self._opened = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def open(self) -> None:
        """打开 transport（绑定 socket）。"""
        if self._opened:
            raise RuntimeError("GatewayRuntime 已经 open()")

        self._transport.open()
        self._opened = True

    def close(self) -> None:
        """关闭 transport。**幂等**：无论正常退出还是异常退出都可安全调用。"""
        self._transport.close()
        self._opened = False

    def stop(self) -> None:
        """
        请求退出。

        刻意只置位一个 Event、不做任何 I/O，因此可以从信号处理器里安全调用。
        ``serve_forever()`` 最迟再等一个 ``wait_timeout`` 就会返回。
        """
        self._stopping.set()

    def install_signal_handlers(self) -> None:
        """
        接管 SIGINT / SIGTERM，使 Ctrl-C 与 kill 都走**优雅退出**路径。

        处理器只调用 ``stop()``；真正的 socket 清理由 ``serve_forever()``
        返回之后、调用方 ``finally`` 里的 ``close()`` 完成 —— 这样"正常退出"
        与"异常退出"共用同一条清理路径。

        说明：CPython 的信号处理器是在主线程的字节码之间执行的，并不是原生
        异步信号上下文，因此这里打印一行日志是安全的。
        """
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._on_signal)

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        self._log(f"收到信号 {signal.Signals(signum).name} — 准备退出")
        self.stop()

    # ------------------------------------------------------------------
    # 服务循环
    # ------------------------------------------------------------------

    def serve_forever(self) -> None:
        """
        循环服务直到 ``stop()`` 被调用（信号处理器会调用它）。

        本方法**不**关闭 transport：关闭由调用方的 ``finally`` 负责，
        以保证异常路径同样会清理。
        """
        while not self._stopping.is_set():
            self.serve_once(self.wait_timeout)

    def serve_once(self, timeout: float | None = None) -> bool:
        """
        处理至多一条进入网关的请求（或等待至 ``timeout`` 到期）。

        :returns: 是否真的处理了一条请求（``False`` = 本轮空闲，或本次传输
            因可恢复错误被丢弃）。
        """
        if timeout is None:
            timeout = self.wait_timeout

        try:
            request = self._transport.receive(timeout)
        except TimeoutError:
            # 空闲：没收到请求，不是错误。
            # 顺序重要：TimeoutError 是 OSError 的子类，必须排在前面。
            return False
        except OSError as exc:
            if _is_recoverable_isotp_error(exc):
                self._log(
                    f"ISO-TP RX error: {_isotp_error_detail(exc)} "
                    f"— 丢弃当前传输，继续运行"
                )
                return False

            raise

        if request is None:
            return False

        can_id, payload = request
        self.handle_request(can_id, payload)

        return True

    def handle_request(self, can_id: int, payload: bytes) -> list[RouteResult]:
        """
        处理一条已重组的请求：交给协议层路由，并把响应发回总线。

        :returns: 协议层的路由结果（便于测试断言"决定了什么"）。
        """
        self._log(f"RX 0x{can_id:03X}: {payload.hex(' ')}")

        results = self.gateway.route(can_id, payload)

        if not results:
            # 三种"不回帧"情形在这里合流，**都不是错误**：
            #   1. 未知物理 ID —— 本网关不认识的地址，留给链路上其它设备；
            #   2. 功能寻址 0x7DF —— 刻意不收集响应（多 ECU 同时驱动总线会仲裁冲突）；
            #   3. 服务抑制正响应（如 3E 80）—— 协议层返回 b""。
            return []

        for response_id, response_payload in results:
            self._send(response_id, response_payload)

        return results

    def _send(self, response_id: int, payload: bytes) -> None:
        """发送一条响应，按 ``ecu.py`` 的策略遏制传输错误。"""
        try:
            self._transport.send(response_id, payload)
        except TimeoutError as exc:
            # 本层给 socket 设了超时（ecu.py 的默认配置没有），所以发送侧
            # 超时需要单独接住：含义是"本次响应没能发出去"，而不是"socket
            # 坏了" —— 单次失败不应终止整个守护进程。
            self._log(f"ISO-TP TX timeout: {exc} — 丢弃本次响应发送，继续运行")
            return
        except OSError as exc:
            if _is_recoverable_isotp_error(exc):
                self._log(
                    f"ISO-TP TX error: {_isotp_error_detail(exc)} "
                    f"— 丢弃本次响应发送，继续运行"
                )
                return

            raise

        self._log(f"TX 0x{response_id:03X}: {payload.hex(' ')}")


def build_runtime(
    interface: str = DEFAULT_INTERFACE,
    *,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    log: Callable[[str], None] = print,
) -> GatewayRuntime:
    """
    构造默认三 ECU 网段的实时网关（transport 尚未 ``open()``）。

    transport 的地址表直接取自协议的网关对象，因此**发送映射与路由表
    必然一致** —— 不会出现"路由到一个没有 socket 的应答地址"。
    """
    gateway = build_default_gateway()

    transport = IsotpTransport(
        interface,
        [(ecu.request_id, ecu.response_id) for ecu in gateway.ecus],
        functional_address=gateway.functional_address,
        wait_timeout=wait_timeout,
    )

    return GatewayRuntime(
        gateway,
        transport,
        wait_timeout=wait_timeout,
        log=log,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        prog="gateway_runtime.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "UDS Basic Gateway Runtime v1 —— 在 SocketCAN 上按 CAN ID 把 UDS 请求\n"
            "路由到虚拟 ECU_A/B/C 的网关守护进程。"
        ),
        epilog=(
            "网段拓扑：\n"
            "  0x7E0 -> ECU_A -> 0x7E8\n"
            "  0x7E1 -> ECU_B -> 0x7E9\n"
            "  0x7E2 -> ECU_C -> 0x7EA\n"
            "  0x7DF -> 功能寻址广播到 A/B/C（不回帧）\n"
            "\n"
            "安全边界：只用于 vcan0 虚拟 CAN 实验，不要指向真实 CAN 总线、\n"
            "真实 ECU 或真实车辆。"
        ),
    )
    parser.add_argument(
        "--interface",
        default=DEFAULT_INTERFACE,
        help=f"CAN 接口名（默认 {DEFAULT_INTERFACE}；虚拟总线）",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT,
        help=(
            "单次总线等待上限（秒），同时用作 socket 超时；"
            f"决定退出响应延迟（默认 {DEFAULT_WAIT_TIMEOUT}）"
        ),
    )

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """
    守护进程入口。

    :returns: 进程退出码（0 = 正常退出，2 = 启动失败，1 = 传输层致命错误）。
    """
    args = parse_args(argv)

    if args.wait_timeout <= 0:
        print("--wait-timeout 必须为正数", file=sys.stderr)
        return 2

    if not str(args.interface).startswith("vcan"):
        # 安全边界：本实验框架只针对虚拟总线。接口允许被显式覆盖（实验需要），
        # 但"我正在指向一条非虚拟接口"必须足够显眼。
        print(
            f"[WARN] 接口 {args.interface!r} 不是 vcan* 虚拟接口 —— 请确认你没有"
            f"连接真实 CAN 总线 / 真实 ECU / 真实车辆。"
            f"本框架默认且仅验证 {DEFAULT_INTERFACE}。",
            file=sys.stderr,
        )

    runtime = build_runtime(args.interface, wait_timeout=args.wait_timeout)
    runtime.install_signal_handlers()

    try:
        runtime.open()
    except OSError as exc:
        print(
            f"启动失败：无法在 {args.interface!r} 上绑定 ISO-TP socket —— {exc}",
            file=sys.stderr,
        )
        return 2

    print(f"Gateway Runtime v1 已启动（interface={args.interface}）")
    for ecu in runtime.gateway.ecus:
        print(
            f"  {ecu.name}: request 0x{ecu.request_id:03X} "
            f"/ response 0x{ecu.response_id:03X}"
        )
    print(f"  功能寻址: 0x{runtime.gateway.functional_address:03X}（广播，不回帧）")
    print("等待诊断请求 ...")

    try:
        runtime.serve_forever()
    except OSError as exc:
        print(f"传输层致命错误，网关退出：{exc}", file=sys.stderr)
        return 1
    finally:
        runtime.close()

    print("Gateway Runtime 已停止，socket 已清理。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
