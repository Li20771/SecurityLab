"""SecurityLab MCP server 的受信配置加载。

配置是 **server 启动参数**，不是模型输入。它定义：

- 允许接入的 workspace（每个 workspace 固定 workspace/reports/artifacts root）；
- report / run-result ID 到 root 相对路径的显式索引。

所有 root 在加载期做 ``realpath`` + containment 校验；workspace root 之间
不得互相包含；资源索引路径必须通过统一的相对路径校验且不得指向凭据路径。
任何非法/未知字段/版本不符都 fail closed（``ConfigError``），server 不启动。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from .binding import WORKSPACE_ID_PATTERN
from .errors import CONTRACT_VERSION, SERVER_RO
from .paths import (
    PathPolicyError,
    SymlinkEscapeError,
    canonicalize,
    is_sensitive_path,
    is_within,
    resolve_contained,
    sanitize_relative_path,
)

REPORT_ID_PATTERN: Final = re.compile(r"^rep_[A-Za-z0-9_-]{4,64}$")
RESULT_ID_PATTERN: Final = re.compile(r"^res_[A-Za-z0-9_-]{4,64}$")

MAX_TITLE_LENGTH: Final = 200
MAX_STATUS_LENGTH: Final = 64
MAX_RESOURCES_PER_KIND: Final = 1000
MAX_ROOT_STRING_LENGTH: Final = 4096

_WORKSPACE_KEYS: Final = frozenset(
    {"workspace_root", "reports_root", "artifacts_root", "reports", "run_results"}
)
_CONFIG_KEYS: Final = frozenset({"contract_version", "server", "workspaces"})
_RESOURCE_KEYS: Final = frozenset({"title", "path", "status"})


class ConfigError(ValueError):
    """配置缺失、非法或版本不符；消息不得包含绝对路径。"""


@dataclass(frozen=True)
class ResourceEntry:
    resource_id: str
    title: str
    relative_path: str
    status: str | None = None


@dataclass(frozen=True)
class WorkspaceConfig:
    workspace_id: str
    workspace_root: Path
    reports_root: Path
    artifacts_root: Path
    reports: Mapping[str, ResourceEntry]
    run_results: Mapping[str, ResourceEntry]

    def root_for(self, root_id: str) -> Path:
        if root_id == "workspace":
            return self.workspace_root
        if root_id == "reports":
            return self.reports_root
        if root_id == "artifacts":
            return self.artifacts_root
        raise KeyError(root_id)


@dataclass(frozen=True)
class ServerConfig:
    contract_version: str
    server: str
    workspaces: Mapping[str, WorkspaceConfig]


def _require_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{label} must be an object")
    return value


def _require_str(value: Any, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} must be a non-empty string")
    if len(value) > max_length:
        raise ConfigError(f"{label} is too long")
    if "\x00" in value or any(ord(char) < 0x20 for char in value):
        raise ConfigError(f"{label} contains control characters")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ConfigError(f"{label} is not valid Unicode")
    return value


def _resolve_root(base_dir: Path, value: Any, label: str) -> Path:
    text = _require_str(value, label, max_length=MAX_ROOT_STRING_LENGTH)
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = base_dir.joinpath(candidate)
    return canonicalize(candidate)


def _parse_resource_entry(
    resource_id: Any,
    raw: Any,
    *,
    root: Path,
    id_pattern: re.Pattern[str],
    label: str,
) -> ResourceEntry:
    if not isinstance(resource_id, str) or not id_pattern.fullmatch(resource_id):
        raise ConfigError(f"{label} id is invalid")
    entry = _require_object(raw, f"{label} '{resource_id}'")
    if not set(entry.keys()).issubset(_RESOURCE_KEYS):
        raise ConfigError(f"{label} '{resource_id}' contains unknown fields")
    title = _require_str(entry.get("title"), f"{label} title", max_length=MAX_TITLE_LENGTH)
    relative = _require_str(
        entry.get("path"), f"{label} path", max_length=512
    )
    status = entry.get("status")
    if status is not None:
        status = _require_str(status, f"{label} status", max_length=MAX_STATUS_LENGTH)
    try:
        relative = sanitize_relative_path(relative)
        if is_sensitive_path(relative):
            raise ConfigError(f"{label} path points to a protected location")
        resolve_contained(root, relative)
    except ConfigError:
        raise
    except (PathPolicyError, SymlinkEscapeError) as exc:
        raise ConfigError(f"{label} path is not contained in its root") from exc
    return ResourceEntry(
        resource_id=resource_id,
        title=title,
        relative_path=relative,
        status=status,
    )


def _parse_resource_map(
    raw: Any,
    *,
    root: Path,
    id_pattern: re.Pattern[str],
    label: str,
) -> Mapping[str, ResourceEntry]:
    mapping = _require_object(raw, label)
    if len(mapping) > MAX_RESOURCES_PER_KIND:
        raise ConfigError(f"{label} has too many entries")
    parsed: dict[str, ResourceEntry] = {}
    for resource_id, entry in mapping.items():
        parsed[str(resource_id)] = _parse_resource_entry(
            resource_id,
            entry,
            root=root,
            id_pattern=id_pattern,
            label=label,
        )
    return MappingProxyType(parsed)


def _parse_workspace(workspace_id: Any, raw: Any, base_dir: Path) -> WorkspaceConfig:
    if not isinstance(workspace_id, str) or not WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise ConfigError("workspace id is invalid")
    entry = _require_object(raw, f"workspace '{workspace_id}'")
    if not set(entry.keys()).issubset(_WORKSPACE_KEYS):
        raise ConfigError(f"workspace '{workspace_id}' contains unknown fields")
    if "workspace_root" not in entry:
        raise ConfigError(f"workspace '{workspace_id}' is missing workspace_root")
    workspace_root = _resolve_root(
        base_dir, entry["workspace_root"], f"workspace '{workspace_id}' root"
    )
    if not workspace_root.is_dir():
        raise ConfigError(f"workspace '{workspace_id}' root is not a directory")

    reports_root = _resolve_root(
        workspace_root,
        entry.get("reports_root", "reports"),
        f"workspace '{workspace_id}' reports root",
    )
    artifacts_root = _resolve_root(
        workspace_root,
        entry.get("artifacts_root", "artifacts"),
        f"workspace '{workspace_id}' artifacts root",
    )
    for label, root in (("reports_root", reports_root), ("artifacts_root", artifacts_root)):
        if not is_within(workspace_root, root):
            raise ConfigError(f"workspace '{workspace_id}' {label} escapes the workspace root")
        if root.exists() and not root.is_dir():
            raise ConfigError(f"workspace '{workspace_id}' {label} is not a directory")

    reports = _parse_resource_map(
        entry.get("reports", {}),
        root=reports_root,
        id_pattern=REPORT_ID_PATTERN,
        label=f"workspace '{workspace_id}' report",
    )
    run_results = _parse_resource_map(
        entry.get("run_results", {}),
        root=artifacts_root,
        id_pattern=RESULT_ID_PATTERN,
        label=f"workspace '{workspace_id}' run result",
    )
    return WorkspaceConfig(
        workspace_id=workspace_id,
        workspace_root=workspace_root,
        reports_root=reports_root,
        artifacts_root=artifacts_root,
        reports=reports,
        run_results=run_results,
    )


def server_config_from_mapping(raw: Any, base_dir: Path) -> ServerConfig:
    """从已解析 JSON 构造配置；``base_dir`` 用于解析相对 root。"""

    config = _require_object(raw, "config")
    if not set(config.keys()).issubset(_CONFIG_KEYS):
        raise ConfigError("config contains unknown fields")
    if config.get("contract_version") != CONTRACT_VERSION:
        raise ConfigError("config contract_version is not supported")
    if config.get("server") != SERVER_RO:
        raise ConfigError("config server must be securitylab_ro")
    workspaces_raw = _require_object(config.get("workspaces"), "workspaces")
    if not workspaces_raw:
        raise ConfigError("config must define at least one workspace")

    workspaces: dict[str, WorkspaceConfig] = {}
    for workspace_id, workspace_raw in workspaces_raw.items():
        workspace = _parse_workspace(workspace_id, workspace_raw, base_dir)
        if workspace.workspace_id in workspaces:
            raise ConfigError("duplicate workspace id")
        for existing in workspaces.values():
            if is_within(existing.workspace_root, workspace.workspace_root) or is_within(
                workspace.workspace_root, existing.workspace_root
            ):
                raise ConfigError("workspace roots must not overlap")
        workspaces[workspace.workspace_id] = workspace
    return ServerConfig(
        contract_version=CONTRACT_VERSION,
        server=SERVER_RO,
        workspaces=MappingProxyType(workspaces),
    )


def load_server_config(path: Path) -> ServerConfig:
    """从 JSON 文件加载配置；任何读取/解析失败都转换为 ``ConfigError``。"""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError("config file cannot be read") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError("config file is not valid JSON") from exc
    return server_config_from_mapping(raw, path.parent)
