#!/usr/bin/env python3
"""
C1 Phase 2 —— legacy golden corpus 共享基础设施（捕获 / 重放 / Schema）

设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md``

- **§13.2**：``GoldenCase`` 是**单请求**结构，**不含** ``sequence`` / ``steps``；
  所有前置条件由 ``initial_state`` 一次性给出。
- **§13.3**：JSON 存储，可 diff、可人工审计、可 code review。
- **§13.4**：golden **必须**在 ``dispatcher_mode == "legacy"`` 时捕获。
- **§13.5**：单请求不变式 —— 每条 case 使用**全新的 state 实例**，
  不得复用被前一条 case 修改过的 state。
- **§14.2**：``StateSnapshot`` 三字段投影，既是 ``initial_state`` 的格式，
  也是 final-state 的强制比较对象。

**A0 定位**：本模块当前只服务 0x22 的 **legacy 基准采集**（Migration A0）。
捕获脚本与重放测试都通过它读写同一份 corpus，确保二者永远是同一套语义。
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
from uds_server import (  # noqa: E402
    _SPEC_EXECUTION,
    EcuState,
    handle_uds_request,
)

#: corpus 文件位置（§13.3）。
GOLDEN_DIR = os.path.join(_HERE, "golden")
CORPUS_PATH = os.path.join(GOLDEN_DIR, "c1_phase2_0x22_legacy.json")

#: Schema 版本；不兼容变更时必须递增。
SCHEMA_VERSION = 1

#: legacy 模式的标志值（§13.4）。
LEGACY_DISPATCHER_MODE = "legacy"

#: §14.2 —— 强制比较的状态投影字段。
STATE_PROJECTION_FIELDS = ("session", "security_level", "did_overrides")

SID_READ = 0x22

# ----------------------------------------------------------------------
# 0x22 输入空间（**来源：真实实现 + 既有测试**，非凭空设计）
# ----------------------------------------------------------------------
#
# DID 取值来自 ``did_database.DID_DATABASE``：
#   0xF180 / 0xF190  公开可读
#   0xF187           PROTECTED_DIDS（需 SECURITY_LEVEL1）
#   0xF102           可写 DID（本实验里同样可读）
#   0xFFFF           不在库中 → 未知
#
# 长度规则来自 ``service_read_data_by_identifier`` 的首个判定：
#   ``len(request) < 3 or (len(request) - 1) % 2 != 0``  → 0x13
#
# 状态取值来自 ``uds_types``：三种会话 × 两种安全等级。

DID_PUBLIC_A = 0xF180
DID_PUBLIC_B = 0xF190
DID_PROTECTED = 0xF187
DID_WRITABLE = 0xF102
DID_UNKNOWN = 0xFFFF

#: 单 DID 覆盖：每个 DID 都要在**全部** 6 种状态组合下被记录。
SINGLE_DID_DIDS = (
    DID_PUBLIC_A,
    DID_PUBLIC_B,
    DID_PROTECTED,
    DID_WRITABLE,
    DID_UNKNOWN,
)

#: 仅用于证明"格式判定与状态无关"的两种代表状态。
FORMAT_STATES = (
    (uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
    (uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
)

#: 全部 6 种状态组合。
ALL_STATES = tuple(
    (session, security)
    for session in (
        uds_types.SESSION_DEFAULT,
        uds_types.SESSION_EXTENDED,
        uds_types.SESSION_PROGRAMMING,
    )
    for security in (uds_types.SECURITY_LOCKED, uds_types.SECURITY_LEVEL1)
)

#: 格式 / 长度非法请求（均违反 ``len>=3`` 或"DID 列表长度为偶数"）。
MALFORMED_REQUESTS = (
    (bytes([SID_READ]), "只有 SID，长度 1"),
    (bytes([SID_READ, 0xF1]), "长度 2，DID 不完整"),
    (bytes([SID_READ, 0xF1, 0x90, 0xF1]), "长度 4，DID 列表长度为奇数"),
    (bytes([SID_READ, 0xF1, 0x90, 0xF1, 0x80, 0xF1]), "长度 6，DID 列表长度为奇数"),
)

#: 多 DID 请求 —— 含两个**冻结锚点**（设计 §11.3 第 14 项）。
MULTI_DID_REQUESTS = (
    (bytes([SID_READ, 0xF1, 0x87, 0xFF, 0xFF]), "锚点：受保护在前 → 33"),
    (bytes([SID_READ, 0xFF, 0xFF, 0xF1, 0x87]), "锚点：未知在前 → 31"),
    (bytes([SID_READ, 0xF1, 0x90, 0xFF, 0xFF]), "公开 + 未知"),
    (bytes([SID_READ, 0xFF, 0xFF, 0xF1, 0x90]), "未知 + 公开"),
    (bytes([SID_READ, 0xF1, 0x90, 0xF1, 0x80]), "两个公开 DID"),
    (bytes([SID_READ, 0xF1, 0x80, 0xF1, 0x90]), "两个公开 DID（顺序相反）"),
    (bytes([SID_READ, 0xF1, 0x87, 0xF1, 0x87]), "同一受保护 DID 出现两次"),
    (bytes([SID_READ, 0xF1, 0x90, 0xF1, 0x87]), "公开在前、受保护在后"),
)

#: ``did_overrides`` 预置用例（§13.5：前置状态**只能**由 initial_state 构造）。
OVERRIDE_CASES = (
    (bytes([SID_READ, 0xF1, 0x02]), {DID_WRITABLE: b"CUSTOM-CFG"}, "覆盖可写 DID 的读值"),
    (bytes([SID_READ, 0xF1, 0x87]), {DID_PROTECTED: b"OVR-SPARE"}, "覆盖受保护 DID 的读值"),
    (
        bytes([SID_READ, 0xFF, 0xFF]),
        {DID_UNKNOWN: b"MADE-UP"},
        "未知 DID 经 override 变得可读",
    ),
)

OVERRIDE_STATES = (
    (uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
    (uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
)


# ----------------------------------------------------------------------
# StateSnapshot（§14.2）
# ----------------------------------------------------------------------


def snapshot(state: EcuState) -> dict:
    """把 ``EcuState`` 投影为 §14.2 的三字段快照（JSON 友好）。"""
    return {
        "session": state.session,
        "security_level": state.security_level,
        "did_overrides": {
            f"{did:04x}": value.hex() for did, value in sorted(state.did_overrides.items())
        },
    }


def build_state(initial: dict) -> EcuState:
    """
    由 ``initial_state`` 构造一个**全新**的 ``EcuState``（§13.5）。

    每条 case 都必须调用本函数拿到独立实例，避免复用被前一条 case 修改过的 state。
    """
    state = EcuState()
    state.session = initial["session"]
    state.security_level = initial["security_level"]
    state.did_overrides = {
        int(did, 16): bytes.fromhex(value)
        for did, value in initial.get("did_overrides", {}).items()
    }
    return state


# ----------------------------------------------------------------------
# case 定义
# ----------------------------------------------------------------------


def _case(case_id, description, request, session, security, overrides=None):
    return {
        "case_id": case_id,
        "description": description,
        "request": request,          # bytes，暂存；序列化时转 hex
        "initial_state": {
            "session": session,
            "security_level": security,
            "did_overrides": {
                f"{did:04x}": value.hex() for did, value in sorted((overrides or {}).items())
            },
        },
    }


def build_cases() -> list[dict]:
    """
    构造 0x22 的完整 case 定义（**不含 expected 值** —— 由捕获阶段实测填入）。

    分组：
      F  格式 / 长度边界
      S  单 DID × 全部 6 种状态
      M  多 DID（含顺序敏感的两个冻结锚点）
      O  ``did_overrides`` 预置
    """
    cases = []

    for index, (request, label) in enumerate(MALFORMED_REQUESTS):
        for session, security in FORMAT_STATES:
            cases.append(
                _case(
                    f"F{index + 1}-s{session:02x}-k{security:02x}",
                    f"格式：{label}",
                    request,
                    session,
                    security,
                )
            )

    for index, did in enumerate(SINGLE_DID_DIDS):
        for session, security in ALL_STATES:
            cases.append(
                _case(
                    f"S{index + 1}-{did:04x}-s{session:02x}-k{security:02x}",
                    f"单 DID 0x{did:04X}",
                    bytes([SID_READ, (did >> 8) & 0xFF, did & 0xFF]),
                    session,
                    security,
                )
            )

    for index, (request, label) in enumerate(MULTI_DID_REQUESTS):
        for session, security in ALL_STATES:
            cases.append(
                _case(
                    f"M{index + 1}-s{session:02x}-k{security:02x}",
                    f"多 DID：{label}",
                    request,
                    session,
                    security,
                )
            )

    for index, (request, overrides, label) in enumerate(OVERRIDE_CASES):
        for session, security in OVERRIDE_STATES:
            cases.append(
                _case(
                    f"O{index + 1}-s{session:02x}-k{security:02x}",
                    f"override：{label}",
                    request,
                    session,
                    security,
                    overrides,
                )
            )

    return cases


# ----------------------------------------------------------------------
# 执行（捕获与重放共用同一条路径）
# ----------------------------------------------------------------------


def execute(case: dict) -> tuple[bytes, dict]:
    """
    对**单条** case 执行一次请求，返回 ``(response, final_snapshot)``。

    走的是当前**实际生产路径** ``handle_uds_request``（§13 的 legacy oracle）。
    **不**调用 ``SERVICE_SPECS[0x22].guards`` —— 期望值必须来自可观测行为，
    而不是来自被验证对象自身。

    每次调用都用 ``build_state()`` 新建独立 state（§13.5）。
    """
    state = build_state(case["initial_state"])
    response = handle_uds_request(case["request"], state)
    return response, snapshot(state)


def spec_execution_state() -> list[int]:
    """当前 ``_SPEC_EXECUTION`` 的排序列表（供 corpus 头记录与断言）。"""
    return sorted(_SPEC_EXECUTION)


def git_commit() -> str:
    """捕获时的 git commit（§13.4 要求可追溯）。失败时返回 ``"unknown"``。"""
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


def serialize_case(case: dict, response: bytes, final_state: dict) -> dict:
    return {
        "case_id": case["case_id"],
        "description": case["description"],
        "request": case["request"].hex(),
        "initial_state": case["initial_state"],
        "legacy_response": response.hex(),
        "legacy_final_state": final_state,
    }


def deserialize_case(entry: dict) -> dict:
    """把 corpus 中的一条记录还原为可执行的 case（``request`` 转回 bytes）。"""
    return {
        "case_id": entry["case_id"],
        "description": entry["description"],
        "request": bytes.fromhex(entry["request"]),
        "initial_state": entry["initial_state"],
    }


def build_corpus() -> dict:
    """对全部 case 实测一遍，生成完整 corpus（**捕获阶段**使用）。"""
    entries = []
    for case in build_cases():
        response, final_state = execute(case)
        entries.append(serialize_case(case, response, final_state))

    return {
        "schema_version": SCHEMA_VERSION,
        "sid": SID_READ,
        "sid_hex": f"0x{SID_READ:02X}",
        "description": "C1 Phase 2 Migration A —— 0x22 legacy golden corpus",
        "captured_at_commit": git_commit(),
        "dispatcher_mode": LEGACY_DISPATCHER_MODE,
        "spec_execution_at_capture": spec_execution_state(),
        "state_projection_fields": list(STATE_PROJECTION_FIELDS),
        "cases": entries,
    }


def save_corpus(corpus: dict, path: str = CORPUS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(corpus, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def load_corpus(path: str = CORPUS_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
