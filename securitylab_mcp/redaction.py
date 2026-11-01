"""输出脱敏。

contract 要求把 ``token`` / ``api_key`` / ``apikey`` / ``password`` /
``secret`` / ``authorization`` / ``cookie`` 等键值替换为 ``[REDACTED]``。
本模块在文本层面执行同等语义，并额外覆盖：

- JSON 风格的带引号键与值：``{"token":"..."}``、嵌套对象、含空格或
  转义引号的值；替换时保留原引号风格（``"`` / ``'``）；
- YAML / INI / ENV 形式：``password: "..."``、``token = '...'``、
  ``TOKEN=...`` 以及既有的无引号形式；
- ``Bearer`` / ``Basic`` 凭据与 PEM 私钥块。

键名按完整词干 + 分隔符匹配，避免把 ``tokenizer`` 之类的普通词误判为
secret key。脱敏是 fail-closed 的：任何异常都向上传播，由 service 转换为
``redaction-failed``，而不是返回未脱敏内容。
"""

from __future__ import annotations

import re
from typing import Final

_REDACTED: Final = "[REDACTED]"

# 复合键先于其子串排列（如 access_token 先于 token），覆盖 contract
# 要求的最小集合以及常见的下划线/连字符变体。匹配不区分大小写。
_SECRET_KEY_NAMES: Final = (
    r"proxy[_-]?authorization"
    r"|access[_-]?token"
    r"|refresh[_-]?token"
    r"|client[_-]?secret"
    r"|private[_-]?key"
    r"|set[_-]?cookie"
    r"|authorization"
    r"|api[_-]?key"
    r"|password"
    r"|passwd"
    r"|pwd"
    r"|secret"
    r"|token"
    r"|cookie"
)

# 匹配 ``key: value`` / ``key = value``（JSON/YAML/INI/ENV 共用）。
# 组说明：
# - ``prefix`` 保留键及其引号、分隔符和空白，替换时原样输出；
# - ``key_quote`` 捕获 JSON/YAML 键两侧的引号（可为空）；
# - ``double`` / ``single`` 捕获带引号的值，替换时只保留同款引号；
# - ``bare`` 覆盖既有的无引号形式；允许值中出现空格（YAML/INI 常见），
#   但止于行尾或右括号，避免跨行吞掉后续内容。
# 键前的 ``(?<![A-Za-z0-9])`` 保证不会从 ``mytoken`` 中间开始匹配；
# 键后必须紧跟分隔符，因此 ``tokenizer`` / ``tokens`` 不会被误伤。
# 分隔符两侧只允许水平空白，避免跨行匹配。
_KEY_VALUE: Final = re.compile(
    r"(?P<prefix>(?<![A-Za-z0-9])(?P<key_quote>[\"']?)"
    r"(?P<key>" + _SECRET_KEY_NAMES + r")(?P=key_quote)[ \t]*[:=][ \t]*)"
    r"(?:(?P<double>\"(?:[^\"\\]|\\.)*\")"
    r"|(?P<single>'(?:[^'\\]|''|\\.)*')"
    r"|(?P<bare>[^\n\r}\]]+))",
    re.IGNORECASE,
)

# Pretty-printed JSON may put the quoted value on the following line:
#
#   "token":
#     "SENTINEL"
#
# The normal key/value regex deliberately does not cross a line break for
# unquoted YAML/INI values. Handle only the JSON shape (double-quoted key and a
# double-quoted value after a newline) so the value cannot slip through.
_JSON_PRETTY_KEY_VALUE: Final = re.compile(
    r"(?P<prefix>\"(?P<key>" + _SECRET_KEY_NAMES + r")\"[ \t]*:[ \t]*(?:\r?\n[ \t]*)+)"
    r"(?P<value>\"(?:[^\"\\]|\\.)*\")",
    re.IGNORECASE,
)

_AUTH_SCHEME: Final = re.compile(
    r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)

_PRIVATE_KEY_BLOCK: Final = re.compile(
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

    # 先处理 Bearer/Basic，避免键值规则只吃掉 scheme 而把凭据留在行尾。
    output = _AUTH_SCHEME.sub(auth_replacement, content)

    def pretty_json_replacement(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return f'{match.group("prefix")}"{_REDACTED}"'

    output = _JSON_PRETTY_KEY_VALUE.sub(pretty_json_replacement, output)

    def key_value_replacement(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        prefix = match.group("prefix")
        if match.group("double") is not None:
            return f'{prefix}"{_REDACTED}"'
        if match.group("single") is not None:
            return f"{prefix}'{_REDACTED}'"
        return f"{prefix}{_REDACTED}"

    output = _KEY_VALUE.sub(key_value_replacement, output)

    def private_key_replacement(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return _REDACTED + " PRIVATE KEY"

    output = _PRIVATE_KEY_BLOCK.sub(private_key_replacement, output)
    return output, redacted
