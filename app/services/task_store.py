from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Protocol

from app.schemas.task import Artifact, StepStatus, TaskRecord, TaskStatus, TaskStep, TaskType, utc_now


class TaskStore(Protocol):
    def create_task(self, task_type: TaskType, params: dict) -> TaskRecord: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def list_tasks(self) -> list[TaskRecord]: ...

    def start_task(self, task_id: str) -> TaskRecord: ...

    def finish_task(
        self,
        task_id: str,
        status: TaskStatus,
        result: dict | None,
        error_code: str | None,
        error_message: str | None,
    ) -> TaskRecord: ...

    def add_step(self, task_id: str, step_index: int, name: str) -> TaskStep: ...

    def finish_step(
        self,
        step_id: str,
        status: StepStatus,
        output: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> TaskStep: ...

    def add_artifact(self, artifact: Artifact) -> Artifact: ...

    def get_artifact(self, artifact_id: str) -> tuple[Artifact, str] | None: ...


class InMemoryTaskStore:
    """Week-one contract implementation; replace with 陈咏忻's SQLite store."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._artifacts: dict[str, Artifact] = {}
        self._lock = threading.RLock()

    def create_task(self, task_type: TaskType, params: dict) -> TaskRecord:
        task = TaskRecord(task_type=task_type, params=params)
        with self._lock:
            self._tasks[task.id] = task
        return task.model_copy(deep=True)

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.model_copy(deep=True) if task else None

    def list_tasks(self) -> list[TaskRecord]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)
            return [task.model_copy(deep=True) for task in tasks]

    def start_task(self, task_id: str) -> TaskRecord:
        with self._lock:
            task = self._require_task(task_id)
            task.status = TaskStatus.RUNNING
            task.started_at = utc_now()
            task.error_code = None
            task.error_message = None
            return task.model_copy(deep=True)

    def finish_task(
        self,
        task_id: str,
        status: TaskStatus,
        result: dict | None,
        error_code: str | None,
        error_message: str | None,
    ) -> TaskRecord:
        with self._lock:
            task = self._require_task(task_id)
            task.status = status
            task.result = result
            task.error_code = error_code
            task.error_message = error_message
            task.finished_at = utc_now()
            return task.model_copy(deep=True)

    def add_step(self, task_id: str, step_index: int, name: str) -> TaskStep:
        with self._lock:
            task = self._require_task(task_id)
            step = TaskStep(task_id=task_id, step_index=step_index, name=name)
            task.steps.append(step)
            return step.model_copy(deep=True)

    def finish_step(
        self,
        step_id: str,
        status: StepStatus,
        output: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> TaskStep:
        with self._lock:
            for task in self._tasks.values():
                for step in task.steps:
                    if step.id == step_id:
                        step.status = status
                        step.output = output
                        step.error_code = error_code
                        step.error_message = error_message
                        step.finished_at = datetime.now(timezone.utc)
                        step.duration_ms = int((step.finished_at - step.started_at).total_seconds() * 1000)
                        return step.model_copy(deep=True)
        raise KeyError(step_id)

    def add_artifact(self, artifact: Artifact) -> Artifact:
        with self._lock:
            task = self._require_task(artifact.task_id)
            task.artifacts.append(artifact)
            self._artifacts[artifact.id] = artifact
            return artifact.model_copy(deep=True)

    def get_artifact(self, artifact_id: str) -> tuple[Artifact, str] | None:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                return None
            return artifact.model_copy(deep=True), artifact.task_id

    def _require_task(self, task_id: str) -> TaskRecord:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        return task
