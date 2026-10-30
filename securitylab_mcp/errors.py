"""SecurityLab MCP v1 —— 错误码与结果信封。

本模块只定义**数据形状**，不做 I/O。信封字段与
``brief/contracts/securitylab-tool-result.v1.schema.json``（contract 1.0.0）
保持一致；本地镜像位于 ``securitylab_mcp/contracts/``。
"""

from __future__ import annotations

import json
from typing import Any, Final

CONTRACT_VERSION: Final = "1.0.0"

SERVER_RO: Final = "securitylab_ro"
SERVER_VCAN: Final = "securitylab_vcan"

TOOL_LAB_CATALOG: Final = "lab_catalog"
TOOL_LAB_STATUS: Final = "lab_status"
TOOL_LAB_READ_FILE: Final = "lab_read_file"
TOOL_LAB_GET_REPORT: Final = "lab_get_report"
TOOL_LAB_GET_RUN_RESULT: Final = "lab_get_run_result"
TOOL_SECURITYLAB_VCAN: Final = "securitylab_vcan"

#: 与 contract 的 tool 枚举逐项一致（顺序仅用于工具清单展示）。
TOOL_NAMES: Final = (
    TOOL_LAB_CATALOG,
    TOOL_LAB_STATUS,
    TOOL_LAB_READ_FILE,
    TOOL_LAB_GET_REPORT,
    TOOL_LAB_GET_RUN_RESULT,
    TOOL_SECURITYLAB_VCAN,
)

#: securitylab_ro 提供的只读工具。
READ_ONLY_TOOLS: Final = (
    TOOL_LAB_CATALOG,
    TOOL_LAB_STATUS,
    TOOL_LAB_READ_FILE,
    TOOL_LAB_GET_REPORT,
    TOOL_LAB_GET_RUN_RESULT,
)

#: 与 contract 的 error.code 枚举逐项一致。
ERROR_CODES: Final = (
    "tool-not-allowed",
    "tool-disabled",
    "identity-in-args",
    "invalid-args",
    "path-out-of-root",
    "symlink-escape",
    "cross-workspace-denied",
    "not-found",
    "output-limit",
    "redaction-failed",
    "internal-error",
)

OUTPUT_LIMIT: Final = 65536
MAX_ERROR_MESSAGE: Final = 512


def server_for_tool(tool: str) -> str:
    """返回工具所属的 server 标识。"""

    return SERVER_VCAN if tool == TOOL_SECURITYLAB_VCAN else SERVER_RO


def _base_meta(*, executed: bool, redacted: bool, byte_count: int) -> dict[str, Any]:
    return {
        "identity_source": "server-binding",
        "executed": executed,
        "side_effects": 0,
        "redacted": redacted,
        "output_limit": OUTPUT_LIMIT,
        "bytes": byte_count,
    }


def success_result(
    tool: str,
    data: dict[str, Any],
    *,
    redacted: bool,
    byte_count: int,
    executed: bool = True,
) -> dict[str, Any]:
    """构造成功信封。``tool`` 必须是 contract 中的已知工具。"""

    if tool not in TOOL_NAMES:
        raise ValueError("unknown tool in result envelope")
    return {
        "contract_version": CONTRACT_VERSION,
        "server": server_for_tool(tool),
        "tool": tool,
        "ok": True,
        "data": data,
        "error": None,
        "meta": _base_meta(executed=executed, redacted=redacted, byte_count=byte_count),
    }


def failure_result(
    tool: str,
    code: str,
    message: str,
    *,
    executed: bool = False,
) -> dict[str, Any]:
    """构造失败信封。

    未知工具名不能写进 contract 的 ``tool`` 枚举；调用方应传
    ``lab_catalog`` 作为占位（与 Workbench fake client 的行为一致）。
    """

    if tool not in TOOL_NAMES:
        raise ValueError("unknown tool in result envelope")
    if code not in ERROR_CODES:
        raise ValueError("unknown error code")
    safe_message = " ".join(str(message).split())[:MAX_ERROR_MESSAGE]
    if not safe_message:
        safe_message = "request rejected"
    return {
        "contract_version": CONTRACT_VERSION,
        "server": server_for_tool(tool),
        "tool": tool,
        "ok": False,
        "data": None,
        "error": {"code": code, "message": safe_message},
        "meta": _base_meta(executed=executed, redacted=False, byte_count=0),
    }


def compact_json_size(value: Any) -> int:
    """返回紧凑 JSON 的 UTF-8 字节数（仅用于 meta.bytes）。"""

    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(encoded.encode("utf-8"))
