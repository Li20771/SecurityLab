"""Trusted server config validation tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from securitylab_mcp.config import ConfigError, load_server_config, server_config_from_mapping


def _base_config(tmp_path: Path) -> dict[str, Any]:
    ws_a = tmp_path / "ws_a"
    ws_b = tmp_path / "ws_b"
    (ws_a / "reports").mkdir(parents=True, exist_ok=True)
    (ws_a / "artifacts").mkdir(exist_ok=True)
    (ws_b / "reports").mkdir(parents=True, exist_ok=True)
    (ws_b / "artifacts").mkdir(exist_ok=True)
    (ws_a / "reports" / "report-1.md").write_text("report", encoding="utf-8")
    (ws_a / "artifacts" / "run-1.txt").write_text("result", encoding="utf-8")
    return {
        "contract_version": "1.0.0",
        "server": "securitylab_ro",
        "workspaces": {
            "ws_a": {
                "workspace_root": str(ws_a),
                "reports": {
                    "rep_a_0001": {"title": "Report A", "path": "report-1.md"},
                },
                "run_results": {
                    "res_a_0001": {
                        "title": "Result A",
                        "path": "run-1.txt",
                        "status": "completed",
                    },
                },
            },
            "ws_b": {
                "workspace_root": str(ws_b),
                "reports": {},
                "run_results": {},
            },
        },
    }


def test_valid_config_loads_and_defaults_are_inside_workspace(tmp_path: Path) -> None:
    raw = _base_config(tmp_path)
    config = server_config_from_mapping(raw, tmp_path)
    assert config.contract_version == "1.0.0"
    assert config.server == "securitylab_ro"
    workspace = config.workspaces["ws_a"]
    assert workspace.reports_root == (tmp_path / "ws_a" / "reports").resolve()
    assert workspace.artifacts_root == (tmp_path / "ws_a" / "artifacts").resolve()
    entry = workspace.reports["rep_a_0001"]
    assert entry.relative_path == "report-1.md"
    result = workspace.run_results["res_a_0001"]
    assert result.status == "completed"


def test_relative_workspace_root_resolves_against_config_directory(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    raw = {
        "contract_version": "1.0.0",
        "server": "securitylab_ro",
        "workspaces": {"ws_a": {"workspace_root": "../workspace"}},
    }
    config = server_config_from_mapping(raw, config_dir)
    assert config.workspaces["ws_a"].workspace_root == ws.resolve()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.update(contract_version="2.0.0"),
        lambda raw: raw.update(server="securitylab_vcan"),
        lambda raw: raw.update(extra=True),
        lambda raw: raw.pop("workspaces"),
        lambda raw: raw.update(workspaces={}),
    ],
)
def test_invalid_top_level_config_is_rejected(
    tmp_path: Path, mutate: Any
) -> None:
    raw = _base_config(tmp_path)
    mutate(raw)
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


def test_workspace_root_must_exist_and_be_a_directory(tmp_path: Path) -> None:
    raw = _base_config(tmp_path)
    raw["workspaces"]["ws_a"]["workspace_root"] = str(tmp_path / "missing")
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)

    file_root = tmp_path / "file-root"
    file_root.write_text("not a dir", encoding="utf-8")
    raw = _base_config(tmp_path)
    raw["workspaces"]["ws_a"]["workspace_root"] = str(file_root)
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


@pytest.mark.parametrize("key", ["reports_root", "artifacts_root"])
def test_workspace_subroots_must_not_escape(tmp_path: Path, key: str) -> None:
    raw = _base_config(tmp_path)
    raw["workspaces"]["ws_a"][key] = "../outside"
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


def test_workspace_roots_must_not_overlap(tmp_path: Path) -> None:
    raw = _base_config(tmp_path)
    raw["workspaces"]["ws_b"]["workspace_root"] = str(tmp_path / "ws_a")
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)

    raw = _base_config(tmp_path)
    nested = tmp_path / "ws_a" / "nested"
    nested.mkdir()
    raw["workspaces"]["ws_b"]["workspace_root"] = str(nested)
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "/etc/passwd", "C:\\Windows\\win.ini", ".env", ".git/config"],
)
def test_invalid_resource_paths_are_rejected(tmp_path: Path, path: str) -> None:
    raw = _base_config(tmp_path)
    raw["workspaces"]["ws_a"]["reports"]["rep_a_0001"]["path"] = path
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


@pytest.mark.parametrize(
    "entry",
    [
        {"title": "", "path": "report-1.md"},
        {"title": "x" * 201, "path": "report-1.md"},
        {"title": "bad\ud800title", "path": "report-1.md"},
        {"title": "Report", "path": "report-1.md", "extra": True},
        {"title": "Report"},
        {"title": "Report", "path": "report-1.md", "status": "x" * 65},
    ],
)
def test_invalid_resource_entries_are_rejected(
    tmp_path: Path, entry: dict[str, Any]
) -> None:
    raw = _base_config(tmp_path)
    raw["workspaces"]["ws_a"]["reports"] = {"rep_a_0001": entry}
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


@pytest.mark.parametrize("bad_id", ["rep_bad", "report_1", "rep_" + "x" * 65])
def test_invalid_report_id_is_rejected(tmp_path: Path, bad_id: str) -> None:
    raw = _base_config(tmp_path)
    raw["workspaces"]["ws_a"]["reports"] = {
        bad_id: {"title": "Report", "path": "report-1.md"}
    }
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


def test_too_many_resources_are_rejected(tmp_path: Path) -> None:
    raw = _base_config(tmp_path)
    raw["workspaces"]["ws_a"]["reports"] = {
        f"rep_{index:04d}": {"title": "Report", "path": "report-1.md"}
        for index in range(1001)
    }
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


def test_unknown_workspace_fields_are_rejected(tmp_path: Path) -> None:
    raw = _base_config(tmp_path)
    raw["workspaces"]["ws_a"]["extra"] = True
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


def test_invalid_ids_are_rejected(tmp_path: Path) -> None:
    raw = _base_config(tmp_path)
    raw["workspaces"]["WS_A"] = copy.deepcopy(raw["workspaces"]["ws_a"])
    with pytest.raises(ConfigError):
        server_config_from_mapping(raw, tmp_path)


def test_load_config_from_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_server_config(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_server_config(bad)
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_base_config(tmp_path)), encoding="utf-8")
    assert load_server_config(ok).workspaces["ws_a"].workspace_id == "ws_a"
