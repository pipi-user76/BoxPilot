from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
import sqlite3
import unittest

from backend.models import Artifact, StepStatus, TaskStatus, TaskType, utc_now
from backend.sqlite_store import SQLiteTaskStore
from tests.support import workspace_temp_dir


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(workspace_temp_dir())
        self.path = folder / "tasks.db"
        self.store = SQLiteTaskStore(self.path)
        self.store.init_db()

    def test_persists_records_and_cascades_delete(self):
        task = self.store.create_task(TaskType.CSV_ANALYSIS, {"csv_filename": "input.csv"})
        self.store.start_task(task.id)
        step = self.store.add_step(task.id, 1, "分析 CSV")
        self.store.finish_step(step.id, StepStatus.SUCCESS, "已完成", None, None)
        artifact = self.store.add_artifact(
            Artifact(
                task_id=task.id,
                filename="summary.json",
                relative_path="summary.json",
                content_type="application/json",
                size_bytes=20,
            )
        )
        self.store.finish_task(task.id, TaskStatus.SUCCESS, {"rows": 3}, None, None)

        reopened = SQLiteTaskStore(self.path)
        reopened.init_db()
        saved = reopened.get_task(task.id)
        self.assertEqual(saved.result, {"rows": 3})
        self.assertEqual(saved.steps[0].output, "已完成")
        self.assertEqual(reopened.get_artifact(artifact.id), (artifact, task.id))
        self.assertEqual(reopened.list_tasks(query="分析 csv")[1], 1)
        self.assertEqual(reopened.count_tasks()["SUCCESS"], 1)

        self.assertTrue(reopened.delete_task(task.id))
        self.assertIsNone(reopened.get_artifact(artifact.id))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM steps").fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_restart_closes_interrupted_tasks_and_steps(self):
        waiting = self.store.create_task(TaskType.MOCK, {})
        running = self.store.create_task(TaskType.MOCK, {})
        self.store.start_task(running.id)
        self.store.add_step(running.id, 1, "处理中")

        reopened = SQLiteTaskStore(self.path)
        reopened.init_db()
        reopened.recover_interrupted()
        for original in (waiting, running):
            record = reopened.get_task(original.id)
            self.assertEqual(record.status, TaskStatus.FAILED)
            self.assertEqual(record.error_code, "SERVICE_RESTARTED")
        self.assertEqual(reopened.get_task(running.id).steps[0].status, StepStatus.FAILED)

    def test_concurrent_duplicate_submissions_create_one_task(self):
        cutoff = utc_now() - timedelta(minutes=5)

        def submit(_):
            return self.store.create_task(
                TaskType.WEB_DIGEST, {}, dedupe_key="same", dedupe_since=cutoff
            )

        with ThreadPoolExecutor(max_workers=8) as workers:
            responses = list(workers.map(submit, range(16)))
        self.assertEqual(len({r.id for r in responses}), 1)
        self.assertEqual(sum(not r.deduplicated for r in responses), 1)
        self.assertEqual(self.store.count_tasks()["total"], 1)

    def test_upgrades_v2_database_and_keeps_existing_tasks(self):
        task = self.store.create_task(TaskType.MOCK, {"tag": "legacy"})
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TABLE schedules")
            db.execute("DROP TABLE notifications")
            db.execute("DROP TABLE kb_messages")
            db.execute("DROP TABLE kb_documents")
            db.execute("DROP TABLE knowledge_bases")
            db.execute("PRAGMA user_version = 2")
            db.commit()
        upgraded = SQLiteTaskStore(self.path)
        upgraded.init_db()
        with closing(sqlite3.connect(self.path)) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(version, 10)
        self.assertIn("schedules", tables)
        self.assertIn("notifications", tables)
        self.assertIn("knowledge_bases", tables)
        self.assertEqual(upgraded.get_task(task.id).params["tag"], "legacy")
