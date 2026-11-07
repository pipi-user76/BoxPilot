"""文件职责：提供 CSV 上传接口，保存文件后交给统一任务调度器。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile, status

from backend.config import settings
from backend.models import TaskRecord, TaskStatus, TaskType
from backend.storage import ArtifactStore
from backend.scheduler import TaskRunner


router = APIRouter()


@router.post("/tasks/csv", response_model=TaskRecord, status_code=status.HTTP_202_ACCEPTED)
async def create_csv_task(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
) -> TaskRecord:
    """校验 CSV 文件、保存到任务目录，并异步启动固定分析流程。"""
    original_name = Path(file.filename or "").name
    if not original_name or Path(original_name).suffix.lower() != ".csv":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "VALIDATION_ERROR", "message": "only .csv files are accepted"},
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "VALIDATION_ERROR", "message": "CSV file is empty"},
        )
    if len(content) > settings.MAX_CSV_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "VALIDATION_ERROR", "message": "CSV file exceeds the 5 MB limit"},
        )

    store = request.app.state.task_store
    artifact_store: ArtifactStore = request.app.state.artifact_store
    runner: TaskRunner = request.app.state.task_runner
    task = store.create_task(
        TaskType.CSV_ANALYSIS,
        {"source_filename": original_name, "csv_filename": "input.csv"},
    )
    try:
        artifact_store.write_bytes(task.id, "input.csv", content, "text/csv")
    except Exception as exc:
        store.start_task(task.id)
        store.finish_task(task.id, TaskStatus.FAILED, None, "INTERNAL_ERROR", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": "failed to save uploaded CSV"},
        ) from exc

    background_tasks.add_task(runner.run, task.id)
    return task
