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

    def add(self, *, kb_id, document_id, title, url, chunks, embeddings, headings=None, kind="text") -> int:
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

    def query(self, *, kb_id, embedding, top_k, document_ids=None, kinds=None):
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

    def chat_stream(self, messages, **kwargs):
        for part in ("根据资料，", "答案是 42 ", "[1]。"):
            yield part

    def chat_stream_events(self, messages, **kwargs):
        yield {"type": "reasoning", "text": "先理解问题。"}
        for part in ("根据资料，", "答案是 42 ", "[1]。"):
            yield {"type": "content", "text": part}


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

    def test_rename_base_updates_name_and_is_owner_scoped(self) -> None:
        renamed = self.service.rename(self.kb.id, "alice", "HTML 资料")
        self.assertEqual(renamed.name, "HTML 资料")
        self.assertEqual(self.service.list("alice")[0].name, "HTML 资料")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self.service.rename(self.kb.id, "bob", "越权改名")
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

    def test_ask_stream_emits_status_and_deltas(self) -> None:
        self.service.add_text(
            self.kb.id, "alice",
            title="示例页面",
            url="https://example.com",
            content="完整度用于描述缺失值的比例。" * 30,
        )
        events = list(self.service.ask_stream(self.kb.id, "alice", "完整度是什么？", 2))
        names = [item["event"] for item in events]
        self.assertEqual(names[0], "status")
        self.assertEqual(events[0]["data"]["stage"], "retrieving")
        self.assertIn("generating", [item["data"].get("stage") for item in events if item["event"] == "status"])
        self.assertTrue(any(item["event"] == "delta" for item in events))
        self.assertEqual(names[-1], "done")

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

    def test_document_summaries_drop_content_and_preview(self) -> None:
        self.service.add_text(
            self.kb.id, "alice", title="长文档", url="https://example.com",
            content="# 标题\n\n" + "这是正文内容。" * 40,
        )
        summaries = self.service.list_document_summaries(self.kb.id, "alice")
        self.assertEqual(len(summaries), 1)
        self.assertFalse(hasattr(summaries[0], "content"))
        self.assertLessEqual(len(summaries[0].preview), 140)
        self.assertTrue(summaries[0].preview.startswith("标题"))

    def test_list_task_documents_is_owner_scoped_and_cleared_on_delete(self) -> None:
        task = self.store.create_task(
            TaskType.WEB_DIGEST, {"url": "https://example.com"}, owner_id="alice",
        )
        self.service.add_text(
            self.kb.id, "alice", title="任务归档", url="", content="任务正文" * 20,
            task_id=task.id,
        )
        rows = self.service.list_task_documents(task.id, "alice")
        self.assertEqual([row.kb_id for row in rows], [self.kb.id])
        self.assertEqual(rows[0].kb_name, "竞赛资料")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self.service.list_task_documents(task.id, "bob")
        self.assertEqual(ctx.exception.status, 404)
        self.service.delete(self.kb.id, "alice")
        self.assertEqual(self.service.list_task_documents(task.id, "alice"), [])
        self.assertEqual(self.index.rows, [])

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
