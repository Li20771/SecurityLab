"""Structured secret-redaction tests (synthetic sentinels only)."""

from __future__ import annotations

import pytest

from securitylab_mcp.redaction import redact_text
from securitylab_mcp.tests.conftest import LabFixture


def _assert_redacted(source: str) -> str:
    result, changed = redact_text(source)
    assert changed is True
    assert "SENTINEL" not in result, result
    assert "[REDACTED]" in result
    return result


def test_json_quoted_key_and_value_are_redacted() -> None:
    result = _assert_redacted('{"token":"SENTINEL"}')
    assert result == '{"token":"[REDACTED]"}'


def test_json_nested_objects_and_arrays_are_redacted() -> None:
    source = (
        '{"outer":{"password":"PASSWORD_SENTINEL"},'
        '"items":[{"api_key":"API_SENTINEL"},{"secret":"SECRET_SENTINEL"}]}'
    )
    result = _assert_redacted(source)
    assert result.count("[REDACTED]") == 3
    assert '"password":"[REDACTED]"' in result
    assert '"api_key":"[REDACTED]"' in result
    assert '"secret":"[REDACTED]"' in result


def test_json_quoted_value_with_spaces_is_fully_replaced() -> None:
    result = _assert_redacted('{"password": "my secret SENTINEL"}')
    assert result == '{"password": "[REDACTED]"}'


def test_json_escaped_quotes_in_value_are_redacted() -> None:
    source = '{"secret":"a\\"b SENTINEL"}'
    result = _assert_redacted(source)
    assert result == '{"secret":"[REDACTED]"}'


def test_json_value_with_spaces_and_escaped_quotes() -> None:
    source = '{"password": "my \\"secret\\" SENTINEL"}'
    result = _assert_redacted(source)
    assert result == '{"password": "[REDACTED]"}'


def test_json_non_secret_pairs_are_preserved() -> None:
    result = _assert_redacted('{"token":"SENTINEL","name":"keep-me"}')
    assert result == '{"token":"[REDACTED]","name":"keep-me"}'


def test_pretty_printed_json_value_on_next_line_is_redacted() -> None:
    source = (
        '{\n'
        '  "token":\n'
        '    "PRETTY_SENTINEL",\n'
        '  "name": "keep-me"\n'
        '}'
    )
    result = _assert_redacted(source)
    assert result == (
        '{\n'
        '  "token":\n'
        '    "[REDACTED]",\n'
        '  "name": "keep-me"\n'
        '}'
    )


def test_pretty_printed_nested_json_is_redacted() -> None:
    source = (
        '{\n'
        '  "outer": {\n'
        '    "password":\n'
        '      "NESTED_PRETTY_SENTINEL"\n'
        '  }\n'
        '}'
    )
    result = _assert_redacted(source)
    assert '"password":\n      "[REDACTED]"' in result
    assert "NESTED_PRETTY_SENTINEL" not in result


@pytest.mark.parametrize(
    "source,expected",
    [
        ('{"token":"SENTINEL"}', '{"token":"[REDACTED]"}'),
        ("{'token': 'SENTINEL'}", "{'token': '[REDACTED]'}"),
        ('token: "SENTINEL"', 'token: "[REDACTED]"'),
        ("token: 'SENTINEL'", "token: '[REDACTED]'"),
        ("token: SENTINEL", "token: [REDACTED]"),
        ("token = 'SENTINEL'", "token = '[REDACTED]'"),
    ],
)
def test_quote_style_is_preserved(source: str, expected: str) -> None:
    result, changed = redact_text(source)
    assert changed is True
    assert result == expected


def test_yaml_quoted_values_are_redacted() -> None:
    source = (
        'password: "my password SENTINEL"\n'
        "token: 'token SENTINEL'\n"
        "api-key: plain SENTINEL\n"
        "note: keep-me\n"
    )
    result = _assert_redacted(source)
    assert result.count("[REDACTED]") == 3
    assert "keep-me" in result


