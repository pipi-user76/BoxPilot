from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.artifact_store import ArtifactStore


class FakeSandbox:
    def ensure_task_workspace(self, task_id: str) -> dict:
        return {"success": True, "data": {"path": f"/home/gem/boxpilot/{task_id}"}, "error": None}

    def task_workdir(self, task_id: str) -> str:
        return f"/home/gem/boxpilot/{task_id}"

    def exec_shell(self, command: str, task_id: str, timeout_s: int = 120) -> dict:
        return {"success": True, "data": {"output": "boxpilot mock ok", "exit_code": 0}, "error": None}

    def health(self) -> dict:
        return {"success": True, "data": {"base_url": "fake"}, "error": None}


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        artifact_store = ArtifactStore(Path(self.temp_dir.name))
        self.client_context = TestClient(create_app(artifact_store=artifact_store, adapter=FakeSandbox()))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_health_and_mock_task_end_to_end(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")

        created = self.client.post("/tasks", json={"task_type": "MOCK", "params": {}})
        self.assertEqual(created.status_code, 202)
        task_id = created.json()["id"]

        detail = self.client.get(f"/tasks/{task_id}").json()
        self.assertEqual(detail["status"], "SUCCESS")
        self.assertEqual([step["status"] for step in detail["steps"]], ["SUCCESS", "SUCCESS", "SUCCESS"])
        self.assertEqual(len(detail["artifacts"]), 1)

        artifact_id = detail["artifacts"][0]["id"]
        downloaded = self.client.get(f"/artifacts/{artifact_id}/download")
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn("BoxPilot scheduler smoke test", downloaded.text)

    def test_unregistered_template_is_rejected(self) -> None:
        response = self.client.post("/tasks", json={"task_type": "WEB_DIGEST", "params": {"url": "https://example.com"}})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "TEMPLATE_NOT_AVAILABLE")

    def test_missing_task_returns_404(self) -> None:
        response = self.client.get("/tasks/not-found")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "TASK_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
