"""路径规范化、root containment 与有界只读。

安全语义：

- 调用方只提供 **root 相对 POSIX 路径**；绝对路径、盘符、UNC、``..`` /
  ``.`` 段、NUL/控制字符、ADS、Windows 保留名一律拒绝；
- 服务端先做规范化 + root containment，再对 **realpath** 复核 containment，
  以拒绝 symlink/junction escape；
- 只允许读取 root 内已存在的普通文件；目录、设备、FIFO、非 UTF-8 文本
  一律按 ``not-found`` 处理，不泄露类型或存在性；
- 不读取已知凭据路径（``.env`` / ``.git`` / ``.ssh`` / 私钥后缀等）。

本模块只读；除 ``read_text_bounded`` 外不做任何 I/O。路径穿越测试只调用
``sanitize_relative_path`` / containment 计算并断言，不对外部目标写入。
"""

from __future__ import annotations

import codecs
import os
import re
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

CONTROL_CHARS: Final = re.compile(r"[\u0000-\u001f\u007f]")
DRIVE_PREFIX: Final = re.compile(r"^[A-Za-z]:")
WINDOWS_RESERVED: Final = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE
)

MAX_RELATIVE_PATH_LENGTH: Final = 512

_SENSITIVE_DIRS: Final = frozenset({".git", ".ssh", ".dsh", "credentials", "secrets"})
_SENSITIVE_NAMES: Final = frozenset(
    {
        ".env",
        "credentials.yaml",
        ".credentials.yaml",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials",
        "secrets",
    }
)
_SENSITIVE_SUFFIXES: Final = (".key", ".pem", ".p12", ".pfx")


class PathPolicyError(ValueError):
    """路径语法不合法或词法上越界（映射为 ``path-out-of-root``）。"""


class SymlinkEscapeError(ValueError):
    """realpath 解析后逃逸 root（映射为 ``symlink-escape``）。"""


class ResourceUnavailableError(ValueError):
    """root 内资源不存在、不可读或不是受支持的 UTF-8 文本（映射为 ``not-found``）。"""


