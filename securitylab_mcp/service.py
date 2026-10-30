"""securitylab_ro 只读工具实现。

职责边界：

- 身份与 workspace 来自 ``Binding``（server 启动时由受信配置文件提供）；
- 每次调用独立检查工具 allowlist、身份字段、参数、root、输出上限；
- 不提供任意 shell/Python、命令拼接、sudo、写操作、仓库外访问或 Git 操作；
- ``securitylab_vcan`` 只保留 disabled schema，任何调用都在执行前返回
  ``tool-disabled``，不触碰 vcan/CAN/ECU/车辆/设备。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .binding import Binding, BindingError
from .config import (
    REPORT_ID_PATTERN,
    RESULT_ID_PATTERN,
    ResourceEntry,
    ServerConfig,
)
from .errors import (
    OUTPUT_LIMIT,
    TOOL_LAB_CATALOG,
    TOOL_LAB_GET_REPORT,
    TOOL_LAB_GET_RUN_RESULT,
    TOOL_LAB_READ_FILE,
    TOOL_LAB_STATUS,
    TOOL_NAMES,
    TOOL_SECURITYLAB_VCAN,
    compact_json_size,
    failure_result,
    success_result,
)
from .paths import (
    PathPolicyError,
    ResourceUnavailableError,
    SymlinkEscapeError,
    canonicalize,
    read_text_bounded,
    truncate_utf8,
)
from .redaction import redact_text

MAX_CATALOG_ITEMS: Final = 100

_IDENTITY_ARG_KEYS: Final = frozenset(
    {
        # contract §3：可信身份字段
        "context",
        "role",
        "workspace",
        "workspace_id",
        "episode_id",
        "run_id",
        "session_id",
        "tool_profile",
        "approval_ref",
        # 权限/策略字段
        "permissions",
        "permission",
        "scope",
        "allowed_tools",
        "preset",
        "preset_id",
        "preset_version",
        "profile",
        # contract §2：明确不提供的执行/远程能力
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

_EMPTY_INPUT_SCHEMA: Final = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}

_READ_FILE_INPUT_SCHEMA: Final = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string", "minLength": 1, "maxLength": 512},
        "root_id": {"enum": ["workspace", "reports", "artifacts"]},
    },
}

_GET_REPORT_INPUT_SCHEMA: Final = {
    "type": "object",
    "additionalProperties": False,
    "required": ["report_id"],
    "properties": {
        "report_id": {"type": "string", "pattern": "^rep_[A-Za-z0-9_-]{4,64}$"},
    },
}

_GET_RESULT_INPUT_SCHEMA: Final = {
    "type": "object",
    "additionalProperties": False,
    "required": ["result_id"],
    "properties": {
        "result_id": {"type": "string", "pattern": "^res_[A-Za-z0-9_-]{4,64}$"},
    },
}

_VCAN_INPUT_SCHEMA: Final = {
    "type": "object",
    "additionalProperties": False,
    "required": ["suite"],
    "properties": {"suite": {"enum": ["unit_smoke", "virtual_smoke"]}},
}

_TOOL_DESCRIPTIONS: Final = {
    TOOL_LAB_CATALOG: "List authorized SecurityLab resources and disabled capabilities.",
    TOOL_LAB_STATUS: "Read static read-only server status; does not probe the environment.",
    TOOL_LAB_READ_FILE: "Read a bounded, redacted UTF-8 file inside the bound root.",
    TOOL_LAB_GET_REPORT: "Read an existing report by report_id; never executes anything.",
    TOOL_LAB_GET_RUN_RESULT: "Read an existing run result by result_id; never re-runs it.",
    TOOL_SECURITYLAB_VCAN: (
        "[DISABLED] Fixed-suite virtual lab interface. Calls return tool-disabled "
        "and execute nothing."
    ),
}

_TOOL_SCHEMAS: Final = {
    TOOL_LAB_CATALOG: _EMPTY_INPUT_SCHEMA,
    TOOL_LAB_STATUS: _EMPTY_INPUT_SCHEMA,
    TOOL_LAB_READ_FILE: _READ_FILE_INPUT_SCHEMA,
    TOOL_LAB_GET_REPORT: _GET_REPORT_INPUT_SCHEMA,
    TOOL_LAB_GET_RUN_RESULT: _GET_RESULT_INPUT_SCHEMA,
    TOOL_SECURITYLAB_VCAN: _VCAN_INPUT_SCHEMA,
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    enabled: bool
    description: str
    input_schema: Mapping[str, Any]


class ReadOnlySecurityLabService:
    """单个受信 binding 对应的只读工具面。"""

    def __init__(
        self,
        config: ServerConfig,
        binding: Binding,
        *,
        protected_paths: Iterable[Path] = (),
    ) -> None:
        workspace = config.workspaces.get(binding.workspace_id)
        if workspace is None:
            raise BindingError("workspace is not registered")
        self.config = config
        self.binding = binding
        self.workspace = workspace
        # 受信 config/binding 文件本身不应通过 lab_read_file 暴露。
        self.protected_paths = tuple(canonicalize(path) for path in protected_paths)

    # ------------------------------------------------------------------
    # 工具清单
    # ------------------------------------------------------------------
    def list_tools(self) -> tuple[ToolSpec, ...]:
        specs: list[ToolSpec] = []
        for name in self.binding.allowed_tools:
            if name not in TOOL_NAMES:
                continue
            specs.append(
                ToolSpec(
                    name=name,
                    enabled=name != TOOL_SECURITYLAB_VCAN,
                    description=_TOOL_DESCRIPTIONS[name],
                    input_schema=_TOOL_SCHEMAS[name],
                )
            )
        return tuple(specs)

    # ------------------------------------------------------------------
    # 调用入口
    # ------------------------------------------------------------------
    def call_tool(self, tool: object, arguments: object) -> dict[str, Any]:
        if not isinstance(tool, str) or tool not in TOOL_NAMES:
            # contract 的 tool 枚举无法表示未知名称；使用占位工具名。
            return failure_result(TOOL_LAB_CATALOG, "tool-not-allowed", "unknown tool")

        if tool not in self.binding.allowed_tools:
            return failure_result(tool, "tool-not-allowed", "tool is not allowed for this role")

        if tool == TOOL_SECURITYLAB_VCAN:
            return failure_result(tool, "tool-disabled", "securitylab_vcan is disabled")

        if not isinstance(arguments, Mapping):
            return failure_result(tool, "invalid-args", "arguments must be an object")

        identity_key = _find_identity_key(arguments)
        if identity_key is not None:
            return failure_result(
                tool,
                "identity-in-args",
                "identity or permission fields are not allowed in arguments",
            )

        args_error = self._validate_arguments(tool, arguments)
        if args_error is not None:
            return failure_result(tool, "invalid-args", args_error)

        if tool == TOOL_LAB_CATALOG:
            return self._catalog()
        if tool == TOOL_LAB_STATUS:
            return self._status()
        if tool == TOOL_LAB_READ_FILE:
            return self._read_file(arguments)
        if tool == TOOL_LAB_GET_REPORT:
            return self._get_report(arguments)
        if tool == TOOL_LAB_GET_RUN_RESULT:
            return self._get_run_result(arguments)
        return failure_result(tool, "tool-not-allowed", "tool is not implemented")

    # ------------------------------------------------------------------
    # 参数校验
    # ------------------------------------------------------------------
    def _validate_arguments(self, tool: str, arguments: Mapping[Any, Any]) -> str | None:
        keys = set(arguments.keys())
        if tool in (TOOL_LAB_CATALOG, TOOL_LAB_STATUS):
            if keys:
                return f"{tool} takes no arguments"
            return None
        if tool == TOOL_LAB_READ_FILE:
            unknown = keys - {"path", "root_id"}
            if unknown:
                return "unknown argument"
            path = arguments.get("path")
            if not isinstance(path, str) or not path:
                return "path is required"
            root_id = arguments.get("root_id", "workspace")
            if root_id not in ("workspace", "reports", "artifacts"):
                return "root_id is invalid"
            return None
        if tool == TOOL_LAB_GET_REPORT:
            if keys != {"report_id"}:
                return "report_id is required"
            report_id = arguments.get("report_id")
            if not isinstance(report_id, str) or not REPORT_ID_PATTERN.fullmatch(report_id):
                return "report_id is invalid"
            return None
        if tool == TOOL_LAB_GET_RUN_RESULT:
            if keys != {"result_id"}:
                return "result_id is required"
            result_id = arguments.get("result_id")
            if not isinstance(result_id, str) or not RESULT_ID_PATTERN.fullmatch(result_id):
                return "result_id is invalid"
            return None
        return "tool is not implemented"

    # ------------------------------------------------------------------
    # 各工具
    # ------------------------------------------------------------------
    def _status(self) -> dict[str, Any]:
        data = {"status": "ready", "execution": "disabled", "vcan": "disabled"}
        return success_result(
            TOOL_LAB_STATUS,
            data,
            redacted=False,
            byte_count=compact_json_size(data),
            executed=True,
        )

    def _catalog(self) -> dict[str, Any]:
        resources: list[dict[str, str]] = []
        redacted = False
        for entry in self.workspace.reports.values():
            title, changed = redact_text(entry.title)
            redacted = redacted or changed
            resources.append(
                {"id": entry.resource_id, "kind": "report", "title": title}
            )
        for entry in self.workspace.run_results.values():
            title = entry.title
            if entry.status is not None:
                title = f"Run result ({entry.status})"
            title, changed = redact_text(title)
            redacted = redacted or changed
            resources.append(
                {"id": entry.resource_id, "kind": "run_result", "title": title}
            )
        resources.extend(
            [
                {"id": "workspace", "kind": "file_root", "title": "Workspace files"},
                {"id": "reports", "kind": "file_root", "title": "Reports"},
                {"id": "artifacts", "kind": "file_root", "title": "Artifacts"},
            ]
        )
        truncated = len(resources) > MAX_CATALOG_ITEMS
        bounded = resources[:MAX_CATALOG_ITEMS]
        data = {"resources": bounded, "truncated": truncated}
        byte_count = compact_json_size(data)
        if byte_count > OUTPUT_LIMIT:
            return failure_result(
                TOOL_LAB_CATALOG,
                "output-limit",
                "catalog cannot be returned within the output limit",
            )
        return success_result(
            TOOL_LAB_CATALOG,
            data,
            redacted=redacted,
            byte_count=byte_count,
            executed=True,
        )

    def _read_file(self, arguments: Mapping[Any, Any]) -> dict[str, Any]:
        path = arguments["path"]
        root_id = arguments.get("root_id", "workspace")
        try:
            root = self.workspace.root_for(str(root_id))
            content, byte_count, truncated, redacted = _read_present(
                root, str(path), self.protected_paths
            )
        except PathPolicyError:
            return failure_result(
                TOOL_LAB_READ_FILE,
                "path-out-of-root",
                "path is outside the bound root",
            )
        except SymlinkEscapeError:
            return failure_result(
                TOOL_LAB_READ_FILE,
                "symlink-escape",
                "resolved path escapes the bound root",
            )
        except ResourceUnavailableError:
            return failure_result(TOOL_LAB_READ_FILE, "not-found", "file is not available")
        except (TypeError, UnicodeError):
            return failure_result(
                TOOL_LAB_READ_FILE,
                "redaction-failed",
                "output redaction failed",
            )
        except OSError:
            return failure_result(TOOL_LAB_READ_FILE, "internal-error", "internal error")
        data = {"content": content, "bytes": byte_count, "truncated": truncated}
        return success_result(
            TOOL_LAB_READ_FILE,
            data,
            redacted=redacted,
            byte_count=byte_count,
            executed=True,
        )

    def _get_report(self, arguments: Mapping[Any, Any]) -> dict[str, Any]:
        report_id = str(arguments["report_id"])
        entry = self.workspace.reports.get(report_id)
        if entry is None:
            return failure_result(TOOL_LAB_GET_REPORT, "not-found", "report is not available")
        return self._resource_result(
            TOOL_LAB_GET_REPORT,
            self.workspace.reports_root,
            entry,
            id_key="report_id",
            include_status=False,
        )

    def _get_run_result(self, arguments: Mapping[Any, Any]) -> dict[str, Any]:
        result_id = str(arguments["result_id"])
        entry = self.workspace.run_results.get(result_id)
        if entry is None:
            return failure_result(
                TOOL_LAB_GET_RUN_RESULT, "not-found", "run result is not available"
            )
        return self._resource_result(
            TOOL_LAB_GET_RUN_RESULT,
            self.workspace.artifacts_root,
            entry,
            id_key="result_id",
            include_status=True,
        )

    def _resource_result(
        self,
        tool: str,
        root: Path,
        entry: ResourceEntry,
        *,
        id_key: str,
        include_status: bool,
    ) -> dict[str, Any]:
        try:
            content, byte_count, truncated, redacted = _read_present(
                root, entry.relative_path, self.protected_paths
            )
        except PathPolicyError:
            return failure_result(tool, "path-out-of-root", "path is outside the bound root")
        except SymlinkEscapeError:
            return failure_result(
                tool, "symlink-escape", "resolved path escapes the bound root"
            )
        except ResourceUnavailableError:
            return failure_result(tool, "not-found", "resource is not available")
        except (TypeError, UnicodeError):
            return failure_result(tool, "redaction-failed", "output redaction failed")
        except OSError:
            return failure_result(tool, "internal-error", "internal error")
        title, title_redacted = redact_text(entry.title)
        data: dict[str, Any] = {
            id_key: entry.resource_id,
            "title": title,
            "content": content,
            "bytes": byte_count,
            "truncated": truncated,
        }
        status_redacted = False
        if include_status and entry.status is not None:
            status, status_redacted = redact_text(entry.status)
            data["status"] = status
        return success_result(
            tool,
            data,
            redacted=redacted or title_redacted or status_redacted,
            byte_count=byte_count,
            executed=True,
        )


def _find_identity_key(arguments: Mapping[Any, Any]) -> str | None:
    for key in arguments:
        if isinstance(key, str) and key.lower() in _IDENTITY_ARG_KEYS:
            return key
    return None


def _read_present(
    root: Path,
    relative_path: str,
    protected_paths: Iterable[Path] = (),
) -> tuple[str, int, bool, bool]:
    bounded = read_text_bounded(
        root, relative_path, OUTPUT_LIMIT, protected_paths=protected_paths
    )
    redacted_text, redacted = redact_text(bounded.content)
    content, byte_count, capped = truncate_utf8(redacted_text, OUTPUT_LIMIT)
    return content, byte_count, bounded.truncated or capped, redacted
