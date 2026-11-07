"""文件职责：验证 API 契约、后台调度、产物下载和前端静态页面是否可用。

测试使用 FakeSandbox 和临时产物目录，不依赖真实 Docker，可在普通开发环境中快速回归。
真实沙箱能力由 tests/test_sandbox_integration.py 单独验证。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.storage import ArtifactStore

try:
    from tests.support import make_temp_dir, remove_temp_dir
except ImportError:  # 兼容以 tests 目录为顶层的 unittest discover
    from support import make_temp_dir, remove_temp_dir


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
        self.temp_dir = make_temp_dir()
        artifact_store = ArtifactStore(self.temp_dir)
        self.client_context = TestClient(create_app(artifact_store=artifact_store, adapter=FakeSandbox()))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        remove_temp_dir(self.temp_dir)

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
        self.assertEqual(detail["steps"][0]["name"], "准备任务目录")
        self.assertEqual(detail["result"]["summary"]["kind"], "mock")
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

    def test_task_type_fields_have_hidden_style_guard(self) -> None:
        """防止组件的 display 样式覆盖 hidden 属性，确保字段切换始终生效。"""
        stylesheet = self.client.get("/ui/styles.css")
        script = self.client.get("/ui/create_task.js")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("[hidden]", stylesheet.text)
        self.assertIn("display: none !important", stylesheet.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn("input.disabled = !visible", script.text)
        task_script = self.client.get("/ui/task_list.js")
        self.assertEqual(task_script.status_code, 200)
        self.assertIn("renderResultSummary", task_script.text)

    def test_invalid_task_type_is_rejected(self) -> None:
        """未知任务类型应在 Pydantic 校验阶段被拒绝。"""
        response = self.client.post("/tasks", json={"task_type": "UNKNOWN", "params": {}})
        self.assertEqual(response.status_code, 422)

    def test_csv_upload_requires_file(self) -> None:
        """CSV 上传接口缺少文件时必须返回 422。"""
        response = self.client.post("/tasks/csv")
        self.assertEqual(response.status_code, 422)

    def test_missing_task_returns_404(self) -> None:
        """不存在的任务应返回稳定错误码，便于前端显示。"""
        response = self.client.get("/tasks/not-found")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "TASK_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
