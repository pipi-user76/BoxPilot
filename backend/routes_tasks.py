"""文件职责：提供任务创建、分页查询、取消重试、批量清理和产物预览接口。

本模块只处理 HTTP 协议和输入输出，不直接执行沙箱命令。真正的任务执行由
TaskRunner 完成，任务数据由 TaskStore 保存。
"""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from backend.config import settings
from backend.db import TaskStore
from backend.logging_config import get_logger
from backend.models import (
    Artifact,
    ArtifactPreviewResponse,
    BatchDeleteRequest,
    BatchDeleteResponse,
    CleanupRequest,
    CreateTaskRequest,
    TaskListResponse,
    TaskRecord,
    TaskStatus,
    TaskType,
    utc_now,
)
from backend.scheduler import TaskConflict, TaskNotFound, TaskRunner
from backend.storage import ArtifactStore


router = APIRouter()
log = get_logger("api")

# 只有这些扩展名和 MIME 类型按文本预览，其余二进制文件只提供下载。
_TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml", ".xml", ".html", ".htm"}


def _store(request: Request) -> TaskStore:
    """从应用状态中取得任务存储，避免路由内部重新创建存储导致数据割裂。"""
    return request.app.state.task_store


def _runner(request: Request) -> TaskRunner:
    return request.app.state.task_runner


def _artifact_store(request: Request) -> ArtifactStore:
    return request.app.state.artifact_store


def _parse_statuses(raw: str | None) -> list[TaskStatus] | None:
    """支持 status=FAILED,TIMEOUT 形式的多状态筛选。"""
    if not raw:
        return None
    values = [item.strip().upper() for item in raw.split(",") if item.strip()]
    try:
        return [TaskStatus(value) for value in values]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "VALIDATION_ERROR", "message": f"unknown task status: {raw}"},
        ) from exc


@router.post("/tasks", response_model=TaskRecord, status_code=status.HTTP_202_ACCEPTED)
def create_task(
    payload: CreateTaskRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> TaskRecord:
    # 先检查模板是否已注册，避免创建永远无法执行的任务记录。
    runner = _runner(request)
    if payload.task_type == TaskType.CSV_ANALYSIS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CSV_UPLOAD_REQUIRED",
                "message": "CSV_ANALYSIS tasks must be submitted through POST /tasks/csv",
            },
        )
    if payload.task_type not in runner.supported_types():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "TEMPLATE_NOT_AVAILABLE",
                "message": f"task type {payload.task_type.value} is not registered yet",
            },
        )
    task = _store(request).create_task(payload.task_type, payload.params)
    log.info(
        "task_created",
        extra={"task_id": task.id, "task_type": task.task_type.value, "attempt": task.attempt},
    )
    # 返回 202 表示任务已接收但尚未完成；后台任务随后驱动 PENDING -> RUNNING。
    background_tasks.add_task(runner.run, task.id)
    return task


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    request: Request,
    q: str = Query("", max_length=200, description="按任务 ID、类型、参数或错误信息搜索"),
    status_filter: str | None = Query(None, alias="status", description="逗号分隔的状态筛选"),
    task_type: TaskType | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.TASK_PAGE_SIZE_DEFAULT, ge=1, le=settings.TASK_PAGE_SIZE_MAX),
) -> TaskListResponse:
    store = _store(request)
    statuses = _parse_statuses(status_filter)
    items, total = store.list_tasks(
        query=q,
        statuses=statuses,
        task_type=task_type,
        page=page,
        page_size=page_size,
    )
    return TaskListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil(total / page_size)),
        counts=store.count_tasks(),
    )


@router.post("/tasks/batch-delete", response_model=BatchDeleteResponse)
def batch_delete_tasks(payload: BatchDeleteRequest, request: Request) -> BatchDeleteResponse:
    """批量清理已结束任务；等待中或运行中的任务交给取消接口处理。"""
    store = _store(request)
    artifact_store = _artifact_store(request)
    deleted = 0
    skipped: list[str] = []
    for task_id in payload.task_ids:
        task = store.get_task(task_id)
        if task is None or task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            skipped.append(task_id)
            continue
        store.delete_task(task_id)
        artifact_store.delete_task_dir(task_id)
        deleted += 1
    log.info("tasks_batch_deleted", extra={"deleted": deleted, "skipped": len(skipped)})
    return BatchDeleteResponse(deleted=deleted, skipped=skipped)


