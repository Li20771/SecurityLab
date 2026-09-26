#!/usr/bin/env python3
"""
C3-A2 —— **Direct ECU ↔ Gateway 差分**基础设施

设计出处：``docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md``
§13（Direct ECU ↔ Gateway 等价性策略）/ §11（多 ECU 不变量）/ §10.3（functional）。

## oracle 是**未被修改的直连 ECU 路径**

```
oracle  = Direct ECU   `ecu.handle_uds_request(request)`
          —— C3 **没有**修改它的行为（C3-A1 只把绑定改为委托，语义不变）

subject = Gateway      `DiagnosticGateway.route()`，经 C3-A1 接线的 0x27
```

**不是** oracle 的东西：

```
✗ 手写 expected
✗ C3-A0 的 Gateway golden（那是"C3 接线之前"的历史基线，只能作历史事实/迁移参照）
✗ 当前 Gateway 自己的 expected
✗ 任何"重新生成的正确答案"
```

## 单 case 的差分模型

```
        same input (request 序列)
        same initial security state
        same clock / SECURITY_CONFIG
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   Direct ECU             Gateway（经 0x27 接线）
   （全新 ecu.STATE）      （全新 gateway + 全新三 ECU）
        │                       │
   responses / state       responses / state
   / security_state        / security_state（**逐 ECU**）
        └───────────┬───────────┘
                    ▼
            DifferentialResult
              ├── response_diffs
              ├── state_diffs
              ├── security_diffs
              └── metadata（case_id / request / 初始状态 / 两侧终态）
```

## 独立性硬约束（设计 §13 / 用户要求）

```
每个 case：Direct 与 Gateway **各自新建** ECU / EcuState，**不共享实例**；
           **不**允许"Direct 执行后拿同一批 ECU 再跑 Gateway"；
           两侧使用**同一个** clock 快照与**同一个** SECURITY_CONFIG 值。
```

## 物理 vs 功能寻址的比较映射（**刻意不同**）

```
physical     Direct（单 ECU）  vs  Gateway 的**目标 ECU**
functional   Direct（单 ECU）  vs  Gateway 的**每一个** ECU
             —— C2-OD-4 冻结"功能寻址逐 ECU 判定"，
                故每个 ECU 都应各自表现得像"直连 ECU 收到同一请求"
```

安全边界：只做本地离线差分，不接触真实 CAN / ECU / 车辆。
"""

import os
import sys
from dataclasses import dataclass, field
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
if _ECU_DIR not in sys.path:
    sys.path.insert(0, _ECU_DIR)

import keygen  # noqa: E402
import uds_server  # noqa: E402
import uds_types  # noqa: E402
from gateway import (  # noqa: E402
    FUNCTIONAL_ADDRESS,
    DiagnosticGateway,
    VirtualEcu,
)
from uds_server import EcuState  # noqa: E402

#: 实现标识。
IMPL_DIRECT = "direct_ecu"
IMPL_GATEWAY = "gateway"

#: 寻址模式。
ADDRESSING_PHYSICAL = "physical"
ADDRESSING_FUNCTIONAL = "functional"

#: 差分使用的固定时钟与安全配置（两侧**同值**）。
BASE_CLOCK = 1000.0
DIFFERENTIAL_SECURITY_CONFIG = {
    "max_attempts": 3,
    "lockout_seconds": 10,
    "seed_expiry_seconds": 5,
}

#: 默认三 ECU 拓扑（与生产 ``DEFAULT_ECUS`` 一致）。
DEFAULT_TOPOLOGY = (
    ("ECU_A", 0x7E0, 0x7E8),
    ("ECU_B", 0x7E1, 0x7E9),
    ("ECU_C", 0x7E2, 0x7EA),
)

#: request_id → ECU 名（physical 差分时用于定位被比较的目标 ECU）。
REQUEST_ID_TO_NAME = {request_id: name for name, request_id, _ in DEFAULT_TOPOLOGY}

#: 默认网段的 ECU 名序列（拓扑顺序）。
TOPOLOGY_NAMES = tuple(name for name, _req, _resp in DEFAULT_TOPOLOGY)

SID_SECURITY_ACCESS = 0x27
SID_READ_DID = 0x22
SID_SESSION = 0x10
SID_ECU_RESET = 0x11

