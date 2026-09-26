#!/usr/bin/env python3
"""
C3-A0 —— 0x27 SecurityAccess **实现前基线**（baseline）的定义 / 捕获 / 重放

设计出处：``docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md``
（C3-OD-1 … OD-15、§6 行为契约、§13 对拍模型、§14.2 冻结类别）。

## 本模块只捕获 **C3 实现之前**的现状

**A0 在 C3 接线之前运行**，因此捕获到的就是"Gateway 尚不能执行 0x27"
这一**实现前事实**。本模块：

* **不实现** 0x27 through Gateway；
* **不修改** 任何 production 代码；
* **不修改** 任何既有测试（含 C3-B1 的旧 pin）。

## 两条基线路径

```
direct_ecu   `ecu.handle_uds_request(request)`        —— 单 ECU 入口（0x27 今天已可用）
gateway      `DiagnosticGateway.route(can_id, payload)` —— 网关路径（0x27 今天不可达）
```

## CASE 结构（与 C2-A0 的差异，**刻意**）

C2-A0 的不变式是"一个 case = 恰好一次 ``route()``"。
C3-A0 **不能**沿用该不变式：以下类别天然需要**序列**才能表达

    第三次失败 → lockout
    seed → key → 重放 key（消费后再发）
    解锁 → 读受保护 DID

因此 C3-A0 的不变式改为：

    一个 case = 一段**脚本化步骤序列**（steps），
    每步一次请求；case **自带完整初始状态** ⇒ 仍可独立 replay。

## 冻结的边界（**不混入任何 A1 预期**）

部分 gateway case 记录的是 **C2 时代的分层边界**：

    Gateway 路径 27 01 → 7F 27 11（serviceNotSupported）

这类 case 以 ``frozen_boundary = "c2_era_gateway_0x27_unreachable"`` 标记。
该字段只**描述这是 C2 时代边界**，**不预测** C3-A1 之后的新取值 ——
新取值属 C3-A1 的实现与 pin 更新范围（C3-OD-15）。

安全边界：本模块只做本地离线基线，不接触真实 CAN / ECU / 车辆。
"""

import json
import os
import subprocess
import sys
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
if _ECU_DIR not in sys.path:
    sys.path.insert(0, _ECU_DIR)

import keygen  # noqa: E402
import uds_types  # noqa: E402
from gateway import (  # noqa: E402
    FUNCTIONAL_ADDRESS,
    DiagnosticGateway,
    VirtualEcu,
)
from uds_server import EcuState  # noqa: E402

#: corpus 文件位置。
GOLDEN_DIR = os.path.join(_HERE, "golden")
CORPUS_PATH = os.path.join(GOLDEN_DIR, "c3_security_access_baseline.json")

#: schema 版本（C3-A0 首次建立，独立于 C1 / C2 的 corpus）。
SCHEMA_VERSION = 1

#: 基线路径标识。
PATH_DIRECT_ECU = "direct_ecu"
PATH_GATEWAY = "gateway"

#: 寻址模式标识。
ADDRESSING_PHYSICAL = "physical"
ADDRESSING_FUNCTIONAL = "functional"

#: **C2 时代分层边界**的标记 —— 只描述事实，不预测 C3-A1 之后的新取值。
FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE = "c2_era_gateway_0x27_unreachable"

#: 逐 ECU 常规状态投影字段（与 C2-A0/A1/A2/A4 同口径）。
STATE_PROJECTION_FIELDS = ("session", "security_level", "did_overrides")

#: 逐 ECU **安全状态**投影字段（C3 新增的关注对象）。
SECURITY_STATE_FIELDS = (
    "pending_seed",
    "seed_consumed",
    "failed_attempts",
    "lockout_until",
    "seed_counter",
)

#: 基线使用的固定时钟（``ecu._now`` 注入值）——
#: 使 seed 过期 / 锁定到期可确定性地表达，无需 sleep。
BASE_CLOCK = 1000.0

#: 基线使用的固定安全配置（写入 ``ecu.SECURITY_CONFIG`` 的同一个 dict 对象）。
BASELINE_SECURITY_CONFIG = {
    "max_attempts": 3,
    "lockout_seconds": 10,
    "seed_expiry_seconds": 5,
}

