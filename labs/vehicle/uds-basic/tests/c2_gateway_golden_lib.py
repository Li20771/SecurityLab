#!/usr/bin/env python3
"""
C2-A0 —— Legacy Gateway golden corpus 的 case 定义与捕获

设计出处：``docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md``
§3（当前 Gateway 行为）/ §13（迁移策略：C2-A0 无 policy trace 要求）。

## 本模块只捕获 **legacy** 行为

**A0 在 C2 policy 机制引入之前运行**，因此捕获到的
``DiagnosticGateway.route()`` 行为就是**策略尚不存在时**的真实可观测行为。

**不实现 policy、不实现 ``route_with_trace()``、不修改任何生产代码。**

## 单请求不变式

一个 ``GoldenCase`` = **恰好一次** ``route(can_id, payload)``。

* **无** sequence / steps / 多请求场景；
* 每个 case **自带完整 `ecus` 初始配置**（含各自的 `EcuState`），
  因此任何 case 都可**独立 replay**，不依赖前一个 case 的执行结果。

## GatewayStateSnapshot（设计文档 §13）

```
对**每个** ECU 记录：
    (name, session, security_level, frozenset(did_overrides.items()))
```

只比较 ``route()`` 返回值**不足以**发现 **错误扇出 / 漏扇出 / 状态泄漏 /
ECU 间状态污染** —— 因此逐 ECU 的最终状态是 Golden 的另一半。
"""

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
if _ECU_DIR not in sys.path:
    sys.path.insert(0, _ECU_DIR)

import uds_types  # noqa: E402
from gateway import (  # noqa: E402
    DEFAULT_ECUS,
    FUNCTIONAL_ADDRESS,
    DiagnosticGateway,
    VirtualEcu,
)
from uds_server import EcuState  # noqa: E402

#: corpus 文件位置。
GOLDEN_DIR = os.path.join(_HERE, "golden")
CORPUS_PATH = os.path.join(GOLDEN_DIR, "c2_gateway_legacy.json")

#: schema 版本（C2-A0 首次建立，独立于 C1 的 corpus）。
SCHEMA_VERSION = 1

#: 捕获时的 Gateway 模式（设计文档 §13：A0 无 policy trace 要求）。
GATEWAY_MODE_LEGACY = "legacy"

#: 捕获时 policy 机制**尚不存在** —— 这是 A0 Golden 的**前提**。
POLICY_EXECUTION_ABSENT = "absent"

#: 逐 ECU 状态投影字段（设计文档 §13）。
STATE_PROJECTION_FIELDS = ("name", "session", "security_level", "did_overrides")

#: 自定义 handler 的**具名种类**（lambda 不可序列化，故用具名 kind + 确定性实现）。
HANDLER_DEFAULT = "default"
HANDLER_ECHO_77 = "echo_77"

#: SID / DID 常量（与既有测试一致）。
SID_READ = 0x22
SID_WRITE = 0x2E
SID_TESTER_PRESENT = 0x3E

DID_VIN = 0xF190
DID_PROTECTED = 0xF187
DID_WRITABLE = 0xF102

SESSION_DEFAULT = uds_types.SESSION_DEFAULT
SESSION_EXTENDED = uds_types.SESSION_EXTENDED
SECURITY_LOCKED = uds_types.SECURITY_LOCKED
SECURITY_LEVEL1 = uds_types.SECURITY_LEVEL1


def echo_77_handler(payload: bytes) -> bytes:
    """确定性自定义 handler（与 ``test_gateway_unit.py`` 的用法同构）。"""
    return bytes([0x77]) + payload


def _handler_for(kind: str):
    if kind == HANDLER_ECHO_77:
        return echo_77_handler
    return None  # None ⇒ VirtualEcu 绑定默认协议层 dispatcher


# ----------------------------------------------------------------------
# 状态
# ----------------------------------------------------------------------


def _state_spec(session=SESSION_DEFAULT, security=SECURITY_LOCKED, overrides=None) -> dict:
    return {
        "session": session,
        "security_level": security,
        "did_overrides": {
            f"{did:04x}": value.hex() for did, value in sorted((overrides or {}).items())
        },
    }


