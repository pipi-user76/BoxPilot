"""文件职责：提供 CSV 上传接口，保存文件后交给统一任务调度器。"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile, status

from backend.config import settings
from backend.dedupe import build_dedupe_key
from backend.models import TaskRecord, TaskStatus, TaskType, utc_now
from backend.storage import ArtifactStore
from backend.scheduler import TaskRunner


router = APIRouter()


def _bad_request(message: str, status_code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": "VALIDATION_ERROR", "message": message},
    )


@router.post("/tasks/csv", response_model=TaskRecord, status_code=status.HTTP_202_ACCEPTED)
async def create_csv_task(
    background_tasks: BackgroundTasks,
    request: Request,
    files: list[UploadFile] | None = File(None),
    file: UploadFile | None = File(None),
) -> TaskRecord:
    """接收 1–5 个 CSV，保存后异步启动分析流程。"""
    incoming = list(files or [])
    if file is not None:
        incoming.insert(0, file)
    if not incoming:
        raise _bad_request("select at least one .csv file")
    if len(incoming) > settings.MAX_CSV_FILES:
        raise _bad_request(f"at most {settings.MAX_CSV_FILES} CSV files are allowed")

    prepared: list[tuple[str, bytes, str]] = []
    seen_names: set[str] = set()
    total_bytes = 0
    for upload in incoming:
        original_name = Path(upload.filename or "").name
        if not original_name or Path(original_name).suffix.lower() != ".csv":
            raise _bad_request("only .csv files are accepted")
        if original_name in seen_names:
            raise _bad_request(f"duplicate file name: {original_name}")
        seen_names.add(original_name)
        content = await upload.read(settings.MAX_CSV_UPLOAD_BYTES + 1)
        if not content:
            raise _bad_request("CSV file is empty")
        if len(content) > settings.MAX_CSV_UPLOAD_BYTES:
            raise _bad_request("CSV file exceeds the 5 MB limit", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        total_bytes += len(content)
        if total_bytes > settings.MAX_CSV_TOTAL_BYTES:
            raise _bad_request("CSV files exceed the 20 MB total limit", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        prepared.append((original_name, content, hashlib.sha256(content).hexdigest()))

    store = request.app.state.task_store
    artifact_store: ArtifactStore = request.app.state.artifact_store
    runner: TaskRunner = request.app.state.task_runner
    user = request.state.user

    digest = hashlib.sha256(
        "|".join(f"{name}:{sha}" for name, _, sha in prepared).encode("utf-8")
    ).hexdigest()
    dedupe_key = build_dedupe_key(
        TaskType.CSV_ANALYSIS,
        {"source_filename": prepared[0][0], "sha256": digest, "files": len(prepared)},
    )
    sources = []
    for index, (name, content, sha) in enumerate(prepared, start=1):
        artifact_name = "input.csv" if len(prepared) == 1 else f"input-{index:02d}-{Path(name).name}"
        sources.append(
            {
                "name": name,
                "artifact": artifact_name,
                "sha256": sha,
                "size_bytes": len(content),
            }
        )
    task = store.create_task(
        TaskType.CSV_ANALYSIS,
        {
            "source_filename": prepared[0][0],
            "csv_filename": sources[0]["artifact"],
            "sources": sources,
        },
        owner_id=user.id,
        dedupe_key=dedupe_key,
        dedupe_since=utc_now() - timedelta(seconds=settings.DEDUPE_WINDOW_SECONDS),
    )
    if task.deduplicated:
        return task
    try:
        for source, (_, content, _) in zip(sources, prepared):
            artifact_store.write_bytes(task.id, source["artifact"], content, "text/csv")
    except Exception as exc:
        store.start_task(task.id)
        store.finish_task(task.id, TaskStatus.FAILED, None, "INTERNAL_ERROR", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": "failed to save uploaded CSV"},
        ) from exc

    background_tasks.add_task(runner.run, task.id)
    return task