def sanitize_relative_path(value: object) -> str:
    """校验并规范化 root 相对路径；失败抛 ``PathPolicyError``。

    行为与 Workbench ``path-safety.ts`` 的 ``sanitizeRelativePath`` 对齐：
    反斜杠视为分隔符，但拒绝一切可能歧义的绝对/越界/Windows 特殊形式。
    """

    if not isinstance(value, str):
        raise PathPolicyError("path must be a string")
    if not value:
        raise PathPolicyError("path is empty")
    if len(value) > MAX_RELATIVE_PATH_LENGTH:
        raise PathPolicyError("path is too long")
    if CONTROL_CHARS.search(value):
        raise PathPolicyError("path contains control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PathPolicyError("path is not valid UTF-8") from exc
    normalized = value.replace("\\", "/")
    if normalized.startswith("/"):
        raise PathPolicyError("absolute paths are not allowed")
    if DRIVE_PREFIX.match(normalized):
        raise PathPolicyError("drive-qualified paths are not allowed")
    if normalized.startswith("//"):
        raise PathPolicyError("UNC paths are not allowed")
    segments = normalized.split("/")
    for segment in segments:
        if not segment:
            raise PathPolicyError("empty path segment")
        if segment in (".", ".."):
            raise PathPolicyError("relative traversal segments are not allowed")
        if ":" in segment:
            raise PathPolicyError("alternate data streams are not allowed")
        if segment.endswith(".") or segment.endswith(" "):
            raise PathPolicyError("Windows trailing dot/space is not allowed")
        if WINDOWS_RESERVED.match(segment):
            raise PathPolicyError("Windows reserved names are not allowed")
    return "/".join(segments)


def is_sensitive_path(relative_path: str) -> bool:
    """已知凭据/仓库内部路径返回 True：调用方按 not-found 处理。"""

    segments = relative_path.split("/")
    lowered = [segment.lower() for segment in segments]
    if any(segment in _SENSITIVE_DIRS for segment in lowered):
        return True
    name = lowered[-1]
    if name in _SENSITIVE_NAMES or name.startswith(".env."):
        return True
    return name.endswith(_SENSITIVE_SUFFIXES)


def canonicalize(path: Path) -> Path:
    """``abspath`` + ``realpath``；用于配置加载期固定 root。"""

    return Path(os.path.realpath(os.path.abspath(path)))


def is_within(root: Path, candidate: Path) -> bool:
    """组件级 containment；Windows 下大小写不敏感。"""

    root_norm = os.path.normcase(os.path.abspath(root))
    candidate_norm = os.path.normcase(os.path.abspath(candidate))
    try:
        common = os.path.commonpath([root_norm, candidate_norm])
    except ValueError:
        return False
    return common == root_norm


def resolve_contained(root: Path, relative_path: str) -> Path:
    """返回 root 内、realpath 复核后的目标路径。

    只做路径计算，不要求目标存在；存在性由调用方另行处理。
    """

    relative = sanitize_relative_path(relative_path)
    lexical = Path(os.path.abspath(root.joinpath(*relative.split("/"))))
    if not is_within(root, lexical):
        raise PathPolicyError("path is outside the bound root")
    real_root = canonicalize(root)
    real_candidate = canonicalize(lexical)
    if not is_within(real_root, real_candidate):
        raise SymlinkEscapeError("resolved path escapes the bound root")
    return real_candidate


@dataclass(frozen=True)
class BoundedText:
    content: str
    byte_count: int
    truncated: bool


def _decode_utf8_prefix(raw: bytes, *, complete: bool) -> str:
    """解码已读取的字节前缀。

    ``complete=True`` 表示已读到文件末尾，末尾不完整序列视为非 UTF-8 文本；
    ``complete=False`` 表示按输出上限截断读取，允许末尾半个字符。
    """

    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        return decoder.decode(raw, final=complete)
    except UnicodeDecodeError as exc:
        raise ResourceUnavailableError("file is not available as UTF-8 text") from exc


def truncate_utf8(content: str, limit: int) -> tuple[str, int, bool]:
    """按 UTF-8 字节上限截断，不产生半个字符。"""

    encoded = content.encode("utf-8")
    if len(encoded) <= limit:
        return content, len(encoded), False
    cut = encoded[:limit]
    while cut:
        try:
            decoded = cut.decode("utf-8")
            break
        except UnicodeDecodeError:
            cut = cut[:-1]
    else:
        decoded = ""
    return decoded, len(decoded.encode("utf-8")), True


def _same_path(left: Path, right: Path) -> bool:
    left_norm = os.path.normcase(os.path.abspath(left))
    right_norm = os.path.normcase(os.path.abspath(right))
    return left_norm == right_norm


def read_text_bounded(
    root: Path,
    relative_path: str,
    limit: int,
    protected_paths: Iterable[Path] = (),
) -> BoundedText:
    """读取 root 内的 UTF-8 文本文件，最多返回 ``limit`` 字节。

    读取阶段只取 ``limit + 4`` 字节，避免大文件/设备文件造成无界内存占用。
    ``protected_paths`` 用于拒绝读取 server config / binding 等受信文件本身。
    """

    relative = sanitize_relative_path(relative_path)
    if is_sensitive_path(relative):
        raise ResourceUnavailableError("file is not available")
    target = resolve_contained(root, relative)
    if any(_same_path(target, protected) for protected in protected_paths):
        raise ResourceUnavailableError("file is not available")
    try:
        info = target.stat()
    except OSError as exc:
        raise ResourceUnavailableError("file is not available") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ResourceUnavailableError("file is not available")
    try:
        with target.open("rb") as handle:
            raw = handle.read(limit + 4)
    except OSError as exc:
        raise ResourceUnavailableError("file is not available") from exc
    file_truncated = info.st_size > len(raw)
    text = _decode_utf8_prefix(raw, complete=not file_truncated)
    content, byte_count, capped = truncate_utf8(text, limit)
    return BoundedText(content=content, byte_count=byte_count, truncated=file_truncated or capped)
