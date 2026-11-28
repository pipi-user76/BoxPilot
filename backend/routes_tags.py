"""标签、批注、快照与导出接口。"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from backend.models import (
    AnnotationCreate,
    AnnotationRecord,
    AnnotationUpdate,
    DocumentTagsRequest,
    TagRecord,
    TaskTagsRequest,
)
from backend.naming import display_name
from backend.routes_tasks import _scoped_task, _user

router = APIRouter()


def _store(request: Request):
    return request.app.state.task_store


@router.get("/tags", response_model=list[TagRecord])
def list_tags(request: Request):
    return _store(request).list_tags(_user(request).id)


@router.post("/tags", response_model=TagRecord, status_code=status.HTTP_201_CREATED)
def create_tag(payload: TaskTagsRequest, request: Request):
    names = [name for name in payload.names if name.strip()]
    if not names:
        raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "标签名不能为空"})
    return _store(request).create_tag(_user(request).id, names[0])


@router.get("/tasks/{task_id}/tags", response_model=list[str])
def get_task_tags(task_id: str, request: Request):
    _scoped_task(request, task_id)
    return _store(request).get_task_tags(task_id)


@router.put("/tasks/{task_id}/tags", response_model=list[str])
def put_task_tags(task_id: str, payload: TaskTagsRequest, request: Request):
    _scoped_task(request, task_id)
    return _store(request).set_task_tags(task_id, _user(request).id, payload.names)


@router.get("/kb/documents/{document_id}/tags", response_model=list[str])
def get_document_tags(document_id: str, request: Request):
    docs = {doc.id: doc for doc in _store(request).list_kb_documents(owner_id=_user(request).id)}
    if document_id not in docs:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "文档不存在"})
    return _store(request).get_document_tags(document_id)


@router.put("/kb/documents/{document_id}/tags", response_model=list[str])
def put_document_tags(document_id: str, payload: DocumentTagsRequest, request: Request):
    docs = {doc.id: doc for doc in _store(request).list_kb_documents(owner_id=_user(request).id)}
    if document_id not in docs:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "文档不存在"})
    return _store(request).set_document_tags(document_id, _user(request).id, payload.names)


@router.get("/tasks/{task_id}/annotations", response_model=list[AnnotationRecord])
def list_annotations(task_id: str, request: Request):
    _scoped_task(request, task_id)
    return _store(request).list_annotations(task_id=task_id, owner_id=_user(request).id)


@router.post("/tasks/{task_id}/annotations", response_model=AnnotationRecord, status_code=201)
def add_annotation(task_id: str, payload: AnnotationCreate, request: Request):
    _scoped_task(request, task_id)
    if payload.end_offset <= payload.start_offset:
        raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "批注范围无效"})
    return _store(request).add_annotation(
        _user(request).id,
        task_id=task_id,
        artifact_name=payload.artifact_name,
        block_index=payload.block_index,
        start_offset=payload.start_offset,
        end_offset=payload.end_offset,
        quote=payload.quote,
        note=payload.note,
    )


@router.patch("/annotations/{annotation_id}", response_model=AnnotationRecord)
def update_annotation(annotation_id: str, payload: AnnotationUpdate, request: Request):
    updated = _store(request).update_annotation(annotation_id, _user(request).id, payload.note)
    if not updated:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "批注不存在"})
    return updated


@router.delete("/annotations/{annotation_id}")
def delete_annotation(annotation_id: str, request: Request):
    if not _store(request).delete_annotation(annotation_id, _user(request).id):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "批注不存在"})
    return {"deleted": True}


@router.get("/tasks/{task_id}/snapshots")
def list_snapshots(task_id: str, request: Request):
    task = _scoped_task(request, task_id)
    images = [item for item in task.artifacts if item.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
    if not images:
        message = "快照生成失败" if task.status.value != "SUCCESS" else "这个任务没有截图产物"
        return {"items": [], "message": message}
    return {
        "items": [
            {
                "name": item.filename,
                "size": item.size_bytes,
                "url": f"/tasks/{task_id}/artifacts/{quote(item.filename)}?inline=1",
            }
            for item in images
        ]
    }


def _summary_lines(task) -> list[tuple[str, str]]:
    summary = (task.result or {}).get("summary") or {}
    lines: list[tuple[str, str]] = []
    for key, value in summary.items():
        if isinstance(value, (str, int, float)):
            lines.append((str(key), str(value)))
    return lines


@router.get("/tasks/{task_id}/export")
def export_task(task_id: str, request: Request, format: str = "md"):
    task = _scoped_task(request, task_id)
    title = display_name(task)
    fmt = format.lower()
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["字段", "值"])
        writer.writerow(["任务", title])
        writer.writerow(["任务ID", task.id])
        writer.writerow(["状态", task.status.value])
        writer.writerow(["创建时间", task.created_at.isoformat(timespec="seconds")])
        writer.writerows(_summary_lines(task))
        for artifact in task.artifacts:
            writer.writerow(["产物", artifact.filename])
        body = buffer.getvalue().encode("utf-8-sig")
        media = "text/csv; charset=utf-8"
        filename = f"{task.id[:8]}.csv"
    elif fmt == "gb7714":
        now = datetime.now(timezone.utc).astimezone()
        entry = f"[1] {title}[EB/OL]. ({task.created_at:%Y-%m-%d})[{now:%Y-%m-%d}]. {task.params.get('url', '')}."
        body = entry.encode("utf-8")
        media = "text/plain; charset=utf-8"
        filename = f"{task.id[:8]}-gb7714.txt"
    else:
        lines = [f"# {title}", "", f"- 任务 ID：{task.id}", f"- 状态：{task.status.value}", ""]
        for key, value in _summary_lines(task):
            lines.append(f"- {key}：{value}")
        if task.steps:
            lines += ["", "## 执行步骤", ""]
            for index, step in enumerate(task.steps, 1):
                lines.append(f"{index}. {step.name}（{step.status.value}）")
        if task.artifacts:
            lines += ["", "## 产物", ""]
            lines += [f"- {item.filename}（{item.size_bytes} B）" for item in task.artifacts]
        body = "\n".join(lines).encode("utf-8")
        media = "text/markdown; charset=utf-8"
        filename = f"{task.id[:8]}.md"
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
