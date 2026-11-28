"""知识库：一个任务按一次会话入库，重复覆盖，删除任务后保留。"""

from __future__ import annotations

import unittest

from backend.db import InMemoryTaskStore
from backend.kb import KnowledgeBaseService
from backend.models import Artifact, TaskStatus, TaskType
from backend.storage import ArtifactStore
from tests.support import workspace_temp_dir


class _Index:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, *, kb_id, document_id, title, url, chunks, embeddings, headings=None, kind="text") -> int:
        for text in chunks:
            self.rows.append({"document_id": document_id, "text": text})
        return len(chunks)

    def delete_document(self, document_id: str) -> None:
        self.rows = [row for row in self.rows if row["document_id"] != document_id]

    def delete_base(self, kb_id: str) -> None:
        self.rows = []

    def query(self, *, kb_id, embedding, top_k, document_ids=None, kinds=None):
        return []


class _LLM:
    def available(self) -> bool:
        return True

    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class KbIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = self.enterContext(workspace_temp_dir())
        self.store = InMemoryTaskStore()
        self.artifacts = ArtifactStore(folder / "artifacts", 1024 * 1024)
        self.service = KnowledgeBaseService(self.store, self.artifacts, _LLM(), _Index())
        self.kb = self.service.auto_kb("alice")
        self.task = self.store.create_task(
            TaskType.WEB_COMPARE,
            {"urls": ["https://example.com", "https://example.org"]},
            owner_id="alice",
        )
        self.store.finish_task(
            self.task.id,
            TaskStatus.SUCCESS,
            {"summary": {"kind": "web_compare", "conclusion": "两者定位不同，按场景选择。"}},
            None,
            None,
        )
        for name, body in (
            ("compare.md", "# 对比\n\n这是分析报告正文。"),
            ("source-1.md", "来源一正文"),
        ):
            self.artifacts.write_bytes(self.task.id, name, body.encode("utf-8"), "text/markdown")
            self.store.add_artifact(
                Artifact(
                    task_id=self.task.id,
                    filename=name,
                    relative_path=name,
                    content_type="text/markdown",
                    size_bytes=len(body),
                )
            )
        self.store.add_task_message(self.task.id, "user", "有什么区别？")
        self.store.add_task_message(self.task.id, "assistant", "一个偏目录，一个偏文本。")

    def test_ingest_creates_single_session_document(self) -> None:
        docs = self.service.ingest_task_artifacts(self.kb.id, "alice", self.task.id)
        self.assertEqual(len(docs), 1)
        doc = docs[0]
        self.assertEqual(doc.artifact_name, "session.md")
        self.assertEqual(doc.kind, "web_compare")
        self.assertIn("## 结论", doc.content)
        self.assertIn("## 分析报告（compare.md）", doc.content)
        self.assertIn("## 对话记录", doc.content)
        self.assertIn("## 产物清单", doc.content)
        self.assertIn("source-1.md", doc.content)

    def test_reingest_replaces_single_document(self) -> None:
        self.service.ingest_task_artifacts(self.kb.id, "alice", self.task.id)
        self.service.ingest_task_artifacts(self.kb.id, "alice", self.task.id)
        listed = self.service.list_documents(self.kb.id, "alice")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].artifact_name, "session.md")

    def test_include_chat_false_omits_conversation(self) -> None:
        docs = self.service.ingest_task_artifacts(
            self.kb.id, "alice", self.task.id, include_chat=False
        )
        self.assertNotIn("## 对话记录", docs[0].content)

    def test_deleting_task_keeps_document_but_marks_orphaned(self) -> None:
        self.service.ingest_task_artifacts(self.kb.id, "alice", self.task.id)
        self.store.mark_kb_documents_orphaned(self.task.id)
        self.store.delete_task(self.task.id)
        docs = self.service.list_documents(self.kb.id, "alice")
        self.assertEqual(len(docs), 1)
        self.assertTrue(docs[0].orphaned)
