"""站内通知、定时任务手动触发与 APScheduler 持久化。"""

from __future__ import annotations

import time
import unittest

from fastapi.testclient import TestClient

from backend.main import create_app
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


def build_app(temp_dir):
    return TestClient(
        create_app(
            database_path=temp_dir / "tasks.db",
            artifact_store=ArtifactStore(temp_dir),
            adapter=FakeSandbox(),
            auth_required=False,
        )
    )


class NotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.context = build_app(self.temp_dir)
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        remove_temp_dir(self.temp_dir)

    def test_task_completion_creates_notification(self) -> None:
        created = self.client.post("/tasks", json={"task_type": "MOCK", "params": {}})
        self.assertEqual(created.status_code, 202, created.text)
        task_id = created.json()["id"]

        listing = self.client.get("/notifications")
        self.assertEqual(listing.status_code, 200)
        payload = listing.json()
        self.assertEqual(payload["unread"], 1)
        self.assertEqual(payload["items"][0]["task_id"], task_id)
        self.assertEqual(payload["items"][0]["kind"], "success")

        first_id = payload["items"][0]["id"]
        marked = self.client.post(f"/notifications/{first_id}/read")
        self.assertEqual(marked.status_code, 204)
        self.assertEqual(self.client.get("/notifications/unread").json()["unread"], 0)

    def test_mark_all_read(self) -> None:
        self.client.post("/tasks", json={"task_type": "MOCK", "params": {"tag": "a"}})
        self.client.post("/tasks", json={"task_type": "MOCK", "params": {"tag": "b"}})
        self.assertEqual(self.client.get("/notifications/unread").json()["unread"], 2)
        result = self.client.post("/notifications/read-all")
        self.assertEqual(result.json()["updated"], 2)
        self.assertEqual(self.client.get("/notifications/unread").json()["unread"], 0)


class ScheduleExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.context = build_app(self.temp_dir)
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        remove_temp_dir(self.temp_dir)

    def test_run_now_and_history(self) -> None:
        created = self.client.post(
            "/schedules",
            json={"task_type": "MOCK", "params": {}, "cron": "*/10 * * * *", "name": "立即执行"},
        )
        schedule_id = created.json()["id"]

        fired = self.client.post(f"/schedules/{schedule_id}/run")
        self.assertEqual(fired.status_code, 202, fired.text)
        task_id = fired.json()["id"]

        runs = self.client.get(f"/schedules/{schedule_id}/runs")
        self.assertEqual(runs.status_code, 200)
        self.assertEqual([item["id"] for item in runs.json()], [task_id])

        detail = self.client.get(f"/tasks/{task_id}")
        for _ in range(40):
            if detail.json()["status"] not in ("PENDING", "RUNNING"):
                break
            time.sleep(0.05)
            detail = self.client.get(f"/tasks/{task_id}")
        self.assertEqual(detail.json()["status"], "SUCCESS")
        self.assertEqual(detail.json()["params"]["_schedule_id"], schedule_id)

    def test_schedule_survives_restart(self) -> None:
        created = self.client.post(
            "/schedules",
            json={"task_type": "MOCK", "params": {}, "cron": "0 8 * * *", "name": "每日"},
        )
        schedule_id = created.json()["id"]
        self.context.__exit__(None, None, None)

        with build_app(self.temp_dir) as client:
            listed = client.get("/schedules").json()
            self.assertEqual([item["id"] for item in listed], [schedule_id])
            self.assertTrue(listed[0]["enabled"])


if __name__ == "__main__":
    unittest.main()
