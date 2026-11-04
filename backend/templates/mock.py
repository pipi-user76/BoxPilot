"""文件职责：提供开发期 MOCK 模板，验证调度器和产物链路是否畅通。

该模板不属于四个正式业务模板，只在真实模板尚未接入时使用，不能被当成产品功能。
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
            StepDefinition("prepare_workspace", self._prepare_workspace),
            StepDefinition("sandbox_echo", self._sandbox_echo),
            StepDefinition("archive_result", self._archive_result),
        )

    def _prepare_workspace(self, context: TaskContext) -> StepOutcome:
        result = self.sandbox.ensure_task_workspace(context.task_id)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        return StepOutcome(output=f"workspace ready: {self.sandbox.task_workdir(context.task_id)}")

    def _sandbox_echo(self, context: TaskContext) -> StepOutcome:
        result = self.sandbox.exec_shell("printf 'boxpilot mock ok'", task_id=context.task_id, timeout_s=15)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        output = result["data"]["output"].strip()
        if not output:
            raise StepExecutionError("ARTIFACT_EMPTY", "sandbox returned no output")
        return StepOutcome(output=output)

    def _archive_result(self, context: TaskContext) -> StepOutcome:
        spec = context.artifact_store.write_text(
            context.task_id,
            "mock-result.txt",
            "BoxPilot scheduler smoke test completed successfully.\n",
        )
        return StepOutcome(output=f"artifact ready: {spec.filename}", artifacts=[spec])
