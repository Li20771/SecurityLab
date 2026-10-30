"""SecurityLab MCP v1 —— stdio JSON-RPC server（只读）。

启动方式（由受信 host/Fleet 负责）::

    python -m securitylab_mcp.server \
        --config  <server-config.json> \
        --binding <trusted-binding.json>

传输：MCP 的 stdio 传输，每行一个 JSON-RPC 2.0 消息。stdout 只输出协议
消息；诊断信息只写 stderr。server 不读取环境变量中的身份，也不从
``tools/call`` 参数接受 role/workspace/run/session/tool_profile。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TextIO

from .binding import Binding, BindingError, binding_from_mapping
from .config import ConfigError, load_server_config
from .errors import (
    CONTRACT_VERSION,
    SERVER_RO,
    TOOL_LAB_CATALOG,
    TOOL_NAMES,
    failure_result,
)
from .service import ReadOnlySecurityLabService, ToolSpec

LATEST_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")

_JSONRPC_INVALID_REQUEST = -32600
_JSONRPC_METHOD_NOT_FOUND = -32601
_JSONRPC_INVALID_PARAMS = -32602
_JSONRPC_INTERNAL_ERROR = -32603
_JSONRPC_PARSE_ERROR = -32700

_CALL_PARAM_KEYS = frozenset({"name", "arguments", "_meta"})
_IDENTITY_PARAM_KEYS = frozenset(
    {
        "context",
        "role",
        "workspace",
        "workspace_id",
        "episode_id",
        "run_id",
        "session_id",
        "tool_profile",
        "approval_ref",
        "permissions",
        "permission",
        "scope",
        "allowed_tools",
        "preset",
        "preset_id",
        "preset_version",
        "profile",
        "command",
        "shell",
        "python",
        "script",
        "url",
        "target",
        "host",
        "sudo",
        "git",
        "env",
        "environment",
    }
)


class JsonRpcError(Exception):
    """携带 JSON-RPC error code 的协议级错误。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class McpSecurityLabServer:
    """把只读 service 暴露为 MCP tools。"""

    def __init__(self, service: ReadOnlySecurityLabService) -> None:
        self.service = service
        self.protocol_version = LATEST_PROTOCOL_VERSION

    # ------------------------------------------------------------------
    # JSON-RPC / MCP 分发
    # ------------------------------------------------------------------
    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, Mapping):
            return _error_response(None, _JSONRPC_INVALID_REQUEST, "invalid request")
        if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            if "id" not in message:
                return None
            return _error_response(
                _message_id(message), _JSONRPC_INVALID_REQUEST, "invalid request"
            )

        request_id = _message_id(message)
        is_notification = "id" not in message
        method = str(message["method"])
        params = message.get("params", {})

        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = self._tools_list()
            elif method == "tools/call":
                result = self._tools_call(params)
            elif method.startswith("notifications/"):
                return None
            else:
                raise JsonRpcError(_JSONRPC_METHOD_NOT_FOUND, "method not found")
        except JsonRpcError as exc:
            if is_notification:
                return None
            return _error_response(request_id, exc.code, exc.message)
        except Exception:
            if is_notification:
                return None
            return _error_response(request_id, _JSONRPC_INTERNAL_ERROR, "internal error")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _initialize(self, params: Any) -> dict[str, Any]:
        requested = params.get("protocolVersion") if isinstance(params, Mapping) else None
        version = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        self.protocol_version = str(version)
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_RO, "version": CONTRACT_VERSION},
        }

    def _tools_list(self) -> dict[str, Any]:
        return {"tools": [_tool_descriptor(spec) for spec in self.service.list_tools()]}

    def _tools_call(self, params: Any) -> dict[str, Any]:
        if not isinstance(params, Mapping):
            raise JsonRpcError(_JSONRPC_INVALID_PARAMS, "invalid params")
        extra_keys = set(params.keys()) - _CALL_PARAM_KEYS
        tool_name = params.get("name")
        if extra_keys:
            lowered = {str(key).lower() for key in extra_keys}
            code = (
                "identity-in-args"
                if lowered & _IDENTITY_PARAM_KEYS
                else "invalid-args"
            )
            envelope = _tool_error(tool_name, code, "invalid tools/call params")
            return _tool_result(
                envelope, structured=self.protocol_version == "2025-06-18"
            )
        if not isinstance(tool_name, str):
            raise JsonRpcError(_JSONRPC_INVALID_PARAMS, "invalid params")
        arguments = params.get("arguments", {})
        try:
            envelope = self.service.call_tool(tool_name, arguments)
        except Exception:
            envelope = _tool_error(tool_name, "internal-error", "internal error")
        return _tool_result(envelope, structured=self.protocol_version == "2025-06-18")


def _message_id(message: Mapping[str, Any]) -> Any:
    request_id = message.get("id")
    if isinstance(request_id, (str, int)) or request_id is None:
        return request_id
    return None


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_error(tool_name: Any, code: str, message: str) -> dict[str, Any]:
    if isinstance(tool_name, str) and tool_name in TOOL_NAMES:
        return failure_result(tool_name, code, message)
    return failure_result(TOOL_LAB_CATALOG, code, message)


def _tool_descriptor(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "inputSchema": spec.input_schema,
        "annotations": {
            "readOnlyHint": spec.enabled,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _tool_result(envelope: dict[str, Any], *, structured: bool) -> dict[str, Any]:
    text = json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": not bool(envelope.get("ok")),
    }
    if structured:
        result["structuredContent"] = envelope
    return result


# ----------------------------------------------------------------------
# stdio 传输
# ----------------------------------------------------------------------
def serve_stdio(
    server: McpSecurityLabServer,
    stdin: Iterable[str],
    stdout: TextIO,
) -> None:
    for line in stdin:
        if not line.strip():
            continue
        # 容忍部分 shell 管道在首行插入的 UTF-8 BOM。
        try:
            message = json.loads(line.lstrip(chr(0xFEFF)))
        except json.JSONDecodeError:
            _write_message(
                stdout,
                _error_response(None, _JSONRPC_PARSE_ERROR, "parse error"),
            )
            continue
        response = server.handle(message)
        if response is not None:
            _write_message(stdout, response)


def _write_message(stdout: TextIO, message: Mapping[str, Any]) -> None:
    stdout.write(json.dumps(message, ensure_ascii=True, separators=(",", ":")))
    stdout.write("\n")
    stdout.flush()


def _configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


def _load_binding(path: str) -> Binding:
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    return binding_from_mapping(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="securitylab_mcp.server",
        description="SecurityLab MCP v1 read-only stdio server",
    )
    parser.add_argument("--config", required=True, help="trusted server config JSON")
    parser.add_argument("--binding", required=True, help="trusted run/session binding JSON")
    parser.add_argument("--version", action="version", version=f"securitylab_mcp {CONTRACT_VERSION}")
    args = parser.parse_args(argv)

    _configure_stdio()
    try:
        config = load_server_config(Path(args.config))
        binding = _load_binding(args.binding)
        service = ReadOnlySecurityLabService(
            config,
            binding,
            protected_paths=(Path(args.config), Path(args.binding)),
        )
    except (ConfigError, BindingError) as exc:
        print(f"securitylab_mcp: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        print("securitylab_mcp: binding or config file cannot be read", file=sys.stderr)
        return 2

    try:
        serve_stdio(McpSecurityLabServer(service), sys.stdin, sys.stdout)
    except BrokenPipeError:
        return 0
    except Exception:
        print("securitylab_mcp: internal server error", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
