import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ValidationFailure(ValueError):
    pass


def validate_task_id(task_id: str) -> str:
    if not _TASK_ID_RE.fullmatch(task_id):
        raise ValidationFailure("task_id contains unsupported characters")
    return task_id


def validate_filename(filename: str) -> str:
    if not filename or filename in {".", ".."}:
        raise ValidationFailure("invalid filename")
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValidationFailure("filename must not contain path separators")
    return filename


def safe_relative_path(path: str) -> str:
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
        raise ValidationFailure("relative path escapes task workspace")
    return pure.as_posix()


def validate_http_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationFailure("url must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise ValidationFailure("credentials in URLs are not allowed")
    return url
