"""Idempotently initialize the configured SQLite database."""
from backend.config import settings
from backend.sqlite_store import SQLiteTaskStore

if __name__ == '__main__':
    store = SQLiteTaskStore(settings.DATABASE_PATH)
    store.init_db()
    print(store.path)
