"""文件职责：提供开发期 MOCK 模板，验证调度器和产物链路是否畅通。

该模板不属于四个正式业务模板，保留用于快速验证调度器和沙箱链路，不能被当成产品功能。
"""

from __future__ import annotations

from backend.sandbox_client import SandboxAdapter
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext


class MockTemplate:
    """开发期模板：创建工作目录、执行一次沙箱命令、生成一个文本产物。"""

    name = "MOCK"

    def __init__(self, sandbox: SandboxAdapter) -> None:
        self.sandbox = sandbox
        self.steps = (
            StepDefinition("准备任务目录", self._prepare_workspace),
            StepDefinition("执行沙箱验证", self._sandbox_echo),
            StepDefinition("归档验证结果", self._archive_result),
        )

    def _prepare_workspace(self, context: TaskContext) -> StepOutcome:
        result = self.sandbox.ensure_task_workspace(context.task_id)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        return StepOutcome(output="任务目录已准备完成")

    def _sandbox_echo(self, context: TaskContext) -> StepOutcome:
        result = self.sandbox.exec_shell("printf 'boxpilot mock ok'", task_id=context.task_id, timeout_s=15)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        output = result["data"]["output"].strip()
        if not output:
            raise StepExecutionError("ARTIFACT_EMPTY", "sandbox returned no output")
        context.state["sandbox_output"] = output
        return StepOutcome(output=f"沙箱命令执行成功：{output}")

    def _archive_result(self, context: TaskContext) -> StepOutcome:
        spec = context.artifact_store.write_text(
            context.task_id,
            "mock-result.txt",
            "BoxPilot scheduler smoke test completed successfully.\n",
        )
        return StepOutcome(
            output="验证结果已归档",
            artifacts=[spec],
            result={
                "kind": "mock",
                "message": "开发验证完成",
                "command_output": context.state.get("sandbox_output", ""),
            },
        )
