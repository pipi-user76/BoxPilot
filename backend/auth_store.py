"""用户账号存储：登录所需的最小字段，密码只保存 scrypt 哈希。

两种实现共用同一份协议：生产用 SQLite（与任务表同库），测试用内存版。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Protocol

from backend.models import UserRecord, utc_now


class AuthStore(Protocol):
    def count_users(self) -> int: ...

    def create_user(self, username: str, password_hash: str, *, is_admin: bool = False) -> UserRecord: ...

    def get_user(self, user_id: str) -> UserRecord | None: ...

    def get_user_with_hash(self, username: str) -> tuple[UserRecord, str] | None: ...

    def touch_login(self, user_id: str) -> None: ...

    def list_users(self) -> list[UserRecord]: ...

    def delete_user(self, user_id: str) -> bool: ...

    def set_password_hash(self, user_id: str, password_hash: str) -> bool: ...


class InMemoryAuthStore:
    name = "memory"

    def __init__(self) -> None:
        self._users: dict[str, UserRecord] = {}
        self._hashes: dict[str, str] = {}

    def count_users(self) -> int:
        return len(self._users)

    def create_user(self, username: str, password_hash: str, *, is_admin: bool = False) -> UserRecord:
        lowered = username.strip().lower()
        if any(user.username.lower() == lowered for user in self._users.values()):
            raise ValueError("username already exists")
        user = UserRecord(username=username.strip(), is_admin=is_admin)
        self._users[user.id] = user
        self._hashes[user.id] = password_hash
        return user.model_copy(deep=True)

    def get_user(self, user_id: str) -> UserRecord | None:
        user = self._users.get(user_id)
        return user.model_copy(deep=True) if user else None

    def get_user_with_hash(self, username: str) -> tuple[UserRecord, str] | None:
        lowered = username.strip().lower()
        for user in self._users.values():
            if user.username.lower() == lowered:
                return user.model_copy(deep=True), self._hashes[user.id]
        return None

    def touch_login(self, user_id: str) -> None:
        user = self._users.get(user_id)
        if user:
            user.last_login_at = utc_now()

    def list_users(self) -> list[UserRecord]:
        users = [user.model_copy(deep=True) for user in self._users.values()]
        return sorted(users, key=lambda item: item.created_at)

    def delete_user(self, user_id: str) -> bool:
        if user_id not in self._users:
            return False
        self._users.pop(user_id, None)
        self._hashes.pop(user_id, None)
        return True

    def set_password_hash(self, user_id: str, password_hash: str) -> bool:
        if user_id not in self._users:
            return False
        self._hashes[user_id] = password_hash
        return True


class SQLiteAuthStore:
    name = "sqlite"

    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _model(row: sqlite3.Row) -> UserRecord:
        return UserRecord(
            id=row["id"],
            username=row["username"],
            is_admin=bool(row["is_admin"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_login_at=datetime.fromisoformat(row["last_login_at"]) if row["last_login_at"] else None,
        )

    def count_users(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT count(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password_hash: str, *, is_admin: bool = False) -> UserRecord:
        user = UserRecord(username=username.strip(), is_admin=is_admin)
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user.id, user.username, password_hash, int(user.is_admin), user.created_at.isoformat()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("username already exists") from exc
        return user

    def get_user(self, user_id: str) -> UserRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._model(row) if row else None

    def get_user_with_hash(self, username: str) -> tuple[UserRecord, str] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)
            ).fetchone()
        return (self._model(row), row["password_hash"]) if row else None

    def touch_login(self, user_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE users SET last_login_at=? WHERE id=?", (utc_now().isoformat(), user_id))

    def list_users(self) -> list[UserRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [self._model(row) for row in rows]

    def delete_user(self, user_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM users WHERE id=?", (user_id,))
        return cursor.rowcount > 0

    def set_password_hash(self, user_id: str, password_hash: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
        return cursor.rowcount > 0
