"""站内通知接口：列表、未读数、单条已读与全部已读。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from backend.db import TaskStore
from backend.models import NotificationList, UserRecord

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _store(request: Request) -> TaskStore:
    return request.app.state.task_store


def _user(request: Request) -> UserRecord:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "请先登录"},
        )
    return user


@router.get("", response_model=NotificationList)
def list_notifications(request: Request, limit: int = 20, unread_only: bool = False) -> NotificationList:
    user = _user(request)
    items = _store(request).list_notifications(
        user.id, limit=min(max(limit, 1), 100), unread_only=unread_only
    )
    return NotificationList(
        items=items, unread=_store(request).unread_notifications(user.id)
    )


@router.get("/unread")
def unread_count(request: Request) -> dict:
    user = _user(request)
    return {"unread": _store(request).unread_notifications(user.id)}


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(notification_id: str, request: Request) -> None:
    user = _user(request)
    if not _store(request).mark_notification_read(notification_id, user.id):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOTIFICATION_NOT_FOUND", "message": "通知不存在"},
        )
    return None


@router.post("/read-all")
def mark_all_read(request: Request) -> dict:
    user = _user(request)
    return {"updated": _store(request).mark_all_notifications_read(user.id)}
