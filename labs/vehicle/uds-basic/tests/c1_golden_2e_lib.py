#!/usr/bin/env python3
"""
C1 Phase 2 Migration **B0** —— 0x2E legacy golden corpus 的 case 定义与捕获

设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md``
§12（Migration B）/ §13（Golden Corpus）/ §13.5（单请求不变式）/ §14.2（StateSnapshot）。

**本模块只捕获，不做 spec 对拍。** 0x2E 的 spec 执行属 **B1**，
legacy ↔ spec 逐字节对拍属 **B2**。

## Oracle 路径（B0 最重要的一句话）

整个 dispatcher **并非**纯 legacy：``_SPEC_EXECUTION = {0x22}``，
0x22 已走 ServiceSpec guards。但本 corpus 的 **target SID = 0x2E**，
而 ``0x2E ∉ _SPEC_EXECUTION``，故：

    target SID 0x2E  →  _guards_for(0x2E) == ()  →  service_write_data_by_identifier()
                     →  **legacy oracle**

因此每个 B0 请求的 **target execution path = legacy handler**。
捕获脚本以 ``assert SID_WRITE not in _SPEC_EXECUTION`` 作为 fail-closed 护栏
（**不是** A0 的"整个白名单必须为空"——那样在 B0 已不成立）。

## 复用而非复制

``snapshot`` / ``build_state`` / ``execute`` / ``serialize_case`` /
``deserialize_case`` / ``git_commit`` 全部直接复用 ``c1_golden_lib`` 中
**与 SID 无关**的助手，**不修改** A0 已冻结的实现。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import c1_golden_lib as lib  # noqa: E402
import uds_types  # noqa: E402
from did_database import WRITABLE_DID_LENGTHS  # noqa: E402

# ----------------------------------------------------------------------
# corpus 身份
# ----------------------------------------------------------------------

#: 本 corpus 的 **target SID**（0x2E WriteDataByIdentifier）。
SID_WRITE = 0x2E

#: B0 corpus 路径（**独立文件**，不覆盖 A0 的 0x22 corpus）。
CORPUS_PATH = os.path.join(lib.GOLDEN_DIR, "c1_phase2_0x2e_legacy.json")

#: schema 版本。
#:
#: ``1`` = A0 的 0x22 corpus（**未改动**，其常量仍在 ``c1_golden_lib.SCHEMA_VERSION``）。
#: ``2`` = 本 B0 corpus：在 v1 的字段集上**新增**两个显式字段
#: ``target_sid`` / ``target_execution_mode``。
#:
#: 之所以升版而非"悄悄扩展 v1"：B0 时点整个 dispatcher **不是**纯 legacy
#: （0x22 已迁移），单看 ``dispatcher_mode`` 无法无歧义地表达
#: "本 corpus 的目标服务走的是哪条路径"。升版使该差异是**显式**的。
SCHEMA_VERSION = 2

#: target SID 的执行路径。B0 时点 0x2E 尚未迁移 → legacy。
TARGET_EXECUTION_MODE = lib.LEGACY_DISPATCHER_MODE  # "legacy"

# ----------------------------------------------------------------------
# 0x2E 输入空间（**来源：真实实现**，非凭空设计）
# ----------------------------------------------------------------------
#
# DID 取值全部来自 ``did_database``：
#   DID_DATABASE          = {0xF180, 0xF190, 0xF187, 0xF102}
#   WRITABLE_DIDS         = {0xF102}
#   WRITABLE_DID_LENGTHS  = {0xF102: (1, 16)}
#
# 六道 rung（``service_write_data_by_identifier``）：
#   ① len(request) < 4                        → 0x13
#   ② did not in DID_DATABASE                 → 0x31
#   ③ state.session == SESSION_DEFAULT        → 0x7F   （OD-5 冻结写法）
#   ④ state.security_level < SECURITY_LEVEL1  → 0x33
#   ⑤ did not in WRITABLE_DIDS                → 0x22
#   ⑥ len(data) ∉ WRITABLE_DID_LENGTHS[did]   → 0x13

DID_READONLY_VIN = 0xF190
DID_READONLY_BOOT = 0xF180
DID_READONLY_PROTECTED = 0xF187
DID_WRITABLE = 0xF102
DID_UNKNOWN_A = 0xFFFF
DID_UNKNOWN_B = 0x0000

WRITABLE_MIN_LEN, WRITABLE_MAX_LEN = WRITABLE_DID_LENGTHS[DID_WRITABLE]

#: 全部 6 种状态组合（三种会话 × 两种安全等级）。
ALL_STATES = tuple(
    (session, security)
    for session in (
        uds_types.SESSION_DEFAULT,
        uds_types.SESSION_EXTENDED,
        uds_types.SESSION_PROGRAMMING,
    )
    for security in (uds_types.SECURITY_LOCKED, uds_types.SECURITY_LEVEL1)
)

#: 能通过 rung③（非 DEFAULT）的状态 —— 用于抵达 rung④⑤⑥ 的用例。
NON_DEFAULT_STATES = tuple(
    (session, security)
    for session in (uds_types.SESSION_EXTENDED, uds_types.SESSION_PROGRAMMING)
    for security in (uds_types.SECURITY_LOCKED, uds_types.SECURITY_LEVEL1)
)

#: 同时通过 rung③④ 的状态 —— 用于抵达 rung⑤⑥ 与成功写入的用例。
UNLOCKED_STATES = tuple(
    (session, security)
    for session in (uds_types.SESSION_EXTENDED, uds_types.SESSION_PROGRAMMING)
    for security in (uds_types.SECURITY_LEVEL1,)
)

#: rung① 畸形长度：全部违反 ``len(request) < 4``。
MALFORMED_REQUESTS = (
    (bytes([SID_WRITE]), "只有 SID，长度 1"),
    (bytes([SID_WRITE, 0xF1]), "长度 2，DID 不完整"),
    (bytes([SID_WRITE, 0xF1, 0x02]), "长度 3，数据为空（仍违反 len>=4）"),
)

#: rung② 未知 DID：两个都不在 ``DID_DATABASE`` 中。
UNKNOWN_DID_REQUESTS = (
    (bytes([SID_WRITE, 0xFF, 0xFF]) + b"X", DID_UNKNOWN_A),
    (bytes([SID_WRITE, 0x00, 0x00]) + b"X", DID_UNKNOWN_B),
)

#: rung⑤ 已知但**不可写**的 DID（覆盖全部三个非可写 DID）。
READONLY_DIDS = (DID_READONLY_VIN, DID_READONLY_BOOT, DID_READONLY_PROTECTED)

#: rung⑥ 数据长度：闭区间 ``[1, 16]``。取上界/下界/越界。
DATA_LENGTH_PROBES = (
    (1, "下界（合法）"),
    (2, "合法"),
    (WRITABLE_MAX_LEN - 1, "上界前一位（合法）"),
    (WRITABLE_MAX_LEN, "上界（合法）"),
    (WRITABLE_MAX_LEN + 1, "越界（应 0x13）"),
)

#: ``did_overrides`` 预置用例（§13.5：前置状态**只能**由 initial_state 构造）。
OVERRIDE_CASES = (
    (
        bytes([SID_WRITE, 0xF1, 0x02]) + b"NEW",
        {DID_WRITABLE: b"OLD"},
        "可写 DID 已有覆盖 → 成功路径应改写为 NEW",
    ),
    (
        bytes([SID_WRITE, 0xFF, 0xFF]) + b"X",
        {DID_UNKNOWN_A: b"GHOST"},
        "未知 DID 的覆盖**不**使其可写（rung② 只看 DID_DATABASE）",
    ),
    (
        bytes([SID_WRITE, 0xF1, 0x90]) + b"X",
        {DID_READONLY_VIN: b"GHOST"},
        "只读 DID 的覆盖**不**使其可写（rung⑤ 只看 WRITABLE_DIDS）",
    ),
)


def _case(case_id, description, request, session, security, overrides=None):
    """构造一条单请求 case 定义（**不含 expected 值** —— 由捕获阶段实测填入）。"""
    return {
        "case_id": case_id,
        "description": description,
        "request": request,
        "initial_state": {
            "session": session,
            "security_level": security,
            "did_overrides": {
                f"{did:04x}": value.hex()
                for did, value in sorted((overrides or {}).items())
            },
        },
    }


def build_cases() -> list[dict]:
    """
    构造 0x2E 的完整 case 定义。

    分组（**单请求**，无 sequence / steps / setup request）：

      L  rung① 畸形长度
      D  rung② 未知 DID
      W  rung③④⑤⑥ 可写 DID × 全部 6 种状态（rung 矩阵）
      R  rung⑤ 已知但只读的 DID × 全部 6 种状态
      N  rung⑥ 数据长度边界（仅可达状态）
      O  did_overrides 预置与状态副作用
    """
    cases = []

    for index, (request, label) in enumerate(MALFORMED_REQUESTS):
        for session, security in ALL_STATES:
            cases.append(
                _case(
                    f"L{index + 1}-s{session:02x}-k{security:02x}",
                    f"rung① 长度：{label}",
                    request,
                    session,
                    security,
                )
            )

    for index, (request, did) in enumerate(UNKNOWN_DID_REQUESTS):
        for session, security in ALL_STATES:
            cases.append(
                _case(
                    f"D{index + 1}-s{session:02x}-k{security:02x}",
                    f"rung② 未知 DID 0x{did:04X}",
                    request,
                    session,
                    security,
                )
            )

    for session, security in ALL_STATES:
        cases.append(
            _case(
                f"W-s{session:02x}-k{security:02x}",
                "rung③④⑤⑥ 可写 DID 全状态矩阵",
                bytes([SID_WRITE, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF]) + b"X",
                session,
                security,
            )
        )

    for index, did in enumerate(READONLY_DIDS):
        for session, security in ALL_STATES:
            cases.append(
                _case(
                    f"R{index + 1}-{did:04x}-s{session:02x}-k{security:02x}",
                    f"rung⑤ 已知只读 DID 0x{did:04X}",
                    bytes([SID_WRITE, (did >> 8) & 0xFF, did & 0xFF]) + b"X",
                    session,
                    security,
                )
            )

    for length, label in DATA_LENGTH_PROBES:
        for session, security in UNLOCKED_STATES:
            cases.append(
                _case(
                    f"N{length:02d}-s{session:02x}-k{security:02x}",
                    f"rung⑥ 数据长度 {length}（{label}）",
                    bytes([SID_WRITE, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF])
                    + bytes([0x58] * length),
                    session,
                    security,
                )
            )

    for index, (request, overrides, label) in enumerate(OVERRIDE_CASES):
        for session, security in UNLOCKED_STATES:
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


def build_corpus() -> dict:
    """对全部 case 实测一遍，生成完整 B0 corpus（**捕获阶段**使用）。"""
    entries = []
    for case in build_cases():
        response, final_state = lib.execute(case)
        entries.append(lib.serialize_case(case, response, final_state))

    return {
        "schema_version": SCHEMA_VERSION,
        "sid": SID_WRITE,
        "sid_hex": f"0x{SID_WRITE:02X}",
        "target_sid": SID_WRITE,
        "target_execution_mode": TARGET_EXECUTION_MODE,
        "description": "C1 Phase 2 Migration B0 —— 0x2E legacy golden corpus",
        "captured_at_commit": lib.git_commit(),
        # 注意：B0 时点整个 dispatcher **不是**纯 legacy；
        # ``dispatcher_mode`` 描述的是**本 corpus 的 target SID** 的执行路径。
        # 真实白名单内容见 ``spec_execution_at_capture``。
        "dispatcher_mode": TARGET_EXECUTION_MODE,
        "spec_execution_at_capture": lib.spec_execution_state(),
        "state_projection_fields": list(lib.STATE_PROJECTION_FIELDS),
        "cases": entries,
    }


def save_corpus(corpus: dict, path: str = CORPUS_PATH) -> None:
    lib.save_corpus(corpus, path)


def load_corpus(path: str = CORPUS_PATH) -> dict:
    return lib.load_corpus(path)
