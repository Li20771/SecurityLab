#!/usr/bin/env python3
"""
C1 —— 服务授权规格模型（``ServiceSpec`` / ``Guard``）

**Phase 1 定位（关键，请勿误读）：本模块只建立"模型"，不参与"执行"。**

    Phase 1（本模块）   dispatcher 仍走   SERVICES[sid] → legacy handler
                        本模块的 guards  **不被执行**，只用于
                        模型校验 / capability matrix / 审计
    Phase 2（后续）      dispatcher 改走   spec.guards → spec.handler

切换执行路径属 **Phase 2**，本模块**不得**被 `handle_uds_request()` 调用。
因此引入本模块**不改变任何可观测行为**（响应字节、NRC、会话与安全语义）。

设计出处：``docs/OQ_C1_PERMISSION_SESSION_DESIGN_REVIEW_2026-09-24.md``
（OD-1/OD-2 与 Phase 1/Phase 2 均已 DESIGN FROZEN）。

为什么需要"模型"而不是直接改执行路径：既有 907 个用例**无法发现 NRC 优先级变化**
（见设计文档 §15.4 的 N1/N2），因此先把真实授权语义**显式建模并可校验**，
再用它作为 Phase 2 迁移的对拍基准。

依赖方向：本模块是**零依赖叶子模块**（只 import 标准库与 ``uds_types``），
与 ``uds_types.py`` / ``did_database.py`` 同级，**不** import ``uds_server``
（否则与 uds_server 形成环）。具体服务的 guards 由 ``uds_server.py`` 构造。
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

import uds_types

__all__ = [
    "Guard",
    "ServiceSpec",
    "SpecError",
    "validate_specs",
]


#: 单个 guard 的形态：吃 ``(request, state)``，返回 **NRC 表示拒绝**、
#: ``None`` 表示通过。
#:
#: 语义约定（与设计文档 §7.4 一致）：
#:
#: - guard 只做**判定**，不修改状态、不产生响应；拒绝时**只**返回 NRC 值。
#: - ``guards`` 是**有序**的，**顺序即语义** —— 迁移时必须逐字节保持
#:   （例如 ``0x2E`` 的 ``13 → 31 → 7F → 33 → 22 → 13``）。
#:
#: ``ServiceSpec`` 与 ``Guard`` 都对**状态类型**做泛型参数化，而不是
#: import ``EcuState``：本模块是零依赖叶子模块，而 ``uds_server``
#: （``EcuState`` 的定义处）会 import 本模块 —— 直接 import 会成环。
#: 参数化让 ``uds_server`` 侧实例化为 ``ServiceSpec[EcuState]``，
#: 从而**保留完整的类型检查**（handlers 与 guards 都按其真实状态类型校验），
#: 同时不引入反向依赖。
type Guard[S] = Callable[[bytes, S], "int | None"]


class SpecError(ValueError):
    """``ServiceSpec`` 违反结构约束时抛出（fail-fast，不静默容忍）。"""


@dataclass(frozen=True)
class ServiceSpec[S]:
    """
    一个已实现服务的**完整**授权规格。

    **可执行部分**（Phase 2 才会被执行）：

    - :attr:`guards` —— 有序判定链，**顺序即语义**。

    **声明部分**（Phase 1 起即存在；**不参与执行**，仅供矩阵 / 审计 / 校验）：

    - :attr:`allowed_sessions` —— ``None`` 表示该服务无会话约束。
    - :attr:`required_security` —— ``None`` 表示**无法用单一等级概括**
      （payload 相关的权限，如 ``0x22`` 的 per-DID）；否则为该服务**整体**
      要求的等级。
    - :attr:`payload_scoped` —— ``True`` 表示**该服务的完整授权行为不能仅由
      单一 SID-level 声明字段完整描述**（存在 payload / DID / request-specific
      语义）。

    **注意**：``payload_scoped=True`` **不蕴含** ``required_security is None`` ——
    两者回答的是不同问题（"能否完整描述" vs "是否整体要求某等级"）。
    例如 ``0x2E`` 整体确实要求 ``LEVEL1``（合法声明），但其完整授权链还依赖
    payload 与**顺序**，故同时也标 ``payload_scoped=True``。
    """

    #: 服务标识符。
    sid: int

    #: 该服务的（legacy）处理函数；Phase 1 仍由 ``SERVICES`` 提供，此处仅作绑定。
    handler: Callable[[bytes, S], bytes]

    #: 有序判定链；**顺序即语义**。无授权判定的服务为 ``()``。
    guards: tuple[Guard[S], ...] = ()

    #: ``None`` = 该服务无会话约束。
    allowed_sessions: frozenset[int] | None = None

    #: ``None`` = 无法用单一等级概括（payload 相关）。
    required_security: int | None = uds_types.SECURITY_LOCKED

    #: ``True`` = 单靠声明字段不足以完整描述其授权行为。
    payload_scoped: bool = False

    #: 便于诊断的人类可读说明（可选；不参与任何判定）。
    note: str = field(default="")


def validate_specs(specs: Mapping[int, ServiceSpec]) -> None:
    """
    校验一组 spec 的**结构约束**（Phase 1 的静态检查）。

    规则（全部来自设计文档 OD-2 与 §11 的冻结内容）：

    ``R1`` ``payload_scoped=True`` → ``guards`` **必须非空**。
        理由：``guards`` 是唯一执行源；若声明字段不足以完整描述其授权行为，
        那些行为**只能**存在于 guards 中，故不可能为空。
        **注意方向**：``guards`` 非空 **不**蕴含 ``payload_scoped``（允许
        非 payload-scoped 的服务也有 guards）。

    ``R2`` 每个 guard 必须可调用。

    ``R3`` ``allowed_sessions`` 必须是 ``None`` 或**非空**的已知会话集合。

    ``R4`` ``required_security`` 必须是 ``None`` 或已知的安全等级。

    ``R5`` ``spec.sid`` 必须与其在映射中的键一致（防止装配错位）。

    :raises SpecError: 任一规则被违反（fail-fast；不静默容忍）。
    """
    for sid, spec in specs.items():
        where = f"ServiceSpec(0x{sid:02X})"

        if spec.sid != sid:
            raise SpecError(f"{where}: sid 与映射键不一致（spec.sid=0x{spec.sid:02X}）")

        if spec.payload_scoped and not spec.guards:
            raise SpecError(
                f"{where}: payload_scoped=True 但 guards 为空 —— "
                f"按 OD-2 冻结规则，payload 相关的授权行为只能存在于 guards 中"
            )

        for index, guard in enumerate(spec.guards):
            if not callable(guard):
                raise SpecError(f"{where}: guards[{index}] 不可调用")

        if spec.allowed_sessions is not None:
            if not spec.allowed_sessions:
                raise SpecError(f"{where}: allowed_sessions 不得为空集合（用 None 表示不限制）")
            unknown = set(spec.allowed_sessions) - set(uds_types.SUPPORTED_SESSIONS)
            if unknown:
                raise SpecError(
                    f"{where}: allowed_sessions 含未知会话 "
                    f"{sorted(hex(s) for s in unknown)}"
                )

        if spec.required_security is not None and spec.required_security not in (
            uds_types.SECURITY_LOCKED,
            uds_types.SECURITY_LEVEL1,
        ):
            raise SpecError(
                f"{where}: required_security=0x{spec.required_security:02X} 不是已知安全等级"
            )


def iter_payload_scoped(specs: Mapping[int, ServiceSpec]) -> Iterable[ServiceSpec]:
    """返回被标记为 payload-scoped 的 spec（供矩阵/审计遍历）。"""
    return (spec for spec in specs.values() if spec.payload_scoped)
