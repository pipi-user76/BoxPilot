"""文件职责：提供任务创建、查询、步骤详情和已登记产物下载接口。

本模块只处理 HTTP 协议和输入输出，不直接执行沙箱命令。真正的任务执行由
TaskRunner 完成，任务数据由 TaskStore 保存。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import FileResponse

from backend.db import TaskStore
from backend.models import Artifact, CreateTaskRequest, TaskRecord
from backend.scheduler import TaskRunner
from backend.storage import ArtifactStore


router = APIRouter()


def _store(request: Request) -> TaskStore:
    """从应用状态中取得任务存储，避免路由内部重新创建存储导致数据割裂。"""
    return request.app.state.task_store


def _runner(request: Request) -> TaskRunner:
    return request.app.state.task_runner


@router.post("/tasks", response_model=TaskRecord, status_code=status.HTTP_202_ACCEPTED)
def create_task(
    payload: CreateTaskRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> TaskRecord:
    # 先检查模板是否已注册，避免创建永远无法执行的任务记录。
    runner = _runner(request)
    if payload.task_type not in runner.supported_types():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "TEMPLATE_NOT_AVAILABLE",
                "message": f"task type {payload.task_type.value} is not registered yet",
            },
        )
    task = _store(request).create_task(payload.task_type, payload.params)
    # 返回 202 表示任务已接收但尚未完成；后台任务随后驱动 PENDING -> RUNNING。
    background_tasks.add_task(runner.run, task.id)
    return task


@router.get("/tasks", response_model=list[TaskRecord])
def list_tasks(request: Request) -> list[TaskRecord]:
    return _store(request).list_tasks()


@router.get("/tasks/{task_id}", response_model=TaskRecord)
def get_task(task_id: str, request: Request) -> TaskRecord:
    task = _store(request).get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TASK_NOT_FOUND", "message": f"task not found: {task_id}"},
        )
    return task


@router.get("/tasks/{task_id}/artifacts", response_model=list[Artifact])
def list_artifacts(task_id: str, request: Request) -> list[Artifact]:
    task = _store(request).get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TASK_NOT_FOUND", "message": f"task not found: {task_id}"},
        )
    return task.artifacts


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, request: Request) -> FileResponse:
    # 下载入口只接受产物 ID，真实路径必须来自已登记记录并经过 ArtifactStore 校验。
    store = _store(request)
    artifact_store: ArtifactStore = request.app.state.artifact_store
    found = store.get_artifact(artifact_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ARTIFACT_NOT_FOUND", "message": f"artifact not found: {artifact_id}"},
        )
    artifact, task_id = found
    try:
        path = artifact_store.resolve(task_id, artifact.relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ARTIFACT_NOT_FOUND", "message": str(exc)},
        ) from exc
    return FileResponse(path=path, media_type=artifact.content_type, filename=artifact.filename)
