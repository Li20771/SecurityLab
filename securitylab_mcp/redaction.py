"""输出脱敏。

contract 要求把 ``token`` / ``api_key`` / ``apikey`` / ``password`` /
``secret`` / ``authorization`` / ``cookie`` 等键值替换为 ``[REDACTED]``。
本模块在文本层面执行同等语义，并额外覆盖 ``Bearer`` / ``Basic`` 凭据与
PEM 私钥块，避免只脱敏键名而遗漏同一行的凭据值。
"""

from __future__ import annotations

import re
from typing import Final

_REDACTED: Final = "[REDACTED]"

_KEY_VALUE = re.compile(
    r"((?:token|api_key|apikey|password|secret|authorization|cookie)\s*[:=]\s*)"
    r"([^\s,;]+)",
    re.IGNORECASE,
)

_AUTH_SCHEME = re.compile(
    r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_text(content: str) -> tuple[str, bool]:
    """返回 ``(脱敏后文本, 是否发生替换)``；内容类型非法时抛 ``TypeError``。"""

    if not isinstance(content, str):
        raise TypeError("content must be text")

    redacted = False

    def auth_replacement(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return f"{match.group(1)} {_REDACTED}"

    output = _AUTH_SCHEME.sub(auth_replacement, content)

    def key_value_replacement(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return f"{match.group(1)}{_REDACTED}"

    output = _KEY_VALUE.sub(key_value_replacement, output)

    def private_key_replacement(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return _REDACTED + " PRIVATE KEY"

    output = _PRIVATE_KEY_BLOCK.sub(private_key_replacement, output)
    return output, redacted
