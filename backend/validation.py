"""文件职责：集中处理任务 ID、文件名、相对路径和 URL 的安全校验。

这些函数是沙箱边界和产物边界的统一入口，路由、适配器和存储层不应各自复制规则。
"""

import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ValidationFailure(ValueError):
    """输入不符合安全边界时抛出；调用方应转换为结构化错误响应。"""
    pass


def validate_task_id(task_id: str) -> str:
    """限制任务 ID 字符集，避免目录名注入和路径拼接异常。"""
    if not _TASK_ID_RE.fullmatch(task_id):
        raise ValidationFailure("task_id contains unsupported characters")
    return task_id


def validate_filename(filename: str) -> str:
    """产物文件名只能是单层文件名，不允许目录分隔符。"""
    if not filename or filename in {".", ".."}:
        raise ValidationFailure("invalid filename")
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValidationFailure("filename must not contain path separators")
    return filename


def safe_relative_path(path: str) -> str:
    """拒绝绝对路径和任何 `..` 片段，确保文件留在任务目录内。"""
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
        raise ValidationFailure("relative path escapes task workspace")
    return pure.as_posix()


def validate_http_url(url: str) -> str:
    """只允许无凭据的 HTTP/HTTPS 绝对地址。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationFailure("url must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise ValidationFailure("credentials in URLs are not allowed")
    return url


def validate_git_url(url: str, allowed_hosts: set[str]) -> str:
    """只允许白名单主机上的 HTTPS Git 仓库地址。

    代码体检会执行仓库中的测试，因此首版不接受 SSH、未知域名、查询参数或 URL 凭据。
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValidationFailure("repository URL must use https")
    if parsed.username or parsed.password:
        raise ValidationFailure("credentials in repository URLs are not allowed")
    if parsed.hostname.lower() not in allowed_hosts:
        raise ValidationFailure("repository host is not trusted")
    if parsed.query or parsed.fragment:
        raise ValidationFailure("repository URL must not contain query or fragment")
    path = parsed.path.rstrip("/")
    if path.count("/") < 2 or not path.strip("/"):
        raise ValidationFailure("repository URL must include owner and repository")
    return url
