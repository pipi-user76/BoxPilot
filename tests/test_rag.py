"""文件职责：文本分块、向量写入与余弦检索。"""

from __future__ import annotations

import unittest

from backend.rag import VectorStore, chunk_text, cosine

try:
    from tests.support import make_temp_dir, remove_temp_dir
except ImportError:
    from support import make_temp_dir, remove_temp_dir


class FakeEmbedder:
    """把关键词映射到固定维度，便于断言检索顺序。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lower = text.lower()
            vectors.append(
                [
                    float(lower.count("价格") + lower.count("price")),
                    float(lower.count("库存") + lower.count("stock")),
                    float(lower.count("标题") + lower.count("title")),
                ]
            )
        return vectors


class VectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.store = VectorStore(self.temp_dir / "vectors.db")

    def tearDown(self) -> None:
        remove_temp_dir(self.temp_dir)

    def test_chunk_text_splits_long_paragraphs(self) -> None:
        text = "段落一。" * 60 + "\n\n" + "段落二。" * 60
        chunks = chunk_text(text, chunk_chars=120, overlap=20)
        self.assertGreaterEqual(len(chunks), 3)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))

    def test_search_orders_by_similarity(self) -> None:
        embedder = FakeEmbedder()
        self.store.add_texts(
            ["价格 价格 价格", "库存 库存 库存", "标题 标题"],
            embedder,
            task_id="t1",
            source="a.md",
        )
        hits = self.store.search("价格 price", embedder, limit=2)
        self.assertEqual(hits[0].text, "价格 价格 价格")
        self.assertGreater(hits[0].score, hits[1].score)
        self.assertEqual(self.store.count("t1"), 3)

    def test_search_filters_by_task(self) -> None:
        embedder = FakeEmbedder()
        self.store.add_texts(["价格"], embedder, task_id="t1")
        self.store.add_texts(["价格"], embedder, task_id="t2")
        self.assertEqual([hit.task_id for hit in self.store.search("价格", embedder, task_id="t2")], ["t2"])

    def test_cosine_edge_cases(self) -> None:
        self.assertEqual(cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertEqual(cosine([0.0, 0.0], [1.0, 0.0]), 0.0)
        self.assertEqual(cosine([1.0], [1.0, 0.0]), 0.0)


if __name__ == "__main__":
    unittest.main()
