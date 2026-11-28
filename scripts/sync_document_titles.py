"""Sync knowledge-base document titles with their task display names."""
from __future__ import annotations

import argparse
import sqlite3

from backend.config import settings
from backend.naming import display_name
from backend.sqlite_store import SQLiteTaskStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = SQLiteTaskStore(settings.DATABASE_PATH)
    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        rows = conn.execute(
            "SELECT id, title, task_id FROM kb_documents WHERE task_id IS NOT NULL"
        ).fetchall()

    changed = 0
    for document_id, old_title, task_id in rows:
        task = store.get_task(task_id)
        if task is None:
            continue
        new_title = display_name(task)
        if new_title == old_title:
            continue
        changed += 1
        print(f"{document_id}: {old_title!r} -> {new_title!r}")
        if not args.dry_run:
            store.update_kb_document_title(document_id, new_title)
    print(f"{'would update' if args.dry_run else 'updated'} {changed} document(s)")


if __name__ == "__main__":
    main()
