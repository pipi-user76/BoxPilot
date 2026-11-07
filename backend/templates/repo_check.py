"""文件职责：可信代码仓库体检模板。

模板限定 HTTPS 且主机在白名单内的仓库，只执行预设的 Python 或 Node 测试命令。
克隆失败属于任务执行失败；测试命令返回非零属于有效检查结果，会写入报告但不使任务失败。
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from backend.config import settings
from backend.sandbox_client import SandboxAdapter
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext
from backend.validation import ValidationFailure, validate_git_url


class RepoCheckTemplate:
    """克隆可信仓库、识别技术栈并运行固定测试命令。"""

    name = "REPO_CHECK"

    def __init__(
        self,
        sandbox: SandboxAdapter,
        trusted_hosts: set[str] | None = None,
        timeout_s: int | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.trusted_hosts = trusted_hosts or settings.trusted_git_hosts()
        self.timeout_s = timeout_s or settings.REPO_TEST_TIMEOUT_SECONDS
        self.steps = (
            StepDefinition("校验仓库地址", self._validate_repository),
            StepDefinition("获取仓库代码", self._clone_repository),
            StepDefinition("识别项目类型", self._detect_toolchain),
            StepDefinition("运行项目测试", self._run_repository_tests),
            StepDefinition("生成检查报告", self._write_check_report),
        )

    def _validate_repository(self, context: TaskContext) -> StepOutcome:
        """只接受白名单主机上的公开 HTTPS 仓库。"""
        repo_url = str(context.params.get("repo_url", "")).strip()
        if repo_url == "demo://python" and settings.ENABLE_DEMO_TASKS:
            context.state.update(repo_url=repo_url, demo_repo=True)
            return StepOutcome(output="已选择内置的离线 Python 示例仓库")
        try:
            validate_git_url(repo_url, self.trusted_hosts)
        except ValidationFailure as exc:
            raise StepExecutionError("VALIDATION_ERROR", str(exc)) from exc
        context.state.update(repo_url=repo_url, demo_repo=False)
        return StepOutcome(output=f"可信仓库地址校验通过：{repo_url}")

    def _clone_repository(self, context: TaskContext) -> StepOutcome:
        """浅克隆仓库，减少网络流量和演示等待时间。"""
        repo_url = context.state["repo_url"]
        if context.state.get("demo_repo"):
            return self._prepare_demo_repository(context)
        command = f"git clone --depth 1 -- {shlex.quote(repo_url)} repo"
        result = self.sandbox.exec_shell(command, task_id=context.task_id, timeout_s=self.timeout_s)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        context.state["clone_output"] = result["data"]["output"]
        return StepOutcome(output="仓库代码获取成功")

    def _prepare_demo_repository(self, context: TaskContext) -> StepOutcome:
        """上传 samples/repo_check_demo，并在沙箱内创建 Git 提交。"""
        sample_root = Path(__file__).resolve().parents[2] / "samples" / "repo_check_demo"
        if not sample_root.is_dir():
            raise StepExecutionError("FILE_NOT_FOUND", f"demo repository not found: {sample_root}")
        for source in sorted(path for path in sample_root.rglob("*") if path.is_file()):
            relative = source.relative_to(sample_root).as_posix()
            result = self.sandbox.upload_file(context.task_id, source, f"repo/{relative}")
            if not result["success"]:
                error = result["error"]
                raise StepExecutionError(error["code"], error["message"])
        command = (
            "git init -q repo && git -C repo config user.email boxpilot@example.local && "
            "git -C repo config user.name BoxPilot && git -C repo add . && "
            "git -C repo commit -q -m 'offline demo repository'"
        )
        result = self.sandbox.exec_shell(command, task_id=context.task_id, timeout_s=self.timeout_s)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        context.state["clone_output"] = result["data"]["output"]
        return StepOutcome(output="内置示例仓库准备完成")

    def _detect_toolchain(self, context: TaskContext) -> StepOutcome:
        """优先识别 Python，再识别 Node；其他仓库只生成未运行报告。"""
        command = (
            "if test -f repo/boxpilot-unittest; then printf python-unittest; "
            "elif test -f repo/pyproject.toml || test -f repo/setup.py || "
            "test -f repo/pytest.ini || test -d repo/tests; then printf python; "
            "elif test -f repo/package.json; then printf node; else printf unknown; fi"
        )
        result = self.sandbox.exec_shell(command, task_id=context.task_id, timeout_s=30)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        toolchain = result["data"]["output"].strip().lower()
        if toolchain not in {"python", "python-unittest", "node", "unknown"}:
            toolchain = "unknown"
        context.state["toolchain"] = toolchain
        labels = {
            "python": "Python 测试项目",
            "python-unittest": "Python 标准库测试项目",
            "node": "Node.js 测试项目",
            "unknown": "未识别到支持的项目类型",
        }
        return StepOutcome(output=f"项目类型：{labels[toolchain]}")

    def _run_repository_tests(self, context: TaskContext) -> StepOutcome:
        """执行固定命令；非零退出码保存为检查结果，而不是抛出系统异常。"""
        toolchain = context.state["toolchain"]
        commands = {
            "python": "cd repo && python -m pytest -q",
            "python-unittest": "cd repo && python -m unittest discover -q",
            "node": "cd repo && npm test -- --runInBand",
        }
        test_command = commands.get(toolchain)
        if test_command is None:
            context.state.update(
                test_command=None,
                test_exit_code=None,
                test_output="No supported test runner detected.",
                test_status="not_run",
            )
            return StepOutcome(output="未检测到可运行的测试命令")

        result = self.sandbox.exec_shell(
            test_command,
            task_id=context.task_id,
            timeout_s=self.timeout_s,
            check_exit=False,
        )
        if not result["success"]:
            error = result["error"]
            context.state.update(
                test_command=test_command,
                test_exit_code=None,
                test_output=error["message"],
                test_status="error",
            )
            return StepOutcome(output=f"测试执行异常：{error['message']}")

        exit_code = result["data"].get("exit_code")
        test_output = result["data"].get("output") or "(no test output)"
        context.state.update(
            test_command=test_command,
            test_exit_code=exit_code,
            test_output=test_output,
            test_status="passed" if exit_code == 0 else "failed",
        )
        status_label = "测试通过" if context.state["test_status"] == "passed" else "测试未通过"
        return StepOutcome(output=f"{status_label}，退出码：{exit_code}")

    def _write_check_report(self, context: TaskContext) -> StepOutcome:
        """把结构化结论和原始测试输出保存为可下载产物。"""
        report = {
            "repo_url": context.state["repo_url"],
            "toolchain": context.state["toolchain"],
            "test_command": context.state["test_command"],
            "exit_code": context.state["test_exit_code"],
            "test_status": context.state["test_status"],
            "clone_output": context.state.get("clone_output", ""),
            "test_output": context.state["test_output"],
        }
        report_text = json.dumps(report, ensure_ascii=False, indent=2)
        report_spec = context.artifact_store.write_text(
            context.task_id,
            "check-report.json",
            report_text,
        )
        log_spec = context.artifact_store.write_text(
            context.task_id,
            "test.log",
            context.state["test_output"] + "\n",
        )
        status_messages = {
            "passed": "仓库测试通过",
            "failed": "仓库测试未通过",
            "not_run": "未检测到可运行的测试",
            "error": "测试执行异常",
        }
        return StepOutcome(
            output=f"检查完成：{status_messages[context.state['test_status']]}",
            artifacts=[report_spec, log_spec],
            result={
                "kind": "repo_check",
                "message": status_messages[context.state["test_status"]],
                "test_status": context.state["test_status"],
                "toolchain": context.state["toolchain"],
                "exit_code": context.state["test_exit_code"],
            },
        )
