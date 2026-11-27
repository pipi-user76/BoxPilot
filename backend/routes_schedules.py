"""定时任务接口：增删改查、手动触发与执行历史。

定时任务只保存“模板 + 参数 + cron”，真正执行仍走统一 TaskRunner；
删除定时任务不会影响它已经创建的历史任务。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from backend.cron import CronError
from backend.db import TaskStore
from backend.models import (
    ScheduleCreate,
    ScheduleRecord,
    ScheduleUpdate,
    TaskRecord,
    UserRecord,
    utc_now,
)
from backend.schedules import next_run_for

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _store(request: Request) -> TaskStore:
    return request.app.state.task_store


def _runner(request: Request):
    return getattr(request.app.state, "schedule_runner", None)


def _user(request: Request) -> UserRecord:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "请先登录"},
        )
    return user


def _owner(request: Request) -> str | None:
    user = _user(request)
    return None if user.is_admin else user.id


def _scoped(request: Request, schedule_id: str) -> ScheduleRecord:
    for record in _store(request).list_schedules(_owner(request)):
        if record.id == schedule_id:
            return record
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "SCHEDULE_NOT_FOUND", "message": "定时任务不存在"},
    )


def _validate_cron(expression: str):
    try:
        return next_run_for(expression, utc_now())
    except (CronError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_CRON", "message": str(exc)},
        ) from exc


@router.get("", response_model=list[ScheduleRecord])
def list_schedules(request: Request) -> list[ScheduleRecord]:
    return _store(request).list_schedules(_owner(request))


@router.post("", response_model=ScheduleRecord, status_code=status.HTTP_201_CREATED)
def create_schedule(payload: ScheduleCreate, request: Request) -> ScheduleRecord:
    _validate_cron(payload.cron)
    templates = getattr(request.app.state, "templates", {})
    if templates and payload.task_type not in templates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "TEMPLATE_NOT_AVAILABLE", "message": "该任务类型尚未注册"},
        )
    user = _user(request)
    runner = _runner(request)
    if runner is not None:
        return runner.create(payload, owner_id=user.id)
    record = _store(request).create_schedule(
        payload.task_type,
        payload.params,
        payload.cron.strip(),
        next_run_for(payload.cron, utc_now()),
        owner_id=user.id,
        name=payload.name.strip(),
    )
    return record


@router.patch("/{schedule_id}", response_model=ScheduleRecord)
def update_schedule(
    schedule_id: str, payload: ScheduleUpdate, request: Request
) -> ScheduleRecord:
    record = _scoped(request, schedule_id)
    next_at = None
    if payload.enabled:
        next_at = _validate_cron(record.cron)
    updated = _store(request).set_schedule_enabled(
        schedule_id, payload.enabled, next_at
    )
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCHEDULE_NOT_FOUND", "message": "定时任务不存在"},
        )
    runner = _runner(request)
    if runner is not None:
        runner.sync(updated)
    return updated


@router.put("/{schedule_id}", response_model=ScheduleRecord)
def edit_schedule(
    schedule_id: str, payload: ScheduleCreate, request: Request
) -> ScheduleRecord:
    _scoped(request, schedule_id)
    next_at = _validate_cron(payload.cron)
    templates = getattr(request.app.state, "templates", {})
    if templates and payload.task_type not in templates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "TEMPLATE_NOT_AVAILABLE", "message": "该任务类型尚未注册"},
        )
    updated = _store(request).update_schedule(
        schedule_id,
        name=payload.name.strip(),
        task_type=payload.task_type,
        params=payload.params,
        cron=payload.cron.strip(),
        next_run_at=next_at,
    )
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCHEDULE_NOT_FOUND", "message": "定时任务不存在"},
        )
    runner = _runner(request)
    if runner is not None:
        runner.sync(updated)
    return updated


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: str, request: Request) -> None:
    _scoped(request, schedule_id)
    runner = _runner(request)
    if runner is not None:
        runner.remove(schedule_id)
    if not _store(request).delete_schedule(schedule_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "SCHEDULE_NOT_FOUND", "message": "定时任务不存在"},
        )
    return None


@router.post(
    "/{schedule_id}/run",
    response_model=TaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_schedule(schedule_id: str, request: Request) -> TaskRecord:
    record = _scoped(request, schedule_id)
    runner = _runner(request)
    if runner is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "SCHEDULER_UNAVAILABLE", "message": "调度器未启动"},
        )
    task = runner.fire(record.id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCHEDULE_NOT_FOUND", "message": "定时任务不存在"},
        )
    return task


@router.get("/{schedule_id}/runs", response_model=list[TaskRecord])
def schedule_runs(schedule_id: str, request: Request, limit: int = 20) -> list[TaskRecord]:
    _scoped(request, schedule_id)
    return _store(request).schedule_runs(schedule_id, limit=min(max(limit, 1), 100))
