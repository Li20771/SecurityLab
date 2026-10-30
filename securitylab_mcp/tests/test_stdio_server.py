"""End-to-end stdio MCP tests.

The subprocess is this repository's own offline server. It does not connect to
DSH, a real MCP endpoint, SecurityLab labs, vcan, CAN/ECU, vehicles or devices.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from securitylab_mcp.tests.conftest import LabFixture

_REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _write_binding(lab: LabFixture, *, role: str = "researcher") -> Path:
    profiles = {
        "researcher": "researcher_ro_v1",
        "lab_runner": "lab_runner_vcan_v1",
    }
    path = lab.root / f"binding-{role}.json"
    path.write_text(
        json.dumps(
            {
                "contract_version": "1.0.0",
                "run_id": RUN_ID,
                "role": role,
                "workspace_id": "ws_a",
                "episode_id": "ep_1",
                "tool_profile": profiles[role],
            }
        ),
        encoding="utf-8",
    )
    return path


def _run_server(
    config_path: Path, binding_path: Path, requests: list[dict[str, Any]]
) -> subprocess.CompletedProcess[str]:
    payload = "\n".join(json.dumps(request) for request in requests) + "\n"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "securitylab_mcp.server",
            "--config",
            str(config_path),
            "--binding",
            str(binding_path),
        ],
        cwd=str(_REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        stdout, stderr = process.communicate(input=payload, timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise AssertionError(f"server timed out; stderr={stderr!r}") from None
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _responses(completed: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]


def _call(name: str, arguments: object, *, request_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_stdio_initialize_list_and_read_tool(lab: LabFixture) -> None:
    binding = _write_binding(lab)
    requests: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "offline-test", "version": "1.0.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        _call("lab_read_file", {"path": "notes.txt"}, request_id=3),
        _call("lab_read_file", {"path": "../ws_b/notes.txt"}, request_id=4),
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "lab_read_file",
                "arguments": {"path": "notes.txt"},
                "role": "orchestrator",
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "securitylab_vcan", "arguments": {"suite": "unit_smoke"}},
        },
    ]
    completed = _run_server(lab.config_path, binding, requests)
    assert completed.returncode == 0
    assert "Traceback" not in completed.stderr
    responses = _responses(completed)
    assert len(responses) == 6

    initialize = responses[0]
    assert initialize["result"]["protocolVersion"] == "2025-06-18"
    assert initialize["result"]["serverInfo"]["name"] == "securitylab_ro"

    listed = responses[1]["result"]["tools"]
    names = {tool["name"] for tool in listed}
    assert names == {
        "lab_catalog",
        "lab_status",
        "lab_read_file",
        "lab_get_report",
        "lab_get_run_result",
    }
    assert "securitylab_vcan" not in names

    allowed = responses[2]["result"]
    assert allowed["isError"] is False
    envelope = allowed["structuredContent"]
    assert envelope["ok"] is True
    assert envelope["data"]["content"] == "WS_A_WORKSPACE_CANARY"
    assert json.loads(allowed["content"][0]["text"]) == envelope

    traversal = responses[3]["result"]
    assert traversal["isError"] is True
    assert traversal["structuredContent"]["error"]["code"] == "path-out-of-root"
    assert traversal["structuredContent"]["meta"]["executed"] is False

    identity = responses[4]["result"]
    assert identity["isError"] is True
    assert identity["structuredContent"]["error"]["code"] == "identity-in-args"

    vcan = responses[5]["result"]
    assert vcan["isError"] is True
    assert vcan["structuredContent"]["error"]["code"] == "tool-not-allowed"


def test_stdio_older_protocol_gets_text_content_only(lab: LabFixture) -> None:
    binding = _write_binding(lab)
    requests: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
        },
        _call("lab_read_file", {"path": "notes.txt"}, request_id=2),
    ]
    completed = _run_server(lab.config_path, binding, requests)
    assert completed.returncode == 0
    responses = _responses(completed)
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    tool_result = responses[1]["result"]
    assert "structuredContent" not in tool_result
    assert tool_result["isError"] is False
    assert json.loads(tool_result["content"][0]["text"])["ok"] is True


def test_stdio_lab_runner_vcan_is_listed_but_disabled(lab: LabFixture) -> None:
    binding = _write_binding(lab, role="lab_runner")
    requests: list[dict[str, Any]] = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        _call("securitylab_vcan", {"suite": "unit_smoke"}, request_id=2),
        _call("lab_catalog", {}, request_id=3),
    ]
    completed = _run_server(lab.config_path, binding, requests)
    assert completed.returncode == 0
    responses = _responses(completed)
    assert len(responses) == 3

    tools = responses[0]["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "securitylab_vcan"
    assert tools[0]["description"].startswith("[DISABLED]")

    disabled = responses[1]["result"]
    assert disabled["isError"] is True
    envelope = disabled["structuredContent"]
    assert envelope["error"]["code"] == "tool-disabled"
    assert envelope["meta"]["executed"] is False
    assert envelope["meta"]["side_effects"] == 0

    denied = responses[2]["result"]
    assert denied["structuredContent"]["error"]["code"] == "tool-not-allowed"


def test_stdio_protocol_errors_are_generic(lab: LabFixture) -> None:
    binding = _write_binding(lab)
    payload = (
        '{"jsonrpc":"2.0","id":1,"method":"unknown/method"}\n'
        "{not-json}\n"
        '﻿{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "securitylab_mcp.server",
            "--config",
            str(lab.config_path),
            "--binding",
            str(binding),
        ],
        cwd=str(_REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    stdout, _ = process.communicate(input=payload, timeout=20)
    assert process.returncode == 0
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    assert len(responses) == 3
    assert responses[0]["error"]["code"] == -32601
    assert responses[1]["error"]["code"] == -32700
    assert len(responses[2]["result"]["tools"]) == 5


def test_stdio_unregistered_workspace_exits_cleanly(lab: LabFixture) -> None:
    binding = lab.root / "binding-missing-workspace.json"
    binding.write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "role": "researcher",
                "workspace_id": "ws_missing",
                "episode_id": "ep_1",
                "tool_profile": "researcher_ro_v1",
            }
        ),
        encoding="utf-8",
    )
    completed = _run_server(lab.config_path, binding, [])
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "Traceback" not in completed.stderr


def test_stdio_invalid_binding_exits_without_serving(lab: LabFixture) -> None:
    bad_binding = lab.root / "bad-binding.json"
    bad_binding.write_text(
        json.dumps(
            {
                "run_id": "bad",
                "role": "researcher",
                "workspace_id": "ws_a",
                "tool_profile": "researcher_ro_v1",
            }
        ),
        encoding="utf-8",
    )
    completed = _run_server(lab.config_path, bad_binding, [])
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "Traceback" not in completed.stderr