#: 默认三 ECU 拓扑（取自生产 ``DEFAULT_ECUS``，此处显式列出以免 import 顺序耦合）。
DEFAULT_TOPOLOGY = (
    ("ECU_A", 0x7E0, 0x7E8),
    ("ECU_B", 0x7E1, 0x7E9),
    ("ECU_C", 0x7E2, 0x7EA),
)

SID_SECURITY_ACCESS = 0x27
SID_READ_DID = 0x22
SID_SESSION = 0x10
SID_ECU_RESET = 0x11

DID_PROTECTED = 0xF187
DID_PUBLIC = 0xF190

REQUEST_SEED = bytes([SID_SECURITY_ACCESS, 0x01])
SEND_KEY_NO_SEED = bytes([SID_SECURITY_ACCESS, 0x02]) + bytes(4)


# ----------------------------------------------------------------------
# 状态投影
# ----------------------------------------------------------------------


def state_projection(state: EcuState) -> dict:
    """逐 ECU 三字段常规状态投影（与 C2 各阶段同口径）。"""
    return {
        "session": state.session,
        "security_level": state.security_level,
        "did_overrides": {
            f"{did:04x}": value.hex() for did, value in sorted(state.did_overrides.items())
        },
    }


def security_state_projection(state: EcuState) -> dict:
    """
    0x27 **安全状态**投影（C3 新增关注对象）。

    ``pending_seed`` 只记录**是否存在**（seed 取值本身由 ``(counter, level)``
    决定、不含 ECU 身份，见设计文档 §3.2；把它当跨路径常量会误当等价性证据）。
    """
    return {
        "pending_seed": None if state.pending_seed is None else state.pending_seed.hex(),
        "seed_consumed": state.seed_consumed,
        "failed_attempts": state.failed_attempts,
        "lockout_until": state.lockout_until,
        "seed_counter": state.seed_counter,
    }


# ----------------------------------------------------------------------
# 策略（**A0 测试用**，不是生产策略内容）
# ----------------------------------------------------------------------


def allow_all_policy(ecu, addressing_mode, sid):
    """与生产 ``DEFAULT_POLICY`` 同语义（allow-all）—— 此处显式重述以免耦合。"""
    return True


def deny_target(request_id):
    """拒绝指定 target，其余放行（用于 C2 interaction baseline）。"""

    def policy(ecu, addressing_mode, sid):
        return ecu.request_id != request_id

    return policy


POLICY_DEFAULT_ALLOW = "default_allow"
POLICY_DENY_TARGET_7E1 = "deny_target_7e1"

_POLICIES = {
    POLICY_DEFAULT_ALLOW: allow_all_policy,
    POLICY_DENY_TARGET_7E1: deny_target(0x7E1),
}


# ----------------------------------------------------------------------
# case 定义
# ----------------------------------------------------------------------


def _ecu_spec(name, request_id, response_id, *, session=None, security=None, overrides=None):
    return {
        "name": name,
        "request_id": request_id,
        "response_id": response_id,
        "session": uds_types.SESSION_DEFAULT if session is None else session,
        "security_level": uds_types.SECURITY_LOCKED if security is None else security,
        "did_overrides": {
            f"{did:04x}": value.hex() for did, value in sorted((overrides or {}).items())
        },
    }


def default_ecus(per_ecu=None) -> list:
    """默认三 ECU 的初始配置；``per_ecu`` 可对指定 ECU 覆盖。"""
    per_ecu = per_ecu or {}
    return [
        _ecu_spec(name, req, resp, **per_ecu.get(name, {}))
        for name, req, resp in DEFAULT_TOPOLOGY
    ]


def _case(
    case_id,
    description,
    *,
    path,
    steps,
    ecus,
    addressing=ADDRESSING_PHYSICAL,
    target=None,
    policy=POLICY_DEFAULT_ALLOW,
    clock=BASE_CLOCK,
    frozen_boundary=None,
):
    return {
        "case_id": case_id,
        "description": description,
        "path": path,
        "addressing": addressing,
        "target": target,
        "policy": policy,
        "clock": clock,
        "frozen_boundary": frozen_boundary,
        "ecus": ecus,
        "steps": list(steps),
    }