def test_ini_and_env_forms_are_redacted() -> None:
    source = (
        "TOKEN=ENV_TOKEN_SENTINEL\n"
        "PASSWORD = ENV_PASSWORD_SENTINEL\n"
        "pwd=ENV_PWD_SENTINEL\n"
        "api_key: ENV_API_SENTINEL\n"
        "normal=keep-me\n"
    )
    result = _assert_redacted(source)
    assert result.count("[REDACTED]") == 4
    assert "normal=keep-me" in result


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "api-key",
        "password",
        "passwd",
        "pwd",
        "secret",
        "client_secret",
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "private_key",
    ],
)
def test_required_key_names_are_redacted(key: str) -> None:
    result = _assert_redacted(f'{{"{key}":"KEY_SENTINEL"}}')
    assert result == f'{{"{key}":"[REDACTED]"}}'


@pytest.mark.parametrize("key", ["TOKEN", "Password", "Client_Secret", "API-KEY"])
def test_key_matching_is_case_insensitive(key: str) -> None:
    result = _assert_redacted(f"{key}=CASE_SENTINEL")
    assert result == f"{key}=[REDACTED]"


@pytest.mark.parametrize(
    "source",
    [
        "tokenizer: keep-me",
        "tokens: keep-me",
        "token_count = 5",
        "mytoken = 5",
        "The tokenizer is fast and has no credentials.",
    ],
)
def test_key_boundaries_avoid_ordinary_word_false_positives(source: str) -> None:
    result, changed = redact_text(source)
    assert changed is False
    assert result == source


def test_bearer_and_basic_credentials_are_redacted() -> None:
    source = (
        "Authorization: Bearer BEARER_SENTINEL\n"
        "Proxy-Authorization: Basic BASIC_SENTINEL\n"
        "authorization: bearer BEARER_TOKEN_SENTINEL\n"
    )
    result = _assert_redacted(source)
    assert "[REDACTED]" in result
    assert "BEARER_TOKEN_SENTINEL" not in result


def test_quoted_authorization_bearer_value_is_redacted() -> None:
    result = _assert_redacted('{"authorization":"Bearer QUOTED_BEARER_SENTINEL"}')
    assert result == '{"authorization":"[REDACTED]"}'


def test_pem_private_key_block_is_redacted() -> None:
    source = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "PEM_BLOCK_SENTINEL\n"
        "-----END RSA PRIVATE KEY-----"
    )
    result, changed = redact_text(source)
    assert changed is True
    assert "PEM_BLOCK_SENTINEL" not in result
    assert "[REDACTED] PRIVATE KEY" in result


def test_normal_text_is_preserved() -> None:
    source = "This sentence has no secrets.\nReport A: synthetic fixture.\n"
    result, changed = redact_text(source)
    assert changed is False
    assert result == source


def test_empty_text_is_unchanged() -> None:
    assert redact_text("") == ("", False)


@pytest.mark.parametrize("value", [None, 123, b"bytes", ["token"]])
def test_non_text_input_raises_type_error(value: object) -> None:
    with pytest.raises(TypeError):
        redact_text(value)  # type: ignore[arg-type]


def test_lab_read_file_marks_structured_redaction(lab: LabFixture) -> None:
    path = lab.ws_a / "structured.json"
    path.write_text(
        '{"token":"SERVICE_SENTINEL","name":"keep-me"}', encoding="utf-8"
    )
    result = lab.service().call_tool("lab_read_file", {"path": "structured.json"})
    assert result["ok"] is True
    assert result["meta"]["redacted"] is True
    content = result["data"]["content"]
    assert "SERVICE_SENTINEL" not in content
    assert '"token":"[REDACTED]"' in content
    assert '"name":"keep-me"' in content


def test_lab_read_file_marks_yaml_redaction(lab: LabFixture) -> None:
    path = lab.ws_a / "config.yaml"
    path.write_text('password: "YAML SECRET SENTINEL"\n', encoding="utf-8")
    result = lab.service().call_tool("lab_read_file", {"path": "config.yaml"})
    assert result["ok"] is True
    assert result["meta"]["redacted"] is True
    assert "YAML SECRET SENTINEL" not in result["data"]["content"]
    assert 'password: "[REDACTED]"' in result["data"]["content"]
