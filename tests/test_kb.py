"""知识库：文档入库、按用户隔离、问答引用与历史。"""

from __future__ import annotations

import unittest
from pathlib import Path

from backend.db import InMemoryTaskStore
from backend.kb import KnowledgeBaseError, KnowledgeBaseService
from backend.llm import ChatResult
from backend.models import TaskType
from backend.storage import ArtifactStore


class FakeIndex:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, *, kb_id, document_id, title, url, chunks, embeddings) -> int:
        for index, text in enumerate(chunks):
            self.rows.append(
                {
                    "kb_id": kb_id,
                    "document_id": document_id,
                    "title": title,
                    "url": url,
                    "text": text,
                    "index": index,
                }
            )
        return len(chunks)

    def delete_document(self, document_id: str) -> None:
        self.rows = [row for row in self.rows if row["document_id"] != document_id]

    def delete_base(self, kb_id: str) -> None:
        self.rows = [row for row in self.rows if row["kb_id"] != kb_id]

    def query(self, *, kb_id, embedding, top_k):
        hits = [row for row in self.rows if row["kb_id"] == kb_id][:top_k]
        return [
            {
                "text": row["text"],
                "document_id": row["document_id"],
                "title": row["title"],
                "url": row["url"],
                "score": 0.5,
            }
            for row in hits
        ]


class FakeLLM:
    def available(self) -> bool:
        return True

    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    def chat(self, messages, **kwargs):
        return ChatResult(text="根据资料，答案是 42 [1]。", model="fake", usage={}, raw={})


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryTaskStore()
        self.artifacts = ArtifactStore(Path("runtime/artifacts"), 1024 * 1024)
        self.index = FakeIndex()
        self.service = KnowledgeBaseService(self.store, self.artifacts, FakeLLM(), self.index)
        self.kb = self.service.create("竞赛资料", "校内比赛", "alice")

    def test_ingest_and_list_documents(self) -> None:
        doc = self.service.add_text(
            self.kb.id, "alice",
            title="示例页面",
            url="https://example.com",
            content="BoxPilot 是一个任务执行工作台。" * 20,
        )
        self.assertGreater(doc.chunk_count, 0)
        docs = self.service.list_documents(self.kb.id, "alice")
        self.assertEqual([d.id for d in docs], [doc.id])
        self.assertEqual(len(self.index.rows), doc.chunk_count)

    def test_ownership_isolation(self) -> None:
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self.service.list_documents(self.kb.id, "bob")
        self.assertEqual(ctx.exception.status, 404)

    def test_ask_returns_citations_and_history(self) -> None:
        self.service.add_text(
            self.kb.id, "alice",
            title="示例页面",
            url="https://example.com",
            content="完整度用于描述缺失值的比例。" * 30,
        )
        answer = self.service.ask(self.kb.id, "alice", "完整度是什么？", 2)
        self.assertIn("[1]", answer.answer)
        self.assertEqual(answer.citations[0].title, "示例页面")
        history = self.service.history(self.kb.id, "alice")
        self.assertEqual(history[0].question, "完整度是什么？")
        self.assertEqual(history[0].citations[0].url, "https://example.com")

    def test_ask_threshold_filters_low_scores(self) -> None:
        self.service.add_text(
            self.kb.id, "alice", title="示例页面", url="", content="内容" * 50,
        )
        answer = self.service.ask(self.kb.id, "alice", "随便问问", 3, 0.9)
        self.assertEqual(answer.citations, [])
        self.assertIn("没有检索到", answer.answer)

    def test_extract_upload_text(self) -> None:
        from backend.routes_kb import _extract_upload

        self.assertEqual(_extract_upload("notes.txt", "知识库测试".encode("utf-8")), "知识库测试")
        self.assertEqual(_extract_upload("notes.txt", "知识库测试".encode("gb18030")), "知识库测试")

    def test_delete_document_removes_vectors(self) -> None:
        doc = self.service.add_text(
            self.kb.id, "alice", title="t", url="", content="内容" * 50,
        )
        self.service.delete_document(self.kb.id, "alice", doc.id)
        self.assertEqual(self.service.list_documents(self.kb.id, "alice"), [])
        self.assertEqual(self.index.rows, [])


from pathlib import Path

if __name__ == "__main__":
    unittest.main()
