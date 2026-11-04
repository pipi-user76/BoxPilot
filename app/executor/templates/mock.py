from __future__ import annotations

from app.adapters.sandbox import SandboxAdapter
from app.executor.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext


class MockTemplate:
    """Development-only template used to prove the scheduler contract."""

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