def _ecu_spec(name, request_id, response_id, handler=HANDLER_DEFAULT, **state) -> dict:
    return {
        "name": name,
        "request_id": request_id,
        "response_id": response_id,
        "handler": handler,
        "state": _state_spec(**state),
    }


def _default_ecus(**per_ecu) -> list[dict]:
    """
    默认三 ECU 拓扑（``DEFAULT_ECUS``），可对指定 ECU 覆盖状态。

    ``per_ecu`` 形如 ``ECU_A=dict(session=…, security=…, overrides={…})``。
    """
    ecus = []
    for name, request_id, response_id in DEFAULT_ECUS:
        ecus.append(_ecu_spec(name, request_id, response_id, **per_ecu.get(name, {})))
    return ecus


def build_gateway(case: dict) -> DiagnosticGateway:
    """
    由 case 的 ``ecus`` 规格构造**全新**的 ``DiagnosticGateway``。

    每个 case 独立构造 ⇒ 互不污染 ⇒ 可独立 replay（单请求不变式）。
    """
    ecus = []
    for spec in case["ecus"]:
        state = EcuState()
        state.session = spec["state"]["session"]
        state.security_level = spec["state"]["security_level"]
        state.did_overrides = {
            int(did, 16): bytes.fromhex(value)
            for did, value in spec["state"]["did_overrides"].items()
        }
        ecus.append(
            VirtualEcu(
                name=spec["name"],
                request_id=spec["request_id"],
                response_id=spec["response_id"],
                state=state,
                handler=_handler_for(spec["handler"]),
            )
        )
    return DiagnosticGateway(ecus)


# ----------------------------------------------------------------------
# GatewayStateSnapshot
# ----------------------------------------------------------------------


def gateway_snapshot(gw: DiagnosticGateway) -> dict:
    """
    逐 ECU 的三字段状态投影（设计文档 §13）。

    ``did_overrides`` 规范化为按 DID 排序的 ``{hex: hex}`` ——
    **不依赖 dict 插入顺序**，保证"同逻辑状态 ⇒ 同 Golden 字节"。
    """
    return {
        ecu.name: {
            "session": ecu.state.session,
            "security_level": ecu.state.security_level,
            "did_overrides": {
                f"{did:04x}": value.hex()
                for did, value in sorted(ecu.state.did_overrides.items())
            },
        }
        for ecu in gw.ecus
    }


# ----------------------------------------------------------------------
# case 定义
# ----------------------------------------------------------------------


def _case(case_id, description, can_id, payload, ecus) -> dict:
    return {
        "case_id": case_id,
        "description": description,
        "can_id": can_id,
        "payload": payload,
        "ecus": ecus,
    }


UNKNOWN_ADDRESS_A = 0x7E5
UNKNOWN_ADDRESS_B = 0x7E3


