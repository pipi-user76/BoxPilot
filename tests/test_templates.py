"""文件职责：验证仓库体检和网页验收模板的状态语义、报告与产物。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.storage import ArtifactStore

try:
    from tests.support import make_temp_dir, remove_temp_dir
except ImportError:  # 兼容以 tests 目录为顶层的 unittest discover
    from support import make_temp_dir, remove_temp_dir


class TemplateSandbox:
    """供模板测试使用的可配置沙箱替身。"""

    def __init__(
        self,
        title: str = "BoxPilot 任务工作台",
        page_text: str = "BoxPilot page content",
        test_exit_code: int = 0,
        navigate_ok: bool = True,
    ):
        self.title = title
        self.page_text = page_text
        self.test_exit_code = test_exit_code
        self.navigate_ok = navigate_ok
        self.demo_prepared = False
        self.last_full_page = False

    def ensure_task_workspace(self, task_id: str) -> dict:
        return {"success": True, "data": {"path": f"/home/gem/boxpilot/{task_id}"}, "error": None}

    def task_workdir(self, task_id: str) -> str:
        return f"/home/gem/boxpilot/{task_id}"

    def exec_shell(self, command: str, task_id: str, timeout_s: int = 120, check_exit: bool = True) -> dict:
        if command.startswith("git clone"):
            output, exit_code = "cloned", 0
        elif command.startswith("if test"):
            output, exit_code = ("python-unittest" if self.demo_prepared else "python"), 0
        elif "pytest" in command:
            output, exit_code = "2 passed", self.test_exit_code
        else:
            output, exit_code = "ok", 0
        return {"success": True, "data": {"output": output, "exit_code": exit_code}, "error": None}

    def upload_file(self, task_id: str, local_path: Path, relative_path: str) -> dict:
        self.demo_prepared = True
        return {"success": True, "data": {"path": f"/home/gem/boxpilot/{task_id}/{relative_path}"}, "error": None}

    def navigate(self, url: str, timeout_s: int | None = None) -> dict:
        if not self.navigate_ok:
            return {"success": False, "data": None, "error": {"code": "COMMAND_FAILED", "message": "navigation failed"}}
        return {
            "success": True,
            "data": {"url": url, "title": self.title, "status": 200, "load_time_ms": 12.5},
            "error": None,
        }

    def take_screenshot(self, path: Path, full_page: bool = False) -> dict:
        self.last_full_page = full_page
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return {"success": True, "data": {"path": str(path), "bytes": path.stat().st_size}, "error": None}

    def get_page_text(self) -> dict:
        return {"success": True, "data": {"text": self.page_text}, "error": None}

    def get_page_markdown(self) -> dict:
        return {
            "success": True,
            "data": {"title": self.title, "markdown": self.page_text, "extraction": "readability"},
            "error": None,
        }

    def health(self) -> dict:
        return {"success": True, "data": {"base_url": "fake"}, "error": None}


class TemplateTests(unittest.TestCase):
    def create_client(self, sandbox: TemplateSandbox):
        """为每个用例创建独立临时产物目录和 TestClient。"""
        self.temp_dir = make_temp_dir()
        self.addCleanup(remove_temp_dir, self.temp_dir)
        artifact_store = ArtifactStore(self.temp_dir)
        context = TestClient(create_app(artifact_store=artifact_store, adapter=sandbox))
        self.addCleanup(context.__exit__, None, None, None)
        return context.__enter__()

    def artifact_json(self, client: TestClient, task: dict, filename: str) -> dict:
        """下载指定 JSON 产物并解析，供测试检查内部结论。"""
        artifact = next(item for item in task["artifacts"] if item["filename"] == filename)
        response = client.get(f"/artifacts/{artifact['id']}/download")
        self.assertEqual(response.status_code, 200)
        return json.loads(response.text)

    def test_repo_check_passing_report(self) -> None:
        """仓库测试通过时，任务成功且报告 test_status=passed。"""
        client = self.create_client(TemplateSandbox())
        created = client.post(
            "/tasks",
            json={"task_type": "REPO_CHECK", "params": {"repo_url": "https://github.com/example/demo"}},
        )
        self.assertEqual(created.status_code, 202)
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual(len(task["artifacts"]), 2)
        report = self.artifact_json(client, task, "check-report.json")
        self.assertEqual(report["test_status"], "passed")
        self.assertEqual(report["toolchain"], "python")
        self.assertEqual(task["result"]["summary"]["message"], "仓库测试通过")

    def test_repo_check_failed_tests_are_valid_result(self) -> None:
        """测试命令非零退出属于有效检查结果，任务仍为成功。"""
        client = self.create_client(TemplateSandbox(test_exit_code=1))
        created = client.post(
            "/tasks",
            json={"task_type": "REPO_CHECK", "params": {"repo_url": "https://github.com/example/demo"}},
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        report = self.artifact_json(client, task, "check-report.json")
        self.assertEqual(report["test_status"], "failed")
        self.assertEqual(report["exit_code"], 1)

    def test_repo_check_rejects_untrusted_host(self) -> None:
        """非白名单仓库在第一步失败，不能进入克隆和测试流程。"""
        client = self.create_client(TemplateSandbox())
        created = client.post(
            "/tasks",
            json={"task_type": "REPO_CHECK", "params": {"repo_url": "https://example.invalid/repo"}},
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(task["error_code"], "VALIDATION_ERROR")

    def test_repo_check_offline_demo_repository(self) -> None:
        """特殊 demo://python 输入应使用内置样例和 unittest。"""
        client = self.create_client(TemplateSandbox())
        created = client.post(
            "/tasks",
            json={"task_type": "REPO_CHECK", "params": {"repo_url": "demo://python"}},
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        report = self.artifact_json(client, task, "check-report.json")
        self.assertEqual(report["toolchain"], "python-unittest")
        self.assertEqual(report["test_status"], "passed")

    def test_web_acceptance_matching_title(self) -> None:
        """标题匹配时生成 passed=true 的验收报告。"""
        client = self.create_client(TemplateSandbox(title="BoxPilot 任务工作台"))
        created = client.post(
            "/tasks",
            json={
                "task_type": "WEB_ACCEPTANCE",
                "params": {"url": "https://example.com", "expected_title": "boxpilot 任务工作台"},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        report = self.artifact_json(client, task, "acceptance.json")
        self.assertTrue(report["passed"])
        self.assertTrue(task["result"]["summary"]["passed"])
        self.assertEqual(task["result"]["summary"]["message"], "标题匹配")
        self.assertEqual(task["steps"][0]["name"], "校验验收参数")

    def test_web_acceptance_title_mismatch_still_produces_report(self) -> None:
        """标题不匹配时任务成功，但报告明确记录 passed=false。"""
        client = self.create_client(TemplateSandbox(title="实际标题"))
        created = client.post(
            "/tasks",
            json={
                "task_type": "WEB_ACCEPTANCE",
                "params": {"url": "https://example.com", "expected_title": "预期标题"},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        report = self.artifact_json(client, task, "acceptance.json")
        self.assertFalse(report["passed"])
        self.assertFalse(task["result"]["summary"]["passed"])
        self.assertEqual(task["result"]["summary"]["message"], "标题不匹配")
        self.assertEqual(task["result"]["summary"]["actual_title"], "实际标题")

    def test_web_acceptance_navigation_failure_marks_task_failed(self) -> None:
        """页面无法打开时任务失败，且不应伪造截图或报告产物。"""
        client = self.create_client(TemplateSandbox(navigate_ok=False))
        created = client.post(
            "/tasks",
            json={
                "task_type": "WEB_ACCEPTANCE",
                "params": {"url": "https://example.com", "expected_title": "Example Domain"},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(task["error_code"], "COMMAND_FAILED")
        self.assertEqual(task["artifacts"], [])

    def test_web_digest_produces_text_screenshot_and_report(self) -> None:
        """整理任务应产出正文、纯文本、整页截图和报告四件产物。"""
        sandbox = TemplateSandbox(page_text="页面正文内容")
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={"task_type": "WEB_DIGEST", "params": {"url": "https://example.com"}},
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual(
            [artifact["filename"] for artifact in task["artifacts"]],
            ["article.md", "page.txt", "screenshot.png", "web-digest.json"],
        )
        page_artifact = next(item for item in task["artifacts"] if item["filename"] == "page.txt")
        page = client.get(f"/artifacts/{page_artifact['id']}/download")
        self.assertIn("页面正文内容", page.text)
        summary = task["result"]["summary"]
        self.assertEqual(summary["kind"], "web_digest")
        self.assertEqual(summary["text_length"], len("页面正文内容"))
        self.assertEqual(summary["extraction"], "readability")
        self.assertTrue(summary["full_page"])
        self.assertTrue(sandbox.last_full_page)

    def test_web_digest_falls_back_to_visible_text(self) -> None:
        """Readability 提不到正文时退回可见文本，产物仍然完整。"""
        sandbox = TemplateSandbox(page_text="仅可见文本")
        sandbox.get_page_markdown = lambda: {
            "success": True,
            "data": {"title": sandbox.title, "markdown": ""},
            "error": None,
        }
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={"task_type": "WEB_DIGEST", "params": {"url": "https://example.com"}},
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual(task["result"]["summary"]["extraction"], "text")

    def test_web_digest_empty_text_marks_task_failed(self) -> None:
        client = self.create_client(TemplateSandbox(page_text=""))
        created = client.post(
            "/tasks",
            json={"task_type": "WEB_DIGEST", "params": {"url": "https://example.com"}},
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(task["error_code"], "ARTIFACT_EMPTY")


if __name__ == "__main__":
    unittest.main()
