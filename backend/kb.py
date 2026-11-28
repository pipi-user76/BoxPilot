"""私有知识库：SQLite 存元数据与正文，Chroma 存向量，问答走 LLM 网关。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import csv
import io
import logging
import re

from backend.config import settings
from backend.llm import LLMClient, LLMError
from backend.naming import display_name
from backend.models import (
    KbAnswer,
    KbCitation,
    KbDocumentRecord,
    KbDocumentSummary,
    KbMessageRecord,
    KnowledgeBaseRecord,
    TaskKbDocument,
    TaskRecord,
)
from backend.rag import chunk_sections, chunk_text

logger = logging.getLogger(__name__)
from backend.storage import ArtifactStore

ANSWER_SYSTEM = (
    "你是 BoxPilot 的知识库助手。只依据给定的资料片段回答问题，用中文。"
    "回答用 Markdown 组织：第一行给一句结论，然后分成 2–4 个小节或要点，"
    "每节之间空一行，每段不超过 4 行；不要把所有内容挤成一大段。"
    "如果资料不足，直接说明缺什么，不要编造。"
    "引用资料时在相应句子后用 [序号] 标注，例如 [1]。"
)


def document_preview(content: str, limit: int = 140) -> str:
    """列表展示用的正文摘要：去掉标题行与多余空白，截断到固定长度。"""
    lines: list[str] = []
    for raw in (content or "").splitlines():
        line = raw.strip().lstrip("#").strip()
        if not line:
            continue
        lines.append(line)
    if lines and lines[0].startswith(("网页对比", "网页资料整理", "开发验证", "CSV 数据分析", "代码仓库体检", "网页应用验收")):
        lines = lines[1:]
    parts: list[str] = []
    for line in lines:
        if any(marker in line for marker in ("任务类型：", "创建时间：", "抓取时间：")):
            continue
        if line.startswith("- 来源："):
            continue
        parts.append(line)
        if len(" ".join(parts)) >= limit:
            break
    text = " ".join(parts)
    text = " ".join(text.split())
    return text[:limit]


class KnowledgeBaseError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ChromaIndex:
    """Chroma 持久化索引；一条记录对应一个切片。"""

    def __init__(self, path: Path, collection: str = "kb_chunks") -> None:
        import chromadb

        self.client = chromadb.PersistentClient(path=str(path))
        self.collection = self.client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        *,
        kb_id: str,
        document_id: str,
        title: str,
        url: str,
        chunks: list[str],
        embeddings: list[list[float]],
        headings: list[str] | None = None,
        kind: str = "text",
    ) -> int:
        if not chunks:
            return 0
        self.collection.add(
            ids=[f"{document_id}:{i}" for i in range(len(chunks))],
            documents=chunks,
            embeddings=embeddings,
            metadatas=[
                {"kb_id": kb_id, "document_id": document_id, "title": title, "url": url,
                 "heading": (headings[i] if headings and i < len(headings) else ""), "kind": kind}
                for i in range(len(chunks))
            ],
        )
        return len(chunks)

    def delete_document(self, document_id: str) -> None:
        self.collection.delete(where={"document_id": document_id})

    def delete_base(self, kb_id: str) -> None:
        self.collection.delete(where={"kb_id": kb_id})

    def query(
        self, *, kb_id: str, embedding: list[float], top_k: int,
        document_ids: list[str] | None = None, kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = [{"kb_id": kb_id}]
        if document_ids:
            filters.append({"document_id": {"$in": document_ids}})
        if kinds:
            filters.append({"kind": {"$in": kinds}})
        where = filters[0] if len(filters) == 1 else {"$and": filters}
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=where,
        )
        hits: list[dict[str, Any]] = []
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for text, meta, distance in zip(documents, metadatas, distances):
            hits.append(
                {
                    "text": text,
                    "document_id": meta.get("document_id", ""),
                    "title": meta.get("title", ""),
                    "url": meta.get("url", ""),
                    "score": 1.0 - float(distance),
                    "heading": meta.get("heading", ""),
                }
            )
        return hits


TEXT_ARTIFACTS = ("article.md", "compare.md", "reading.md")
KIND_BY_ARTIFACT = {
    "article.md": "web_digest",
    "reading.md": "web_digest",
    "compare.md": "web_compare",
}


class KnowledgeBaseService:
    """知识库业务逻辑；路由只负责鉴权和参数转换。"""

    def __init__(
        self,
        store,
        artifact_store: ArtifactStore,
        llm: LLMClient | None = None,
        index: ChromaIndex | None = None,
        answer_llm: LLMClient | None = None,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.llm = llm
        self.index = index
        self.answer_llm = answer_llm

    # ---------- 知识库 ----------

    def create(self, name: str, note: str, owner_id: str) -> KnowledgeBaseRecord:
        return self.store.create_knowledge_base(name.strip(), note.strip(), owner_id)

    def rename(self, kb_id: str, owner_id: str, name: str) -> KnowledgeBaseRecord:
        record = self.store.get_knowledge_base(kb_id)
        if record is None or record.owner_id != owner_id:
            raise KnowledgeBaseError("KB_NOT_FOUND", "知识库不存在或无权访问。", 404)
        if not self.store.rename_knowledge_base(kb_id, owner_id, name.strip()):
            raise KnowledgeBaseError("KB_NOT_FOUND", "知识库不存在或无权访问。", 404)
        return self.store.get_knowledge_base(kb_id) or record

    def list(self, owner_id: str) -> list[KnowledgeBaseRecord]:
        return self.store.list_knowledge_bases(owner_id)

    def require(self, kb_id: str, owner_id: str) -> KnowledgeBaseRecord:
        record = self.store.get_knowledge_base(kb_id)
        if record is None or record.owner_id != owner_id:
            raise KnowledgeBaseError("KB_NOT_FOUND", "知识库不存在或无权访问", 404)
        return record

    def delete(self, kb_id: str, owner_id: str) -> None:
        self.require(kb_id, owner_id)
        if self.index is not None:
            self.index.delete_base(kb_id)
        self.store.delete_knowledge_base(kb_id)

    # ---------- 文档 ----------

    def list_documents(self, kb_id: str, owner_id: str) -> list[KbDocumentRecord]:
        self.require(kb_id, owner_id)
        return self.store.list_kb_documents(kb_id)

    def list_document_summaries(self, kb_id: str, owner_id: str) -> list[KbDocumentSummary]:
        self.require(kb_id, owner_id)
        return [
            KbDocumentSummary(
                id=doc.id,
                kb_id=doc.kb_id,
                task_id=doc.task_id,
                title=doc.title,
                url=doc.url,
                preview=document_preview(doc.content),
                chunk_count=doc.chunk_count,
                created_at=doc.created_at,
                artifact_name=doc.artifact_name,
                kind=doc.kind,
                orphaned=doc.orphaned,
            )
            for doc in self.store.list_kb_documents(kb_id)
        ]

    def list_task_documents(self, task_id: str, owner_id: str) -> list[TaskKbDocument]:
        task = self.store.get_task(task_id)
        if task is None or task.owner_id != owner_id:
            raise KnowledgeBaseError("TASK_NOT_FOUND", "任务不存在或无权访问", 404)
        return [
            TaskKbDocument(kb_id=kb_id, kb_name=kb_name, document_id=document_id)
            for kb_id, kb_name, document_id in self.store.list_task_kb_documents(task_id, owner_id)
        ]

    def auto_kb(self, owner_id: str) -> KnowledgeBaseRecord:
        """按 owner 找（或创建）自动归档库。"""
        for record in self.store.list_knowledge_bases(owner_id):
            if record.name == settings.KB_AUTO_INGEST_NAME:
                return record
        return self.create(settings.KB_AUTO_INGEST_NAME, "任务完成后自动归档的产物", owner_id)

    def ingest_task_artifacts(
        self,
        kb_id: str,
        owner_id: str,
        task_id: str,
        artifacts: list[str] | None = None,
        *,
        allow_any: bool = False,
        include_chat: bool = True,
    ) -> list[KbDocumentRecord]:
        """把一个任务当作一次会话整体入库：一个任务只产生一条文档。"""
        task = self.store.get_task(task_id)
        if task is None or (not allow_any and task.owner_id != owner_id):
            raise KnowledgeBaseError("TASK_NOT_FOUND", "任务不存在或无权访问", 404)
        self.require(kb_id, owner_id)

        title_prefix, url = self._describe_task(task)
        summary = task.result.get("summary") if isinstance(task.result, dict) else None
        summary = summary if isinstance(summary, dict) else {}

        parts = [f"# {title_prefix}", ""]
        parts.append(f"- 任务类型：{task.task_type.value}")
        parts.append(f"- 创建时间：{task.created_at.isoformat(timespec='minutes')}")
        if task.params.get("url"):
            parts.append(f"- 来源：{task.params['url']}")
        for link in task.params.get("urls") or []:
            parts.append(f"- 来源：{link}")
        parts.append("")

        conclusion = summary.get("conclusion") or summary.get("reason")
        if conclusion:
            parts += ["## 结论", str(conclusion), ""]

        analysis_name = next(
            (name for name in ("compare.md", "reading.md", "article.md") if name in {a.filename for a in task.artifacts}),
            None,
        )
        if analysis_name:
            body = self._artifact_text(task, analysis_name).strip()
            if body:
                parts += [f"## 分析报告（{analysis_name}）", body, ""]

        if include_chat:
            chat_text = self._conversation_text(task.id)
            if chat_text:
                parts += ["## 对话记录", chat_text, ""]

        if task.artifacts:
            parts.append("## 产物清单")
            for item in task.artifacts:
                parts.append(f"- {item.filename}（{item.size_bytes} B）")
            parts.append("")

        content = "\n".join(parts).strip()
        if not content:
            raise KnowledgeBaseError("TASK_TEXT_EMPTY", "该任务没有可入库的内容", 422)

        for doc in self.list_documents(kb_id, owner_id):
            if doc.task_id != task.id:
                continue
            if self.index is not None:
                self.index.delete_document(doc.id)
            self.store.delete_kb_document(doc.id)

        created = self._ingest(
            kb_id,
            owner_id,
            title=title_prefix,
            url=url,
            content=content,
            task_id=task.id,
            artifact_name="session.md",
            kind=task.task_type.value.lower(),
        )
        return [created]

    def add_from_task(
        self, kb_id: str, owner_id: str, task_id: str, *, allow_any: bool = False
    ) -> KbDocumentRecord:
        task = self.store.get_task(task_id)
        if task is None or (not allow_any and task.owner_id != owner_id):
            raise KnowledgeBaseError("TASK_NOT_FOUND", "任务不存在或无权访问", 404)
        title, url = self._describe_task(task)
        content = self._task_text(task)
        if not content.strip():
            raise KnowledgeBaseError("TASK_TEXT_EMPTY", "该任务没有可入库的正文", 422)
        return self._ingest(kb_id, owner_id, title=title, url=url, content=content, task_id=task_id)

    def add_text(
        self, kb_id: str, owner_id: str, *, title: str, url: str, content: str, task_id: str | None = None
    ) -> KbDocumentRecord:
        self.require(kb_id, owner_id)
        if not content.strip():
            raise KnowledgeBaseError("CONTENT_EMPTY", "正文为空，无法入库", 422)
        return self._ingest(kb_id, owner_id, title=title or "未命名文档", url=url, content=content, task_id=task_id)

    def delete_document(self, kb_id: str, owner_id: str, document_id: str) -> None:
        self.require(kb_id, owner_id)
        doc = self.store.get_kb_document(document_id)
        if doc is None or doc.kb_id != kb_id:
            raise KnowledgeBaseError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
        if self.index is not None:
            self.index.delete_document(document_id)
        self.store.delete_kb_document(document_id)

    def refresh_document(self, kb_id: str, owner_id: str, document_id: str) -> KbDocumentRecord:
        self.require(kb_id, owner_id)
        doc = self.store.get_kb_document(document_id)
        if doc is None or doc.kb_id != kb_id:
            raise KnowledgeBaseError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
        if self.index is not None:
            self.index.delete_document(document_id)
        self._embed_into_index(doc)
        return self.store.get_kb_document(document_id) or doc

    def _ingest(
        self, kb_id: str, owner_id: str, *, title: str, url: str, content: str,
        task_id: str | None, artifact_name: str | None = None, kind: str = "text",
    ) -> KbDocumentRecord:
        self.require(kb_id, owner_id)
        doc = self.store.add_kb_document(
            kb_id, title=title, url=url, content=content, task_id=task_id,
            artifact_name=artifact_name, kind=kind,
        )
        if self.index is not None:
            self._embed_into_index(doc)
        return self.store.get_kb_document(doc.id) or doc

    def _embed_into_index(self, doc: KbDocumentRecord) -> None:
        assert self.index is not None
        sections = chunk_sections(doc.content)
        if not sections:
            self.store.set_kb_chunk_count(doc.id, 0)
            self.store.replace_kb_chunks(doc.kb_id, doc.id, [])
            return
        headings = [heading for heading, _ in sections]
        chunks = [text for _, text in sections]
        vectors = self._embed([
            f"{heading}\n{text}" if heading else text for heading, text in sections
        ])
        self.index.delete_document(doc.id)
        self.index.add(
            kb_id=doc.kb_id, document_id=doc.id, title=doc.title, url=doc.url,
            chunks=chunks, embeddings=vectors, headings=headings, kind=doc.kind,
        )
        self.store.replace_kb_chunks(
            doc.kb_id, doc.id, [{"heading": h, "text": t} for h, t in sections]
        )
        self.store.set_kb_chunk_count(doc.id, len(chunks))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self.llm is None or not self.llm.available():
            raise KnowledgeBaseError("LLM_NOT_CONFIGURED", "未配置嵌入模型，无法写入向量", 503)
        try:
            vectors: list[list[float]] = []
            for start in range(0, len(texts), 10):
                vectors.extend(self.llm.embed(texts[start:start + 10]))
            return vectors
        except LLMError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, 502) from exc

    # ---------- 问答 ----------

    def _rewrite_question(
        self, kb_id: str, question: str, conversation_id: str, use_history: bool
    ) -> str:
        if not (use_history and settings.KB_REWRITE):
            return question
        if self.llm is None or not self.llm.available():
            return question
        history = self.store.list_kb_messages(kb_id, limit=4, conversation_id=conversation_id)
        if not history:
            return question
        context = "\n".join(
            f"问：{item.question}\n答：{item.answer[:200]}" for item in reversed(history)
        )
        try:
            result = (self.answer_llm or self.llm).chat(
                [
                    {"role": "system", "content": "把用户的问题改写成一个不依赖上下文、可独立检索的中文问句，只输出改写后的问句。"},
                    {"role": "user", "content": f"历史：\n{context}\n\n当前问题：{question}"},
                ],
                max_tokens=120,
            )
        except LLMError:
            return question
        rewritten = result.text.strip().splitlines()[0].strip() if result.text.strip() else ""
        return (rewritten or question)[:200]

    def _embed_query(self, question: str) -> list[float]:
        try:
            return self.llm.embed([question], timeout=6, retries=0)[0]
        except TypeError:
            return self.llm.embed([question])[0]

    def _retrieve(
        self, kb_id: str, question: str, top_k: int, min_score: float,
        document_ids: list[str] | None, kinds: list[str] | None,
    ) -> list[dict[str, Any]]:
        vector_hits: list[dict[str, Any]] = []
        try:
            vector = self._embed_query(question)
            vector_hits = self.index.query(
                kb_id=kb_id, embedding=vector, top_k=max(20, top_k * 4),
                document_ids=document_ids, kinds=kinds,
            )
        except Exception as exc:
            logger.warning("kb_embed_unavailable", extra={"error": str(exc)[:200]})
        keyword_hits = self.store.search_kb_chunks(
            kb_id, question, limit=20, document_ids=document_ids, kinds=kinds,
        )
        fused: dict[tuple[str, str], dict[str, Any]] = {}
        for rank, hit in enumerate(vector_hits, start=1):
            key = (hit["document_id"], hit["text"][:80])
            item = fused.setdefault(key, {**hit, "rrf": 0.0, "vector_score": 0.0})
            item["rrf"] += 1.0 / (60 + rank)
            item["vector_score"] = max(item["vector_score"], float(hit.get("score", 0.0)))
        for rank, hit in enumerate(keyword_hits, start=1):
            key = (hit["document_id"], hit["text"][:80])
            item = fused.setdefault(key, {**hit, "rrf": 0.0, "vector_score": 0.0})
            item["rrf"] += 1.0 / (60 + rank)
        hits = list(fused.values())
        if min_score > 0:
            hits = [hit for hit in hits if hit["vector_score"] >= min_score]
        hits.sort(key=lambda item: item["rrf"], reverse=True)
        return hits[:top_k]

    def ask(
        self,
        kb_id: str,
        owner_id: str,
        question: str,
        top_k: int = 6,
        min_score: float = 0.0,
        conversation_id: str = "default",
        use_history: bool = True,
        document_ids: list[str] | None = None,
        kinds: list[str] | None = None,
    ) -> KbAnswer:
        self.require(kb_id, owner_id)
        if self.index is None:
            raise KnowledgeBaseError("RAG_UNAVAILABLE", "向量索引未启用", 503)
        search_question = self._rewrite_question(kb_id, question, conversation_id, use_history)
        hits = self._retrieve(
            kb_id, search_question, top_k, min_score, document_ids or None, kinds or None,
        )
        first_title = question.strip()[:60] or "新对话"
        if not hits:
            answer = "没有检索到足够相关的资料，换个问法、放宽相似度阈值，或先补充文档。"
            message = self.store.add_kb_message(
                kb_id, question, answer, [], conversation_id=conversation_id
            )
            self.store.upsert_kb_conversation(kb_id, owner_id, conversation_id, first_title)
            return KbAnswer(answer=answer, citations=[], message_id=message.id)
        context = "\n\n".join(
            f"[{index}] {hit['title']}（{hit.get('url') or '无链接'}）"
            f"{(' · ' + hit['heading']) if hit.get('heading') else ''}\n{hit['text']}"
            for index, hit in enumerate(hits, start=1)
        )
        messages = [{"role": "system", "content": ANSWER_SYSTEM}]
        if use_history:
            history = self.store.list_kb_messages(kb_id, limit=6, conversation_id=conversation_id)
            for item in reversed(history):
                messages.append({"role": "user", "content": item.question})
                messages.append({"role": "assistant", "content": item.answer[:600]})
        messages.append(
            {"role": "user", "content": f"资料：\n{context}\n\n问题：{question}"}
        )
        if self.llm is None or not self.llm.available():
            raise KnowledgeBaseError("LLM_NOT_CONFIGURED", "未配置对话模型，无法回答", 503)
        try:
            result = (self.answer_llm or self.llm).chat(messages, max_tokens=4000)
        except LLMError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, 502) from exc
        if not result.text.strip():
            raise KnowledgeBaseError("ANSWER_EMPTY", "模型没有返回内容，请换个问法或稍后重试。", 502)
        used = {int(value) for value in re.findall(r"\[(\d+)\]", result.text)}
        citations = [
            KbCitation(
                index=index,
                document_id=hit["document_id"],
                title=hit["title"],
                url=hit.get("url") or "",
                snippet=hit["text"][:300],
                heading=hit.get("heading") or "",
                used=index in used,
            )
            for index, hit in enumerate(hits, start=1)
        ]
        message = self.store.add_kb_message(
            kb_id, question, result.text, citations, conversation_id=conversation_id
        )
        self.store.upsert_kb_conversation(kb_id, owner_id, conversation_id, first_title)
        return KbAnswer(answer=result.text, citations=citations, message_id=message.id)

    def ask_stream(
        self,
        kb_id: str,
        owner_id: str,
        question: str,
        top_k: int = 6,
        min_score: float = 0.0,
        conversation_id: str = "default",
        use_history: bool = True,
        document_ids: list[str] | None = None,
        kinds: list[str] | None = None,
    ):
        self.require(kb_id, owner_id)
        yield {"event": "status", "data": {"stage": "retrieving", "text": "正在检索资料…"}}
        if self.index is None:
            yield {"event": "error", "data": {"code": "RAG_UNAVAILABLE", "message": "向量索引未启用"}}
            return
        search_question = self._rewrite_question(kb_id, question, conversation_id, use_history)
        hits = self._retrieve(kb_id, search_question, top_k, min_score, document_ids or None, kinds or None)
        first_title = question.strip()[:60] or "新对话"
        if not hits:
            answer = "没有检索到足够相关的资料，换个问法、放宽相似度阈值，或先补充文档。"
            message = self.store.add_kb_message(kb_id, question, answer, [], conversation_id=conversation_id)
            self.store.upsert_kb_conversation(kb_id, owner_id, conversation_id, first_title)
            yield {"event": "citations", "data": {"citations": []}}
            yield {"event": "delta", "data": {"text": answer}}
            yield {"event": "done", "data": {"message_id": message.id, "answer": answer}}
            return
        citations = [
            KbCitation(
                index=index, document_id=hit["document_id"], title=hit["title"],
                url=hit.get("url") or "", snippet=hit["text"][:300],
                heading=hit.get("heading") or "", used=True,
            )
            for index, hit in enumerate(hits, start=1)
        ]
        yield {"event": "citations", "data": {"citations": [item.model_dump() for item in citations]}}
        yield {"event": "status", "data": {"stage": "generating", "text": "正在生成回答…"}}
        context = "\n\n".join(
            f"[{index}] {hit['title']}（{hit.get('url') or '无链接'}）"
            f"{(' · ' + hit['heading']) if hit.get('heading') else ''}\n{hit['text']}"
            for index, hit in enumerate(hits, start=1)
        )
        messages = [{"role": "system", "content": ANSWER_SYSTEM}]
        if use_history:
            for item in reversed(self.store.list_kb_messages(kb_id, limit=6, conversation_id=conversation_id)):
                messages.append({"role": "user", "content": item.question})
                messages.append({"role": "assistant", "content": item.answer[:600]})
        messages.append({"role": "user", "content": f"资料：\n{context}\n\n问题：{question}"})
        answer_client = self.answer_llm or self.llm
        if answer_client is None or not answer_client.available():
            yield {"event": "error", "data": {"code": "LLM_NOT_CONFIGURED", "message": "未配置对话模型"}}
            return
        result = None
        try:
            result = answer_client.chat(messages, max_tokens=900, timeout=45, retries=0)
        except LLMError as exc:
            if exc.code in {"LLM_UNAVAILABLE", "LLM_TIMEOUT"}:
                yield {"event": "status", "data": {
                    "stage": "retrying", "text": "模型响应较慢，正在重试一次…",
                }}
                try:
                    result = answer_client.chat(messages, max_tokens=700, timeout=30, retries=0)
                except LLMError as again:
                    yield {"event": "error", "data": {"code": again.code, "message": again.message}}
                    return
            else:
                yield {"event": "error", "data": {"code": exc.code, "message": exc.message}}
                return
        answer = (result.text if result else "").strip()
        buffer = [answer]
        for offset in range(0, len(answer), 60):
            chunk = answer[offset:offset + 60]
            if chunk:
                yield {"event": "delta", "data": {"text": chunk}}
        answer = "".join(buffer).strip()
        if not answer:
            yield {"event": "error", "data": {"code": "ANSWER_EMPTY", "message": "模型没有返回内容，请重试"}}
            return
        used = {int(value) for value in re.findall(r"\[(\d+)\]", answer)}
        for item in citations:
            item.used = item.index in used
        message = self.store.add_kb_message(kb_id, question, answer, citations, conversation_id=conversation_id)
        self.store.upsert_kb_conversation(kb_id, owner_id, conversation_id, first_title)
        yield {"event": "done", "data": {"message_id": message.id, "answer": answer,
                                          "citations": [item.model_dump() for item in citations]}}

    def rename_conversation(self, kb_id: str, owner_id: str, conversation_id: str, title: str) -> bool:
        self.require(kb_id, owner_id)
        return self.store.rename_kb_conversation(kb_id, owner_id, conversation_id, title)

    def delete_conversation(self, kb_id: str, owner_id: str, conversation_id: str) -> bool:
        self.require(kb_id, owner_id)
        return self.store.delete_kb_conversation(kb_id, owner_id, conversation_id)

    def conversations(self, kb_id: str, owner_id: str, limit: int = 50) -> list[dict]:
        self.require(kb_id, owner_id)
        return self.store.list_kb_conversations(kb_id, limit)

    def history(
        self, kb_id: str, owner_id: str, limit: int = 50, conversation_id: str | None = None
    ) -> list[KbMessageRecord]:
        self.require(kb_id, owner_id)
        rows = self.store.list_kb_messages(kb_id, limit, conversation_id)
        return [row for row in rows if row.answer.strip()]

    # ---------- 任务产物 ----------

    def _conversation_text(self, task_id: str, limit: int = 20000) -> str:
        messages = self.store.list_task_messages(task_id, limit=200)
        if not messages:
            return ""
        lines = ["# 对话记录", ""]
        for message in messages:
            role = "我" if message.role == "user" else "助手"
            lines.append(f"**{role}**")
            lines.append("")
            lines.append(message.content.strip())
            lines.append("")
        return "\n".join(lines)[:limit]

    def _artifact_text(self, task: TaskRecord, name: str) -> str:
        try:
            path = self.artifact_store.resolve(task.id, name)
        except FileNotFoundError:
            return ""
        if name.endswith(".csv"):
            try:
                rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))
            except OSError:
                return ""
            if not rows:
                return ""
            header, body = rows[0], rows[1:201]
            lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
            lines += ["| " + " | ".join(row) + " |" for row in body]
            return "\n".join(lines)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _describe_task(self, task: TaskRecord) -> tuple[str, str]:
        summary = (task.result or {}).get("summary") or {}
        try:
            title = display_name(task)
        except Exception:
            title = str(summary.get("title") or "任务记录")
        urls = task.params.get("urls") or []
        url = str(task.params.get("url") or (urls[0] if urls else "") or summary.get("url") or "")
        return title, url

    def _task_text(self, task: TaskRecord) -> str:
        for artifact in task.artifacts:
            if artifact.filename == "article.md":
                path = self.artifact_store.resolve(task.id, artifact.filename)
                return path.read_text(encoding="utf-8", errors="replace")
        return ""
