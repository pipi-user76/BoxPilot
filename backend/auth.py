"""密码哈希与会话 Cookie 的签名、校验。

不引入额外依赖：密码用标准库 scrypt，会话用 HMAC-SHA256 签名。
签名只保证 Cookie 没被篡改，真正的权限判断仍以数据库里的用户为准。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
SESSION_COOKIE = "boxpilot_session"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    """返回 `scrypt$N$r$p$salt$hash`，参数随哈希一起保存，便于以后调整强度。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """校验密码；格式无法解析时按失败处理，不向调用方抛异常。"""
    try:
        scheme, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = _b64decode(salt_b64)
        expected = _b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def issue_session(user_id: str, secret: str, max_age_seconds: int) -> tuple[str, int]:
    """生成签名会话，返回 (token, 过期时间戳)。"""
    expires = int(time.time()) + max_age_seconds
    payload = _b64encode(json.dumps({"uid": user_id, "exp": expires}).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}", expires


def read_session(token: str | None, secret: str) -> str | None:
    """校验签名与过期时间，合法则返回用户 ID。"""
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        data = json.loads(_b64decode(payload))
        user_id = str(data["uid"])
        expires = int(data["exp"])
    except (ValueError, KeyError, TypeError):
        return None
    if expires < int(time.time()):
        return None
    return user_id