REQUEST_SEED = bytes([SID_SECURITY_ACCESS, 0x01])

#: 状态投影字段（与 C2/C3 各阶段同口径）。
STATE_FIELDS = ("session", "security_level", "did_overrides")

#: 0x27 安全状态投影字段。
SECURITY_STATE_FIELDS = (
    "pending_seed",
    "seed_consumed",
    "failed_attempts",
    "lockout_until",
    "seed_counter",
)


# ----------------------------------------------------------------------
# 状态投影
# ----------------------------------------------------------------------


class Clock:
    """可推进的确定性时钟（两侧共享同一个实例 ⇒ 时间输入一致）。"""

    def __init__(self, now: float = BASE_CLOCK):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def state_projection(state: EcuState) -> dict:
    return {
        "session": state.session,
        "security_level": state.security_level,
        "did_overrides": {
            f"{did:04x}": value.hex() for did, value in sorted(state.did_overrides.items())
        },
    }


def security_state_projection(state: EcuState) -> dict:
    return {
        "pending_seed": None if state.pending_seed is None else state.pending_seed.hex(),
        "seed_consumed": state.seed_consumed,
        "failed_attempts": state.failed_attempts,
        "lockout_until": state.lockout_until,
        "seed_counter": state.seed_counter,
    }


def fresh_state(initial: dict) -> EcuState:
    """由 case 的初始状态规格构造**全新** ``EcuState``。"""
    state = EcuState()
    state.session = initial.get("session", uds_types.SESSION_DEFAULT)
    state.security_level = initial.get("security_level", uds_types.SECURITY_LOCKED)
    state.did_overrides = {
        int(did, 16): bytes.fromhex(value)
        for did, value in initial.get("did_overrides", {}).items()
    }
    return state


# ----------------------------------------------------------------------
# case 定义
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class DifferentialCase:
    """一条差分 case：**同一条请求序列**同时喂给两条路径。"""

    case_id: str
    description: str
    steps: tuple

    #: 两侧**相同**的初始安全状态。
    initial: dict = field(default_factory=dict)

    #: ``physical``（比较目标 ECU）或 ``functional``（比较每一个 ECU）。
    addressing: str = ADDRESSING_PHYSICAL

    #: physical 的目标 request_id；functional 时为 ``None``。
    target: int | None = 0x7E0

    #: 策略种类（见 :func:`make_policy`）。
    policy: str = "allow_all"

    #: 额外时钟推进（(step 索引, 秒数)）—— 在运行该步**之前**推进，两侧同步。
    clock_advance_after: tuple = ()

    #: 期望的差分语义：
    #:   ``equivalent``  —— 两侧必须等价（默认）
    #:   ``policy_deny`` —— Gateway 因 **C2 policy deny** 静默丢弃，
    #:                     此时两侧**必然不同**（直连路径没有 policy 层）；
    #:                     应断言"丢弃由策略引起"而不是"两侧相等"。
    expectation: str = "equivalent"

    #: 期望 Gateway 命中的策略 target（``policy_deny`` 时用于断言 decision）。
    expected_denied_target: int | None = None


def make_policy(kind: str):
    """测试侧策略（**不是**生产策略内容）。"""
    if kind == "allow_all":
        def policy(ecu, addressing_mode, sid):
            return True

        return policy
    if kind == "deny_a":
        def policy(ecu, addressing_mode, sid):
            return ecu.request_id != 0x7E0

        return policy
    raise ValueError(f"未知策略种类：{kind!r}")  # pragma: no cover


# ----------------------------------------------------------------------
# 执行：Direct ECU（oracle）
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Execution:
    implementation: str
    responses: tuple
    state: dict
    security_state: dict
    targets: tuple = ()

    #: 每步的 response_id 序列（Direct 路径无寻址身份 ⇒ 全为 ``None``）。
    response_ids: tuple = ()

    #: 每步的 policy decision 序列（仅 Gateway 路径；Direct 无 policy 层）。
    #: 每项形如 ``{"target": int, "addressing_mode": str, "sid": int, "allowed": bool}``。
    policy_decisions: tuple = ()

    @property
    def response_count(self) -> int:
        return len(self.responses)

    def response_ids_of(self, step_index: int) -> tuple:
        if step_index >= len(self.response_ids):
            return ()
        return self.response_ids[step_index]


