"""Synthetic workspace fixtures for offline MCP tests.

All files live under pytest's ``tmp_path``. No real SecurityLab lab, vcan,
CAN/ECU/vehicle/device, credential or external path is touched.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from securitylab_mcp.binding import Binding
from securitylab_mcp.config import ServerConfig, load_server_config
from securitylab_mcp.service import ReadOnlySecurityLabService

RUN_ID = "run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


@dataclass(frozen=True)
class LabFixture:
    root: Path
    ws_a: Path
    ws_b: Path
    config_path: Path
    config: ServerConfig
    symlink_supported: bool

    def binding(
        self,
        *,
        role: str = "researcher",
        workspace_id: str = "ws_a",
        profile: str | None = None,
        run_id: str = RUN_ID,
        episode_id: str | None = "ep_1",
    ) -> Binding:
        profiles = {
            "researcher": "researcher_ro_v1",
            "vehicle_analyst": "vehicle_analyst_ro_v1",
            "challenger": "challenger_ro_v1",
            "evidence_verifier": "evidence_verifier_ro_v1",
            "lab_runner": "lab_runner_vcan_v1",
            "orchestrator": "orchestrator_none_v1",
        }
        return Binding(
            run_id=run_id,
            role=role,
            workspace_id=workspace_id,
            episode_id=episode_id,
            tool_profile=profile or profiles[role],
        )

    def service(self, **kwargs: object) -> ReadOnlySecurityLabService:
        return ReadOnlySecurityLabService(self.config, self.binding(**kwargs))  # type: ignore[arg-type]


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


@pytest.fixture
def lab(tmp_path: Path) -> LabFixture:
    ws_a = tmp_path / "ws_a"
    ws_b = tmp_path / "ws_b"
    ws_a.mkdir()
    ws_b.mkdir()

    _write(ws_a / "notes.txt", "WS_A_WORKSPACE_CANARY")
    _write(
        ws_a / "secret.txt",
        "token=REDACTION_TOKEN_SENTINEL\n"
        "password=REDACTION_PASSWORD_SENTINEL\n"
        "Authorization: Bearer REDACTION_BEARER_SENTINEL\n"
        "normal line\n",
    )
    _write(ws_a / "large.txt", "x" * 70000)
    _write(ws_a / "sub" / "inside.txt", "subdir content")
    _write(ws_a / ".env", "SYNTHETIC_ENV_VALUE")
    _write(ws_a / ".git" / "config", "SYNTHETIC_GIT_CONFIG")
    _write(ws_a / "id_rsa", "SYNTHETIC_PRIVATE_KEY")
    _write(ws_a / "binary.bin", b"\xff\xfe\x00binary")
    _write(ws_a / "reports" / "report-1.md", "Report A: synthetic fixture.")
    _write(ws_a / "artifacts" / "run-1" / "out.txt", "artifact-a")
    _write(ws_b / "notes.txt", "WS_B_WORKSPACE_CANARY")
    _write(ws_b / "reports" / "report-b.md", "WS_B_CANARY")
    _write(ws_b / "artifacts" / "run-b" / "out.txt", "artifact-b")

    symlink_supported = True
    try:
        os.symlink("../ws_b/notes.txt", ws_a / "link-out")
        os.symlink("notes.txt", ws_a / "link-in")
    except (OSError, NotImplementedError):
        symlink_supported = False

    raw_config = {
        "contract_version": "1.0.0",
        "server": "securitylab_ro",
        "workspaces": {
            "ws_a": {
                "workspace_root": str(ws_a),
                "reports": {
                    "rep_a_0001": {
                        "title": "Synthetic report A",
                        "path": "report-1.md",
                    }
                },
                "run_results": {
                    "res_a_0001": {
                        "title": "Synthetic run result A",
                        "path": "run-1/out.txt",
                        "status": "completed",
                    }
                },
            },
            "ws_b": {
                "workspace_root": str(ws_b),
                "reports": {
                    "rep_b_0001": {
                        "title": "Synthetic report B",
                        "path": "report-b.md",
                    }
                },
                "run_results": {
                    "res_b_0001": {
                        "title": "Synthetic run result B",
                        "path": "run-b/out.txt",
                        "status": "completed",
                    }
                },
            },
        },
    }
    config_path = tmp_path / "server.json"
    config_path.write_text(json.dumps(raw_config), encoding="utf-8")
    config = load_server_config(config_path)
    return LabFixture(
        root=tmp_path,
        ws_a=ws_a,
        ws_b=ws_b,
        config_path=config_path,
        config=config,
        symlink_supported=symlink_supported,
    )
