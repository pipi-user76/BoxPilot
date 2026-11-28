"""私有知识库接口：创建、文档入库、刷新向量、问答与历史。"""

from __future__ import annotations

import io
import json

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse

from backend.config import settings
from backend.kb import KnowledgeBaseError, KnowledgeBaseService
from backend.models import (
    KnowledgeBaseRename,
    KbAskRequest,
    KbAnswer,
    KbBatchCreate,
    KbDocumentCreate,
    KbDocumentRecord,
    KbDocumentSummary,
    KbMessageRecord,
    KbTaskIngestRequest,
    TaskKbDocument,
    KnowledgeBaseCreate,
    KnowledgeBaseRecord,
)

router = APIRouter(tags=["knowledge-base"])

SUPPORTED_UPLOADS = (".txt", ".md", ".pdf")


def _extract_upload(filename: str, data: bytes) -> str:
    """文本直接解码，PDF 逐页取文字；失败时抛出可读的中文错误。"""
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception as exc:  # noqa: BLE001 - 解析失败统一转成用户可读错误
            raise KnowledgeBaseError("PDF_PARSE_FAILED", f"PDF 解析失败：{exc}", 422) from exc
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    raise KnowledgeBaseError("TEXT_DECODE_FAILED", "文本编码无法识别，请另存为 UTF-8", 422)


def _service(request: Request) -> KnowledgeBaseService:
    return request.app.state.kb_service


def _user_id(request: Request) -> str:
    return request.state.user.id