def direct_execution(case: DifferentialCase, clock: Clock) -> Execution:
    """
    **oracle 路径**：``ecu.handle_uds_request``，每次用**全新**的 ``ecu.STATE``。

    两侧共享同一个 ``clock`` 实例（时间输入一致）。
    """
    import ecu

    saved_state = ecu.STATE
    saved_config = dict(ecu.SECURITY_CONFIG)
    try:
        ecu.STATE = fresh_state(case.initial)
        ecu.SECURITY_CONFIG.update(DIFFERENTIAL_SECURITY_CONFIG)

        responses = []
        ctx: dict = {}
        for index, step in enumerate(case.steps):
            for _advance_index, seconds in case.clock_advance_after:
                if _advance_index == index:
                    clock.advance(seconds)
            request = expand_step(step, ecu.STATE, ctx)
            with mock.patch.object(ecu, "_now", side_effect=clock):
                # 统一为"每步一个 payload 元组"，使两条路径结构同形、可直接比较
                responses.append((ecu.handle_uds_request(request),))

        return Execution(
            implementation=IMPL_DIRECT,
            responses=tuple(responses),
            state=state_projection(ecu.STATE),
            security_state=security_state_projection(ecu.STATE),
            targets=("ECU",),
        )
    finally:
        ecu.STATE = saved_state
        ecu.SECURITY_CONFIG.clear()
        ecu.SECURITY_CONFIG.update(saved_config)


def expand_step(step, state: EcuState, ctx: dict | None = None) -> bytes:
    """
    把一步展开为具体请求字节。

    支持四种形态：

    * ``"27 01"``                              —— 字面 hex
    * ``{"key_for_current_seed": True}``       —— 由**当前** pending_seed 派生正确 key
    * ``{"wrong_key_for_current_seed": True}`` —— 派生错误 key（按位取反）
    * ``{"replay_last_key": True}``            —— **重用**上一次发出的 key

    最后一种用于"已消费 seed 重放"：成功解锁后 ``pending_seed`` 已被清空，
    无法再由它派生 key —— 必须记住**真正发出去的那把 key**。
    """
    ctx = {} if ctx is None else ctx

    if isinstance(step, str):
        return bytes.fromhex(step)

    if "key_for_current_seed" in step:
        assert state.pending_seed is not None, "case 定义错误：当前无 pending_seed"
        key = keygen.derive_key(state.pending_seed)
        ctx["last_key"] = key
        return bytes([SID_SECURITY_ACCESS, 0x02]) + key

    if "wrong_key_for_current_seed" in step:
        assert state.pending_seed is not None, "case 定义错误：当前无 pending_seed"
        correct = keygen.derive_key(state.pending_seed)
        key = bytes(b ^ 0xFF for b in correct)
        ctx["last_key"] = key
        return bytes([SID_SECURITY_ACCESS, 0x02]) + key

    if "replay_last_key" in step:
        assert ctx.get("last_key") is not None, "case 定义错误：此前未发出过 key"
        return bytes([SID_SECURITY_ACCESS, 0x02]) + ctx["last_key"]

    raise ValueError(f"无法展开的 step：{step!r}")  # pragma: no cover


# ----------------------------------------------------------------------
# 执行：Gateway（subject）
# ----------------------------------------------------------------------


