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

#: 运行时服务条目：``{sid: handler(request, state) -> bytes}``。
ServiceTable = dict[int, Callable[[bytes, "EcuState"], bytes]]

#: 运行时依赖提供者（C3）：**每次请求**调用一次，返回本次请求要合并进静态
#: ``SERVICES`` 的条目。
#:
#: 契约（C3-OD-14）：
#:
#: * 只做 **runtime dependency binding** —— 不得改变 C1 guard 语义，
#:   不得改变 C2 policy 维度，不得产生第二套 0x27 状态机；
#: * 返回的表与静态 ``SERVICES`` 合并（``{**SERVICES, **provider()}``）：
#:   同名 SID 由 provider 覆盖，未同名 SID 保留静态服务；
#: * 对已建模 SID，被替换的只是 **handler**，授权 guard 仍由
#:   ``resolve_service_spec`` 保留（覆盖的是实现，不是授权）。
ServicesProvider = Callable[[], ServiceTable]


# ======================================================================
# C2 —— Gateway policy（机制层）
# ======================================================================
#
# 设计出处：``docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md``
# （C2-OD-1 … C2-OD-9 均已 **DESIGN FROZEN**）。
#
# **C2-A1 只引入机制，不引入任何实际策略。**
# 默认策略是**空策略**（对一切放行），因此本节的引入**不改变任何
# wire 行为或 state 行为**（C2-OD-2 的 default-allow）。

#: 物理寻址（单播）。
ADDRESSING_PHYSICAL = "physical"

#: 功能寻址（广播，``0x7DF``）。
ADDRESSING_FUNCTIONAL = "functional"


@dataclass(frozen=True)
class PolicyDecision:
    """
    一次策略求值的**真实结果**（C2-OD-9）。

    .. note::
       这是**测试 / 调用方观测**用的结构，**不是** wire semantics ——
       ``route()`` 的返回类型不因它而改变。

    字段刻意保持最小（设计文档 §9.2.3）：只有
    ``target`` / ``addressing_mode`` / ``sid`` / ``allowed``；
    **不含** reason / rule_id / message / timestamp / logger。
    """

    #: 目标 ECU 的 ``request_id`` —— 稳定、唯一、可断言的寻址身份。
    #: （刻意**不用** ``id()`` / ``repr()`` / 对象身份。）
    target: int

    #: ``ADDRESSING_PHYSICAL`` 或 ``ADDRESSING_FUNCTIONAL``。
    #: （刻意用字符串而非 ``True``/``False``，让测试报告能直接表达语义。）
    addressing_mode: str

    #: 策略的 SID 输入（``payload[0]``）。
    sid: int

    #: 是否放行。``False`` ⇒ 目标 ECU **不会**执行该请求（C2-OD-3）。
    allowed: bool


@dataclass(frozen=True)
class PolicyTrace:
    """
    **单次** ``route_with_trace()`` 的策略决策记录（C2-OD-9）。

    语义（设计文档 §9.2.2 / §9.2.4 / §9.2.8）：

    * **per-call** —— 本次调用独有，与其它调用互不污染；
      **没有** global last_decision / 计数器 / 模块级可变状态，
      也不允许靠日志 grep 事后重建；
    * **immutable** —— ``decisions`` 是 ``tuple``；
    * **ordered** —— 顺序 = 策略**实际被求值**的顺序；
    * 只包含**真正进入策略求值**的请求：**未知物理 target** 与
      **空 payload** 不产生 decision（C2-OD-8）。
    """

    decisions: tuple[PolicyDecision, ...] = ()


#: 策略判定函数：吃 ``(目标 ECU, 寻址模式, SID)``，返回是否放行。
#:
#: **C2 只能看到这三样** —— 策略**不得**感知 ``EcuState``（session /
#: security_level）、service registry（``SERVICE_SPECS``）或 DID 权限：
#: "该 SID 是否由目标 ECU 实现"属 **C1 / handler** 的语义（C2-OD-8）。
GatewayPolicy = Callable[["VirtualEcu", str, int], bool]


def allow_all_policy(ecu: "VirtualEcu", addressing_mode: str, sid: int) -> bool:
    """
    **空策略** —— C2-OD-2 的 default-allow（机制默认，**不是**部署姿态）。

    对一切放行，因此与"策略机制尚未引入"时的行为**逐字节相同**。
    """
    return True


#: 默认策略：空策略（全放行）。空策略 ⇒ 本机制是 **no-op**。
DEFAULT_POLICY: GatewayPolicy = allow_all_policy


