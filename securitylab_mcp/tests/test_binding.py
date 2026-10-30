"""Trusted binding validation tests."""

from __future__ import annotations

import pytest

from securitylab_mcp.binding import (
    Binding,
    BindingError,
    binding_from_mapping,
)


def _raw(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "contract_version": "1.0.0",
        "run_id": "run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "role": "researcher",
        "workspace_id": "ws_a",
        "episode_id": "ep_1",
        "tool_profile": "researcher_ro_v1",
    }
    raw.update(overrides)
    return raw


def test_valid_binding_from_mapping() -> None:
    binding = binding_from_mapping(_raw())
    assert binding.run_id == "run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert binding.role == "researcher"
    assert binding.workspace_id == "ws_a"
    assert binding.episode_id == "ep_1"
    assert binding.tool_profile == "researcher_ro_v1"
    assert binding.tool_server == "securitylab_ro"
    assert "lab_get_run_result" in binding.allowed_tools


def test_contract_version_is_optional_but_must_match() -> None:
    raw = _raw()
    del raw["contract_version"]
    assert binding_from_mapping(raw).role == "researcher"
    with pytest.raises(BindingError):
        binding_from_mapping(_raw(contract_version="2.0.0"))


@pytest.mark.parametrize("episode_id", [None, "ep_2"])
def test_episode_id_can_be_null_or_valid(episode_id: str | None) -> None:
    assert binding_from_mapping(_raw(episode_id=episode_id)).episode_id == episode_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": "run_short"},
        {"run_id": "not-a-run-id"},
        {"role": "admin"},
        {"workspace_id": "WS_A"},
        {"workspace_id": "w"},
        {"episode_id": "EP_1"},
        {"episode_id": 1},
        {"tool_profile": ""},
        {"tool_profile": "challenger_ro_v1"},
        {"unknown": True},
    ],
)
def test_invalid_bindings_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(BindingError):
        binding_from_mapping(_raw(**overrides))


def test_missing_required_fields_are_rejected() -> None:
    raw = _raw()
    del raw["run_id"]
    with pytest.raises(BindingError):
        binding_from_mapping(raw)


def test_generic_securitylab_ro_profile_is_accepted_for_read_only_roles() -> None:
    for role in ("researcher", "vehicle_analyst", "challenger", "evidence_verifier"):
        binding = binding_from_mapping(_raw(role=role, tool_profile="securitylab_ro"))
        assert binding.role == role
    with pytest.raises(BindingError):
        binding_from_mapping(_raw(role="lab_runner", tool_profile="securitylab_ro"))


def test_role_tool_allowlists_match_frozen_preset_registry() -> None:
    assert binding_from_mapping(_raw(role="challenger", tool_profile="challenger_ro_v1")).allowed_tools == (
        "lab_catalog",
        "lab_status",
        "lab_read_file",
        "lab_get_report",
    )
    runner = binding_from_mapping(_raw(role="lab_runner", tool_profile="lab_runner_vcan_v1"))
    assert runner.allowed_tools == ("securitylab_vcan",)
    assert runner.tool_server == "securitylab_vcan"
    orchestrator = binding_from_mapping(
        _raw(role="orchestrator", tool_profile="orchestrator_none_v1")
    )
    assert orchestrator.allowed_tools == ()


def test_binding_dataclass_rejects_direct_invalid_values() -> None:
    with pytest.raises(BindingError):
        Binding(
            run_id="bad",
            role="researcher",
            workspace_id="ws_a",
            episode_id=None,
            tool_profile="researcher_ro_v1",
        )