def gateway_execution(case: DifferentialCase, clock: Clock) -> Execution:
    """
    **subject 路径**：全新建网的 ``DiagnosticGateway`` + C3-A1 的 0x27 接线。

    每个 case 都**新建** gateway 与三个 ECU ⇒ 与 Direct 路径**不共享任何实例**。
    """

    def provider():
        return uds_server.security_access_services(
            now=clock(), security_config=DIFFERENTIAL_SECURITY_CONFIG
        )

    # **每个** ECU 都从 case 的初始状态开始（两侧**同一初始 security state**）——
    # 不能用 build_default_gateway() 的默认态，否则 D12 之类的
    # "extended 会话下解锁" case 会在两侧处于不同会话。
    ecus = [
        VirtualEcu(name, request_id, response_id, state=fresh_state(case.initial))
        for name, request_id, response_id in DEFAULT_TOPOLOGY
    ]
    gateway = DiagnosticGateway(
        ecus, services_provider=provider, policy=make_policy(case.policy)
    )

    can_id = FUNCTIONAL_ADDRESS if case.addressing == ADDRESSING_FUNCTIONAL else case.target

    responses = []
    response_ids = []
    decisions = []
    ctx: dict = {}
    for index, step in enumerate(case.steps):
        for _advance_index, seconds in case.clock_advance_after:
            if _advance_index == index:
                clock.advance(seconds)
        request = expand_step(step, gateway.ecu_by_name("ECU_A").state, ctx)
        routed, trace = gateway.route_with_trace(can_id, request)
        responses.append(tuple(payload for _rid, payload in routed))
        response_ids.append(tuple(rid for rid, _payload in routed))
        decisions.append(
            tuple(
                {
                    "target": d.target,
                    "addressing_mode": d.addressing_mode,
                    "sid": d.sid,
                    "allowed": d.allowed,
                }
                for d in trace.decisions
            )
        )

    return Execution(
        implementation=IMPL_GATEWAY,
        responses=tuple(responses),
        state={ecu.name: state_projection(ecu.state) for ecu in gateway.ecus},
        security_state={ecu.name: security_state_projection(ecu.state) for ecu in gateway.ecus},
        targets=tuple(ecu.name for ecu in gateway.ecus),
        response_ids=tuple(response_ids),
        policy_decisions=tuple(decisions),
    )


# ----------------------------------------------------------------------
# 差分模型
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDiff:
    where: str
    field: str
    direct: object
    gateway: object


