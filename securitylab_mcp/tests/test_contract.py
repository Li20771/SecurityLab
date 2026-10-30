"""Contract 1.0.0 conformance checks against the mirrored JSON Schemas."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from securitylab_mcp.binding import ROLE_IDS, RUN_ID_PATTERN, WORKSPACE_ID_PATTERN
from securitylab_mcp.errors import (
    CONTRACT_VERSION,
    ERROR_CODES,
    TOOL_LAB_CATALOG,
    TOOL_NAMES,
    failure_result,
    success_result,
)

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "contracts"
_CALL_SCHEMA = "securitylab-tool-call.v1.schema.json"
_RESULT_SCHEMA = "securitylab-tool-result.v1.schema.json"

#: 冻结于 brief/contracts v1.0.0；镜像变化必须显式更新并同步 Workbench。
_PINNED_SHA256 = {
    _CALL_SCHEMA: "92da8139923a7b5132c0add2b977ba33d0928149d1854ecc08ca3c592dfe21a0",
    _RESULT_SCHEMA: "2c491dc9884bf61b80fa4d010bbeccfbd020d7cb16ef152c483a892c8c244b4c",
}

_CONTRACT_IDENTITY_KEYS = {
    "role",
    "workspace_id",
    "episode_id",
    "run_id",
    "session_id",
    "tool_profile",
    "approval_ref",
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_mirrored_schemas_match_frozen_hashes() -> None:
    for name, expected in _PINNED_SHA256.items():
        digest = hashlib.sha256((_SCHEMA_DIR / name).read_bytes()).hexdigest()
        assert digest == expected, f"{name} drifted from the frozen v1.0.0 mirror"


def test_tool_call_schema_matches_implementation_constants() -> None:
    schema = _load(_CALL_SCHEMA)
    assert schema["$id"].endswith(":1.0.0")
    defs = schema["$defs"]
    implemented = {
        defs[name]["properties"]["tool"]["const"]
        for name in (
            "labCatalog",
            "labStatus",
            "labReadFile",
            "labGetReport",
            "labGetRunResult",
            "securitylabVcan",
        )
    }
    assert implemented == set(TOOL_NAMES)
    for name in (
        "labCatalog",
        "labStatus",
        "labReadFile",
        "labGetReport",
        "labGetRunResult",
        "securitylabVcan",
    ):
        assert defs[name]["properties"]["contract_version"]["const"] == CONTRACT_VERSION


def test_result_schema_matches_implementation_constants() -> None:
    schema = _load(_RESULT_SCHEMA)
    assert schema["$id"].endswith(":1.0.0")
    assert set(schema["properties"]["tool"]["enum"]) == set(TOOL_NAMES)
    assert set(schema["properties"]["server"]["enum"]) == {
        "securitylab_ro",
        "securitylab_vcan",
    }
    assert set(schema["$defs"]["error"]["properties"]["code"]["enum"]) == set(ERROR_CODES)
    meta = schema["properties"]["meta"]
    assert set(meta["required"]) == {
        "identity_source",
        "executed",
        "side_effects",
        "redacted",
        "output_limit",
        "bytes",
    }
    assert meta["properties"]["identity_source"]["const"] == "server-binding"
    assert meta["properties"]["side_effects"]["const"] == 0


def _resolve_local_ref(defs: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return defs[ref.rsplit("/", 1)[-1]]
    return schema


def test_tool_arguments_never_accept_trusted_identity_fields() -> None:
    schema = _load(_CALL_SCHEMA)
    defs = schema["$defs"]
    for name in (
        "labCatalog",
        "labStatus",
        "labReadFile",
        "labGetReport",
        "labGetRunResult",
        "securitylabVcan",
    ):
        argument_schema = _resolve_local_ref(defs, defs[name]["properties"]["arguments"])
        properties = set(argument_schema.get("properties", {}))
        assert properties.isdisjoint(_CONTRACT_IDENTITY_KEYS), name
        assert argument_schema.get("additionalProperties") is False, name


def test_context_requires_server_bound_identity() -> None:
    schema = _load(_CALL_SCHEMA)
    context = schema["$defs"]["context"]
    assert set(context["required"]) == {
        "run_id",
        "role",
        "workspace_id",
        "tool_profile",
    }
    assert context["additionalProperties"] is False
    assert context["properties"]["run_id"]["pattern"] == RUN_ID_PATTERN.pattern
    assert context["properties"]["workspace_id"]["pattern"] == WORKSPACE_ID_PATTERN.pattern
    assert set(context["properties"]["role"]["enum"]) == set(ROLE_IDS)


def test_implementation_envelopes_have_schema_shape() -> None:
    envelope = success_result(
        "lab_read_file",
        {"content": "x", "bytes": 1, "truncated": False},
        redacted=False,
        byte_count=1,
    )
    assert set(envelope) == {
        "contract_version",
        "server",
        "tool",
        "ok",
        "data",
        "error",
        "meta",
    }
    assert envelope["contract_version"] == CONTRACT_VERSION
    assert envelope["server"] == "securitylab_ro"
    assert envelope["ok"] is True
    assert envelope["error"] is None
    assert set(envelope["meta"]) == {
        "identity_source",
        "executed",
        "side_effects",
        "redacted",
        "output_limit",
        "bytes",
    }

    failure = failure_result(TOOL_LAB_CATALOG, "tool-not-allowed", "unknown tool")
    assert failure["ok"] is False
    assert failure["data"] is None
    assert failure["error"]["code"] in ERROR_CODES
    assert failure["meta"]["executed"] is False

    with pytest.raises(ValueError):
        failure_result(TOOL_LAB_CATALOG, "not-an-error-code", "x")
    with pytest.raises(ValueError):
        success_result("not_a_tool", {}, redacted=False, byte_count=0)
