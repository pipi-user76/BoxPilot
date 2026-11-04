from __future__ import annotations

from collections.abc import Iterable

from app.adapters.sandbox import SandboxAdapter
from app.executor.templates.base import StepExecutionError, TaskContext, TaskTemplate
from app.schemas.task import Artifact, StepStatus, TaskStatus, TaskType
from app.services.artifact_store import ArtifactStore
from app.services.task_store import TaskStore


class TaskRunner:
    def __init__(self, store: TaskStore, artifact_store: ArtifactStore, sandbox: SandboxAdapter) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.sandbox = sandbox
        self.templates: dict[TaskType, TaskTemplate] = {}

    def register(self, task_type: TaskType, template: TaskTemplate) -> None:
        self.templates[task_type] = template

    def supported_types(self) -> set[TaskType]:
        return set(self.templates)

    def run(self, task_id: str) -> None:
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
        context = TaskContext(task_id=task_id, params=task.params, artifact_store=self.artifact_store)
        completed_steps: list[str] = []
        try:
            for index, definition in enumerate(template.steps, start=1):
                step = self.store.add_step(task_id, index, definition.name)
                try:
                    outcome = definition.action(context)
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
                    self.store.finish_step(step.id, StepStatus.FAILED, None, exc.code, exc.message)
                    self.store.finish_task(task_id, TaskStatus.FAILED, None, exc.code, exc.message)
                    return
        except Exception as exc:
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