@dataclass(frozen=True)
class DifferentialResult:
    case: DifferentialCase
    direct: Execution
    gateway: Execution

    # ---- 比较映射 ----

    @property
    def compared_targets(self) -> tuple:
        """
        比较映射（**physical 与 functional 刻意不同**）：

            physical    ⇒ Gateway 的**目标 ECU**（由 ``case.target`` 定位）
            functional  ⇒ Gateway 的**每一个** ECU
        """
        if self.case.addressing == ADDRESSING_FUNCTIONAL:
            return self.gateway.targets
        return (REQUEST_ID_TO_NAME[self.case.target],)

    # ---- 判定 ----

    @property
    def response_matches(self) -> bool:
        """physical：与目标 ECU 的响应序列比较；functional：恒为空（见 state 比较）。"""
        if self.case.addressing == ADDRESSING_FUNCTIONAL:
            return all(not step_responses for step_responses in self.gateway.responses)
        return self.direct.responses == self.gateway.responses

    @property
    def state_matches(self) -> bool:
        return all(
            self._target_state(name) == self.direct.state
            and self._target_security_state(name) == self.direct.security_state
            for name in self.compared_targets
        )

    @property
    def matched(self) -> bool:
        if self.case.expectation == "policy_deny":
            return self.policy_deny_holds
        return self.response_matches and self.state_matches

    # ---- policy deny 的专门判据（**不要求两侧相等**）----

    @property
    def policy_deny_holds(self) -> bool:
        """
        ``policy deny`` case 的正确语义（与 ``equivalent`` **不同**）。

        C2 policy 是 **Gateway 层**决策，直连 ECU 路径**没有**这一层 ⇒
        两侧**必然不同**。因此本类 case 应断言"丢弃由策略引起"，即：

        1. Gateway **不产生任何帧**（静默丢弃）；
        2. Gateway 的目标 ECU 状态**保持初始值**（未被推进）；
        3. **oracle 侧确实会产生正响应** —— 证明丢弃是策略造成的，
           而不是 0x27 路径坏了；
        4. 策略 decision 记录了 ``allowed=False`` 且 target 正确。
        """
        if not all(not step for step in self.gateway.responses):
            return False

        initial = fresh_state(self.case.initial)
        target_name = REQUEST_ID_TO_NAME[self.case.target]
        if self.gateway.security_state[target_name] != security_state_projection(initial):
            return False

        # oracle 侧必须"本来会成功" —— 否则本 case 证明不了任何事
        if not any(step and step[0][:1] == b"g" for step in self.direct.responses):
            return False

        for step_decisions in self.gateway.policy_decisions:
            for decision in step_decisions:
                if decision["target"] != self.case.expected_denied_target:
                    return False
                if decision["allowed"]:
                    return False
        return True

    def _target_state(self, name: str) -> dict:
        return self.gateway.state[name]

    def _target_security_state(self, name: str) -> dict:
        return self.gateway.security_state[name]

    # ---- 差异定位 ----

    @property
    def response_diffs(self) -> tuple:
        if self.case.addressing == ADDRESSING_FUNCTIONAL:
            return ()
        diffs = []
        for index in range(max(len(self.direct.responses), len(self.gateway.responses))):
            left = self.direct.responses[index] if index < len(self.direct.responses) else None
            right = self.gateway.responses[index] if index < len(self.gateway.responses) else None
            if left != right:
                diffs.append((index, left, right))
        return tuple(diffs)

    @property
    def state_diffs(self) -> tuple:
        diffs = []
        for name in self.compared_targets:
            gateway_state = self.gateway.state[name]
            gateway_security = self.gateway.security_state[name]
            for name_field in STATE_FIELDS:
                if self.direct.state[name_field] != gateway_state[name_field]:
                    diffs.append(
                        FieldDiff(
                            where=name,
                            field=name_field,
                            direct=self.direct.state[name_field],
                            gateway=gateway_state[name_field],
                        )
                    )
            for name_field in SECURITY_STATE_FIELDS:
                if self.direct.security_state[name_field] != gateway_security[name_field]:
                    diffs.append(
                        FieldDiff(
                            where=name,
                            field=name_field,
                            direct=self.direct.security_state[name_field],
                            gateway=gateway_security[name_field],
                        )
                    )
        return tuple(diffs)

    # ---- 报告 ----

    def failure_report(self) -> str:
        lines = [
            f"[MISMATCH] case={self.case.case_id}",
            f"  description = {self.case.description}",
            f"  addressing  = {self.case.addressing}"
            + (f"  target=0x{self.case.target:03X}" if self.case.target else ""),
            f"  steps       = {[describe_step(s) for s in self.case.steps]}",
            f"  direct  responses = {format_responses(self.direct.responses)}",
            f"  gateway responses = {format_responses(self.gateway.responses)}",
        ]
        for diff in self.response_diffs:
            lines.append(f"    step[{diff[0]}] direct={fmt(diff[1])} gateway={fmt(diff[2])}")
        for diff in self.state_diffs:
            lines.append(
                f"    {diff.where}.{diff.field}: direct={diff.direct!r} gateway={diff.gateway!r}"
            )
        return "\n".join(lines)

    def summary_line(self) -> str:
        verdict = "OK  " if self.matched else "DIFF"
        return (
            f"[{verdict}] {self.case.case_id:44s} {self.case.addressing:10s} "
            f"direct={format_responses(self.direct.responses):34s} "
            f"gateway={format_responses(self.gateway.responses)}"
        )


def describe_step(step) -> str:
    """
    **best-effort** 的步骤描述 —— 用于失败报告。

    刻意**不**尝试展开 key 步骤：那些步骤依赖执行序列中的 state
    （``pending_seed`` / 上一次发出的 key），在报告阶段不一定可求值。
    报告必须**永不抛异常**，否则会把"报告渲染失败"误报成"断言失败"。
    """
    if isinstance(step, str):
        return bytes.fromhex(step).hex(" ")
    if isinstance(step, dict):
        return "<" + "|".join(step) + ">"
    return repr(step)  # pragma: no cover


def fmt(value) -> str:
    if value is None:
        return "<missing>"
    if isinstance(value, tuple):
        return "|".join(payload.hex(" ") or "<empty>" for payload in value) or "[]"
    return repr(value)


def format_responses(responses) -> str:
    if not responses:
        return "[]"
    return "  ".join("|".join(p.hex(" ") or "<empty>" for p in step) or "[]" for step in responses)


# ----------------------------------------------------------------------
# 执行入口
# ----------------------------------------------------------------------


def run_case(case: DifferentialCase) -> DifferentialResult:
    """
    跑一条 case：**两侧各自新建实例、各自执行**，时间线完全一致。

    .. important::
       两侧各持一个 ``Clock`` 实例，起始值相同、推进计划相同
       ⇒ **时间输入逐值相同**，但**不共享可变状态**。

       执行是**顺序且隔离**的：Direct 结束后 ``ecu.STATE`` 被恢复，
       Gateway 使用全新网关与全新三 ECU。
       不存在"拿 Direct 执行后的 ECU 再跑 Gateway"。
    """
    direct_clock = Clock()
    direct = direct_execution(case, direct_clock)

    gateway_clock = Clock()
    gateway = gateway_execution(case, gateway_clock)

    return DifferentialResult(case=case, direct=direct, gateway=gateway)


