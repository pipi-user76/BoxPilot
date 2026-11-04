"""文件职责：实现任务状态流转和固定步骤执行。

调度器按 PENDING -> RUNNING -> SUCCESS/FAILED 推进任务；某一步失败后立即停止后续
步骤，避免在错误输入上继续执行。模板只负责定义步骤，本模块负责统一记录状态和产物。
"""

from __future__ import annotations

from collections.abc import Iterable

from backend.db import TaskStore
from backend.models import Artifact, StepStatus, TaskStatus, TaskType
from backend.sandbox_client import SandboxAdapter
from backend.storage import ArtifactStore
from backend.templates.base import StepExecutionError, TaskContext, TaskTemplate


class TaskRunner:
    """按任务类型选择模板并顺序执行其固定步骤。"""

    def __init__(self, store: TaskStore, artifact_store: ArtifactStore, sandbox: SandboxAdapter) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.sandbox = sandbox
        self.templates: dict[TaskType, TaskTemplate] = {}

    def register(self, task_type: TaskType, template: TaskTemplate) -> None:
        """注册任务类型与模板的映射；未注册类型不会通过创建接口。"""
        self.templates[task_type] = template

    def supported_types(self) -> set[TaskType]:
        return set(self.templates)

    def run(self, task_id: str) -> None:
        # 只执行 PENDING 任务，防止同一任务被重复调度。
        task = self.store.get_task(task_id)
        if task is None or task.status != TaskStatus.PENDING:
            return

        template = self.templates.get(task.task_type)
        if template is None:
            self.store.start_task(task_id)
            self.store.finish_task(
                task_id,
                TaskStatus.FAILED,
                None,
                "VALIDATION_ERROR",
                f"no executor registered for {task.task_type.value}",
            )
            return

        self.store.start_task(task_id)
        # Context 只暴露任务参数和产物目录，模板不能直接修改任务状态。
        context = TaskContext(task_id=task_id, params=task.params, artifact_store=self.artifact_store)
        completed_steps: list[str] = []
        try:
            for index, definition in enumerate(template.steps, start=1):
                step = self.store.add_step(task_id, index, definition.name)
                try:
                    outcome = definition.action(context)
                    # 只有非空产物才登记，防止“命令说成功但实际没有文件”。
                    for spec in outcome.artifacts:
                        if spec.size_bytes <= 0:
                            raise StepExecutionError("ARTIFACT_EMPTY", f"artifact {spec.filename} is empty")
                        self.store.add_artifact(
                            Artifact(
                                task_id=task_id,
                                filename=spec.filename,
                                relative_path=spec.filename,
                                content_type=spec.content_type,
                                size_bytes=spec.size_bytes,
                            )
                        )
                    self.store.finish_step(step.id, StepStatus.SUCCESS, outcome.output[:4000], None, None)
                    completed_steps.append(definition.name)
                except StepExecutionError as exc:
                    # 业务步骤失败会同时落库步骤错误和任务错误，前端可直接展示。
                    self.store.finish_step(step.id, StepStatus.FAILED, None, exc.code, exc.message)
                    self.store.finish_task(task_id, TaskStatus.FAILED, None, exc.code, exc.message)
                    return
        except Exception as exc:
            # 未预期异常统一视为内部错误，保留任务记录供后续排查。
            self.store.finish_task(task_id, TaskStatus.FAILED, None, "INTERNAL_ERROR", str(exc))
            return

        self.store.finish_task(
            task_id,
            TaskStatus.SUCCESS,
            {"message": "task completed", "completed_steps": completed_steps},
            None,
            None,
        )


def build_runner(
    store: TaskStore,
    artifact_store: ArtifactStore,
    sandbox: SandboxAdapter,
    templates: Iterable[tuple[TaskType, TaskTemplate]],
) -> TaskRunner:
    runner = TaskRunner(store, artifact_store, sandbox)
    for task_type, template in templates:
        runner.register(task_type, template)
    return runner