def _default_handler_for(
    state: EcuState,
    services_provider: ServicesProvider | None = None,
) -> UdsHandler:
    """
    为一个 ECU 状态绑定协议层 dispatcher。

    ``services_provider is None``（**默认**）
        走静态 ``SERVICE_SPECS`` —— 与 C3 引入前**逐字节相同**。
        网关层因此**不感知**时钟与安全配置（本模块保持纯协议层）。

    ``services_provider`` 非 None（C3）
        **每次请求**现场调用 provider，得到运行时依赖条目，与静态
        ``SERVICES`` 合并后交给协议层 dispatcher：

            {**uds_server.SERVICES, **services_provider()}

        这使 0x27 SecurityAccess（需要请求时依赖）在网关路径上可达，而
        **状态机仍只有一份**（``uds_server.service_security_access``）。

    .. important::
       provider 必须**每请求**调用 —— 不得缓存其返回值、也不得缓存它内部的
       ``now``，否则时间注入会陈旧（C3-OD-14 限制 7/8）。
       provider 只负责**依赖绑定**，不得携带或修改 ``EcuState``。
    """

    def handle(payload: bytes) -> bytes:
        if services_provider is None:
            return uds_server.handle_uds_request(payload, state)

        return uds_server.handle_uds_request(
            payload,
            state,
            services={**uds_server.SERVICES, **services_provider()},
        )

    return handle


