"""可信 server/session binding 校验。

身份字段（``run_id`` / ``role`` / ``workspace_id`` / ``episode_id`` /
``tool_profile``）只能由 host 启动 server 时通过**受信配置文件**提供。
MCP 工具参数中出现的任何身份或权限字段一律拒绝（见 ``service.py``）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from .errors import (
    CONTRACT_VERSION,
    READ_ONLY_TOOLS,
    SERVER_RO,
    SERVER_VCAN,
    TOOL_LAB_CATALOG,
    TOOL_LAB_GET_REPORT,
    TOOL_LAB_READ_FILE,
    TOOL_LAB_STATUS,
    TOOL_SECURITYLAB_VCAN,
)

RUN_ID_PATTERN: Final = re.compile(r"^run_[A-Za-z0-9_-]{8,96}$")
WORKSPACE_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
EPISODE_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

ROLE_IDS: Final = (
    "orchestrator",
    "researcher",
    "vehicle_analyst",
    "challenger",
    "evidence_verifier",
    "lab_runner",
)

#: 与 brief/contracts/examples/role-preset.registry.json 一致。
ROLE_ALLOWED_TOOLS: Final[Mapping[str, tuple[str, ...]]] = {
    "orchestrator": (),
    "researcher": READ_ONLY_TOOLS,
    "vehicle_analyst": READ_ONLY_TOOLS,
    "challenger": (
        TOOL_LAB_CATALOG,
        TOOL_LAB_STATUS,
        TOOL_LAB_READ_FILE,
        TOOL_LAB_GET_REPORT,
    ),
    "evidence_verifier": READ_ONLY_TOOLS,
    "lab_runner": (TOOL_SECURITYLAB_VCAN,),
}

#: role 只能绑定其 preset 的 tool_profile；securitylab_ro 是 contract 中
#: 的通用只读 profile，允许只读角色直接使用。
ROLE_TOOL_PROFILES: Final[Mapping[str, frozenset[str]]] = {
    "orchestrator": frozenset({"orchestrator_none_v1"}),
    "researcher": frozenset({"researcher_ro_v1", SERVER_RO}),
    "vehicle_analyst": frozenset({"vehicle_analyst_ro_v1", SERVER_RO}),
    "challenger": frozenset({"challenger_ro_v1", SERVER_RO}),
    "evidence_verifier": frozenset({"evidence_verifier_ro_v1", SERVER_RO}),
    "lab_runner": frozenset({"lab_runner_vcan_v1"}),
}

_BINDING_KEYS: Final = frozenset(
    {"contract_version", "run_id", "role", "workspace_id", "episode_id", "tool_profile"}
)


class BindingError(ValueError):
    """绑定缺失、非法、版本不符或 role/profile 不匹配。"""


@dataclass(frozen=True)
class Binding:
    """一个 server 进程对应的可信调用身份。"""

    run_id: str
    role: str
    workspace_id: str
    episode_id: str | None
    tool_profile: str

    def __post_init__(self) -> None:
        if not RUN_ID_PATTERN.fullmatch(self.run_id):
            raise BindingError("run_id is invalid")
        if self.role not in ROLE_IDS:
            raise BindingError("role is invalid")
        if not WORKSPACE_ID_PATTERN.fullmatch(self.workspace_id):
            raise BindingError("workspace_id is invalid")
        if self.episode_id is not None and not EPISODE_ID_PATTERN.fullmatch(self.episode_id):
            raise BindingError("episode_id is invalid")
        if not isinstance(self.tool_profile, str) or not self.tool_profile:
            raise BindingError("tool_profile is invalid")
        allowed_profiles = ROLE_TOOL_PROFILES[self.role]
        if self.tool_profile not in allowed_profiles:
            raise BindingError("tool profile does not match the role")

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return ROLE_ALLOWED_TOOLS[self.role]

    @property
    def tool_server(self) -> str:
        return SERVER_VCAN if self.role == "lab_runner" else SERVER_RO


def binding_from_mapping(raw: Any) -> Binding:
    """从受信配置对象构造绑定；未知字段/版本不符一律 fail closed。"""

    if not isinstance(raw, Mapping):
        raise BindingError("binding must be an object")
    keys = set(raw.keys())
    if not keys.issubset(_BINDING_KEYS):
        raise BindingError("binding contains unknown fields")
    missing = {"run_id", "role", "workspace_id", "tool_profile"} - keys
    if missing:
        raise BindingError("binding is missing required fields")
    contract_version = raw.get("contract_version", CONTRACT_VERSION)
    if contract_version != CONTRACT_VERSION:
        raise BindingError("binding contract_version is not supported")
    run_id = raw.get("run_id")
    role = raw.get("role")
    workspace_id = raw.get("workspace_id")
    episode_id = raw.get("episode_id")
    tool_profile = raw.get("tool_profile")
    if not isinstance(run_id, str) or not isinstance(role, str):
        raise BindingError("binding identity fields must be strings")
    if not isinstance(workspace_id, str) or not isinstance(tool_profile, str):
        raise BindingError("binding identity fields must be strings")
    if episode_id is not None and not isinstance(episode_id, str):
        raise BindingError("episode_id must be a string or null")
    return Binding(
        run_id=run_id,
        role=role,
        workspace_id=workspace_id,
        episode_id=episode_id,
        tool_profile=tool_profile,
    )
