"""Read-only tool behavior tests (offline, synthetic fixtures only)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from securitylab_mcp.config import server_config_from_mapping
from securitylab_mcp.errors import READ_ONLY_TOOLS
from securitylab_mcp.service import ReadOnlySecurityLabService
from securitylab_mcp.tests.conftest import LabFixture


def _assert_denied(result: dict[str, Any], code: str) -> None:
    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"]["code"] == code
    assert result["meta"]["executed"] is False
    assert result["meta"]["side_effects"] == 0
    assert result["meta"]["identity_source"] == "server-binding"
    assert result["meta"]["bytes"] == 0


def test_tool_list_is_role_scoped(lab: LabFixture) -> None:
    researcher = lab.service().list_tools()
    assert tuple(tool.name for tool in researcher) == READ_ONLY_TOOLS
    assert all(tool.enabled for tool in researcher)

    analyst = lab.service(role="vehicle_analyst").list_tools()
    assert tuple(tool.name for tool in analyst) == READ_ONLY_TOOLS

    challenger = lab.service(role="challenger").list_tools()
    assert "lab_get_run_result" not in {tool.name for tool in challenger}
    assert "lab_get_report" in {tool.name for tool in challenger}

    verifier = lab.service(role="evidence_verifier").list_tools()
    assert tuple(tool.name for tool in verifier) == READ_ONLY_TOOLS

    runner = lab.service(role="lab_runner").list_tools()
    assert len(runner) == 1
    assert runner[0].name == "securitylab_vcan"
    assert runner[0].enabled is False
    assert runner[0].description.startswith("[DISABLED]")

    orchestrator = lab.service(role="orchestrator").list_tools()
    assert orchestrator == ()


def test_allowed_file_reads_are_bounded_and_scoped(lab: LabFixture) -> None:
    service = lab.service()
    result = service.call_tool("lab_read_file", {"path": "notes.txt"})
    assert result["ok"] is True
    assert result["data"]["content"] == "WS_A_WORKSPACE_CANARY"
    assert result["data"]["bytes"] == 21
    assert result["data"]["truncated"] is False
    assert result["meta"]["executed"] is True
    assert result["meta"]["redacted"] is False
    assert result["meta"]["output_limit"] == 65536
    assert str(lab.ws_a) not in json.dumps(result)

    report = service.call_tool(
        "lab_read_file", {"path": "report-1.md", "root_id": "reports"}
    )
    assert report["ok"] is True
    assert report["data"]["content"].startswith("Report A")

    artifact = service.call_tool(
        "lab_read_file", {"path": "run-1/out.txt", "root_id": "artifacts"}
    )
    assert artifact["ok"] is True
    assert artifact["data"]["content"] == "artifact-a"

    nested = service.call_tool("lab_read_file", {"path": "sub/inside.txt"})
    assert nested["ok"] is True
    assert nested["data"]["content"] == "subdir content"


@pytest.mark.parametrize(
    "path",
    [
        "../ws_b/notes.txt",
        "..\\ws_b\\notes.txt",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "\\\\server\\share\\file",
        "notes.txt\x00",
        "notes.txt:stream",
        "CON",
        "a//b",
        "notes.txt ",
    ],
)
def test_traversal_and_special_paths_are_rejected(lab: LabFixture, path: str) -> None:
    result = lab.service().call_tool("lab_read_file", {"path": path})
    _assert_denied(result, "path-out-of-root")
    assert "WS_B_WORKSPACE_CANARY" not in json.dumps(result)


def test_symlink_escape_is_rejected(lab: LabFixture) -> None:
    if not lab.symlink_supported:
        pytest.skip("symlink creation is not permitted in this environment")
    service = lab.service()
    result = service.call_tool("lab_read_file", {"path": "link-out"})
    _assert_denied(result, "symlink-escape")
    assert "WS_B_WORKSPACE_CANARY" not in json.dumps(result)

    inside = service.call_tool("lab_read_file", {"path": "link-in"})
    assert inside["ok"] is True
    assert inside["data"]["content"] == "WS_A_WORKSPACE_CANARY"


@pytest.mark.parametrize("path", [".env", ".git/config", "id_rsa"])
def test_sensitive_paths_are_not_available(lab: LabFixture, path: str) -> None:
    _assert_denied(lab.service().call_tool("lab_read_file", {"path": path}), "not-found")


@pytest.mark.parametrize(
    "key,value",
    [
        ("context", {"role": "orchestrator"}),
        ("role", "orchestrator"),
        ("workspace_id", "ws_b"),
        ("workspace", "ws_b"),
        ("run_id", "run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        ("session_id", "session-1"),
        ("tool_profile", "securitylab_ro"),
        ("approval_ref", "approval-1"),
        ("permissions", ["test"]),
        ("command", "id"),
        ("shell", "bash"),
        ("python", "print(1)"),
        ("sudo", True),
        ("target", "ecu"),
        ("env", {"TOKEN": "x"}),
    ],
)
def test_identity_or_permission_fields_in_arguments_are_rejected(
    lab: LabFixture, key: str, value: object
) -> None:
    result = lab.service().call_tool("lab_read_file", {"path": "notes.txt", key: value})
    _assert_denied(result, "identity-in-args")


def test_unknown_and_disallowed_tools_never_execute(lab: LabFixture) -> None:
    unknown = lab.service().call_tool("lab_delete_file", {})
    _assert_denied(unknown, "tool-not-allowed")
    assert unknown["tool"] == "lab_catalog"

    researcher_vcan = lab.service().call_tool("securitylab_vcan", {"suite": "unit_smoke"})
    _assert_denied(researcher_vcan, "tool-not-allowed")
    assert researcher_vcan["server"] == "securitylab_vcan"

    runner = lab.service(role="lab_runner")
    disabled = runner.call_tool("securitylab_vcan", {"suite": "unit_smoke"})
    _assert_denied(disabled, "tool-disabled")
    assert disabled["server"] == "securitylab_vcan"

    runner_ro = runner.call_tool("lab_catalog", {})
    _assert_denied(runner_ro, "tool-not-allowed")

    challenger = lab.service(role="challenger")
    _assert_denied(
        challenger.call_tool("lab_get_run_result", {"result_id": "res_a_0001"}),
        "tool-not-allowed",
    )


def test_output_is_redacted(lab: LabFixture) -> None:
    result = lab.service().call_tool("lab_read_file", {"path": "secret.txt"})
    assert result["ok"] is True
    assert result["meta"]["redacted"] is True
    content = result["data"]["content"]
    assert "REDACTION_TOKEN_SENTINEL" not in content
    assert "REDACTION_PASSWORD_SENTINEL" not in content
    assert "REDACTION_BEARER_SENTINEL" not in content
    assert "[REDACTED]" in content
    assert "normal line" in content


def test_large_file_is_truncated_at_64_kib(lab: LabFixture) -> None:
    result = lab.service().call_tool("lab_read_file", {"path": "large.txt"})
    assert result["ok"] is True
    assert result["data"]["truncated"] is True
    assert result["data"]["bytes"] == 65536
    assert len(result["data"]["content"]) == 65536
    assert result["meta"]["bytes"] == 65536


def test_reports_and_run_results_are_read_without_execution(lab: LabFixture) -> None:
    service = lab.service()
    report = service.call_tool("lab_get_report", {"report_id": "rep_a_0001"})
    assert report["ok"] is True
    assert report["data"]["title"] == "Synthetic report A"
    assert report["data"]["content"].startswith("Report A")
    assert report["meta"]["executed"] is True
    assert report["meta"]["side_effects"] == 0

    result = service.call_tool("lab_get_run_result", {"result_id": "res_a_0001"})
    assert result["ok"] is True
    assert result["data"]["status"] == "completed"
    assert result["data"]["content"] == "artifact-a"
    assert result["meta"]["executed"] is True
    assert result["meta"]["side_effects"] == 0


def test_cross_workspace_resources_are_not_found(lab: LabFixture) -> None:
    service = lab.service(workspace_id="ws_a")
    _assert_denied(service.call_tool("lab_get_report", {"report_id": "rep_b_0001"}), "not-found")
    _assert_denied(
        service.call_tool("lab_get_run_result", {"result_id": "res_b_0001"}),
        "not-found",
    )

    catalog = service.call_tool("lab_catalog", {})
    assert catalog["ok"] is True
    resources = catalog["data"]["resources"]
    ids = {item["id"] for item in resources}
    assert {"rep_a_0001", "res_a_0001", "workspace", "reports", "artifacts"} <= ids
    assert "rep_b_0001" not in ids
    assert "res_b_0001" not in ids
    for item in resources:
        assert set(item) == {"id", "kind", "title"}
    serialized = json.dumps(catalog)
    assert "ws_b" not in serialized
    assert "WS_B_CANARY" not in serialized
    assert str(lab.ws_a) not in serialized
    assert str(lab.ws_b) not in serialized


def test_status_is_static_and_does_not_leak_paths(lab: LabFixture) -> None:
    result = lab.service().call_tool("lab_status", {})
    assert result["ok"] is True
    assert result["data"] == {
        "status": "ready",
        "execution": "disabled",
        "vcan": "disabled",
    }
    assert result["meta"]["executed"] is True
    assert result["meta"]["side_effects"] == 0
    serialized = json.dumps(result)
    assert str(lab.ws_a) not in serialized
    assert "TOKEN" not in serialized


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("lab_catalog", {"path": "notes.txt"}),
        ("lab_status", {"anything": True}),
        ("lab_read_file", {}),
        ("lab_read_file", {"path": 1}),
        ("lab_read_file", {"path": "notes.txt", "root_id": "secrets"}),
        ("lab_read_file", {"path": "notes.txt", "extra": True}),
        ("lab_get_report", {}),
        ("lab_get_report", {"report_id": "bad"}),
        ("lab_get_report", {"report_id": "rep_a_0001", "extra": True}),
        ("lab_get_run_result", {"result_id": "bad"}),
        ("lab_read_file", "not-an-object"),
        ("lab_read_file", ["notes.txt"]),
    ],
)
def test_invalid_arguments_are_rejected(
    lab: LabFixture, tool: str, arguments: object
) -> None:
    _assert_denied(lab.service().call_tool(tool, arguments), "invalid-args")


def test_non_string_tool_is_rejected(lab: LabFixture) -> None:
    _assert_denied(lab.service().call_tool(123, {}), "tool-not-allowed")


def test_binary_files_and_directories_are_not_found(lab: LabFixture) -> None:
    service = lab.service()
    _assert_denied(service.call_tool("lab_read_file", {"path": "binary.bin"}), "not-found")
    _assert_denied(service.call_tool("lab_read_file", {"path": "sub"}), "not-found")
    _assert_denied(service.call_tool("lab_read_file", {"path": "missing.txt"}), "not-found")


def test_missing_workspace_binding_is_rejected(lab: LabFixture) -> None:
    binding = lab.binding(workspace_id="ws_missing")
    with pytest.raises(ValueError):
        ReadOnlySecurityLabService(lab.config, binding)


def test_catalog_is_capped_at_100_items(tmp_path: Path, lab: LabFixture) -> None:
    raw = {
        "contract_version": "1.0.0",
        "server": "securitylab_ro",
        "workspaces": {
            "ws_a": {
                "workspace_root": str(lab.ws_a),
                "reports": {
                    f"rep_{index:04d}": {
                        "title": f"Report {index}",
                        "path": "report-1.md",
                    }
                    for index in range(150)
                },
            }
        },
    }
    config = server_config_from_mapping(raw, tmp_path)
    service = ReadOnlySecurityLabService(config, lab.binding())
    result = service.call_tool("lab_catalog", {})
    assert result["ok"] is True
    assert result["data"]["truncated"] is True
    assert len(result["data"]["resources"]) == 100


def test_redaction_failure_is_fail_closed(
    lab: LabFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_content: str) -> tuple[str, bool]:
        raise TypeError("synthetic redaction failure")

    monkeypatch.setattr("securitylab_mcp.service.redact_text", boom)
    result = lab.service().call_tool("lab_read_file", {"path": "notes.txt"})
    _assert_denied(result, "redaction-failed")


def test_internal_error_does_not_leak_details(
    lab: LabFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("synthetic /absolute/path")

    monkeypatch.setattr("securitylab_mcp.service.read_text_bounded", boom)
    result = lab.service().call_tool("lab_read_file", {"path": "notes.txt"})
    _assert_denied(result, "internal-error")
    assert "absolute" not in json.dumps(result)


def test_catalog_output_limit_is_a_hard_failure(
    lab: LabFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "securitylab_mcp.service.compact_json_size", lambda _data: 70000
    )
    result = lab.service().call_tool("lab_catalog", {})
    _assert_denied(result, "output-limit")


def test_catalog_and_report_titles_are_redacted(tmp_path: Path, lab: LabFixture) -> None:
    raw = {
        "contract_version": "1.0.0",
        "server": "securitylab_ro",
        "workspaces": {
            "ws_a": {
                "workspace_root": str(lab.ws_a),
                "reports": {
                    "rep_title_0001": {
                        "title": "Report token=TITLE_SENTINEL",
                        "path": "report-1.md",
                    }
                },
            }
        },
    }
    config = server_config_from_mapping(raw, tmp_path)
    service = ReadOnlySecurityLabService(config, lab.binding())
    catalog = service.call_tool("lab_catalog", {})
    assert catalog["ok"] is True
    assert catalog["meta"]["redacted"] is True
    assert "TITLE_SENTINEL" not in json.dumps(catalog)
    report = service.call_tool("lab_get_report", {"report_id": "rep_title_0001"})
    assert report["ok"] is True
    assert report["meta"]["redacted"] is True
    assert "TITLE_SENTINEL" not in json.dumps(report)
    assert "[REDACTED]" in report["data"]["title"]


def test_trusted_server_files_are_not_readable_through_tools(lab: LabFixture) -> None:
    protected = lab.ws_a / "protected-server-config.json"
    protected.write_text('{"synthetic": true}', encoding="utf-8")
    service = ReadOnlySecurityLabService(
        lab.config, lab.binding(), protected_paths=(protected,)
    )
    _assert_denied(
        service.call_tool("lab_read_file", {"path": "protected-server-config.json"}),
        "not-found",
    )
    assert service.call_tool("lab_read_file", {"path": "notes.txt"})["ok"] is True


def test_indexed_report_symlink_escape_is_rechecked_at_read_time(lab: LabFixture) -> None:
    if not lab.symlink_supported:
        pytest.skip("symlink creation is not permitted in this environment")
    report_path = lab.ws_a / "reports" / "report-1.md"
    os.remove(report_path)
    os.symlink(lab.ws_b / "reports" / "report-b.md", report_path)
    result = lab.service().call_tool("lab_get_report", {"report_id": "rep_a_0001"})
    _assert_denied(result, "symlink-escape")
    assert "WS_B_CANARY" not in json.dumps(result)
