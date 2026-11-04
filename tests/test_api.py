"""文件职责：验证 API 契约、后台调度、产物下载和前端静态页面是否可用。

测试使用 FakeSandbox 和临时产物目录，不依赖真实 Docker，可在普通开发环境中快速回归。
真实沙箱能力由 tests/test_sandbox_integration.py 单独验证。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.storage import ArtifactStore


class FakeSandbox:
    """最小沙箱替身，只返回调度器需要的成功结构。"""

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
        # 每个测试使用独立临时目录，避免产物或任务状态互相影响。
        self.temp_dir = tempfile.TemporaryDirectory()
        artifact_store = ArtifactStore(Path(self.temp_dir.name))
        self.client_context = TestClient(create_app(artifact_store=artifact_store, adapter=FakeSandbox()))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_health_and_mock_task_end_to_end(self) -> None:
        """主链路：健康检查 -> 创建任务 -> 检查状态/步骤/产物 -> 下载。"""
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

    def test_frontend_is_served_from_new_framework(self) -> None:
        """确认根路径跳转和前台静态文件都来自新 backend/frontend 框架。"""
        root = self.client.get("/", follow_redirects=False)
        self.assertEqual(root.status_code, 307)
        self.assertEqual(root.headers["location"], "/ui/task_list.html")

        page = self.client.get("/ui/task_list.html")
        self.assertEqual(page.status_code, 200)
        self.assertIn("任务工作台", page.text)

    def test_unregistered_template_is_rejected(self) -> None:
        """正式模板未注册时必须明确拒绝，不能创建伪成功任务。"""
        response = self.client.post("/tasks", json={"task_type": "WEB_DIGEST", "params": {"url": "https://example.com"}})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "TEMPLATE_NOT_AVAILABLE")

    def test_csv_route_placeholder_is_available(self) -> None:
        """CSV 占位路由应返回可识别的 501，而不是框架级 404。"""
        response = self.client.post("/tasks/csv")
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["detail"]["code"], "CSV_ROUTE_NOT_IMPLEMENTED")

    def test_missing_task_returns_404(self) -> None:
        """不存在的任务应返回稳定错误码，便于前端显示。"""
        response = self.client.get("/tasks/not-found")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "TASK_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