def build_cases() -> list[dict]:
    """
    C2-A0 的 Gateway routing case 矩阵（**单请求**，每 case 自带完整 ECU 配置）。

    分类见设计文档 §13 的 Golden 覆盖要求与 A0 的 case 分类：

      P  物理寻址（三个 ECU 各自的 response_id）
      N  负响应转发（**Gateway 放行、ECU 拒绝** —— 不是 policy deny）
      S  suppress（空响应不发帧）
      U  未知物理 target（**路由归属失败**，不是 policy deny）
      F  功能寻址（广播扇出，恒不收集响应）
      W  可写 DID 写入
      I  多 ECU 状态隔离
      M  畸形 / 空 payload
      C  自定义 handler / 自定义拓扑（override 基线）
      D  默认状态下的无副作用服务
    """
    unlocked = dict(session=SESSION_EXTENDED, security=SECURITY_LEVEL1)

    return [
        # ---- P 物理寻址：三个 ECU 各自的 response_id ----
        _case(
            "P1-physical-ecu-a", "物理寻址 ECU_A：公开 DID 读",
            0x7E0, bytes([SID_READ, 0xF1, 0x90]), _default_ecus(),
        ),
        _case(
            "P2-physical-ecu-b", "物理寻址 ECU_B：公开 DID 读",
            0x7E1, bytes([SID_READ, 0xF1, 0x90]), _default_ecus(),
        ),
        _case(
            "P3-physical-ecu-c", "物理寻址 ECU_C：公开 DID 读",
            0x7E2, bytes([SID_READ, 0xF1, 0x90]), _default_ecus(),
        ),

        # ---- N 负响应转发（Gateway 放行；ECU 自行拒绝 —— 非 policy deny）----
        _case(
            "N1-unknown-did", "物理寻址：ECU 返回 0x31（未知 DID）",
            0x7E0, bytes([SID_READ, 0xFF, 0xFF]), _default_ecus(),
        ),
        _case(
            "N2-protected-did-locked", "物理寻址：受保护 DID 在 LOCKED 下返回 0x33",
            0x7E0, bytes([SID_READ, 0xF1, 0x87]), _default_ecus(),
        ),

        # ---- S suppress ----
        _case(
            "S1-physical-suppress", "物理寻址：3E 80 抑制正响应 → 不发帧",
            0x7E0, bytes([SID_TESTER_PRESENT, 0x80]), _default_ecus(),
        ),

        # ---- U 未知物理 target（路由归属失败）----
        _case(
            "U1-unknown-target-7e5", "未知物理 target 0x7E5 → 静默丢弃",
            UNKNOWN_ADDRESS_A, bytes([SID_TESTER_PRESENT, 0x00]), _default_ecus(),
        ),
        _case(
            "U2-unknown-target-7e3", "未知物理 target 0x7E3（公开 DID 读）→ 静默丢弃",
            UNKNOWN_ADDRESS_B, bytes([SID_READ, 0xF1, 0x90]), _default_ecus(),
        ),

        # ---- F 功能寻址（广播）----
        _case(
            "F1-functional-session", "功能寻址 10 03：三个 ECU 会话均切到 EXTENDED",
            FUNCTIONAL_ADDRESS, bytes([0x10, 0x03]), _default_ecus(),
        ),
        _case(
            "F2-functional-read", "功能寻址 22 F1 90：每个 ECU 都执行，但网关不回帧",
            FUNCTIONAL_ADDRESS, bytes([SID_READ, 0xF1, 0x90]), _default_ecus(),
        ),
        _case(
            "F3-functional-suppress", "功能寻址 3E 80：每个 ECU 抑制响应，网关仍静默",
            FUNCTIONAL_ADDRESS, bytes([SID_TESTER_PRESENT, 0x80]), _default_ecus(),
        ),
        _case(
            "F4-functional-write", "功能寻址 2E F1 02：三个 ECU 的 did_overrides 均被写入",
            FUNCTIONAL_ADDRESS, bytes([SID_WRITE, 0xF1, 0x02]) + b"BC",
            _default_ecus(ECU_A=unlocked, ECU_B=unlocked, ECU_C=unlocked),
        ),

        # ---- W 可写 DID ----
        _case(
            "W1-physical-write", "物理寻址 ECU_A：写可写 DID（仅 A 的状态变化）",
            0x7E0, bytes([SID_WRITE, 0xF1, 0x02]) + b"OK",
            _default_ecus(ECU_A=unlocked),
        ),

        # ---- I 多 ECU 状态隔离 ----
        _case(
            "I1-override-scoped-to-ecu-a",
            "ECU_A 预置 override；读 ECU_B 的同名 DID → 不得读到 A 的值",
            0x7E1, bytes([SID_READ, 0xF1, 0x02]),
            _default_ecus(ECU_A=dict(overrides={DID_WRITABLE: b"AAA"})),
        ),
        _case(
            "I2-write-scoped-to-ecu-a",
            "写 ECU_A；ECU_B/C 各有自己的 override → B/C 的值不得被改动",
            0x7E0, bytes([SID_WRITE, 0xF1, 0x02]) + b"NEW",
            _default_ecus(
                ECU_A=unlocked,
                ECU_B=dict(session=SESSION_EXTENDED, security=SECURITY_LEVEL1,
                           overrides={DID_WRITABLE: b"BBB"}),
                ECU_C=dict(session=SESSION_EXTENDED, security=SECURITY_LEVEL1,
                           overrides={DID_WRITABLE: b"CCC"}),
            ),
        ),

        # ---- M 畸形 / 空 payload（Gateway 不预分类）----
        _case(
            "M1-empty-payload", "空 payload → 交给协议层（不进入任何 Gateway 判定）",
            0x7E0, b"", _default_ecus(),
        ),
        _case(
            "M2-unregistered-sid", "SID 0x00 未注册 → 协议层返回 0x11",
            0x7E0, bytes([0x00]), _default_ecus(),
        ),

        # ---- C 自定义 handler / 自定义拓扑（override 基线）----
        _case(
            "C1-custom-handler-in-default-topology",
            "默认三 ECU 拓扑中，ECU_B 注入自定义 handler（只影响 B）",
            0x7E1, b"\x01\x02",
            [
                _ecu_spec("ECU_A", 0x7E0, 0x7E8),
                _ecu_spec("ECU_B", 0x7E1, 0x7E9, handler=HANDLER_ECHO_77),
                _ecu_spec("ECU_C", 0x7E2, 0x7EA),
            ],
        ),
        _case(
            "C2-custom-topology-single-ecu",
            "自定义单 ECU 拓扑（0x700/0x708）+ 自定义 handler"
            "（与 test_gateway_unit.py 的既有用法同构）",
            0x700, b"\x01\x02",
            [_ecu_spec("X", 0x700, 0x708, handler=HANDLER_ECHO_77)],
        ),

        # ---- D 无副作用服务 ----
        _case(
            "D1-tester-present", "物理寻址 3E 00：正响应且不改变任何状态",
            0x7E0, bytes([SID_TESTER_PRESENT, 0x00]), _default_ecus(),
        ),
    ]


