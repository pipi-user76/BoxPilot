"""私有知识库：SQLite 存元数据与正文，Chroma 存向量，问答走 LLM 网关。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.llm import LLMClient, LLMError
from backend.models import (
    KbAnswer,
    KbCitation,
    KbDocumentRecord,
    KbMessageRecord,
    KnowledgeBaseRecord,
    TaskRecord,
)
from backend.rag import chunk_text
from backend.storage import ArtifactStore

ANSWER_SYSTEM = (
    "你是 BoxPilot 的知识库助手。只依据给定的资料片段回答问题，"
    "用中文，条理清晰；如果资料不足，直接说明缺什么，不要编造。"
    "引用资料时在相应句子后用 [序号] 标注，例如 [1]。"
)


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
    ) -> int:
        if not chunks:
            return 0
        self.collection.add(
            ids=[f"{document_id}:{i}" for i in range(len(chunks))],
            documents=chunks,
            embeddings=embeddings,
            metadatas=[
                {"kb_id": kb_id, "document_id": document_id, "title": title, "url": url}
                for _ in chunks
            ],
        )
        return len(chunks)

    def delete_document(self, document_id: str) -> None:
        self.collection.delete(where={"document_id": document_id})

    def delete_base(self, kb_id: str) -> None:
        self.collection.delete(where={"kb_id": kb_id})

    def query(self, *, kb_id: str, embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where={"kb_id": kb_id},
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
                }
            )
        return hits


class KnowledgeBaseService:
    """知识库业务逻辑；路由只负责鉴权和参数转换。"""

    def __init__(
        self,
        store,
        artifact_store: ArtifactStore,
        llm: LLMClient | None = None,
        index: ChromaIndex | None = None,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.llm = llm
        self.index = index

    # ---------- 知识库 ----------

    def create(self, name: str, note: str, owner_id: str) -> KnowledgeBaseRecord:
        return self.store.create_knowledge_base(name.strip(), note.strip(), owner_id)

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
        self, kb_id: str, owner_id: str, *, title: str, url: str, content: str, task_id: str | None
    ) -> KbDocumentRecord:
        self.require(kb_id, owner_id)
        doc = self.store.add_kb_document(
            kb_id, title=title, url=url, content=content, task_id=task_id,
        )
        if self.index is not None:
            self._embed_into_index(doc)
        return self.store.get_kb_document(doc.id) or doc

    def _embed_into_index(self, doc: KbDocumentRecord) -> None:
        assert self.index is not None
        chunks = chunk_text(doc.content)
        if not chunks:
            self.store.set_kb_chunk_count(doc.id, 0)
            return
        vectors = self._embed(chunks)
        self.index.add(
            kb_id=doc.kb_id, document_id=doc.id, title=doc.title, url=doc.url,
            chunks=chunks, embeddings=vectors,
        )
        self.store.set_kb_chunk_count(doc.id, len(chunks))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self.llm is None or not self.llm.available():
            raise KnowledgeBaseError("LLM_NOT_CONFIGURED", "未配置嵌入模型，无法写入向量", 503)
        try:
            return self.llm.embed(texts)
        except LLMError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, 502) from exc

    # ---------- 问答 ----------

    def ask(
        self,
        kb_id: str,
        owner_id: str,
        question: str,
        top_k: int = 4,
        min_score: float = 0.0,
    ) -> KbAnswer:
        self.require(kb_id, owner_id)
        if self.index is None:
            raise KnowledgeBaseError("RAG_UNAVAILABLE", "向量索引未启用", 503)
        vectors = self._embed([question])
        hits = self.index.query(kb_id=kb_id, embedding=vectors[0], top_k=top_k)
        hits = [hit for hit in hits if float(hit.get("score", 0.0)) >= min_score]
        if not hits:
            answer = "没有检索到足够相关的资料，换个问法，或把相似度阈值调低一些。"
            message = self.store.add_kb_message(kb_id, question, answer, [])
            return KbAnswer(answer=answer, citations=[], message_id=message.id)
        context = "\n\n".join(
            f"[{index}] {hit['title']}（{hit['url']}）\n{hit['text']}"
            for index, hit in enumerate(hits, start=1)
        )
        messages = [
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"资料：\n{context}\n\n问题：{question}"},
        ]
        if self.llm is None or not self.llm.available():
            raise KnowledgeBaseError("LLM_NOT_CONFIGURED", "未配置对话模型，无法回答", 503)
        try:
            result = self.llm.chat(messages)
        except LLMError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, 502) from exc
        citations = [
            KbCitation(
                index=index,
                document_id=hit["document_id"],
                title=hit["title"],
                url=hit["url"],
                snippet=hit["text"][:200],
            )
            for index, hit in enumerate(hits, start=1)
        ]
        message = self.store.add_kb_message(kb_id, question, result.text, citations)
        return KbAnswer(answer=result.text, citations=citations, message_id=message.id)

    def history(self, kb_id: str, owner_id: str, limit: int = 50) -> list[KbMessageRecord]:
        self.require(kb_id, owner_id)
        return self.store.list_kb_messages(kb_id, limit)

    # ---------- 任务产物 ----------

    def _describe_task(self, task: TaskRecord) -> tuple[str, str]:
        summary = (task.result or {}).get("summary") or {}
        title = str(summary.get("title") or "网页资料整理")
        url = str(task.params.get("url") or summary.get("url") or "")
        return title, url

    def _task_text(self, task: TaskRecord) -> str:
        for artifact in task.artifacts:
            if artifact.filename == "article.md":
                path = self.artifact_store.resolve(task.id, artifact.filename)
                return path.read_text(encoding="utf-8", errors="replace")
        return ""
