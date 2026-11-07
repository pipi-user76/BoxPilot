"""文件职责：实现任务状态流转、取消、重试和固定步骤执行。

调度器按 PENDING -> RUNNING -> SUCCESS/FAILED/CANCELLED 推进任务；某一步失败后立即
停止后续步骤，避免在错误输入上继续执行。模板只负责定义步骤，本模块负责统一记录
状态、产物和结构化日志。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

from backend.db import TaskStore
from backend.logging_config import get_logger
from backend.models import (
    TERMINAL_TASK_STATUSES,
    Artifact,
    StepStatus,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from backend.sandbox_client import SandboxAdapter
from backend.storage import ArtifactStore
from backend.templates.base import StepExecutionError, TaskContext, TaskTemplate


class TaskNotFound(KeyError):
    """任务不存在；路由层会转换为 404。"""

    def __init__(self, task_id: str) -> None:
        super().__init__(task_id)
        self.task_id = task_id


class TaskConflict(RuntimeError):
    """任务当前状态不允许该操作；路由层会转换为 409。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TaskRunner:
    """按任务类型选择模板并顺序执行其固定步骤。"""

    def __init__(self, store: TaskStore, artifact_store: ArtifactStore, sandbox: SandboxAdapter) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.sandbox = sandbox
        self.templates: dict[TaskType, TaskTemplate] = {}
        # 演示环境共享一个浏览器实例，串行执行可避免页面导航互相覆盖。
        self._execution_lock = threading.Lock()
        # 取消请求只对正在运行的任务生效，在步骤边界检查，避免中断正在写的产物。
        self._state_lock = threading.Lock()
        self._cancel_requested: set[str] = set()
        self.log = get_logger("scheduler")

    def register(self, task_type: TaskType, template: TaskTemplate) -> None:
        """注册任务类型与模板的映射；未注册类型不会通过创建接口。"""
        self.templates[task_type] = template

    def supported_types(self) -> set[TaskType]:
        return set(self.templates)

    def run(self, task_id: str) -> None:
        """串行执行任务，保证状态记录和共享浏览器不会被并发请求打乱。"""
        with self._execution_lock:
            try:
                self._run_locked(task_id)
            finally:
                # 无论成功、失败还是取消，都不能让取消标记泄漏到下一次执行。
                with self._state_lock:
                    self._cancel_requested.discard(task_id)

    def request_cancel(self, task_id: str) -> TaskRecord:
        """请求取消任务：等待中的任务立即结束，运行中的任务在下一步骤前结束。"""
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        if task.status in TERMINAL_TASK_STATUSES:
            raise TaskConflict(
                "TASK_NOT_CANCELLABLE",
                f"任务已结束（{task.status.value}），不能取消",
            )

        if task.status == TaskStatus.PENDING:
            # 先抢在后台线程启动前结束；如果此时已被调度器抢到，再退回标记方式。
            current = self._finish_cancelled(task_id, task.task_type, [], {})
            if current.status in TERMINAL_TASK_STATUSES:
                return current
            task = current

        with self._state_lock:
            self._cancel_requested.add(task_id)
        self.log.info(
            "task_cancel_requested",
            extra={"task_id": task_id, "task_type": task.task_type.value, "status": task.status.value},
        )
        return self.store.get_task(task_id) or task

    def retry_task(self, task_id: str) -> TaskRecord:
        """为已失败/超时/取消的任务创建一次新执行，原任务记录保持不变。"""
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        if task.status not in {TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED}:
            raise TaskConflict(
                "TASK_NOT_RETRYABLE",
                f"只有失败、超时或已取消的任务可以重试（当前 {task.status.value}）",
            )

        retried = self.store.create_task(
            task.task_type,
            task.params,
            attempt=task.attempt + 1,
            retry_of=task.id,
        )
        if task.task_type == TaskType.CSV_ANALYSIS:
            try:
                self._copy_csv_input(task.id, retried.id)
            except (FileNotFoundError, ValueError) as exc:
                # 输入文件已被清理时不能创建一个注定失败的重试任务。
                self.store.delete_task(retried.id)
                raise TaskConflict("INPUT_NOT_FOUND", "原任务的 input.csv 已不存在，无法重试") from exc

        self.log.info(
            "task_retried",
            extra={
                "task_id": retried.id,
                "retry_of": task.id,
                "task_type": task.task_type.value,
                "attempt": retried.attempt,
            },
        )
        return retried

    def _copy_csv_input(self, source_task_id: str, target_task_id: str) -> None:
        """把原任务的 CSV 输入复制到新任务目录，让重试复用同一份数据。"""
        source = self.artifact_store.resolve(source_task_id, "input.csv")
        self.artifact_store.write_bytes(target_task_id, "input.csv", source.read_bytes(), "text/csv")

    def _run_locked(self, task_id: str) -> None:
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
            self.log.error(
                "task_failed",
                extra={
                    "task_id": task_id,
                    "task_type": task.task_type.value,
                    "error_code": "VALIDATION_ERROR",
                },
            )
            return

        if self._is_cancel_requested(task_id):
            self._finish_cancelled(task_id, task.task_type, [], {})
            return

        self.store.start_task(task_id)
        self.log.info(
            "task_started",
            extra={
                "task_id": task_id,
                "task_type": task.task_type.value,
                "attempt": task.attempt,
                "step_count": len(template.steps),
            },
        )
        # Context 只暴露任务参数和产物目录，模板不能直接修改任务状态。
        context = TaskContext(task_id=task_id, params=task.params, artifact_store=self.artifact_store)
        completed_steps: list[str] = []
        task_result: dict = {}
        try:
            for index, definition in enumerate(template.steps, start=1):
                # 只在线性步骤之间响应取消，已写入的产物保持可下载、可审查。
                if self._is_cancel_requested(task_id):
                    self._finish_cancelled(task_id, task.task_type, completed_steps, task_result)
                    return

                step = self.store.add_step(task_id, index, definition.name)
                started = time.perf_counter()
                self.log.info(
                    "step_started",
                    extra={"task_id": task_id, "step": definition.name, "step_index": index},
                )
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
                    if outcome.result:
                        task_result.update(outcome.result)
                    self.log.info(
                        "step_succeeded",
                        extra={
                            "task_id": task_id,
                            "step": definition.name,
                            "step_index": index,
                            "duration_ms": int((time.perf_counter() - started) * 1000),
                        },
                    )
                except StepExecutionError as exc:
                    # 业务步骤失败会同时落库步骤错误和任务错误，前端可直接展示。
                    self.store.finish_step(step.id, StepStatus.FAILED, None, exc.code, exc.message)
                    self.store.finish_task(task_id, TaskStatus.FAILED, None, exc.code, exc.message)
                    self.log.warning(
                        "step_failed",
                        extra={
                            "task_id": task_id,
                            "step": definition.name,
                            "step_index": index,
                            "duration_ms": int((time.perf_counter() - started) * 1000),
                            "error_code": exc.code,
                            "error_message": exc.message,
                        },
                    )
                    return
        except Exception as exc:
            # 未预期异常统一视为内部错误，保留任务记录供后续排查。
            self.store.finish_task(task_id, TaskStatus.FAILED, None, "INTERNAL_ERROR", str(exc))
            self.log.exception(
                "task_crashed",
                extra={"task_id": task_id, "task_type": task.task_type.value},
            )
            return

        self.store.finish_task(
            task_id,
            TaskStatus.SUCCESS,
            {
                "message": "任务执行完成",
                "task_type": task.task_type.value,
                "completed_steps": completed_steps,
                "summary": task_result,
            },
            None,
            None,
        )
        self.log.info(
            "task_succeeded",
            extra={
                "task_id": task_id,
                "task_type": task.task_type.value,
                "step_count": len(completed_steps),
            },
        )

    def _finish_cancelled(
        self,
        task_id: str,
        task_type: TaskType,
        completed_steps: list[str],
        task_result: dict,
    ) -> TaskRecord:
        """写入取消结果；已结束的任务不会被覆盖。"""
        current = self.store.get_task(task_id)
        if current is None:
            raise TaskNotFound(task_id)
        if current.status in TERMINAL_TASK_STATUSES:
            return current
        cancelled = self.store.finish_task(
            task_id,
            TaskStatus.CANCELLED,
            {
                "message": "任务已取消",
                "task_type": task_type.value,
                "completed_steps": completed_steps,
                "summary": task_result,
            },
            "TASK_CANCELLED",
            "任务被用户取消",
        )
        self.log.info(
            "task_cancelled",
            extra={"task_id": task_id, "task_type": task_type.value, "step_count": len(completed_steps)},
        )
        return cancelled

    def _is_cancel_requested(self, task_id: str) -> bool:
        with self._state_lock:
            return task_id in self._cancel_requested


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