# ----------------------------------------------------------------------
# 执行（捕获与未来 replay 共用同一条路径）
# ----------------------------------------------------------------------


def execute(case: dict) -> tuple[list, dict]:
    """
    对**单条** case 执行**恰好一次** ``route()``，返回
    ``(response, final_gateway_snapshot)``。

    每次调用都用 ``build_gateway(case)`` **新建**网关（单请求不变式）。
    """
    gw = build_gateway(case)
    results = gw.route(case["can_id"], case["payload"])
    return results, gateway_snapshot(gw)


def route_result_to_json(results: list) -> list:
    """``[(response_id, payload)]`` → ``[[response_id, payload_hex], …]``。"""
    return [[response_id, payload.hex()] for response_id, payload in results]


def policy_execution_present() -> bool:
    """
    A0 的 fail-closed 前提：**C2 policy 机制是否已经出现**。

    当前生产代码中不存在任何 Gateway policy 机制；若将来引入
    （例如 `gateway.py` 出现 policy 相关符号），本函数返回 True，
    捕获脚本据此**拒绝**重新生成 legacy Golden。
    """
    source_path = os.path.join(_ECU_DIR, "gateway.py")
    with open(source_path, encoding="utf-8") as handle:
        source = handle.read()

    markers = ("PolicyTrace", "route_with_trace", "policy_evaluate", "GatewayPolicy")
    return any(marker in source for marker in markers)


def git_commit() -> str:
    """捕获时的 git commit（provenance 的核心，见设计文档 §13）。"""
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


def serialize_case(case: dict, response: list, final_state: dict) -> dict:
    return {
        "case_id": case["case_id"],
        "description": case["description"],
        "can_id": case["can_id"],
        "payload": case["payload"].hex(),
        "ecus": case["ecus"],
        "expected_response": route_result_to_json(response),
        "expected_gateway_state": final_state,
    }


def deserialize_case(entry: dict) -> dict:
    """把 corpus 中的一条记录还原为可执行 case（``payload`` 转回 bytes）。"""
    return {
        "case_id": entry["case_id"],
        "description": entry["description"],
        "can_id": entry["can_id"],
        "payload": bytes.fromhex(entry["payload"]),
        "ecus": entry["ecus"],
    }


def build_corpus() -> dict:
    """对全部 case 实测一遍，生成完整 corpus（**捕获阶段**使用）。"""
    entries = []
    for case in build_cases():
        response, final_state = execute(case)
        entries.append(serialize_case(case, response, final_state))

    return {
        "schema_version": SCHEMA_VERSION,
        "description": "C2-A0 —— legacy Gateway golden corpus",
        "captured_at_commit": git_commit(),
        "gateway_mode": GATEWAY_MODE_LEGACY,
        "policy_execution": POLICY_EXECUTION_ABSENT,
        "functional_address": FUNCTIONAL_ADDRESS,
        "state_projection_fields": list(STATE_PROJECTION_FIELDS),
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
