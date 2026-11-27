"""SQLite implementation of the shared TaskStore contract."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from backend.models import Artifact, StepStatus, TaskRecord, TaskStatus, TaskStep, utc_now

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

SCHEMA_VERSION = 2


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
                db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
                return
            if version != 0 or db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone():
                raise RuntimeError(f'无法使用此数据库结构：{self.path}。请配置新的 DATABASE_PATH。')
            for statement in SCHEMA:
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

    def list_tasks(self, *, query='', statuses=None, task_type=None, page=1, page_size=20,
                   owner_id=None):
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

    def count_tasks(self, owner_id=None, task_type=None):
        counts = {status.value: 0 for status in TaskStatus}
        clauses, params = [], []
        if owner_id is not None:
            clauses.append('owner_id=?')
            params.append(owner_id)
        if task_type is not None:
            clauses.append('task_type=?')
            params.append(task_type)
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

    def get_artifact(self, artifact_id):
        with self._connection() as db:
            row = db.execute('SELECT * FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
            if row:
                return Artifact.model_validate_json(row['payload']), row['task_id']
        return None

    def recover_interrupted(self):
        """Called at single-worker startup; unfinished operations can be retried manually."""
        with self._connection() as db:
            ids = [row['id'] for row in db.execute(
                "SELECT id FROM tasks WHERE status IN ('PENDING','RUNNING')"
            )]
        for task_id in ids:
            self.finish_task(task_id, TaskStatus.FAILED, None, 'SERVICE_RESTARTED',
                             '服务重启中断了任务，可以手动重试')
