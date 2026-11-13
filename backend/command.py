"""文件职责：构造沙箱 Shell 命令的唯一边界。

模板只描述“要执行什么”，用户输入一律经过 quote_arg 的白名单与转义；
禁止在模板里用 f-string 直接拼接用户参数，避免命令注入和选项注入。
"""

from __future__ import annotations

import re
import shlex

from backend.config import settings

# URL 允许的可见字符集合；空白、换行、分号、管道、反引号等一律拒绝。
_URL_SAFE = re.compile(r"^[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+$")
_FILE_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


class CommandBuildError(ValueError):
    """命令参数不满足白名单时抛出；调用方应转换为校验错误。"""


def quote_arg(value: str, *, max_length: int | None = None) -> str:
    """校验字符集与长度，再按 POSIX shell 规则转义。"""
    limit = max_length or settings.COMMAND_MAX_LENGTH
    if not isinstance(value, str) or not value:
        raise CommandBuildError("empty command argument")
    if len(value) > limit:
        raise CommandBuildError("command argument is too long")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise CommandBuildError("command argument contains control characters")
    return shlex.quote(value)


def quote_url(url: str) -> str:
    """URL 额外限制为可见字符集合，避免引号与控制字符进入命令。"""
    if len(url) > settings.URL_MAX_LENGTH or not _URL_SAFE.fullmatch(url):
        raise CommandBuildError("url contains unsupported characters")
    return quote_arg(url)


def quote_filename(name: str) -> str:
    """文件名只允许字母、数字、点、下划线和短横线。"""
    if not _FILE_SAFE.fullmatch(name):
        raise CommandBuildError("filename contains unsupported characters")
    return quote_arg(name, max_length=255)


def build_command(*parts: str, terminator: bool = False) -> str:
    """把固定模板与已转义的参数拼成一条命令。

    terminator=True 时在末尾追加 `--`，确保以 `-` 开头的用户值不会被当成选项。
    """
    if not parts:
        raise CommandBuildError("command template is empty")
    command = " ".join(parts)
    if len(command) > settings.COMMAND_MAX_LENGTH:
        raise CommandBuildError("command is too long")
    if terminator:
        command = f"{command} --"
    return command
