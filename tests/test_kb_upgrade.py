from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.kb import KnowledgeBaseService
from backend.rag import chunk_sections
from backend.sqlite_store import SQLiteTaskStore


class ChunkSectionTests(unittest.TestCase):
    def test_keeps_heading_path(self) -> None:
        text = "# 总标题\n\n## 第一节\n" + "甲" * 1200 + "\n\n## 第二节\n" + "乙" * 800
        sections = chunk_sections(text, chunk_chars=400, overlap=50)
        self.assertTrue(sections)
        self.assertTrue(any(heading.endswith("第一节") for heading, _ in sections))
        self.assertTrue(all(len(chunk) <= 450 for _, chunk in sections))


class _LLM:
    def available(self) -> bool:
        return True

    def embed(self, texts):
        return [[0.1, 0.2] for _ in texts]


class _Index:
    def query(self, **_kwargs):
        return [
            {
                "document_id": "doc-vector",
                "title": "向量文档",
                "url": "",
                "text": "向量命中的片段",
                "heading": "结论",
                "score": 0.9,
            }
        ]

    def add(self, **_kwargs):
        return 1

    def delete_document(self, _document_id):
        return None

    def delete_base(self, _kb_id):
        return None


class SqliteKbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteTaskStore(Path(self.temp.name) / "kb.db")
        self.store.init_db()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_keyword_fallback_search(self) -> None:
        doc = self.store.add_kb_document("kb1", title="文档", url="", content="正文")
        self.store.replace_kb_chunks(
            "kb1", doc.id, [{"heading": "结论", "text": "图书价格随机，仅供练习"}]
        )
        hits = self.store.search_kb_chunks("kb1", "价格", limit=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["heading"], "结论")

    def test_conversation_rename_and_delete(self) -> None:
        self.store.upsert_kb_conversation("kb1", "u1", "c1", "第一问")
        self.store.add_kb_message("kb1", "问", "答", [], conversation_id="c1")
        self.assertEqual(self.store.list_kb_conversations("kb1")[0]["title"], "第一问")
        self.assertTrue(self.store.rename_kb_conversation("kb1", "u1", "c1", "改名"))
        self.assertEqual(self.store.list_kb_conversations("kb1")[0]["title"], "改名")
        self.assertTrue(self.store.delete_kb_conversation("kb1", "u1", "c1"))
        self.assertEqual(self.store.list_kb_conversations("kb1"), [])

    def test_rrf_fuses_keyword_and_vector(self) -> None:
        doc = self.store.add_kb_document("kb1", title="关键词文档", url="", content="正文")
        self.store.replace_kb_chunks(
            "kb1", doc.id, [{"heading": "关键词", "text": "价格与库存"}]
        )
        service = KnowledgeBaseService(self.store, None, _LLM(), _Index())
        hits = service._retrieve("kb1", "价格", 6, 0.0, None, None)
        self.assertEqual({hit["document_id"] for hit in hits}, {"doc-vector", doc.id})


if __name__ == "__main__":
    unittest.main()
