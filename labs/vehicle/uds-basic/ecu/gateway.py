#!/usr/bin/env python3
"""
UDS Basic —— 多 ECU 诊断网关（协议层路由分发器）。

本模块是**纯协议层**：不绑定任何 CAN socket、不启动进程，只做
"收到一个 CAN 标识符 + UDS payload → 应该由哪个 ECU 处理、回应到哪个
标识符" 的确定性路由。因此可以在没有 vcan0 / SocketCAN 的机器上做
完整的离线单元测试。

真实车联网诊断拓扑::

    Tester
      │  物理寻址（单播）：0x7E0 → ECU_A，0x7E1 → ECU_B，0x7E2 → ECU_C
      │  功能寻址（广播）：0x7DF → 网段内所有 ECU
      ▼
   Gateway（按 CAN ID 分发）
      ├── ECU_A  request 0x7E0 / response 0x7E8
      ├── ECU_B  request 0x7E1 / response 0x7E9
      └── ECU_C  request 0x7E2 / response 0x7EA

语义约定（与 ISO 14229 / ISO 15765-2 的常见工程实践一致）：

- **物理寻址（physical）**：请求 ID 精确匹配某个 ECU 的 request_id，
  网关把 payload 交给该 ECU 的 UDS handler，并把响应以该 ECU 的
  response_id 单播回 Tester。若服务返回空字节（例如 0x3E 置了
  suppressPosRspMsgIndicationBit），则不产生任何回应（返回空列表）。
- **功能寻址（functional，0x7DF）**：payload **分发给网段内所有 ECU**
  （典型用途：广播 DiagnosticSessionControl、TesterPresent、ECUReset），
  但网关**不收集、不返回任何响应**——多个 ECU 若同时回应会在总线上
  仲裁冲突，真实网络通常规定功能寻址不期待正响应。各 ECU 的状态
  （会话 / 安全等级 / DID 覆盖）仍会被各自的 handler 正常改变。

每个 :class:`VirtualEcu` 持有**独立**的 :class:`EcuState`，因此 ECU 之间
的会话、安全访问状态、0x2E 写入值互不影响（这是车载网段隔离的基础）。

安全边界：本模块只用于本地虚拟实验，不接触真实 CAN / ECU / 车辆。
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import uds_server
from uds_server import EcuState

#: ISO 15765-2 功能寻址（广播）请求标识符。
FUNCTIONAL_ADDRESS = 0x7DF

#: 默认实验网段的物理寻址 / 响应标识符（11-bit 标准帧，0x700 段）。
DEFAULT_ECUS = (
    # name   request_id  response_id
    ("ECU_A", 0x7E0, 0x7E8),
    ("ECU_B", 0x7E1, 0x7E9),
    ("ECU_C", 0x7E2, 0x7EA),
)

#: 一次路由的结果：(回应 CAN ID, 回应 UDS payload)；可能为空。
RouteResult = tuple[int, bytes]

#: UDS handler 签名：吃 payload，吐 UDS 响应（b"" 表示抑制响应）。
UdsHandler = Callable[[bytes], bytes]


def _default_handler_for(state: EcuState) -> UdsHandler:
    """
    为一个 ECU 状态绑定协议层 dispatcher（静态 SERVICES 表）。

    静态表包含 0x10/0x11/0x22/0x2E/0x3E；0x27 SecurityAccess 需要运行时
    时间/配置注入，网关的默认工厂不包含它（网关层聚焦路由，安全解锁的
    端到端行为由 ecu.py 入口层与既有 e2e 测试覆盖）。
    """

    def handle(payload: bytes) -> bytes:
        return uds_server.handle_uds_request(payload, state)

    return handle


@dataclass
class VirtualEcu:
    """网段上的一个逻辑 ECU：寻址参数 + 独立状态 + UDS handler。"""

    name: str
    request_id: int
    response_id: int
    state: EcuState = field(default_factory=EcuState)
    handler: UdsHandler | None = None

    def __post_init__(self) -> None:
        if self.handler is None:
            self.handler = _default_handler_for(self.state)

    def process(self, payload: bytes) -> bytes:
        """把一条 UDS payload 交给本 ECU，返回其 UDS 响应（可能为 b""）。"""
        handler = self.handler
        # __post_init__ 已保证未显式注入时绑定默认 handler
        assert handler is not None
        return handler(payload)


class DiagnosticGateway:
    """
    诊断网关：按 CAN 标识符把 UDS 请求分发到网段内的 ECU。

    用法（离线）::

        gw = build_default_gateway()
        results = gw.route(0x7E0, bytes.fromhex("22 F1 90"))
        # [(0x7E8, b"\\x62\\xF1\\x90...")]

        gw.route(0x7DF, bytes.fromhex("10 03"))   # 功能寻址广播
        # []  —— 不收集响应，但三个 ECU 的会话都已切到 extended
    """

    #: 功能寻址（广播）请求标识符；子类/实例可覆盖。
    functional_address: int = FUNCTIONAL_ADDRESS

    def __init__(
        self,
        ecus: list[VirtualEcu],
        functional_address: int = FUNCTIONAL_ADDRESS,
    ) -> None:
        if not ecus:
            raise ValueError("网关至少需要一个 ECU")

        self.functional_address = functional_address
        self.ecus: list[VirtualEcu] = list(ecus)

        # request_id → ECU，便于 O(1) 物理寻址
        self._by_request: dict[int, VirtualEcu] = {}
        for ecu in self.ecus:
            if ecu.request_id in self._by_request:
                raise ValueError(f"request_id 0x{ecu.request_id:X} 重复")
            self._by_request[ecu.request_id] = ecu

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def ecu_by_name(self, name: str) -> VirtualEcu:
        for ecu in self.ecus:
            if ecu.name == name:
                return ecu
        raise KeyError(f"网段上没有名为 {name!r} 的 ECU")

    def ecu_by_request_id(self, request_id: int) -> VirtualEcu | None:
        return self._by_request.get(request_id)

    # ------------------------------------------------------------------
    # 路由核心
    # ------------------------------------------------------------------

    def route(self, can_id: int, payload: bytes) -> list[RouteResult]:
        """
        路由一条进入网关的 CAN 报文。

        :param can_id: 报文 CAN 标识符（物理请求 ID 或功能地址 0x7DF）。
        :param payload: ISO-TP 重组后的 UDS payload。
        :returns: 需要发回总线的 ``(response_id, response_payload)`` 列表；
                  物理寻址且服务抑制响应、或功能寻址广播时为空列表。
        """
        # ---- 功能寻址：广播给所有 ECU，不收集响应 ----
        if can_id == self.functional_address:
            for ecu in self.ecus:
                ecu.process(payload)
            return []

        # ---- 物理寻址：精确单播 ----
        target = self._by_request.get(can_id)
        if target is None:
            # 本网关不认识的请求 ID：不回应（交给链路上其它网关/设备）
            return []

        response = target.process(payload)

        # 空响应（如 3E 80 suppress bit）：不发任何帧
        if not response:
            return []

        return [(target.response_id, response)]


def build_default_gateway() -> DiagnosticGateway:
    """
    构造默认三 ECU 实验网段（ECU_A / ECU_B / ECU_C，各自独立状态）。
    """
    ecus = [
        VirtualEcu(name=name, request_id=req, response_id=resp)
        for name, req, resp in DEFAULT_ECUS
    ]
    return DiagnosticGateway(ecus)