@dataclass
class VirtualEcu:
    """网段上的一个逻辑 ECU：寻址参数 + 独立状态 + UDS handler。"""

    name: str
    request_id: int
    response_id: int
    state: EcuState = field(default_factory=EcuState)
    handler: UdsHandler | None = None

    #: 可选的运行时依赖提供者（C3）：每次请求调用一次，返回形如
    #: ``{sid: handler}`` 的条目，与静态 ``SERVICES`` 合并。
    #:
    #: ``None``（默认）⇒ 与 C3 引入前行为**逐字节相同**。
    #: 它只提供**依赖**（如 0x27 的 now / security_config），**不提供状态** ——
    #: ``EcuState`` 始终由 :meth:`process` 的调用方按本 ECU 独立持有。
    services_provider: ServicesProvider | None = None

    def __post_init__(self) -> None:
        if self.handler is None:
            self.handler = _default_handler_for(self.state, self.services_provider)

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
        policy: GatewayPolicy | None = None,
        services_provider: ServicesProvider | None = None,
    ) -> None:
        """
        :param policy: 可选的 Gateway 策略。``None`` ⇒ **空策略**
            （``DEFAULT_POLICY``，全放行）—— 因此既有调用方**无需改动**，
            行为与策略机制引入前**逐字节相同**（C2-OD-2）。
        :param services_provider: 可选的运行时依赖提供者（C3）。``None`` ⇒
            **不接线**，网关行为与 C3 引入前**逐字节相同**。
            非 None 时应用到**未自带** provider 的 ECU（ECU 级优先，保持
            override 语义）。**本参数不参与策略判定** —— 策略仍只看
            ``(target, addressing_mode, SID)``（C3-OD-5）。
        """
        if not ecus:
            raise ValueError("网关至少需要一个 ECU")

        self.functional_address = functional_address
        self.ecus: list[VirtualEcu] = list(ecus)

        if services_provider is not None:
            for ecu in self.ecus:
                if ecu.services_provider is None:
                    ecu.services_provider = services_provider
                    if ecu.handler is not None:
                        ecu.handler = _default_handler_for(ecu.state, services_provider)

        #: 本网关的策略（C2-OD-1：求值点在 ``route`` core 内、
        #: ``target.process()`` **之前**）。
        self.policy: GatewayPolicy = policy if policy is not None else DEFAULT_POLICY

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

        **返回契约与策略机制引入前完全一致**（C2-OD-9）——
        策略决策**不**出现在返回值里；需要决策证据时用 :meth:`route_with_trace`。

        :param can_id: 报文 CAN 标识符（物理请求 ID 或功能地址 0x7DF）。
        :param payload: ISO-TP 重组后的 UDS payload。
        :returns: 需要发回总线的 ``(response_id, response_payload)`` 列表；
                  物理寻址且服务抑制响应、或功能寻址广播时为空列表。
        """
        results, _trace = self._route_core(can_id, payload, collect_trace=False)
        return results

    def route_with_trace(
        self, can_id: int, payload: bytes
    ) -> tuple[list[RouteResult], PolicyTrace]:
        """
        与 :meth:`route` **共用同一条 route core**，额外返回本次调用的
        :class:`PolicyTrace`（C2-OD-9）。

        **这不是第二条实现路径** —— 两者调用同一个 ``_route_core``，
        因此"生产路径"与"测试路径"的语义**不可能分叉**（§9.2.9）。
        唯一差别是**是否记录**决策；策略本身在两条路径上都会被求值并生效。

        :returns: ``(results, trace)``，其中 ``results`` 与 :meth:`route`
            的返回**逐字节相同**。
        """
        return self._route_core(can_id, payload, collect_trace=True)

    def _route_core(
        self, can_id: int, payload: bytes, *, collect_trace: bool
    ) -> tuple[list[RouteResult], PolicyTrace]:
        """
        **唯一**的 route core（C2-OD-1 / C2-OD-9）。

        策略求值位置：**目标解析之后、``target.process()`` 之前**，
        因此被拒请求**不会到达目标 ECU**（其 ``EcuState`` 不变）。

        ``C2-OD-8`` —— 只有两种情形**不进入策略**：

        * **未知物理 target**（路由归属失败，**不是** deny）；
        * **空 payload**（结构上取不到 SID）。

        其余情形：``payload[0]`` 就是策略的 SID 输入 —— **不判断**该 SID
        是否由目标 ECU 实现（那是 **C1 / handler** 的语义）。
        """
        decisions: list[PolicyDecision] = []
        sid = payload[0] if payload else None

        # ---- 功能寻址：广播给所有 ECU，不收集响应 ----
        # C2-OD-4：**逐 ECU** 求值，而非整体判定一次。
        if can_id == self.functional_address:
            for ecu in self.ecus:
                if self._allow(ecu, ADDRESSING_FUNCTIONAL, sid, decisions, collect_trace):
                    ecu.process(payload)
            return [], PolicyTrace(tuple(decisions))

        # ---- 物理寻址：精确单播 ----
        target = self._by_request.get(can_id)
        if target is None:
            # 本网关不认识的请求 ID：不回应（交给链路上其它网关/设备）。
            # **不进入策略** —— 这是路由归属判定，不是授权判定。
            return [], PolicyTrace(tuple(decisions))

        if not self._allow(target, ADDRESSING_PHYSICAL, sid, decisions, collect_trace):
            # C2-OD-3：拒绝 = **静默丢弃** ——
            # 不产生帧、不合成 7F、不生成 NRC，且 target.process() **不被调用**。
            return [], PolicyTrace(tuple(decisions))

        response = target.process(payload)

        # 空响应（如 3E 80 suppress bit）：不发任何帧
        if not response:
            return [], PolicyTrace(tuple(decisions))

        return [(target.response_id, response)], PolicyTrace(tuple(decisions))

    def _allow(
        self,
        ecu: VirtualEcu,
        addressing_mode: str,
        sid: int | None,
        decisions: list[PolicyDecision],
        collect_trace: bool,
    ) -> bool:
        """
        对**单个**目标求值策略。

        ``sid is None``（空 payload）⇒ **不进入策略**：直接放行，且
        **不记录** decision（C2-OD-8）。

        否则调用 ``self.policy(...)``，并把**真实求值结果**追加进
        ``decisions`` —— 决策**只**在这里产生：不存在"先执行策略、事后补写
        trace"，也不存在"根据 results / state 事后推断 decision"（§9.2.8）。
        """
        if sid is None:
            return True

        allowed = self.policy(ecu, addressing_mode, sid)

        if collect_trace:
            decisions.append(
                PolicyDecision(
                    target=ecu.request_id,
                    addressing_mode=addressing_mode,
                    sid=sid,
                    allowed=allowed,
                )
            )
        return allowed


def build_default_gateway(
    services_provider: ServicesProvider | None = None,
) -> DiagnosticGateway:
    """
    构造默认三 ECU 实验网段（ECU_A / ECU_B / ECU_C，各自独立状态）。

    :param services_provider: 可选（C3）。**默认 ``None`` ⇒ 不接线**，
        离线网关行为与 C3 引入前逐字节相同（C3-OD-7：机制与接线分离 ——
        由入口层 ``gateway_runtime.build_runtime`` 或测试显式注入）。
    """
    ecus = [
        VirtualEcu(name=name, request_id=req, response_id=resp)
        for name, req, resp in DEFAULT_ECUS
    ]
    return DiagnosticGateway(ecus, services_provider=services_provider)
