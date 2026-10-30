"""Path policy, containment and bounded-read tests.

Traversal cases only compute/validate paths; they never write to the
would-be escape target. The outside sentinel is only read back to prove it
was not modified.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from securitylab_mcp.paths import (
    PathPolicyError,
    ResourceUnavailableError,
    SymlinkEscapeError,
    is_sensitive_path,
    is_within,
    read_text_bounded,
    resolve_contained,
    sanitize_relative_path,
    truncate_utf8,
)


@pytest.mark.parametrize(
    "value",
    [
        "notes.txt",
        "sub/inside.txt",
        "reports/report-1.md",
        "a-b_c.123",
    ],
)
def test_relative_paths_are_accepted(value: str) -> None:
    assert sanitize_relative_path(value) == value


def test_backslash_is_treated_as_separator() -> None:
    assert sanitize_relative_path("sub\\inside.txt") == "sub/inside.txt"


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "../outside.txt",
        "a/../outside.txt",
        "a/./inside.txt",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "c:relative",
        "\\\\server\\share\\file",
        "//server/share/file",
        "a//b",
        "a/",
        "notes.txt\x00",
        "notes.txt\n",
        "bad\ud800name",
        "notes.txt:stream",
        "CON",
        "con.txt",
        "a/PRN.log",
        "LPT9",
        "a/trailing ",
        "a/dot.",
        "x" * 513,
        123,
    ],
)
def test_unsafe_paths_are_rejected(value: object) -> None:
    with pytest.raises(PathPolicyError):
        sanitize_relative_path(value)


def test_sensitive_path_policy() -> None:
    sensitive = [
        ".env",
        ".env.local",
        ".git",
        ".git/config",
        ".ssh/id_rsa",
        ".dsh/.credentials.yaml",
        "credentials/foo",
        "secrets/foo",
        "id_rsa",
        "foo.key",
        "foo.pem",
        "foo.p12",
        "foo.pfx",
    ]
    for path in sensitive:
        assert is_sensitive_path(path) is True, path
    for path in ["secret.txt", "reports/report-1.md", "labs/firmware/sample.bin"]:
        assert is_sensitive_path(path) is False, path


def test_containment_helper_is_component_wise(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert is_within(root, root)
    assert is_within(root, root / "child")
    assert not is_within(root, tmp_path / "root2")
    assert not is_within(root, tmp_path / "root" / ".." / "outside")


def test_resolve_contained_accepts_inside_and_rejects_lexical_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "sub" / "file.txt"
    inside.parent.mkdir()
    inside.write_text("inside", encoding="utf-8")
    assert resolve_contained(root, "sub/file.txt") == inside.resolve()
    with pytest.raises(PathPolicyError):
        resolve_contained(root, "../outside.txt")


def test_symlink_escape_is_detected_and_target_is_not_written(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("UNCHANGED", encoding="utf-8")
    try:
        os.symlink(sentinel, root / "link-out")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted in this environment")

    with pytest.raises(SymlinkEscapeError):
        resolve_contained(root, "link-out")
    with pytest.raises(SymlinkEscapeError):
        read_text_bounded(root, "link-out", 1024)
    assert sentinel.read_text(encoding="utf-8") == "UNCHANGED"


def test_symlink_escape_math_with_injected_realpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.txt").write_text("inside", encoding="utf-8")
    outside = Path(os.path.realpath(tmp_path / "outside" / "file.txt"))

    def fake_canonicalize(path: str | Path) -> Path:
        candidate = Path(path)
        if candidate.name == "file.txt":
            return outside
        return Path(os.path.realpath(os.path.abspath(candidate)))

    monkeypatch.setattr("securitylab_mcp.paths.canonicalize", fake_canonicalize)
    with pytest.raises(SymlinkEscapeError):
        resolve_contained(root, "file.txt")


def test_symlink_inside_root_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("inside", encoding="utf-8")
    try:
        os.symlink("notes.txt", root / "link-in")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted in this environment")
    result = read_text_bounded(root, "link-in", 1024)
    assert result.content == "inside"
    assert result.truncated is False


def test_read_text_bounded_limits_and_rejects_non_text(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "large.txt"
    target.write_text("x" * 70000, encoding="utf-8")
    result = read_text_bounded(root, "large.txt", 65536)
    assert result.truncated is True
    assert result.byte_count == 65536
    assert len(result.content) == 65536

    binary = root / "binary.bin"
    binary.write_bytes(b"\xff\xfe\x00binary")
    with pytest.raises(ResourceUnavailableError):
        read_text_bounded(root, "binary.bin", 1024)

    bad_tail = root / "bad-tail.txt"
    bad_tail.write_bytes(b"abc\xff")
    with pytest.raises(ResourceUnavailableError):
        read_text_bounded(root, "bad-tail.txt", 1024)

    multibyte = root / "multibyte.txt"
    multibyte.write_text("é" * 40000, encoding="utf-8")
    multibyte_result = read_text_bounded(root, "multibyte.txt", 65536)
    assert multibyte_result.truncated is True
    assert multibyte_result.byte_count <= 65536
    assert multibyte_result.byte_count % 2 == 0

    directory = root / "dir"
    directory.mkdir()
    with pytest.raises(ResourceUnavailableError):
        read_text_bounded(root, "dir", 1024)

    with pytest.raises(ResourceUnavailableError):
        read_text_bounded(root, "missing.txt", 1024)


def test_read_text_bounded_rejects_sensitive_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / ".env").write_text("TOKEN=synthetic", encoding="utf-8")
    with pytest.raises(ResourceUnavailableError):
        read_text_bounded(root, ".env", 1024)


def test_truncate_utf8_never_splits_a_character() -> None:
    content = "é" * 40000
    truncated, byte_count, was_truncated = truncate_utf8(content, 65536)
    assert was_truncated is True
    assert byte_count <= 65536
    assert byte_count % 2 == 0
    assert truncated.encode("utf-8").decode("utf-8") == truncated
