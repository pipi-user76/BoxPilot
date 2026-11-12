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
        clone_failures: int = 0,
        clone_error: str = "fatal: unable to access 'https://github.com/example/demo/': Failed to connect to github.com port 443: Connection refused",
        pytest_available: bool = True,
        unittest_tests: int = 0,
        missing_dependency: bool = False,
        install_exit_code: int = 0,
        requires_python: str = "",
        python_available: tuple[str, ...] = ("3.13", "3.12", "3.10"),
        python_install_ok: bool = True,
        python_install_version: str = "3.13",
        install_status: str = "ok",
        declared_dependencies: tuple[str, ...] = ("fastapi>=0.1",),
    ):
        self.title = title
        self.page_text = page_text
        self.test_exit_code = test_exit_code
        self.navigate_ok = navigate_ok
        self.demo_prepared = False
        self.last_full_page = False
        self.clone_failures = clone_failures
        self.clone_error = clone_error
        self.clone_calls = 0
        self.pytest_available = pytest_available
        self.unittest_tests = unittest_tests
        self.missing_dependency = missing_dependency
        self.install_exit_code = install_exit_code
        self.requires_python = requires_python
        self.python_available = python_available
        self.python_install_ok = python_install_ok
        self.python_install_version = python_install_version
        self.python_installs = 0
        self.install_status = install_status
        self.declared_dependencies = declared_dependencies
        self.install_calls = 0
        self.install_result = ""
        self.install_log = ""
        self.commands: list[str] = []

    def ensure_task_workspace(self, task_id: str) -> dict:
        return {"success": True, "data": {"path": f"/home/gem/boxpilot/{task_id}"}, "error": None}

    def task_workdir(self, task_id: str) -> str:
        return f"/home/gem/boxpilot/{task_id}"

    def write_text_file(self, task_id: str, relative_path: str, content: str) -> dict:
        return {"success": True, "data": {"path": f"{self.task_workdir(task_id)}/{relative_path}"}, "error": None}

    def exec_shell(self, command: str, task_id: str, timeout_s: int = 120, check_exit: bool = True) -> dict:
        self.commands.append(command)
        if command.startswith("GIT_TERMINAL_PROMPT") or command.startswith("git clone"):
            self.clone_calls += 1
            if self.clone_calls <= self.clone_failures:
                return {
                    "success": False,
                    "data": None,
                    "error": {"code": "COMMAND_FAILED", "message": self.clone_error},
                }
            output, exit_code = "cloned", 0
        elif command.startswith("if test"):
            output, exit_code = ("python-unittest" if self.demo_prepared else "python"), 0
        elif "uv python install" in command:
            self.python_installs += 1
            if self.python_install_ok:
                if self.python_install_version not in self.python_available:
                    self.python_available = (self.python_install_version,) + self.python_available
                output, exit_code = f"Installed Python {self.python_install_version}.0", 0
            else:
                output, exit_code = "error: failed to download interpreter", 1
        elif "uv python find" in command:
            output, exit_code = "\n".join(self.python_available), 0
        elif "read_repo.py" in command:
            output, exit_code = (
                json.dumps(
                    {
                        "requires_python": self.requires_python,
                        "source": "pyproject.toml",
                        "dependencies": list(self.declared_dependencies),
                    }
                ),
                0,
            )
        elif "sandbox_install.sh" in command:
            self.install_calls += 1
            codes = {"ok": 0, "no_deps": 10, "version": 11, "network": 13, "failed": 15}
            exit_code = codes.get(self.install_status, self.install_exit_code)
            if self.install_status == "ok":
                spec = command.split("sandbox_install.sh", 1)[1].strip().split()[0]
                self.install_result = '{"status":"ok","python":"%s.11"}' % spec
                self.install_log = "installed fastapi"
            elif self.install_status == "no_deps":
                self.install_result = '{"status":"no_deps"}'
                self.install_log = ""
            else:
                self.install_result = (
                    '{"status":"error","code":%d,"message":"simulated install failure"}'
                    % exit_code
                )
                self.install_log = "ERROR: simulated install failure"
            output = self.install_log
        elif "import pytest" in command:
            output, exit_code = ("0" if self.pytest_available else "1"), 0
        elif "unittest discover" in command:
            if self.missing_dependency:
                output, exit_code = "ModuleNotFoundError: No module named 'fastapi'", 1
            elif self.unittest_tests > 0:
                output, exit_code = f"Ran {self.unittest_tests} tests in 0.01s\n\nOK", 0
            else:
                output, exit_code = "Ran 0 tests in 0.000s\n\nOK", 0
        elif "python --version" in command:
            output, exit_code = "Python 3.12.11", 0
        elif "pytest" in command:
            output, exit_code = "2 passed", self.test_exit_code
        else:
            output, exit_code = "ok", 0
        return {"success": True, "data": {"output": output, "exit_code": exit_code}, "error": None}

    def read_text_file(self, task_id: str, relative_path: str) -> dict:
        if relative_path == "install-result.json" and self.install_result:
            return {"success": True, "data": {"content": self.install_result}, "error": None}
        if relative_path == "install.log" and self.install_log:
            return {"success": True, "data": {"content": self.install_log}, "error": None}
        return {"success": False, "data": None, "error": {"code": "FILE_NOT_FOUND", "message": relative_path}}

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
            json={
                "task_type": "REPO_CHECK",
                "params": {"repo_url": "https://github.com/example/demo", "install_deps": False},
            },
        )
        self.assertEqual(created.status_code, 202)
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        # 报告分 Markdown 与 JSON 两份，加 test.log；克隆一次成功、未开安装，不再额外产生日志。
        self.assertEqual(len(task["artifacts"]), 3)
        report = self.artifact_json(client, task, "check-report.json")
        self.assertEqual(report["test_status"], "passed")
        self.assertEqual(report["toolchain"], "python")
        self.assertEqual(task["result"]["summary"]["message"], "仓库测试通过")

        markdown = next(item for item in task["artifacts"] if item["filename"] == "check-report.md")
        md_text = client.get(f"/artifacts/{markdown['id']}/download").text
        self.assertIn("# 代码仓库体检报告", md_text)
        self.assertIn("结论：仓库测试通过", md_text)

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

    def test_repo_check_retries_clone_once(self) -> None:
        """首次克隆网络失败时自动重试，第二次成功仍算任务成功。"""
        sandbox = TemplateSandbox(clone_failures=1)
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={"task_type": "REPO_CHECK", "params": {"repo_url": "https://github.com/example/demo"}},
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual(sandbox.clone_calls, 2)
        clone_step = next(step for step in task["steps"] if step["name"] == "获取仓库代码")
        self.assertIn("第 2 次尝试", clone_step["output"])
        report = self.artifact_json(client, task, "check-report.json")
        self.assertEqual(report["clone_attempts"], 2)

    def test_repo_check_falls_back_to_unittest_when_pytest_missing(self) -> None:
        """沙箱没有 pytest 时改用 unittest，不能把缺工具当成测试失败。"""
        sandbox = TemplateSandbox(pytest_available=False, unittest_tests=3)
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={
                "task_type": "REPO_CHECK",
                "params": {"repo_url": "https://github.com/example/demo", "install_deps": False},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()

        self.assertEqual(task["status"], "SUCCESS")
        report = self.artifact_json(client, task, "check-report.json")
        self.assertEqual(report["test_status"], "passed")
        self.assertIn("pytest", report["test_note"])
        self.assertEqual(report["exit_code"], 0)

    def test_repo_check_reports_not_run_when_no_runner_works(self) -> None:
        """既没有 pytest 也没有 unittest 用例时，结论是“未运行”，不是“未通过”。"""
        sandbox = TemplateSandbox(pytest_available=False, unittest_tests=0)
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={
                "task_type": "REPO_CHECK",
                "params": {"repo_url": "https://github.com/example/demo", "install_deps": False},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()

        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual(task["result"]["summary"]["test_status"], "not_run")
        self.assertIn("没有 unittest 用例", task["result"]["summary"]["message"])
        report = self.artifact_json(client, task, "check-report.json")
        self.assertIsNone(report["exit_code"])

    def test_repo_check_reports_missing_dependency_as_error(self) -> None:
        """导入失败说明依赖没装好，不能归为“测试未通过”。"""
        sandbox = TemplateSandbox(pytest_available=False, missing_dependency=True)
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={
                "task_type": "REPO_CHECK",
                "params": {"repo_url": "https://github.com/example/demo", "install_deps": False},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()

        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual(task["result"]["summary"]["test_status"], "error")
        self.assertIn("未安装该仓库的依赖", task["result"]["summary"]["message"])

    def test_repo_check_can_install_dependencies_on_demand(self) -> None:
        """显式开启 install_deps 时安装依赖；失败要给出明确的安装错误。"""
        sandbox = TemplateSandbox(install_status="failed")
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={
                "task_type": "REPO_CHECK",
                "params": {
                    "repo_url": "https://github.com/example/demo",
                    "install_deps": True,
                },
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()

        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual(task["result"]["summary"]["test_status"], "error")
        self.assertIn("依赖安装失败", task["result"]["summary"]["message"])
        self.assertEqual(sandbox.install_calls, 1)
        filenames = {item["filename"] for item in task["artifacts"]}
        self.assertIn("install.log", filenames)

    def test_repo_check_blocks_when_required_python_is_missing(self) -> None:
        """显式关闭降级时，仓库要求 3.13 而沙箱没有，应明确报版本不匹配而不是硬跑。"""
        sandbox = TemplateSandbox(
            requires_python=">=3.13", python_available=("3.12", "3.10"), python_install_ok=False
        )
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={
                "task_type": "REPO_CHECK",
                "params": {
                    "repo_url": "https://github.com/example/demo",
                    "install_deps": True,
                    "allow_version_mismatch": False,
                },
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()

        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual(task["result"]["summary"]["test_status"], "error")
        self.assertIn("3.13", task["result"]["summary"]["message"])
        self.assertEqual(sandbox.install_calls, 0)
        report = self.artifact_json(client, task, "check-report.json")
        self.assertEqual(report["dependencies"], "version_mismatch")
        self.assertEqual(report["requires_python"], ">=3.13")

    def test_repo_check_relaxes_python_version_by_default(self) -> None:
        """默认允许降一档试跑，并在报告里注明结果仅供参考。"""
        sandbox = TemplateSandbox(
            requires_python=">=3.13", python_available=("3.12", "3.10"), python_install_ok=False
        )
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={
                "task_type": "REPO_CHECK",
                "params": {"repo_url": "https://github.com/example/demo", "install_deps": True},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()

        self.assertEqual(task["status"], "SUCCESS")
        report = self.artifact_json(client, task, "check-report.json")
        self.assertTrue(report["version_relaxed"])
        self.assertEqual(report["python"], "3.12.11")
        self.assertNotEqual(report["dependencies"], "version_mismatch")
        markdown_artifact = next(
            item for item in task["artifacts"] if item["filename"] == "check-report.md"
        )
        markdown = client.get(f"/artifacts/{markdown_artifact['id']}/download")
        self.assertIn("版本说明", markdown.text)

    def test_repo_check_installs_required_python_when_enabled(self) -> None:
        """镜像里没有 3.13 时，先尝试补装；装上后按严格要求运行，不再降级。"""
        sandbox = TemplateSandbox(requires_python=">=3.13", python_available=("3.12", "3.10"))
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={
                "task_type": "REPO_CHECK",
                "params": {"repo_url": "https://github.com/example/demo", "install_deps": True},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()

        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual(sandbox.python_installs, 1)
        self.assertTrue(any("uv python install 3.13" in item for item in sandbox.commands))
        report = self.artifact_json(client, task, "check-report.json")
        self.assertFalse(report["version_relaxed"])
        self.assertEqual(report["python"], "3.13.11")
        markdown_artifact = next(
            item for item in task["artifacts"] if item["filename"] == "check-report.md"
        )
        markdown = client.get(f"/artifacts/{markdown_artifact['id']}/download")
        self.assertIn("解释器准备：按仓库要求现场安装", markdown.text)

    def test_repo_check_runs_tests_inside_uv_virtualenv(self) -> None:
        """安装成功后，测试必须用任务虚拟环境里的解释器，而不是系统 Python。"""
        sandbox = TemplateSandbox(requires_python=">=3.10", python_available=("3.12",))
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={
                "task_type": "REPO_CHECK",
                "params": {"repo_url": "https://github.com/example/demo", "install_deps": True},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()

        self.assertEqual(task["status"], "SUCCESS")
        report = self.artifact_json(client, task, "check-report.json")
        self.assertEqual(report["dependencies"], "installed")
        self.assertEqual(report["python"], "3.12.11")
        self.assertEqual(report["test_status"], "passed")
        self.assertTrue(
            any("../.venv/bin/python -m pytest" in command for command in sandbox.commands)
        )

    def test_repo_check_skips_install_when_repo_declares_no_dependencies(self) -> None:
        """仓库没有声明依赖时，不安装，但仍然正常跑测试。"""
        sandbox = TemplateSandbox(install_status="no_deps", declared_dependencies=())
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={
                "task_type": "REPO_CHECK",
                "params": {"repo_url": "https://github.com/example/demo", "install_deps": True},
            },
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()

        self.assertEqual(task["status"], "SUCCESS")
        report = self.artifact_json(client, task, "check-report.json")
        self.assertEqual(report["dependencies"], "no_deps")
        self.assertEqual(report["test_status"], "passed")

    def test_repo_check_network_failure_is_readable(self) -> None:
        """连续失败时给出可操作的中文说明，原始 git 输出留在技术详情。"""
        sandbox = TemplateSandbox(clone_failures=5)
        client = self.create_client(sandbox)
        created = client.post(
            "/tasks",
            json={"task_type": "REPO_CHECK", "params": {"repo_url": "https://github.com/example/demo"}},
        )
        task = client.get(f"/tasks/{created.json()['id']}").json()
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(task["error_code"], "REPO_UNREACHABLE")
        self.assertIn("无法连接代码托管网站", task["error_message"])
        self.assertIn("demo://python", task["error_message"])
        self.assertEqual(sandbox.clone_calls, 2)
        clone_step = next(step for step in task["steps"] if step["name"] == "获取仓库代码")
        self.assertIn("Connection refused", clone_step["output"])
        # 失败也要留下可下载的说明，否则用户只看到一句错误。
        names = [artifact["filename"] for artifact in task["artifacts"]]
        self.assertIn("clone-failure.json", names)
        failure = self.artifact_json(client, task, "clone-failure.json")
        self.assertEqual(failure["error_code"], "REPO_UNREACHABLE")
        self.assertEqual(failure["attempts"], 2)

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

    def test_web_digest_produces_article_screenshot_and_report(self) -> None:
        """整理任务应产出正文、整页截图和报告三件产物，不再重复输出纯文本。"""
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
            ["article.md", "screenshot.png", "web-digest.json"],
        )
        article_artifact = next(item for item in task["artifacts"] if item["filename"] == "article.md")
        article = client.get(f"/artifacts/{article_artifact['id']}/download")
        self.assertIn("页面正文内容", article.text)
        summary = task["result"]["summary"]
        self.assertEqual(summary["kind"], "web_digest")
        self.assertEqual(summary["words"], 6)
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
