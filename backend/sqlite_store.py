"""SQLite implementation of the shared TaskStore contract."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import uuid

from backend.models import (
    Artifact,
    KbCitation,
    KbDocumentRecord,
    KbMessageRecord,
    KnowledgeBaseRecord,
    NotificationRecord,
    ScheduleRecord,
    StepStatus,
    TaskChatMessage,
    TaskRecord,
    TaskStatus,
    TaskStep,
    TaskType,
    utc_now,
)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, task_type TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','SUCCESS','FAILED','TIMEOUT','CANCELLED')),
        dedupe_key TEXT, owner_id TEXT, created_at TEXT NOT NULL, payload TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS steps (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        step_index INTEGER NOT NULL CHECK(step_index > 0), payload TEXT NOT NULL,
        UNIQUE(task_id, step_index)
    )""",
    """CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        relative_path TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(task_id, relative_path)
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, last_login_at TEXT
    )""",
    'CREATE INDEX IF NOT EXISTS task_created ON tasks(created_at DESC, id DESC)',
    'CREATE INDEX IF NOT EXISTS task_dedupe ON tasks(dedupe_key, created_at DESC)',
    'CREATE INDEX IF NOT EXISTS task_owner ON tasks(owner_id, created_at DESC)',
)

# v1 -> v2：加入用户表与任务归属。历史任务 owner_id 保持 NULL，只有管理员可见。
MIGRATION_V2 = (
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, last_login_at TEXT
    )""",
    'ALTER TABLE tasks ADD COLUMN owner_id TEXT',
    'CREATE INDEX IF NOT EXISTS task_owner ON tasks(owner_id, created_at DESC)',
)

# v2 -> v3：加入定时任务表。next_run_at 用 ISO 字符串，便于直接按时间排序。
MIGRATION_V3 = (
    """CREATE TABLE IF NOT EXISTS schedules (
        id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
        task_type TEXT NOT NULL, cron TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        owner_id TEXT, next_run_at TEXT NOT NULL, last_run_at TEXT,
        last_task_id TEXT, payload TEXT NOT NULL
    )""",
    'CREATE INDEX IF NOT EXISTS schedule_due ON schedules(enabled, next_run_at)',
    'CREATE INDEX IF NOT EXISTS schedule_owner ON schedules(owner_id)',
)

MIGRATION_V4 = (
    """CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, task_id TEXT NOT NULL,
        task_type TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'success',
        title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, read_at TEXT
    )""",
    'CREATE INDEX IF NOT EXISTS notification_user ON notifications(user_id, created_at)',
    'CREATE INDEX IF NOT EXISTS notification_unread ON notifications(user_id, read_at)',
)

MIGRATION_V5 = (
    """CREATE TABLE IF NOT EXISTS knowledge_bases (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
        owner_id TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS kb_documents (
        id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, task_id TEXT,
        title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '', chunk_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS kb_messages (
        id TEXT PRIMARY KEY, kb_id TEXT NOT NULL,
        question TEXT NOT NULL, answer TEXT NOT NULL,
        citations TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
    )""",
    'CREATE INDEX IF NOT EXISTS kb_owner ON knowledge_bases(owner_id, created_at)',
    'CREATE INDEX IF NOT EXISTS kb_doc_kb ON kb_documents(kb_id, created_at)',
    'CREATE INDEX IF NOT EXISTS kb_msg_kb ON kb_messages(kb_id, created_at)',
)

SCHEMA_VERSION = 10

MIGRATION_V6 = (
    "ALTER TABLE kb_messages ADD COLUMN conversation_id TEXT NOT NULL DEFAULT 'default'",
    "CREATE INDEX IF NOT EXISTS kb_msg_conversation ON kb_messages(kb_id, conversation_id, created_at)",
)

MIGRATION_V7 = (
    """CREATE TABLE IF NOT EXISTS task_messages (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS task_msg_task ON task_messages(task_id, created_at)",
)

MIGRATION_V9 = (
    """CREATE TABLE IF NOT EXISTS kb_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kb_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        heading TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS kb_chunk_doc ON kb_chunks(kb_id, document_id)",
    """CREATE TABLE IF NOT EXISTS kb_conversations (
        id TEXT PRIMARY KEY,
        kb_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS kb_conv_kb ON kb_conversations(kb_id, updated_at)",
)

MIGRATION_V10 = (
    """CREATE TABLE IF NOT EXISTS tags (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(owner_id, name)
    )""",
    """CREATE TABLE IF NOT EXISTS task_tags (
        task_id TEXT NOT NULL,
        tag_id TEXT NOT NULL,
        PRIMARY KEY(task_id, tag_id)
    )""",
    "CREATE INDEX IF NOT EXISTS task_tag_tag ON task_tags(tag_id)",
    """CREATE TABLE IF NOT EXISTS kb_document_tags (
        document_id TEXT NOT NULL,
        tag_id TEXT NOT NULL,
        PRIMARY KEY(document_id, tag_id)
    )""",
    "CREATE INDEX IF NOT EXISTS kb_doc_tag_tag ON kb_document_tags(tag_id)",
    """CREATE TABLE IF NOT EXISTS annotations (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        task_id TEXT,
        document_id TEXT,
        artifact_name TEXT NOT NULL,
        block_index INTEGER NOT NULL,
        start_offset INTEGER NOT NULL,
        end_offset INTEGER NOT NULL,
        quote TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS annotation_task ON annotations(task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS annotation_doc ON annotations(document_id, created_at)",
)

MIGRATION_V8 = (
    "ALTER TABLE kb_documents ADD COLUMN artifact_name TEXT",
    "ALTER TABLE kb_documents ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'",
    "ALTER TABLE kb_documents ADD COLUMN orphaned INTEGER NOT NULL DEFAULT 0",
    "CREATE UNIQUE INDEX IF NOT EXISTS kb_doc_task_artifact "
    "ON kb_documents(kb_id, task_id, artifact_name) WHERE task_id IS NOT NULL",
)


class SQLiteTaskStore:
    name = 'sqlite'

    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    @contextmanager
    def _connection(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys = ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            with db:
                yield db
        finally:
            db.close()

    def init_db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA journal_mode = WAL')
        finally:
            db.close()
        with self._connection(write=True) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == SCHEMA_VERSION:
                return
            if version == 1:
                for statement in MIGRATION_V2:
                    db.execute(statement)
                version = 2
            if version == 2:
                for statement in MIGRATION_V3:
                    db.execute(statement)
                version = 3
            if version == 3:
                for statement in MIGRATION_V4:
                    db.execute(statement)
                version = 4
            if version == 4:
                for statement in MIGRATION_V5:
                    db.execute(statement)
                version = 5
            if version == 5:
                for statement in MIGRATION_V6:
                    db.execute(statement)
                version = 6
            if version == 6:
                for statement in MIGRATION_V7:
                    db.execute(statement)
                version = 7
            if version == 7:
                for statement in MIGRATION_V8:
                    db.execute(statement)
                version = 8
            if version == 8:
                for statement in MIGRATION_V9:
                    db.execute(statement)
                version = 9
            if version == 9:
                for statement in MIGRATION_V10:
                    db.execute(statement)
                db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
                return
            if version != 0 or db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone():
                raise RuntimeError(f'无法使用此数据库结构：{self.path}。请配置新的 DATABASE_PATH。')
            for statement in SCHEMA:
                db.execute(statement)
            for statement in MIGRATION_V3:
                db.execute(statement)
            for statement in MIGRATION_V4:
                db.execute(statement)
            for statement in MIGRATION_V5:
                db.execute(statement)
            for statement in MIGRATION_V6:
                db.execute(statement)
            for statement in MIGRATION_V7:
                db.execute(statement)
            for statement in MIGRATION_V8:
                db.execute(statement)
            for statement in MIGRATION_V9:
                db.execute(statement)
            for statement in MIGRATION_V10:
                db.execute(statement)
            db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')

    def _task_models(self, db, rows):
        tasks = {
            row['id']: TaskRecord.model_validate_json(row['payload'])
            for row in rows
        }
        if not tasks:
            return []
        placeholders = ','.join('?' for _ in tasks)
        for row in db.execute(
            f'SELECT task_id, payload FROM steps WHERE task_id IN ({placeholders}) ORDER BY step_index',
            tuple(tasks),
        ):
            tasks[row['task_id']].steps.append(TaskStep.model_validate_json(row['payload']))
        for row in db.execute(
            f'SELECT task_id, payload FROM artifacts WHERE task_id IN ({placeholders}) ORDER BY rowid',
            tuple(tasks),
        ):
            tasks[row['task_id']].artifacts.append(Artifact.model_validate_json(row['payload']))
        return list(tasks.values())

    def _get_task(self, db, task_id):
        rows = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,))
        return next(iter(self._task_models(db, rows)), None)

    def get_task(self, task_id):
        with self._connection() as db:
            return self._get_task(db, task_id)

    def _dedupe_match(self, db, key, since, owner_id=None):
        row = db.execute(
            """SELECT id FROM tasks WHERE dedupe_key=? AND created_at>=? AND owner_id IS ?
            AND status IN ('PENDING','RUNNING','SUCCESS') ORDER BY created_at DESC, id DESC LIMIT 1""",
            (key, since.isoformat(timespec="microseconds"), owner_id),
        ).fetchone()
        return self._get_task(db, row['id']) if row else None

    def find_by_dedupe(self, dedupe_key, since, owner_id=None):
        with self._connection() as db:
            return self._dedupe_match(db, dedupe_key, since, owner_id)

    def create_task(self, task_type, params, *, attempt=1, retry_of=None, owner_id=None,
                    dedupe_key=None, dedupe_since=None):
        task = TaskRecord(task_type=task_type, params=params, attempt=attempt, retry_of=retry_of,
                          owner_id=owner_id, dedupe_key=dedupe_key)
        with self._connection(write=True) as db:
            if dedupe_key and dedupe_since is not None:
                existing = self._dedupe_match(db, dedupe_key, dedupe_since, owner_id)
                if existing is not None:
                    return existing.model_copy(update={'deduplicated': True})
            db.execute(
                'INSERT INTO tasks (id, task_type, status, dedupe_key, owner_id, created_at, payload)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?)',
                (task.id, task.task_type.value, task.status.value, dedupe_key, owner_id,
                 task.created_at.isoformat(timespec="microseconds"),
                 task.model_dump_json(exclude={'steps', 'artifacts'})),
            )
        return task

    def list_tasks(self, *, query='', statuses=None, task_type=None, task_types=None, page=1, page_size=20,
                   owner_id=None, tag=None):
        predicates, params = [], []
        if owner_id is not None:
            predicates.append('owner_id=?')
            params.append(owner_id)
        if statuses:
            predicates.append('status IN (' + ','.join('?' for _ in statuses) + ')')
            params.extend(statuses)
        if task_type is not None:
            predicates.append('task_type=?')
            params.append(task_type)
        if task_types:
            predicates.append('task_type IN (' + ','.join('?' for _ in task_types) + ')')
            params.extend(task_types)
        if tag:
            predicates.append("EXISTS (SELECT 1 FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id "
                              "WHERE tt.task_id=tasks.id AND tg.name=?)")
            params.append(tag)
        if query.strip():
            predicates.append("""(
                instr(lower(id || ' ' || task_type || ' ' || coalesce(json_extract(payload,'$.params'),'')
                || ' ' || coalesce(json_extract(payload,'$.error_code'),'')
                || ' ' || coalesce(json_extract(payload,'$.error_message'),'')), ?) > 0
                OR EXISTS (SELECT 1 FROM steps WHERE task_id=tasks.id
                    AND instr(lower(json_extract(steps.payload,'$.name')), ?) > 0)
            )""")
            params.extend([query.strip().lower()] * 2)
        where = ' WHERE ' + ' AND '.join(predicates) if predicates else ''
        with self._connection() as db:
            total = db.execute('SELECT count(*) FROM tasks' + where, params).fetchone()[0]
            rows = db.execute(
                'SELECT * FROM tasks' + where + ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                (*params, page_size, (page - 1) * page_size),
            )
            return self._task_models(db, rows), total

    def count_tasks(self, owner_id=None, task_type=None, task_types=None, tag=None):
        counts = {status.value: 0 for status in TaskStatus}
        clauses, params = [], []
        if owner_id is not None:
            clauses.append('owner_id=?')
            params.append(owner_id)
        if task_type is not None:
            clauses.append('task_type=?')
            params.append(task_type)
        if task_types:
            clauses.append('task_type IN (' + ','.join('?' for _ in task_types) + ')')
            params.extend(task_types)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self._connection() as db:
            counts.update(dict(db.execute('SELECT status,count(*) FROM tasks' + where + ' GROUP BY status', params)))
        return {'total': sum(counts.values()), **counts}

    def delete_task(self, task_id):
        with self._connection(write=True) as db:
            return db.execute('DELETE FROM tasks WHERE id=?', (task_id,)).rowcount > 0

    def purge_tasks(self, statuses, older_than, owner_id=None):
        if not statuses:
            return []
        placeholders = ','.join('?' for _ in statuses)
        owner_clause, owner_params = (' AND owner_id=?', (owner_id,)) if owner_id is not None else ('', ())
        with self._connection(write=True) as db:
            return [row['id'] for row in db.execute(
                f'DELETE FROM tasks WHERE status IN ({placeholders}) AND created_at<?' + owner_clause + ' RETURNING id',
                (*statuses, older_than.isoformat(timespec="microseconds"), *owner_params),
            )]

    def _require_task(self, db, task_id):
        task = self._get_task(db, task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _save_task(self, db, task):
        db.execute('UPDATE tasks SET status=?, payload=? WHERE id=?',
                   (task.status.value, task.model_dump_json(exclude={'steps', 'artifacts'}), task.id))

    def reset_task_for_retry(self, task_id, attempt):
        with self._connection(write=True) as db:
            row = db.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            data = json.loads(row["payload"])
            data.update(
                {
                    "status": "PENDING",
                    "attempt": attempt,
                    "retry_of": None,
                    "result": None,
                    "error_code": None,
                    "error_message": None,
                    "started_at": None,
                    "finished_at": None,
                }
            )
            db.execute(
                "UPDATE tasks SET status = ?, payload = ? WHERE id = ?",
                ("PENDING", json.dumps(data, ensure_ascii=False), task_id),
            )
            db.execute("DELETE FROM steps WHERE task_id = ?", (task_id,))
            db.execute("DELETE FROM artifacts WHERE task_id = ?", (task_id,))
        return self.get_task(task_id)

    def start_task(self, task_id):
        with self._connection(write=True) as db:
            task = self._require_task(db, task_id)
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.RUNNING
                task.started_at = utc_now()
                task.error_code = task.error_message = None
                self._save_task(db, task)
            return task

    def finish_task(self, task_id, status, result, error_code, error_message):
        with self._connection(write=True) as db:
            task = self._require_task(db, task_id)
            if task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                return task
            stamp = utc_now()
            task.status, task.result, task.finished_at = status, result, stamp
            task.error_code, task.error_message = error_code, error_message
            for step in task.steps:
                if step.status == StepStatus.RUNNING:
                    self._finish_step(db, step, StepStatus.FAILED, step.output, error_code, error_message)
            self._save_task(db, task)
            return task

    def add_step(self, task_id, step_index, name):
        step = TaskStep(task_id=task_id, step_index=step_index, name=name)
        with self._connection(write=True) as db:
            db.execute('INSERT INTO steps VALUES (?, ?, ?, ?)',
                       (step.id, task_id, step_index, step.model_dump_json()))
        return step

    def _finish_step(self, db, step, status, output, error_code, error_message):
        step.status, step.output = status, output
        step.error_code, step.error_message = error_code, error_message
        step.finished_at = utc_now()
        step.duration_ms = int((step.finished_at - step.started_at).total_seconds() * 1000)
        db.execute('UPDATE steps SET payload=? WHERE id=?', (step.model_dump_json(), step.id))
        return step

    def finish_step(self, step_id, status, output, error_code, error_message):
        with self._connection(write=True) as db:
            row = db.execute('SELECT payload FROM steps WHERE id=?', (step_id,)).fetchone()
            if row is None:
                raise KeyError(step_id)
            step = TaskStep.model_validate_json(row['payload'])
            return self._finish_step(db, step, status, output, error_code, error_message)

    def add_artifact(self, artifact):
        with self._connection(write=True) as db:
            db.execute('INSERT INTO artifacts VALUES (?, ?, ?, ?)',
                       (artifact.id, artifact.task_id, artifact.relative_path, artifact.model_dump_json()))
        return artifact

    def add_task_message(self, task_id, role, content):
        message = TaskChatMessage(
            id=uuid.uuid4().hex,
            task_id=task_id,
            role=role,
            content=content,
            created_at=utc_now(),
        )
        with self._connection(write=True) as db:
            db.execute(
                'INSERT INTO task_messages (id, task_id, role, content, created_at) VALUES (?,?,?,?,?)',
                (
                    message.id,
                    message.task_id,
                    message.role,
                    message.content,
                    message.created_at.isoformat(),
                ),
            )
        return message

    def list_task_messages(self, task_id, limit=50):
        with self._connection() as db:
            rows = db.execute(
                'SELECT * FROM task_messages WHERE task_id=? ORDER BY created_at DESC LIMIT ?',
                (task_id, limit),
            ).fetchall()
        items = [
            TaskChatMessage(
                id=row['id'],
                task_id=row['task_id'],
                role=row['role'],
                content=row['content'],
                created_at=datetime.fromisoformat(row['created_at']),
            )
            for row in rows
        ]
        return list(reversed(items))

    def get_artifact(self, artifact_id):
        with self._connection() as db:
            row = db.execute('SELECT * FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
            if row:
                return Artifact.model_validate_json(row['payload']), row['task_id']
        return None

    @staticmethod
    def _schedule_from_row(row) -> ScheduleRecord:
        return ScheduleRecord.model_validate_json(row['payload'])

    def create_schedule(
        self, task_type, params, cron, next_run_at, *, owner_id=None, name=''
    ):
        record = ScheduleRecord(
            name=name, task_type=task_type, params=params, cron=cron,
            owner_id=owner_id, next_run_at=next_run_at,
        )
        with self._connection(write=True) as db:
            db.execute(
                """INSERT INTO schedules
                (id, name, task_type, cron, enabled, owner_id, next_run_at, last_run_at, last_task_id, payload)
                VALUES (?, ?, ?, ?, 1, ?, ?, NULL, NULL, ?)""",
                (record.id, record.name, record.task_type.value, record.cron, record.owner_id,
                 record.next_run_at.isoformat(timespec='microseconds'), record.model_dump_json()),
            )
        return record

    def list_schedules(self, owner_id=None):
        sql = 'SELECT payload FROM schedules'
        params: tuple = ()
        if owner_id is not None:
            sql += ' WHERE owner_id=?'
            params = (owner_id,)
        sql += ' ORDER BY next_run_at, id'
        with self._connection() as db:
            return [self._schedule_from_row(row) for row in db.execute(sql, params)]

    def set_schedule_enabled(self, schedule_id, enabled, next_run_at=None):
        with self._connection(write=True) as db:
            row = db.execute('SELECT * FROM schedules WHERE id=?', (schedule_id,)).fetchone()
            if row is None:
                return None
            record = self._schedule_from_row(row)
            record.enabled = enabled
            if next_run_at is not None:
                record.next_run_at = next_run_at
            db.execute(
                'UPDATE schedules SET enabled=?, next_run_at=?, payload=? WHERE id=?',
                (int(enabled), record.next_run_at.isoformat(timespec='microseconds'),
                 record.model_dump_json(), schedule_id),
            )
            return record

    def delete_schedule(self, schedule_id):
        with self._connection(write=True) as db:
            cursor = db.execute('DELETE FROM schedules WHERE id=?', (schedule_id,))
        return cursor.rowcount > 0

    def due_schedules(self, now, limit=5):
        with self._connection() as db:
            rows = db.execute(
                'SELECT payload FROM schedules WHERE enabled=1 AND next_run_at<=? '
                'ORDER BY next_run_at, id LIMIT ?',
                (now.isoformat(timespec='microseconds'), limit),
            )
            return [self._schedule_from_row(row) for row in rows]

    def mark_schedule_run(self, schedule_id, ran_at, next_run_at, task_id):
        with self._connection(write=True) as db:
            row = db.execute('SELECT payload FROM schedules WHERE id=?', (schedule_id,)).fetchone()
            if row is None:
                return
            record = self._schedule_from_row(row)
            record.last_run_at = ran_at
            record.last_task_id = task_id
            record.next_run_at = next_run_at
            db.execute(
                'UPDATE schedules SET next_run_at=?, last_run_at=?, last_task_id=?, payload=? WHERE id=?',
                (next_run_at.isoformat(timespec='microseconds'),
                 ran_at.isoformat(timespec='microseconds'), task_id,
                 record.model_dump_json(), schedule_id),
            )

    def recover_interrupted(self):
        """Called at single-worker startup; unfinished operations can be retried manually."""
        with self._connection() as db:
            ids = [row['id'] for row in db.execute(
                "SELECT id FROM tasks WHERE status IN ('PENDING','RUNNING')"
            )]
        for task_id in ids:
            self.finish_task(task_id, TaskStatus.FAILED, None, 'SERVICE_RESTARTED',
                             '服务重启中断了任务，可以手动重试')

    def schedule_runs(self, schedule_id, limit=20):
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM tasks WHERE json_extract(payload, '$.params._schedule_id') = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (schedule_id, limit),
            ).fetchall()
            return self._task_models(db, rows)

    def create_notification(self, user_id, task_id, task_type, kind, title, body=""):
        record = NotificationRecord(
            user_id=user_id, task_id=task_id, task_type=task_type,
            kind=kind, title=title, body=body,
        )
        with self._connection(write=True) as db:
            db.execute(
                "INSERT INTO notifications (id,user_id,task_id,task_type,kind,title,body,created_at,read_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (record.id, record.user_id, record.task_id, record.task_type.value,
                 record.kind, record.title, record.body,
                 record.created_at.isoformat(timespec='microseconds'), None),
            )
        return record

    def _notification_from_row(self, row):
        return NotificationRecord(
            id=row['id'], user_id=row['user_id'], task_id=row['task_id'],
            task_type=TaskType(row['task_type']), kind=row['kind'],
            title=row['title'], body=row['body'],
            created_at=datetime.fromisoformat(row['created_at']),
            read_at=datetime.fromisoformat(row['read_at']) if row['read_at'] else None,
        )

    def list_notifications(self, user_id, *, limit=20, unread_only=False):
        sql = "SELECT * FROM notifications WHERE user_id=?"
        params = [user_id]
        if unread_only:
            sql += " AND read_at IS NULL"
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connection() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._notification_from_row(row) for row in rows]

    def unread_notifications(self, user_id):
        with self._connection() as db:
            return db.execute(
                "SELECT count(*) FROM notifications WHERE user_id=? AND read_at IS NULL",
                (user_id,),
            ).fetchone()[0]

    def mark_notification_read(self, notification_id, user_id):
        with self._connection(write=True) as db:
            cursor = db.execute(
                "UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL",
                (utc_now().isoformat(timespec='microseconds'), notification_id, user_id),
            )
            if cursor.rowcount:
                return True
            existing = db.execute(
                "SELECT 1 FROM notifications WHERE id=? AND user_id=?", (notification_id, user_id)
            ).fetchone()
            return existing is not None

    def mark_all_notifications_read(self, user_id):
        with self._connection(write=True) as db:
            cursor = db.execute(
                "UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",
                (utc_now().isoformat(timespec='microseconds'), user_id),
            )
            return cursor.rowcount

    def get_schedule(self, schedule_id):
        with self._connection() as db:
            row = db.execute('SELECT payload FROM schedules WHERE id=?', (schedule_id,)).fetchone()
        return self._schedule_from_row(row) if row is not None else None

    def update_schedule(
        self, schedule_id, *, name, task_type, params, cron, next_run_at
    ):
        with self._connection(write=True) as db:
            row = db.execute('SELECT payload FROM schedules WHERE id=?', (schedule_id,)).fetchone()
            if row is None:
                return None
            record = self._schedule_from_row(row)
            record.name = name
            record.task_type = task_type
            record.params = params
            record.cron = cron
            record.next_run_at = next_run_at
            db.execute(
                'UPDATE schedules SET name=?, task_type=?, cron=?, next_run_at=?, payload=? WHERE id=?',
                (name, task_type.value, cron,
                 next_run_at.isoformat(timespec='microseconds'),
                 record.model_dump_json(), schedule_id),
            )
            return record

    # ---------- 知识库 ----------

    def create_knowledge_base(self, name, note, owner_id):
        record = KnowledgeBaseRecord(name=name, note=note, owner_id=owner_id)
        with self._connection(write=True) as db:
            db.execute(
                'INSERT INTO knowledge_bases (id, name, note, owner_id, created_at) VALUES (?,?,?,?,?)',
                (record.id, record.name, record.note, record.owner_id, record.created_at.isoformat()),
            )
        return record

    def list_knowledge_bases(self, owner_id):
        with self._connection() as db:
            rows = db.execute(
                'SELECT * FROM knowledge_bases WHERE owner_id = ? ORDER BY created_at DESC',
                (owner_id,),
            ).fetchall()
        return [self._kb_from_row(row) for row in rows]

    def get_knowledge_base(self, kb_id):
        with self._connection() as db:
            row = db.execute('SELECT * FROM knowledge_bases WHERE id = ?', (kb_id,)).fetchone()
        return self._kb_from_row(row) if row is not None else None

    def rename_knowledge_base(self, kb_id, owner_id, name):
        with self._connection(write=True) as db:
            cursor = db.execute(
                'UPDATE knowledge_bases SET name = ? WHERE id = ? AND owner_id = ?',
                (name, kb_id, owner_id),
            )
            return cursor.rowcount > 0

    def delete_knowledge_base(self, kb_id):
        with self._connection(write=True) as db:
            db.execute('DELETE FROM kb_documents WHERE kb_id = ?', (kb_id,))
            db.execute('DELETE FROM kb_messages WHERE kb_id = ?', (kb_id,))
            cursor = db.execute('DELETE FROM knowledge_bases WHERE id = ?', (kb_id,))
            return cursor.rowcount > 0

    @staticmethod
    def _kb_from_row(row):
        return KnowledgeBaseRecord(
            id=row['id'], name=row['name'], note=row['note'],
            owner_id=row['owner_id'], created_at=datetime.fromisoformat(row['created_at']),
        )

    @staticmethod
    def _kb_doc_from_row(row):
        return KbDocumentRecord(
            id=row['id'], kb_id=row['kb_id'], task_id=row['task_id'],
            title=row['title'], url=row['url'], content=row['content'],
            chunk_count=row['chunk_count'], created_at=datetime.fromisoformat(row['created_at']),
            artifact_name=row['artifact_name'], kind=row['kind'] or 'text',
            orphaned=bool(row['orphaned']),
        )

    def add_kb_document(self, kb_id, *, title, url, content, task_id=None, artifact_name=None, kind="text"):
        record = KbDocumentRecord(
            kb_id=kb_id, title=title, url=url, content=content, task_id=task_id,
            artifact_name=artifact_name, kind=kind,
        )
        with self._connection(write=True) as db:
            db.execute(
                'INSERT INTO kb_documents '
                '(id, kb_id, task_id, title, url, content, chunk_count, created_at, artifact_name, kind) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)',
                (
                    record.id, kb_id, task_id, title, url, content, 0,
                    record.created_at.isoformat(), artifact_name, kind,
                ),
            )
        return record

    def find_kb_document(self, kb_id, task_id, artifact_name):
        with self._connection() as db:
            row = db.execute(
                'SELECT * FROM kb_documents WHERE kb_id = ? AND task_id = ? AND artifact_name = ?',
                (kb_id, task_id, artifact_name),
            ).fetchone()
        return self._kb_doc_from_row(row) if row is not None else None

    def mark_kb_documents_orphaned(self, task_id):
        with self._connection(write=True) as db:
            cursor = db.execute(
                'UPDATE kb_documents SET orphaned = 1 WHERE task_id = ?', (task_id,)
            )
        return cursor.rowcount

    def list_kb_documents(self, kb_id):
        with self._connection() as db:
            rows = db.execute(
                'SELECT * FROM kb_documents WHERE kb_id = ? ORDER BY created_at DESC',
                (kb_id,),
            ).fetchall()
        return [self._kb_doc_from_row(row) for row in rows]

    def list_task_kb_documents(self, task_id, owner_id):
        with self._connection() as db:
            rows = db.execute(
                'SELECT d.kb_id, k.name, d.id FROM kb_documents d'
                ' JOIN knowledge_bases k ON k.id = d.kb_id'
                ' WHERE d.task_id = ? AND k.owner_id = ?'
                ' ORDER BY d.created_at DESC',
                (task_id, owner_id),
            ).fetchall()
        return [(row['kb_id'], row['name'], row['id']) for row in rows]

    def get_kb_document(self, document_id):
        with self._connection() as db:
            row = db.execute('SELECT * FROM kb_documents WHERE id = ?', (document_id,)).fetchone()
        return self._kb_doc_from_row(row) if row is not None else None

    def set_kb_chunk_count(self, document_id, count):
        with self._connection(write=True) as db:
            db.execute('UPDATE kb_documents SET chunk_count = ? WHERE id = ?', (count, document_id))

    def update_kb_document_title(self, document_id, title):
        with self._connection(write=True) as db:
            cursor = db.execute('UPDATE kb_documents SET title = ? WHERE id = ?', (title, document_id))
            return cursor.rowcount > 0

    def delete_kb_document(self, document_id):
        with self._connection(write=True) as db:
            cursor = db.execute('DELETE FROM kb_documents WHERE id = ?', (document_id,))
            return cursor.rowcount > 0

    def add_kb_message(self, kb_id, question, answer, citations, conversation_id="default"):
        record = KbMessageRecord(
            kb_id=kb_id,
            question=question,
            answer=answer,
            citations=list(citations),
            conversation_id=conversation_id or "default",
        )
        with self._connection(write=True) as db:
            db.execute(
                "INSERT INTO kb_messages (id, kb_id, question, answer, citations, conversation_id, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    record.id,
                    kb_id,
                    question,
                    answer,
                    json.dumps([c.model_dump() for c in citations], ensure_ascii=False),
                    record.conversation_id,
                    record.created_at.isoformat(),
                ),
            )
        return record

    def list_kb_conversations(self, kb_id, limit=50):
        messages = self.list_kb_messages(kb_id, limit=1000)
        groups = {}
        for msg in sorted(messages, key=lambda item: item.created_at):
            item = groups.setdefault(msg.conversation_id, {"id": msg.conversation_id, "title": msg.question, "count": 0, "updated_at": msg.created_at})
            item["count"] += 1
            item["updated_at"] = msg.created_at
        with self._connection() as db:
            titles = {
                row["id"]: (row["title"], row["updated_at"])
                for row in db.execute("SELECT id, title, updated_at FROM kb_conversations WHERE kb_id = ?", (kb_id,))
            }
        for cid, (title, updated) in titles.items():
            item = groups.setdefault(cid, {"id": cid, "title": title, "count": 0, "updated_at": updated})
            item["title"] = title
            if item["count"] == 0:
                item["updated_at"] = updated
        rows = sorted(groups.values(), key=lambda item: item["updated_at"], reverse=True)
        return rows[:limit]

    def list_kb_messages(self, kb_id, limit=50, conversation_id=None):
        sql = "SELECT * FROM kb_messages WHERE kb_id = ?"
        params: list = [kb_id]
        if conversation_id:
            sql += " AND conversation_id = ?"
            params.append(conversation_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connection() as db:
            rows = db.execute(sql, tuple(params)).fetchall()
        return [
            KbMessageRecord(
                id=row["id"],
                kb_id=row["kb_id"],
                question=row["question"],
                answer=row["answer"],
                citations=[KbCitation(**c) for c in json.loads(row["citations"])],
                conversation_id=(row["conversation_id"] if "conversation_id" in row.keys() else "default"),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    # ---------- 知识库切片与检索 ----------

    _fts_ready = False

    def _ensure_chunk_fts(self, db) -> bool:
        if SQLiteTaskStore._fts_ready:
            return True
        try:
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts USING fts5("
                "text, heading, content='kb_chunks', content_rowid='id', tokenize='unicode61')"
            )
            db.execute(
                "CREATE TRIGGER IF NOT EXISTS kb_chunks_ai AFTER INSERT ON kb_chunks BEGIN "
                "INSERT INTO kb_chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading); END"
            )
            db.execute(
                "CREATE TRIGGER IF NOT EXISTS kb_chunks_ad AFTER DELETE ON kb_chunks BEGIN "
                "INSERT INTO kb_chunks_fts(kb_chunks_fts, rowid, text, heading) VALUES('delete', old.id, old.text, old.heading); END"
            )
            SQLiteTaskStore._fts_ready = True
            return True
        except sqlite3.OperationalError:
            return False

    def replace_kb_chunks(self, kb_id: str, document_id: str, chunks: list[dict]) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connection(write=True) as db:
            self._ensure_chunk_fts(db)
            db.execute("DELETE FROM kb_chunks WHERE document_id = ?", (document_id,))
            rows = [
                (kb_id, document_id, index, item.get("heading", ""), item["text"], now)
                for index, item in enumerate(chunks)
            ]
            db.executemany(
                "INSERT INTO kb_chunks (kb_id, document_id, chunk_index, heading, text, created_at)"
                " VALUES (?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def delete_kb_chunks(self, document_id: str) -> None:
        with self._connection(write=True) as db:
            db.execute("DELETE FROM kb_chunks WHERE document_id = ?", (document_id,))

    def search_kb_chunks(
        self, kb_id: str, query: str, limit: int = 20,
        document_ids: list[str] | None = None, kinds: list[str] | None = None,
    ) -> list[dict]:
        tokens = [token for token in __import__("re").findall(r"\w+", query) if len(token) > 1][:8]
        if not tokens:
            return []
        filters = ["c.kb_id = ?"]
        params: list = [kb_id]
        if document_ids:
            filters.append("c.document_id IN (%s)" % ",".join("?" for _ in document_ids))
            params.extend(document_ids)
        if kinds:
            filters.append("d.kind IN (%s)" % ",".join("?" for _ in kinds))
            params.extend(kinds)
        where = " AND ".join(filters)
        with self._connection() as db:
            has_fts = self._ensure_chunk_fts(db)
            if has_fts:
                try:
                    match = " OR ".join(f'"{token}"' for token in tokens)
                    rows = db.execute(
                        "SELECT c.text, c.heading, c.document_id, d.title, d.url, bm25(kb_chunks_fts) AS rank "
                        "FROM kb_chunks_fts JOIN kb_chunks c ON c.id = kb_chunks_fts.rowid "
                        "JOIN kb_documents d ON d.id = c.document_id "
                        f"WHERE kb_chunks_fts MATCH ? AND {where} ORDER BY rank LIMIT ?",
                        (match, *params, limit),
                    ).fetchall()
                    if rows:
                        return [
                            {"text": row["text"], "heading": row["heading"], "document_id": row["document_id"],
                             "title": row["title"], "url": row["url"], "rank": 1.0 / (1.0 + abs(row["rank"]))}
                            for row in rows
                        ]
                except sqlite3.OperationalError:
                    pass
            rows = db.execute(
                "SELECT c.text, c.heading, c.document_id, d.title, d.url "
                "FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id "
                f"WHERE {where}",
                tuple(params),
            ).fetchall()
        hits = []
        for row in rows:
            haystack = f"{row['heading']} {row['text']}".lower()
            score = sum(haystack.count(token.lower()) for token in tokens)
            if score:
                hits.append({
                    "text": row["text"], "heading": row["heading"], "document_id": row["document_id"],
                    "title": row["title"], "url": row["url"], "rank": float(score),
                })
        hits.sort(key=lambda item: item["rank"], reverse=True)
        if not hits:
            for row in rows[:limit]:
                hits.append({
                    "text": row["text"], "heading": row["heading"],
                    "document_id": row["document_id"], "title": row["title"],
                    "url": row["url"], "rank": 0.1,
                })
        return hits[:limit]

    # ---------- 会话标题 ----------

    def upsert_kb_conversation(self, kb_id: str, owner_id: str, conversation_id: str, title: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connection(write=True) as db:
            db.execute(
                "INSERT INTO kb_conversations (id, kb_id, owner_id, title, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at",
                (conversation_id, kb_id, owner_id, title[:80] or "新对话", now, now),
            )

    def rename_kb_conversation(self, kb_id: str, owner_id: str, conversation_id: str, title: str) -> bool:
        with self._connection(write=True) as db:
            cursor = db.execute(
                "UPDATE kb_conversations SET title = ?, updated_at = ? WHERE id = ? AND kb_id = ? AND owner_id = ?",
                (title[:80] or "新对话", datetime.now(timezone.utc).isoformat(timespec="seconds"), conversation_id, kb_id, owner_id),
            )
            return cursor.rowcount > 0

    def delete_kb_conversation(self, kb_id: str, owner_id: str, conversation_id: str) -> bool:
        with self._connection(write=True) as db:
            owned = db.execute(
                "SELECT 1 FROM kb_conversations WHERE id = ? AND kb_id = ? AND owner_id = ?",
                (conversation_id, kb_id, owner_id),
            ).fetchone()
            if not owned:
                return False
            db.execute("DELETE FROM kb_messages WHERE kb_id = ? AND conversation_id = ?", (kb_id, conversation_id))
            db.execute("DELETE FROM kb_conversations WHERE id = ?", (conversation_id,))
            return True

    # ---------- 标签 ----------

    def create_tag(self, owner_id: str, name: str):
        from backend.models import TagRecord

        name = name.strip()[:24]
        if not name:
            raise ValueError("标签名不能为空")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connection(write=True) as db:
            row = db.execute("SELECT * FROM tags WHERE owner_id=? AND name=?", (owner_id, name)).fetchone()
            if row:
                return TagRecord(id=row["id"], owner_id=row["owner_id"], name=row["name"])
            record = TagRecord(owner_id=owner_id, name=name)
            db.execute(
                "INSERT INTO tags (id, owner_id, name, created_at) VALUES (?,?,?,?)",
                (record.id, owner_id, name, now),
            )
        return record

    def list_tags(self, owner_id: str):
        from backend.models import TagRecord

        with self._connection() as db:
            rows = db.execute("SELECT * FROM tags WHERE owner_id=? ORDER BY name", (owner_id,)).fetchall()
        return [TagRecord(id=row["id"], owner_id=row["owner_id"], name=row["name"]) for row in rows]

    def set_task_tags(self, task_id: str, owner_id: str, names):
        with self._connection(write=True) as db:
            db.execute("DELETE FROM task_tags WHERE task_id=?", (task_id,))
        cleaned = list(dict.fromkeys([n.strip()[:24] for n in names if n.strip()]))[:8]
        for name in cleaned:
            tag = self.create_tag(owner_id, name)
            with self._connection(write=True) as db:
                db.execute("INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?,?)", (task_id, tag.id))
        return self.get_task_tags(task_id)

    def get_task_tags(self, task_id: str):
        with self._connection() as db:
            rows = db.execute(
                "SELECT tg.name FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id WHERE tt.task_id=? ORDER BY tg.name",
                (task_id,),
            ).fetchall()
        return [row["name"] for row in rows]

    def set_document_tags(self, document_id: str, owner_id: str, names):
        cleaned = list(dict.fromkeys([n.strip()[:24] for n in names if n.strip()]))[:8]
        with self._connection(write=True) as db:
            db.execute("DELETE FROM kb_document_tags WHERE document_id=?", (document_id,))
        for name in cleaned:
            tag = self.create_tag(owner_id, name)
            with self._connection(write=True) as db:
                db.execute("INSERT OR IGNORE INTO kb_document_tags (document_id, tag_id) VALUES (?,?)",
                           (document_id, tag.id))
        return self.get_document_tags(document_id)

    def get_document_tags(self, document_id: str):
        with self._connection() as db:
            rows = db.execute(
                "SELECT tg.name FROM kb_document_tags dt JOIN tags tg ON tg.id=dt.tag_id "
                "WHERE dt.document_id=? ORDER BY tg.name",
                (document_id,),
            ).fetchall()
        return [row["name"] for row in rows]

    # ---------- 批注 ----------

    def add_annotation(self, owner_id: str, *, task_id=None, document_id=None, artifact_name="article.md",
                       block_index=0, start_offset=0, end_offset=0, quote="", note=""):
        from backend.models import AnnotationRecord

        record = AnnotationRecord(
            owner_id=owner_id, task_id=task_id, document_id=document_id, artifact_name=artifact_name,
            block_index=block_index, start_offset=start_offset, end_offset=end_offset, quote=quote, note=note,
        )
        with self._connection(write=True) as db:
            db.execute(
                "INSERT INTO annotations (id, owner_id, task_id, document_id, artifact_name, block_index,"
                " start_offset, end_offset, quote, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (record.id, owner_id, task_id, document_id, artifact_name, block_index, start_offset,
                 end_offset, quote, note, record.created_at.isoformat(timespec="seconds")),
            )
        return record

    def list_annotations(self, *, task_id=None, document_id=None, owner_id=None):
        from backend.models import AnnotationRecord

        clauses, params = [], []
        if task_id:
            clauses.append("task_id=?"); params.append(task_id)
        if document_id:
            clauses.append("document_id=?"); params.append(document_id)
        if owner_id:
            clauses.append("owner_id=?"); params.append(owner_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as db:
            rows = db.execute("SELECT * FROM annotations" + where + " ORDER BY created_at", params).fetchall()
        return [
            AnnotationRecord(
                id=row["id"], owner_id=row["owner_id"], task_id=row["task_id"], document_id=row["document_id"],
                artifact_name=row["artifact_name"], block_index=row["block_index"],
                start_offset=row["start_offset"], end_offset=row["end_offset"],
                quote=row["quote"], note=row["note"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def update_annotation(self, annotation_id: str, owner_id: str, note: str):
        with self._connection(write=True) as db:
            cursor = db.execute("UPDATE annotations SET note=? WHERE id=? AND owner_id=?",
                                (note, annotation_id, owner_id))
            if not cursor.rowcount:
                return None
        rows = self.list_annotations(owner_id=owner_id)
        return next((item for item in rows if item.id == annotation_id), None)

    def delete_annotation(self, annotation_id: str, owner_id: str) -> bool:
        with self._connection(write=True) as db:
            return db.execute("DELETE FROM annotations WHERE id=? AND owner_id=?",
                              (annotation_id, owner_id)).rowcount > 0