@router.post("/tasks/cleanup", response_model=BatchDeleteResponse)
def cleanup_tasks(payload: CleanupRequest, request: Request) -> BatchDeleteResponse:
    """按状态和创建时间清理历史任务，默认保留最近 7 天。"""
    active = {TaskStatus.PENDING, TaskStatus.RUNNING}
    if active.intersection(payload.statuses):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "VALIDATION_ERROR", "message": "只能清理已结束的任务"},
        )
    store = _store(request)
    artifact_store = _artifact_store(request)
    cutoff = utc_now() - timedelta(days=payload.older_than_days)
    deleted_ids = store.purge_tasks(payload.statuses, cutoff)
    for task_id in deleted_ids:
        artifact_store.delete_task_dir(task_id)
    log.info("tasks_cleaned", extra={"deleted": len(deleted_ids), "older_than_days": payload.older_than_days})
    return BatchDeleteResponse(deleted=len(deleted_ids))


@router.get("/tasks/{task_id}", response_model=TaskRecord)
def get_task(task_id: str, request: Request) -> TaskRecord:
    task = _store(request).get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TASK_NOT_FOUND", "message": f"task not found: {task_id}"},
        )
    return task


@router.post("/tasks/{task_id}/cancel", response_model=TaskRecord, status_code=status.HTTP_202_ACCEPTED)
def cancel_task(task_id: str, request: Request) -> TaskRecord:
    """取消等待中或运行中的任务；运行中任务在当前步骤结束后停止。"""
    try:
        return _runner(request).request_cancel(task_id)
    except TaskNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TASK_NOT_FOUND", "message": f"task not found: {task_id}"},
        ) from exc
    except TaskConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.post("/tasks/{task_id}/retry", response_model=TaskRecord, status_code=status.HTTP_202_ACCEPTED)
def retry_task(task_id: str, background_tasks: BackgroundTasks, request: Request) -> TaskRecord:
    """为失败、超时或取消的任务创建新的执行记录，原记录保留用于对比。"""
    runner = _runner(request)
    try:
        retried = runner.retry_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TASK_NOT_FOUND", "message": f"task not found: {task_id}"},
        ) from exc
    except TaskConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    background_tasks.add_task(runner.run, retried.id)
    return retried


@router.get("/tasks/{task_id}/artifacts", response_model=list[Artifact])
def list_artifacts(task_id: str, request: Request) -> list[Artifact]:
    task = _store(request).get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TASK_NOT_FOUND", "message": f"task not found: {task_id}"},
        )
    return task.artifacts


def _resolve_artifact(artifact_id: str, request: Request) -> tuple[Artifact, Path]:
    """查找产物记录并解析真实路径，下载与预览共用同一套校验。"""
    found = _store(request).get_artifact(artifact_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ARTIFACT_NOT_FOUND", "message": f"artifact not found: {artifact_id}"},
        )
    artifact, task_id = found
    try:
        path = _artifact_store(request).resolve(task_id, artifact.relative_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ARTIFACT_NOT_FOUND", "message": str(exc)},
        ) from exc
    return artifact, path


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, request: Request, inline: bool = False) -> FileResponse:
    # 下载入口只接受产物 ID，真实路径必须来自已登记记录并经过 ArtifactStore 校验。
    artifact, path = _resolve_artifact(artifact_id, request)
    # inline=1 供图片预览使用，普通下载仍然强制附件形式。
    return FileResponse(
        path=path,
        media_type=artifact.content_type,
        filename=artifact.filename,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.get("/artifacts/{artifact_id}/preview", response_model=ArtifactPreviewResponse)
def preview_artifact(artifact_id: str, request: Request) -> ArtifactPreviewResponse:
    """返回文本预览内容或图片元数据；大文件会截断并标记 truncated。"""
    artifact, path = _resolve_artifact(artifact_id, request)
    suffix = path.suffix.lower()
    is_image = artifact.content_type.startswith("image/")
    is_text = (
        artifact.content_type.startswith("text/")
        or artifact.content_type in {"application/json", "application/xml"}
        or suffix in _TEXT_SUFFIXES
    )
    kind = "image" if is_image else "text" if is_text else "binary"
    text: str | None = None
    truncated = False
    if kind == "text":
        # 先限制读取字节数，再按字符上限截断，避免大文件占满内存。
        byte_limit = settings.PREVIEW_TEXT_LIMIT * 4
        with path.open("rb") as handle:
            raw = handle.read(byte_limit)
        truncated = path.stat().st_size > len(raw)
        decoded = raw.decode("utf-8", errors="replace")
        if len(decoded) > settings.PREVIEW_TEXT_LIMIT:
            decoded = decoded[: settings.PREVIEW_TEXT_LIMIT]
            truncated = True
        text = decoded
    return ArtifactPreviewResponse(
        id=artifact.id,
        filename=artifact.filename,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        kind=kind,
        text=text,
        truncated=truncated,
        url=f"/artifacts/{artifact.id}/download?inline=1",
    )
