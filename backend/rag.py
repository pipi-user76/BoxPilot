"""文件职责：轻量向量存储与检索。

嵌入通过 LLM 网关生成，向量以 JSON 存在 SQLite；检索在 Python 里算余弦相似度。
面向几千个片段的任务级检索足够，不额外引入向量数据库服务。
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class SearchHit:
    task_id: str | None
    source: str
    text: str
    score: float


def chunk_text(text: str, chunk_chars: int = 800, overlap: int = 100) -> list[str]:
    """按段落聚合成固定长度的块，保留少量重叠避免语义被切断。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    paragraphs = [part.strip() for part in cleaned.split("\n\n") if part.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs or [cleaned]:
        while len(paragraph) > chunk_chars:
            head, paragraph = paragraph[:chunk_chars], paragraph[chunk_chars - overlap :]
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.append(head)
        if len(buffer) + len(paragraph) + 2 <= chunk_chars:
            buffer = f"{buffer}\n\n{paragraph}".strip()
        else:
            chunks.append(buffer)
            buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return chunks


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute(
                """CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT, source TEXT NOT NULL, text TEXT NOT NULL,
                    vector TEXT NOT NULL, created_at TEXT NOT NULL
                )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS chunk_task ON chunks(task_id)")
            with db:
                yield db
        finally:
            db.close()

    def add_texts(
        self,
        texts: Iterable[str],
        embedder: Embedder,
        *,
        task_id: str | None = None,
        source: str = "",
    ) -> int:
        items = [text for text in texts if text and text.strip()]
        if not items:
            return 0
        vectors = embedder.embed(items)
        if len(vectors) != len(items):
            raise ValueError("嵌入数量与文本数量不一致")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as db:
            db.executemany(
                "INSERT INTO chunks (task_id, source, text, vector, created_at) VALUES (?, ?, ?, ?, ?)",
                [(task_id, source, text, json.dumps(vector), now) for text, vector in zip(items, vectors)],
            )
        return len(items)

    def search(
        self,
        query: str,
        embedder: Embedder,
        *,
        limit: int = 5,
        task_id: str | None = None,
    ) -> list[SearchHit]:
        if not query.strip():
            return []
        vector = embedder.embed([query])[0]
        sql = "SELECT task_id, source, text, vector FROM chunks"
        params: tuple = ()
        if task_id is not None:
            sql += " WHERE task_id=?"
            params = (task_id,)
        hits: list[SearchHit] = []
        with self._connect() as db:
            for row in db.execute(sql, params):
                score = cosine(vector, json.loads(row["vector"]))
                hits.append(SearchHit(row["task_id"], row["source"], row["text"], score))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def count(self, task_id: str | None = None) -> int:
        with self._connect() as db:
            if task_id is None:
                row = db.execute("SELECT COUNT(*) AS total FROM chunks").fetchone()
            else:
                row = db.execute("SELECT COUNT(*) AS total FROM chunks WHERE task_id=?", (task_id,)).fetchone()
        return int(row["total"])