def build_cases() -> list:
    """
    C3-A0 的基线 case 矩阵。

    覆盖设计文档 §14.2 的 16 个类别 + 用户指定的 5 组 baseline categories：

      1  直连 ECU 0x27 baseline（11 类）
      2  当前 Gateway baseline（5 类）
      3  Multi-ECU baseline（4 类）
      4  C2 interaction baseline（4 类）
      5  Direct ECU ↔ Gateway 对照输入（1 类，**不要求相等**）
    """
    return [
        # ==============================================================
        # 组 1 —— 直连 ECU 0x27 baseline（path = direct_ecu）
        # ==============================================================
        _case(
            "D1-request-seed",
            "直连 ECU：27 01 → 67 01 <seed>",
            path=PATH_DIRECT_ECU,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
        ),
        _case(
            "D2-send-key-without-seed",
            "直连 ECU：27 02 无 seed → 7F 27 24",
            path=PATH_DIRECT_ECU,
            steps=[SEND_KEY_NO_SEED.hex()],
            ecus=default_ecus(),
        ),
        _case(
            "D3-malformed-length-seed",
            "直连 ECU：27 01 带多余字节 → 7F 27 13",
            path=PATH_DIRECT_ECU,
            steps=[bytes([SID_SECURITY_ACCESS, 0x01, 0x00]).hex()],
            ecus=default_ecus(),
        ),
        _case(
            "D4-malformed-length-key",
            "直连 ECU：27 02 key 长度不足 → 7F 27 13",
            path=PATH_DIRECT_ECU,
            steps=[bytes([SID_SECURITY_ACCESS, 0x02, 0x00]).hex()],
            ecus=default_ecus(),
        ),
        _case(
            "D5-unsupported-subfunction",
            "直连 ECU：27 03 未支持子功能 → 7F 27 12",
            path=PATH_DIRECT_ECU,
            steps=[bytes([SID_SECURITY_ACCESS, 0x03]).hex()],
            ecus=default_ecus(),
        ),
        _case(
            "D6-wrong-key",
            "直连 ECU：seed 后发错误 key → 7F 27 35（failed_attempts=1）",
            path=PATH_DIRECT_ECU,
            steps=[REQUEST_SEED.hex(), {"wrong_key_for_previous_seed": True}],
            ecus=default_ecus(),
        ),
        _case(
            "D7-third-failure-lockout",
            "直连 ECU：连续三次错误 key → 35 / 35 / 36 并进入 lockout；随后 27 01 → 37",
            path=PATH_DIRECT_ECU,
            steps=[
                REQUEST_SEED.hex(),
                {"wrong_key_for_previous_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_previous_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_previous_seed": True},
                REQUEST_SEED.hex(),
            ],
            ecus=default_ecus(),
        ),
        _case(
            "D8-lockout-expires",
            "直连 ECU：锁定到期后再 27 01 → 恢复正常（failed_attempts 归零）",
            path=PATH_DIRECT_ECU,
            steps=[
                REQUEST_SEED.hex(),
                {"wrong_key_for_previous_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_previous_seed": True},
                REQUEST_SEED.hex(),
                {"wrong_key_for_previous_seed": True},
                {"advance_clock": 11.0},
                REQUEST_SEED.hex(),
            ],
            ecus=default_ecus(),
        ),
        _case(
            "D9-seed-expiry",
            "直连 ECU：seed 过期后 27 02 → 7F 27 24",
            path=PATH_DIRECT_ECU,
            steps=[
                REQUEST_SEED.hex(),
                {"advance_clock": 6.0},
                {"key_for_previous_seed": True},
            ],
            ecus=default_ecus(),
        ),
        _case(
            "D10-replay-consumed-seed",
            "直连 ECU：成功解锁后重放同一 key → 7F 27 24",
            path=PATH_DIRECT_ECU,
            steps=[
                REQUEST_SEED.hex(),
                {"key_for_previous_seed": True},
                {"key_for_previous_seed": True},
            ],
            ecus=default_ecus(),
        ),
        _case(
            "D11-seed-while-unlocked",
            "直连 ECU：已解锁时再 27 01 → 7F 27 22",
            path=PATH_DIRECT_ECU,
            steps=[REQUEST_SEED.hex(), {"key_for_previous_seed": True}, REQUEST_SEED.hex()],
            ecus=default_ecus(),
        ),
        _case(
            "D12-session-switch-relocks",
            "直连 ECU：解锁后切 extended → security_level 归 LOCKED，惩罚状态保留",
            path=PATH_DIRECT_ECU,
            steps=[
                REQUEST_SEED.hex(),
                {"key_for_previous_seed": True},
                bytes([SID_SESSION, uds_types.SESSION_EXTENDED]).hex(),
            ],
            ecus=default_ecus(),
        ),
        _case(
            "D13-ecu-reset-relocks",
            "直连 ECU：解锁后 0x11 reset → security_level 归 LOCKED（惩罚状态保留）",
            path=PATH_DIRECT_ECU,
            steps=[
                REQUEST_SEED.hex(),
                {"key_for_previous_seed": True},
                bytes([SID_ECU_RESET, 0x01]).hex(),
            ],
            ecus=default_ecus(),
        ),
        _case(
            "D14-protected-did-while-locked",
            "直连 ECU：未解锁读受保护 DID → 7F 22 33",
            path=PATH_DIRECT_ECU,
            steps=[bytes([SID_READ_DID, 0xF1, 0x87]).hex()],
            ecus=default_ecus(),
        ),
        _case(
            "D15-protected-did-after-unlock",
            "直连 ECU：解锁（需 extended）后读受保护 DID → 62 F1 87 …",
            path=PATH_DIRECT_ECU,
            steps=[
                bytes([SID_SESSION, uds_types.SESSION_EXTENDED]).hex(),
                REQUEST_SEED.hex(),
                {"key_for_previous_seed": True},
                bytes([SID_READ_DID, 0xF1, 0x87]).hex(),
            ],
            ecus=default_ecus(),
        ),
        # ==============================================================
        # 组 2 —— 当前 Gateway baseline（path = gateway，**C3 实现前事实**）
        # ==============================================================
        _case(
            "G1-physical-27-on-ecu-a",
            "Gateway 物理 0x7E0 27 01 → 当前 7F 27 11（C2 时代分层边界）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E0,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
        _case(
            "G2-response-id-mapping",
            "Gateway 物理 0x7E1 读公开 DID → 回应在 0x7E9（响应 ID 映射现状）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E1,
            steps=[bytes([SID_READ_DID, 0xF1, 0x90]).hex()],
            ecus=default_ecus(),
        ),
        _case(
            "G3-old-pin-explicit",
            "Gateway 物理 0x7E0 27 01 的**当前**取值（C3-B1 旧 pin 所断言的事实）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E0,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
        _case(
            "G4-unknown-physical-target",
            "Gateway 未知物理 target 0x7E5 27 01 → []（路由归属失败，不进入策略）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E5,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
        ),
        _case(
            "G5-functional-27",
            "Gateway 功能寻址 27 01 → []，且**当前**不触及任何 ECU 的 0x27 状态",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_FUNCTIONAL,
            target=None,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
        # ==============================================================
        # 组 3 —— Multi-ECU baseline
        # ==============================================================
        _case(
            "M1-physical-27-each-ecu",
            "Gateway 逐个物理寻址 0x7E0/0x7E1/0x7E2 发 27 01（当前均 7F 27 11）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E0,
            steps=[
                {"route_to": 0x7E0, "request": REQUEST_SEED.hex()},
                {"route_to": 0x7E1, "request": REQUEST_SEED.hex()},
                {"route_to": 0x7E2, "request": REQUEST_SEED.hex()},
            ],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
        _case(
            "M2-security-state-untouched-by-gateway-27",
            "Gateway 物理 27 01 后，三个 ECU 的 security state 全部保持初始值",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E0,
            steps=[REQUEST_SEED.hex(), REQUEST_SEED.hex()],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
        _case(
            "M3-functional-27-state-effect",
            "Gateway 功能寻址 27 01 对三个 ECU 安全状态的影响（当前：无）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_FUNCTIONAL,
            target=None,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
        _case(
            "M4-per-ecu-isolation-preserved",
            "Gateway 对 0x7E0 发 27 01 后，0x7E1/0x7E2 的状态不受影响（隔离现状）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E0,
            steps=[
                {"route_to": 0x7E0, "request": REQUEST_SEED.hex()},
                {"route_to": 0x7E1, "request": bytes([SID_READ_DID, 0xF1, 0x90]).hex()},
            ],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
        # ==============================================================
        # 组 4 —— C2 interaction baseline
        # ==============================================================
        _case(
            "C1-policy-allow-27",
            "policy allow（默认）下 Gateway 27 01 的当前行为",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E0,
            policy=POLICY_DEFAULT_ALLOW,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
        _case(
            "C2-policy-deny-27",
            "policy deny(0x7E1) 下对 0x7E1 发 27 01 → 静默丢弃（[]）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E1,
            policy=POLICY_DENY_TARGET_7E1,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
        ),
        _case(
            "C3-policy-deny-is-silent-drop",
            "deny 语义：无帧、不合成 NRC、目标 EcuState 不变（含 security state）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E1,
            policy=POLICY_DENY_TARGET_7E1,
            steps=[
                {"route_to": 0x7E1, "request": REQUEST_SEED.hex()},
                {"route_to": 0x7E1, "request": bytes([SID_READ_DID, 0xF1, 0x90]).hex()},
            ],
            ecus=default_ecus(),
        ),
        _case(
            "C4-policy-dimensions",
            "策略输入维度 baseline：(target, addressing_mode, SID) 三元组",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E0,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
        # ==============================================================
        # 组 5 —— Direct ECU ↔ Gateway 对照输入（**不要求相等**）
        # ==============================================================
        _case(
            "X1-same-request-both-paths",
            "同一请求在两条路径上的**实现前**结果对照（A0 不要求相等，只冻结现状）",
            path=PATH_GATEWAY,
            addressing=ADDRESSING_PHYSICAL,
            target=0x7E0,
            steps=[REQUEST_SEED.hex()],
            ecus=default_ecus(),
            frozen_boundary=FROZEN_BOUNDARY_GATEWAY_0X27_UNREACHABLE,
        ),
    ]


# ----------------------------------------------------------------------
# 执行（捕获与长期重放共用同一条路径）
# ----------------------------------------------------------------------


def _fresh_ecu_state(spec: dict) -> EcuState:
    state = EcuState()
    state.session = spec["session"]
    state.security_level = spec["security_level"]
    state.did_overrides = {
        int(did, 16): bytes.fromhex(value) for did, value in spec["did_overrides"].items()
    }
    return state


def _build_gateway(case: dict) -> DiagnosticGateway:
    ecus = [
        VirtualEcu(
            name=spec["name"],
            request_id=spec["request_id"],
            response_id=spec["response_id"],
            state=_fresh_ecu_state(spec),
        )
        for spec in case["ecus"]
    ]
    return DiagnosticGateway(ecus, policy=_POLICIES[case["policy"]])


def resolve_step_request(step, previous_seed: bytes | None) -> bytes:
    """
    把一步展开为具体请求字节。

    支持的字面量：hex 字符串、``{"route_to": …}``、以及随序列状态求值的
    ``{"key_for_previous_seed": True}`` / ``{"wrong_key_for_previous_seed": True}``。
    """
    if isinstance(step, str):
        return bytes.fromhex(step)
    if "request" in step:
        return bytes.fromhex(step["request"])
    if "key_for_previous_seed" in step:
        if previous_seed is None:  # pragma: no cover - case 定义错误
            raise ValueError("key_for_previous_seed 需要前一步已产生 seed")
        return bytes([SID_SECURITY_ACCESS, 0x02]) + keygen.derive_key(previous_seed)
    if "wrong_key_for_previous_seed" in step:
        if previous_seed is None:  # pragma: no cover - case 定义错误
            raise ValueError("wrong_key_for_previous_seed 需要前一步已产生 seed")
        correct = keygen.derive_key(previous_seed)
        return bytes([SID_SECURITY_ACCESS, 0x02]) + bytes(b ^ 0xFF for b in correct)
    raise ValueError(f"无法展开的 step：{step!r}")  # pragma: no cover


def route_target(case: dict, step) -> int:
    """一步的目标 CAN ID（functional 用功能地址）。"""
    if case["addressing"] == ADDRESSING_FUNCTIONAL:
        return FUNCTIONAL_ADDRESS
    if isinstance(step, dict) and "route_to" in step:
        return step["route_to"]
    return case["target"]


def execute_direct_ecu(case: dict) -> dict:
    """
    直连 ECU 路径：``ecu.handle_uds_request``（**唯一生产入口**）。

    每次执行前把 ``ecu.STATE`` 换成全新 ``EcuState``，并按 case 设定的
    ``clock`` 与 ``security_config`` 注入 —— 使 case 可独立、确定性地重放。
    """
    import ecu

    results = []
    previous_seed: bytes | None = None
    clock = case["clock"]

    ecu.STATE = _fresh_ecu_state(case["ecus"][0])
    ecu.SECURITY_CONFIG.update(BASELINE_SECURITY_CONFIG)

    for step in case["steps"]:
        if isinstance(step, dict) and "advance_clock" in step:
            clock += step["advance_clock"]
            continue

        request = resolve_step_request(step, previous_seed)
        with mock.patch.object(ecu, "_now", return_value=clock):
            response = ecu.handle_uds_request(request)

        if (
            request[0] == SID_SECURITY_ACCESS
            and len(response) == 2 + keygen.SEED_LENGTH
            and response[0] == 0x67
            and response[1] == 0x01
        ):
            previous_seed = response[2:]

        results.append({"request": request.hex(), "responses": [[None, response.hex()]]})

    return {
        "steps": results,
        "state": {"ECU": state_projection(ecu.STATE)},
        "security_state": {"ECU": security_state_projection(ecu.STATE)},
    }


def execute_gateway(case: dict) -> dict:
    """
    Gateway 路径：``DiagnosticGateway.route_with_trace``（生产 core + 显式 trace API）。

    每个 case 用**全新**网关（序列在同一个网关内推进）。
    """
    gateway = _build_gateway(case)
    results = []

    for step in case["steps"]:
        if isinstance(step, dict) and "advance_clock" in step:  # pragma: no cover
            continue

        request = resolve_step_request(step, None)
        can_id = route_target(case, step)
        routed, trace = gateway.route_with_trace(can_id, request)

        results.append(
            {
                "request": request.hex(),
                "can_id": can_id,
                "responses": [[rid, payload.hex()] for rid, payload in routed],
                "policy_decisions": [
                    {
                        "target": d.target,
                        "addressing_mode": d.addressing_mode,
                        "sid": d.sid,
                        "allowed": d.allowed,
                    }
                    for d in trace.decisions
                ],
            }
        )

    return {
        "steps": results,
        "state": {
            ecu.name: state_projection(ecu.state) for ecu in gateway.ecus
        },
        "security_state": {
            ecu.name: security_state_projection(ecu.state) for ecu in gateway.ecus
        },
    }


def execute(case: dict) -> dict:
    if case["path"] == PATH_DIRECT_ECU:
        return execute_direct_ecu(case)
    return execute_gateway(case)


# ----------------------------------------------------------------------
# 捕获护栏（fail-closed）
# ----------------------------------------------------------------------


#: C3-A1 接线标记 —— 一旦出现在 production 中，A0 的"实现前"前提即失效。
C3_IMPLEMENTATION_MARKERS = ("services_provider", "security_access_services")


def c3_implementation_present() -> bool:
    """
    **捕获护栏**：C3 实现是否已经出现在 production 代码中。

    C3-A0 的基线是"C3 **尚未**接线"时的行为快照。一旦 ``gateway.py`` 出现
    C3-A1 的接线标记（见 :data:`C3_IMPLEMENTATION_MARKERS`），当前路径
    **已不再是"实现前"** —— 此时再跑捕获会写出一份语义不同的 corpus，
    污染 C3-A1/A2 的对比基准。

    因此捕获脚本以本函数为 **fail-closed** 前提：返回 True 即**拒绝写入并 exit 2**。
    """
    source_path = os.path.join(_ECU_DIR, "gateway.py")
    with open(source_path, encoding="utf-8") as handle:
        source = handle.read()

    return any(marker in source for marker in C3_IMPLEMENTATION_MARKERS)


def git_commit() -> str:
    """捕获时的 git commit（provenance）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(_HERE),
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# ----------------------------------------------------------------------
# corpus 读写
# ----------------------------------------------------------------------


def build_corpus() -> dict:
    entries = []
    for case in build_cases():
        outcome = execute(case)
        entry = dict(case)
        entry["expected"] = outcome
        entries.append(entry)

    return {
        "schema_version": SCHEMA_VERSION,
        "description": "C3-A0 —— 0x27 SecurityAccess 实现前基线（direct ECU + gateway）",
        "captured_at_commit": git_commit(),
        "c2_design_baseline": "4cb85274b35ab52fa7df06a346f7afe495ab6714",
        "c3_implementation_present": False,
        "gateway_0x27_reachable": False,
        "functional_address": FUNCTIONAL_ADDRESS,
        "base_clock": BASE_CLOCK,
        "security_config": dict(BASELINE_SECURITY_CONFIG),
        "state_projection_fields": list(STATE_PROJECTION_FIELDS),
        "security_state_projection_fields": list(SECURITY_STATE_FIELDS),
        "cases": entries,
    }


def save_corpus(corpus: dict, path: str = CORPUS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(corpus, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_corpus(path: str = CORPUS_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
