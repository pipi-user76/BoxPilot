"""Rebuild knowledge-base chunks and vectors after retrieval upgrades."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import settings  # noqa: E402
from backend.kb import ChromaIndex, KnowledgeBaseService  # noqa: E402
from backend.llm import LLMClient  # noqa: E402
from backend.sqlite_store import SQLiteTaskStore  # noqa: E402
from backend.storage import ArtifactStore  # noqa: E402


def main() -> int:
    store = SQLiteTaskStore(settings.DATABASE_PATH)
    store.init_db()
    service = KnowledgeBaseService(
        store,
        ArtifactStore(settings.ARTIFACT_ROOT),
        LLMClient(),
        ChromaIndex(settings.CHROMA_PATH),
    )
    with sqlite3.connect(settings.DATABASE_PATH) as connection:
        bases = connection.execute("SELECT id, name FROM knowledge_bases").fetchall()
    total = 0
    for kb_id, name in bases:
        for doc in store.list_kb_documents(kb_id):
            try:
                service._embed_into_index(doc)
                total += 1
                print(f"reindexed {name} / {doc.title}")
            except Exception as exc:  # pragma: no cover - best effort
                print(f"failed {name} / {doc.title}: {exc}", file=sys.stderr)
    print(f"done: {total} documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