# ----------------------------------------------------------------------
# case 矩阵（覆盖用户指定的 20 项）
# ----------------------------------------------------------------------

_UNLOCKED = {
    "session": uds_types.SESSION_EXTENDED,
    "security_level": uds_types.SECURITY_LEVEL1,
}


def build_cases() -> list:
    """A2 的差分 case 矩阵（每条 case = 两侧**相同**的输入与初始状态）。"""
    return [
        # ---- 1 physical 27 01 ----
        DifferentialCase(
            "D01-physical-request-seed",
            "physical 27 01：两侧都产生同一 seed 与同一状态推进",
            steps=(REQUEST_SEED.hex(),),
        ),
        # ---- 2 physical 27 02（正确 key）----
        DifferentialCase(
            "D02-physical-send-key",
            "physical 27 01 → 27 02 正确 key：两侧都解锁",
            steps=(REQUEST_SEED.hex(), {"key_for_current_seed": True}),
        ),
        # ---- 3 wrong key ----
        DifferentialCase(
            "D03-wrong-key",
            "错误 key → 两侧同 NRC 35、failed_attempts 同步",
            steps=(REQUEST_SEED.hex(), {"wrong_key_for_current_seed": True}),
        ),
        # ---- 4 third failure / lockout ----
        DifferentialCase(
            "D04-third-failure-lockout",
            "连续三次错误 → 35/35/36，随后 27 01 → 37",
            steps=(
                REQUEST_SEED.hex(),
                {"wrong_key_for_current_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_current_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_current_seed": True},
                REQUEST_SEED.hex(),
            ),
        ),
        # ---- 5 lockout expiry ----
        DifferentialCase(
            "D05-lockout-expiry",
            "锁定到期后恢复（两侧 lockout_until / failed_attempts 同步归零）",
            steps=(
                REQUEST_SEED.hex(),
                {"wrong_key_for_current_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_current_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_current_seed": True},
                REQUEST_SEED.hex(),
            ),
            clock_advance_after=((6, DIFFERENTIAL_SECURITY_CONFIG["lockout_seconds"] + 1),),
        ),
        # ---- 6 seed expiry ----
        DifferentialCase(
            "D06-seed-expiry",
            "seed 过期后 SendKey → 两侧同 NRC 24",
            steps=(REQUEST_SEED.hex(), {"key_for_current_seed": True}),
            clock_advance_after=((1, DIFFERENTIAL_SECURITY_CONFIG["seed_expiry_seconds"] + 1),),
        ),
        # ---- 7 consumed seed replay ----
        DifferentialCase(
            "D07-consumed-seed-replay",
            "成功解锁后重放**同一把** key → 两侧同 NRC 24",
            steps=(
                REQUEST_SEED.hex(),
                {"key_for_current_seed": True},
                # 成功解锁后 pending_seed 已被清空 ⇒ 必须重用上次发出的 key
                {"replay_last_key": True},
            ),
        ),
        # ---- 8 unlocked request seed ----
        DifferentialCase(
            "D08-seed-while-unlocked",
            "已解锁再请求 seed → 两侧同 NRC 22",
            steps=(
                REQUEST_SEED.hex(),
                {"key_for_current_seed": True},
                REQUEST_SEED.hex(),
            ),
        ),
        # ---- 9 session switch ----
        DifferentialCase(
            "D09-session-switch-relock",
            "解锁后切 extended → 两侧都重新 LOCKED",
            steps=(
                REQUEST_SEED.hex(),
                {"key_for_current_seed": True},
                bytes([SID_SESSION, uds_types.SESSION_EXTENDED]).hex(),
            ),
        ),
        # ---- 10 ECU reset ----
        DifferentialCase(
            "D10-ecu-reset-relock",
            "解锁后 0x11 reset → 两侧都重新 LOCKED",
            steps=(
                REQUEST_SEED.hex(),
                {"key_for_current_seed": True},
                bytes([SID_ECU_RESET, 0x01]).hex(),
            ),
        ),
        # ---- 11 protected DID（locked / unlocked）----
        DifferentialCase(
            "D11-protected-did-locked",
            "未解锁读受保护 DID → 两侧同 NRC 33",
            steps=(bytes([SID_READ_DID, 0xF1, 0x87]).hex(),),
        ),
        DifferentialCase(
            "D12-protected-did-unlocked",
            "extended 会话下先解锁，再读受保护 DID → 两侧同 62 F1 87 …",
            # 注意：初始必须是 **LOCKED** —— 已解锁时 27 01 会返回 22、拿不到 seed
            initial={"session": uds_types.SESSION_EXTENDED},
            steps=(
                REQUEST_SEED.hex(),
                {"key_for_current_seed": True},
                bytes([SID_READ_DID, 0xF1, 0x87]).hex(),
            ),
        ),
        # ---- 12 response_id 映射（对三个目标各跑一次）----
        DifferentialCase(
            "D13a-target-ecu-a", "目标 ECU_A（0x7E0 → 0x7E8）", steps=(REQUEST_SEED.hex(),),
            target=0x7E0,
        ),
        DifferentialCase(
            "D13b-target-ecu-b", "目标 ECU_B（0x7E1 → 0x7E9）", steps=(REQUEST_SEED.hex(),),
            target=0x7E1,
        ),
        DifferentialCase(
            "D13c-target-ecu-c", "目标 ECU_C（0x7E2 → 0x7EA）", steps=(REQUEST_SEED.hex(),),
            target=0x7E2,
        ),
        # ---- 13 / 16 / 17 / 18 per-ECU 隔离（在测试中逐 ECU 断言）----
        DifferentialCase(
            "D14-isolation-unlock-a",
            "只对 A 解锁：A 与 Direct 一致，B/C 保持初始",
            steps=(REQUEST_SEED.hex(), {"key_for_current_seed": True}),
            target=0x7E0,
        ),
        DifferentialCase(
            "D15-isolation-lockout-a",
            "只让 A 进入 lockout：A 与 Direct 一致，B/C 不受影响",
            steps=(
                REQUEST_SEED.hex(),
                {"wrong_key_for_current_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_current_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_current_seed": True},
                REQUEST_SEED.hex(),
            ),
            target=0x7E0,
        ),
        DifferentialCase(
            "D16-a-request-seed-b-request-seed",
            "A 与 B 各自请求 seed：两侧状态逐 ECU 独立且与 Direct 一致",
            steps=(REQUEST_SEED.hex(),),
            target=0x7E0,
        ),
        DifferentialCase(
            "D17-per-ecu-seed-counter",
            "对 A 连续两次请求 seed：A 的 counter=2，B/C 保持 0",
            steps=(REQUEST_SEED.hex(), REQUEST_SEED.hex()),
            target=0x7E0,
        ),
        # ---- 19 physical policy allow / deny ----
        DifferentialCase(
            "D18-policy-allow",
            "policy allow：Gateway 与 Direct 一致",
            steps=(REQUEST_SEED.hex(),),
            policy="allow_all",
        ),
        DifferentialCase(
            "D19-policy-deny",
            "policy deny(0x7E0)：Gateway 静默丢弃（[]）且目标状态不变"
            "—— **两侧必然不同**（直连路径没有 policy 层），故用专门判据",
            steps=(REQUEST_SEED.hex(),),
            policy="deny_a",
            expectation="policy_deny",
            expected_denied_target=0x7E0,
        ),
        # ---- 20 functional 27 的已冻结 known limitation ----
        DifferentialCase(
            "D20-functional-request-seed",
            "functional 27 01：Gateway 返回 []，但每个 ECU 各自与 Direct 一致",
            steps=(REQUEST_SEED.hex(),),
            addressing=ADDRESSING_FUNCTIONAL,
            target=None,
        ),
        DifferentialCase(
            "D21-functional-send-key",
            "functional 27 02：扇出到每个 ECU，逐 ECU 与 Direct 一致",
            steps=(REQUEST_SEED.hex(), {"key_for_current_seed": True}),
            addressing=ADDRESSING_FUNCTIONAL,
            target=None,
        ),
    ]