def _error(exc: KnowledgeBaseError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


@router.post("/kb", response_model=KnowledgeBaseRecord, status_code=status.HTTP_201_CREATED)
def create_kb(payload: KnowledgeBaseCreate, request: Request):
    return _service(request).create(payload.name, payload.note, _user_id(request))


@router.patch("/kb/{kb_id}", response_model=KnowledgeBaseRecord)
def rename_kb(kb_id: str, payload: KnowledgeBaseRename, request: Request):
    try:
        return _service(request).rename(kb_id, _user_id(request), payload.name)
    except KnowledgeBaseError as exc:
        return _error(exc)


@router.get("/kb", response_model=list[KnowledgeBaseRecord])
def list_kb(request: Request):
    return _service(request).list(_user_id(request))


@router.delete("/kb/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kb(kb_id: str, request: Request):
    try:
        _service(request).delete(kb_id, _user_id(request))
    except KnowledgeBaseError as exc:
        return _error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/kb/{kb_id}/ingest-task", response_model=list[KbDocumentRecord], status_code=status.HTTP_201_CREATED)
def ingest_task(kb_id: str, payload: KbTaskIngestRequest, request: Request):
    """把某个任务的产物收进知识库；已存在同名文档时覆盖。"""
    service = _service(request)
    try:
        return service.ingest_task_artifacts(
            kb_id, _user_id(request), payload.task_id, payload.artifacts,
            allow_any=bool(getattr(request.state.user, "is_admin", False)),
            include_chat=payload.include_chat,
        )
    except KnowledgeBaseError as exc:
        return _error(exc)


@router.get("/kb/{kb_id}/documents", response_model=list[KbDocumentSummary])
def list_documents(kb_id: str, request: Request):
    try:
        return _service(request).list_document_summaries(kb_id, _user_id(request))
    except KnowledgeBaseError as exc:
        return _error(exc)


@router.get("/tasks/{task_id}/kb-documents", response_model=list[TaskKbDocument])
def list_task_documents(task_id: str, request: Request):
    """任务详情用来判断“已添加到知识库”的状态。"""
    try:
        return _service(request).list_task_documents(task_id, _user_id(request))
    except KnowledgeBaseError as exc:
        return _error(exc)


@router.post(
    "/kb/{kb_id}/documents",
    response_model=KbDocumentRecord,
    status_code=status.HTTP_201_CREATED,
)
def add_document(kb_id: str, payload: KbDocumentCreate, request: Request):
    service = _service(request)
    user_id = _user_id(request)
    try:
        if payload.task_id:
            return service.add_from_task(
                kb_id, user_id, payload.task_id, allow_any=request.state.user.is_admin
            )
        return service.add_text(
            kb_id,
            user_id,
            title=payload.title,
            url=payload.url,
            content=payload.content,
        )
    except KnowledgeBaseError as exc:
        return _error(exc)


@router.post(
    "/kb/{kb_id}/documents/batch",
    response_model=list[KbDocumentRecord],
    status_code=status.HTTP_201_CREATED,
)
def add_documents_batch(kb_id: str, payload: KbBatchCreate, request: Request):
    service = _service(request)
    user_id = _user_id(request)
    created: list[KbDocumentRecord] = []
    try:
        for task_id in dict.fromkeys(payload.task_ids):
            created.append(
                service.add_from_task(
                    kb_id, user_id, task_id, allow_any=request.state.user.is_admin
                )
            )
    except KnowledgeBaseError as exc:
        return _error(exc)
    return created


@router.delete("/kb/{kb_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(kb_id: str, document_id: str, request: Request):
    try:
        _service(request).delete_document(kb_id, _user_id(request), document_id)
    except KnowledgeBaseError as exc:
        return _error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/kb/{kb_id}/documents/{document_id}/refresh", response_model=KbDocumentRecord)
def refresh_document(kb_id: str, document_id: str, request: Request):
    try:
        return _service(request).refresh_document(kb_id, _user_id(request), document_id)
    except KnowledgeBaseError as exc:
        return _error(exc)


@router.post("/kb/{kb_id}/upload", response_model=KbDocumentRecord, status_code=status.HTTP_201_CREATED)
async def upload_document(kb_id: str, request: Request, file: UploadFile = File(...)):
    filename = file.filename or "未命名文档"
    if not filename.lower().endswith(SUPPORTED_UPLOADS):
        return _error(KnowledgeBaseError("UNSUPPORTED_FILE", "只支持 PDF、TXT、Markdown", 422))
    data = await file.read(settings.KB_UPLOAD_MAX_BYTES + 1)
    if len(data) > settings.KB_UPLOAD_MAX_BYTES:
        return _error(KnowledgeBaseError("FILE_TOO_LARGE", "文件超过 10 MB", 413))
    try:
        text = _extract_upload(filename, data)
        return _service(request).add_text(
            kb_id,
            _user_id(request),
            title=filename,
            url="",
            content=text,
        )
    except KnowledgeBaseError as exc:
        return _error(exc)


@router.post("/kb/{kb_id}/ask", response_model=KbAnswer)
def ask(kb_id: str, payload: KbAskRequest, request: Request):
    try:
        return _service(request).ask(
            kb_id,
            _user_id(request),
            payload.question,
            payload.top_k,
            payload.min_score,
            payload.conversation_id,
            payload.use_history,
            payload.document_ids or None,
            payload.kinds or None,
        )
    except KnowledgeBaseError as exc:
        return _error(exc)


@router.post("/kb/{kb_id}/ask/stream")
def ask_stream(kb_id: str, payload: KbAskRequest, request: Request):
    def events():
        for item in _service(request).ask_stream(
            kb_id, _user_id(request), payload.question, payload.top_k,
            payload.min_score, payload.conversation_id, payload.use_history,
            payload.document_ids or None, payload.kinds or None,
        ):
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.patch("/kb/{kb_id}/conversations/{conversation_id}")
def rename_conversation(kb_id: str, conversation_id: str, payload: KbConversationRename, request: Request):
    try:
        ok = _service(request).rename_conversation(
            kb_id, _user_id(request), conversation_id, payload.title
        )
    except KnowledgeBaseError as exc:
        raise _error(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.delete("/kb/{kb_id}/conversations/{conversation_id}")
def delete_conversation(kb_id: str, conversation_id: str, request: Request):
    try:
        ok = _service(request).delete_conversation(kb_id, _user_id(request), conversation_id)
    except KnowledgeBaseError as exc:
        raise _error(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.get("/kb/{kb_id}/conversations")
def conversations(kb_id: str, request: Request, limit: int = 50):
    try:
        return _service(request).conversations(kb_id, _user_id(request), limit)
    except KnowledgeBaseError as exc:
        raise _error(exc) from exc


@router.get("/kb/{kb_id}/messages", response_model=list[KbMessageRecord])
def history(kb_id: str, request: Request, limit: int = 50, conversation_id: str | None = None):
    try:
        return _service(request).history(
            kb_id,
            _user_id(request),
            max(1, min(limit, 100)),
            conversation_id or None,
        )
    except KnowledgeBaseError as exc:
        return _error(exc)
