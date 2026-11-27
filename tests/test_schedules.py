"""文件职责：定时任务表、触发推进与 HTTP 接口。"""

from __future__ import annotations

import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.db import InMemoryTaskStore
from backend.main import create_app
from backend.models import TaskType
from backend.schedules import ScheduleRunner, next_run_for
from backend.storage import ArtifactStore

try:
    from tests.support import make_temp_dir, remove_temp_dir
except ImportError:
    from support import make_temp_dir, remove_temp_dir


class FakeSandbox:
    def health(self) -> dict:
        return {"success": True, "data": {"base_url": "fake"}, "error": None}

    def ensure_task_workspace(self, task_id: str) -> dict:
        return {"success": True, "data": {"path": task_id}, "error": None}

    def task_workdir(self, task_id: str) -> str:
        return task_id

    def exec_shell(self, command: str, task_id: str, timeout_s: int = 120) -> dict:
        return {"success": True, "data": {"output": "ok", "exit_code": 0}, "error": None}


class RecordingRunner:
    def __init__(self) -> None:
        self.ran: list[str] = []
        self.done = threading.Event()

    def run(self, task_id: str) -> None:
        self.ran.append(task_id)
        self.done.set()


class ScheduleRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryTaskStore()
        self.runner = RecordingRunner()
        self.now = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)

    def test_due_schedule_creates_task_and_advances(self) -> None:
        record = self.store.create_schedule(
            TaskType.MOCK, {"tag": "nightly"}, "*/5 * * * *",
            self.now - timedelta(minutes=1), owner_id="u1", name="夜间巡检",
        )
        runner = ScheduleRunner(self.store, self.runner, poll_seconds=999, clock=lambda: self.now)
        fired = runner.tick(self.now)
        self.assertEqual([item.id for item in fired], [record.id])
        self.assertTrue(self.runner.done.wait(2))
        tasks, total = self.store.list_tasks(owner_id="u1")
        self.assertEqual(total, 1)
        self.assertEqual(tasks[0].params["_schedule_id"], record.id)
        self.assertEqual(tasks[0].owner_id, "u1")
        updated = self.store.list_schedules()[0]
        self.assertEqual(updated.next_run_at, datetime(2026, 11, 27, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(updated.last_task_id, tasks[0].id)
        runner.stop()

    def test_disabled_schedule_is_not_fired(self) -> None:
        record = self.store.create_schedule(
            TaskType.MOCK, {}, "*/5 * * * *", self.now - timedelta(minutes=1)
        )
        self.store.set_schedule_enabled(record.id, False)
        runner = ScheduleRunner(self.store, self.runner, poll_seconds=999)
        self.assertEqual(runner.tick(self.now), [])
        runner.stop()

    def test_next_run_for_rejects_bad_cron(self) -> None:
        with self.assertRaises(ValueError):
            next_run_for("* * *", self.now)


class ScheduleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.context = TestClient(
            create_app(
                database_path=self.temp_dir / "tasks.db",
                artifact_store=ArtifactStore(self.temp_dir),
                adapter=FakeSandbox(),
                auth_required=False,
            )
        )
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        remove_temp_dir(self.temp_dir)

    def test_create_list_toggle_delete(self) -> None:
        created = self.client.post(
            "/schedules",
            json={"task_type": "MOCK", "params": {"tag": "nightly"}, "cron": "*/10 * * * *", "name": "夜间"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        record = created.json()
        self.assertTrue(record["enabled"])

        listed = self.client.get("/schedules")
        self.assertEqual([item["id"] for item in listed.json()], [record["id"]])

        paused = self.client.patch(f"/schedules/{record['id']}", json={"enabled": False})
        self.assertEqual(paused.status_code, 200)
        self.assertFalse(paused.json()["enabled"])

        deleted = self.client.delete(f"/schedules/{record['id']}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get("/schedules").json(), [])

    def test_invalid_cron_returns_422(self) -> None:
        response = self.client.post(
            "/schedules",
            json={"task_type": "MOCK", "params": {}, "cron": "* * *"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_CRON")


if __name__ == "__main__":
    unittest.main()
