"""登录、登出、当前用户与账号创建接口。

会话使用 HttpOnly + SameSite=Lax 的签名 Cookie；所有任务接口再从数据库里的
owner_id 做归属校验，Cookie 只解决“你是谁”，不解决“你能看什么”。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status

from backend.auth import SESSION_COOKIE, hash_password, issue_session, verify_password
from backend.auth_store import AuthStore
from backend.config import settings
from backend.models import (
    AuthResponse,
    LoginRequest,
    NewUserRequest,
    PasswordResetRequest,
    RegisterRequest,
    UserRecord,
    utc_now,
)

router = APIRouter(prefix="/auth")
_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def _store(request: Request) -> AuthStore:
    return request.app.state.auth_store


def _secret(request: Request) -> str:
    return request.app.state.session_secret


def _set_session(response: Response, user: UserRecord, request: Request) -> datetime:
    max_age = settings.SESSION_MAX_AGE_SECONDS
    token, expires = issue_session(user.id, _secret(request), max_age)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )
    return datetime.fromtimestamp(expires, tz=timezone.utc)


def _validate_username(username: str) -> str:
    value = username.strip()
    if not _USERNAME.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "VALIDATION_ERROR", "message": "用户名只能包含字母、数字、下划线、点或短横线，长度 3 到 32 位"},
        )
    return value


@router.get("/bootstrap")
def bootstrap(request: Request) -> dict[str, bool]:
    """登录页据此决定是否显示“创建管理员”；不泄露账号数量以外的信息。"""
    return {"needs_admin": _store(request).count_users() == 0}


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, request: Request, response: Response) -> AuthResponse:
    found = _store(request).get_user_with_hash(payload.username)
    if found is None or not verify_password(payload.password, found[1]):
        # 不区分“用户不存在”和“密码错误”，避免被用来枚举账号。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS", "message": "用户名或密码不正确"},
        )
    user, _ = found
    _store(request).touch_login(user.id)
    expires_at = _set_session(response, user, request)
    return AuthResponse(user=user, expires_at=expires_at)


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserRecord)
def me(request: Request) -> UserRecord:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "请先登录"},
        )
    return user


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, response: Response) -> AuthResponse:
    """只允许创建第一个账号（管理员）；之后必须由管理员添加。"""
    store = _store(request)
    if store.count_users() > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "REGISTRATION_CLOSED", "message": "已有账号，请联系管理员创建新用户"},
        )
    username = _validate_username(payload.username)
    user = store.create_user(username, hash_password(payload.password), is_admin=True)
    expires_at = _set_session(response, user, request)
    return AuthResponse(user=user, expires_at=expires_at)


@router.get("/users", response_model=list[UserRecord])
def list_users(request: Request) -> list[UserRecord]:
    """管理员查看账号列表；普通用户返回 403。"""
    current = getattr(request.state, "user", None)
    if current is None or not current.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "只有管理员可以查看账号"},
        )
    return _store(request).list_users()


@router.post("/users", response_model=UserRecord, status_code=status.HTTP_201_CREATED)
def create_user(payload: NewUserRequest, request: Request) -> UserRecord:
    """管理员创建账号；普通用户调用返回 403。"""
    current = getattr(request.state, "user", None)
    if current is None or not current.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "只有管理员可以创建账号"},
        )
    username = _validate_username(payload.username)
    try:
        return _store(request).create_user(
            username,
            hash_password(payload.password),
            is_admin=payload.is_admin,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "USERNAME_TAKEN", "message": "用户名已存在"},
        ) from exc


def _require_admin(request: Request, action: str) -> UserRecord:
    current = getattr(request.state, "user", None)
    if current is None or not current.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"只有管理员可以{action}"},
        )
    return current


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, request: Request) -> Response:
    """删除账号；不允许删自己，也不允许删掉最后一个管理员。"""
    current = _require_admin(request, "删除账号")
    store = _store(request)
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "账号不存在"},
        )
    if target.id == current.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CANNOT_DELETE_SELF", "message": "不能删除当前登录的账号"},
        )
    if target.is_admin:
        admins = [user for user in store.list_users() if user.is_admin]
        if len(admins) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "LAST_ADMIN", "message": "至少需要保留一个管理员"},
            )
    store.delete_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(user_id: str, payload: PasswordResetRequest, request: Request) -> Response:
    """管理员重置指定账号的密码；旧密码立即失效。"""
    _require_admin(request, "重置密码")
    store = _store(request)
    if store.get_user(user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "账号不存在"},
        )
    store.set_password_hash(user_id, hash_password(payload.password))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
