"""文件职责：集中处理任务 ID、文件名、相对路径和 URL 的安全校验。

这些函数是沙箱边界和产物边界的统一入口，路由、适配器和存储层不应各自复制规则。
"""

import ipaddress
import re
import socket
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from backend.config import settings


_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# 这些主机指向容器宿主或云元数据，必须显式拦截，不能只依赖 IP 判定。
_BLOCKED_HOSTNAMES = {
    "host.docker.internal",
    "gateway.docker.internal",
    "metadata.google.internal",
}
_BLOCKED_SUFFIXES = (".docker.internal", ".internal", ".local")
_ALLOWED_PORTS = {80, 443}


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
    """只允许无凭据的 HTTP/HTTPS 绝对地址，并限制长度与控制字符。"""
    if len(url) > settings.URL_MAX_LENGTH:
        raise ValidationFailure("地址过长")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ValidationFailure("地址包含控制字符")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationFailure("地址必须是完整的 http 或 https 链接")
    if parsed.username or parsed.password:
        raise ValidationFailure("地址里不能包含账号密码")
    if parsed.port not in {None, *_ALLOWED_PORTS}:
        raise ValidationFailure("只允许 80 或 443 端口")
    return url


def _is_blocked_address(address: str) -> bool:
    """回环、内网、链路本地、保留地址都不能作为抓取目标。"""
    ip = ipaddress.ip_address(address)
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_public_addresses(host: str, port: int) -> list[str]:
    """解析域名并返回全部地址；任一地址落在内网即整体拒绝。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValidationFailure("域名无法解析") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise ValidationFailure("域名无法解析")
    blocked = [address for address in addresses if _is_blocked_address(address)]
    if blocked:
        raise ValidationFailure("域名指向内网地址")
    return addresses


def validate_public_url(url: str) -> str:
    """在语法校验之后解析域名，阻止指向本机、内网和云元数据的地址。

    注意：应用层校验挡不住浏览器内的跳转，部署时仍应给沙箱配置出站白名单。
    """
    validate_http_url(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise ValidationFailure("该域名不允许访问")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    resolve_public_addresses(host, port)
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
