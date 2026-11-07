"""文件职责：定义任务持久化契约，并提供开发期使用的内存实现。

生产版本将由陈咏忻在此文件中接入 SQLite 三表：tasks、steps、artifacts。当前
InMemoryTaskStore 用于打通 API、调度器和测试，服务重启后数据不会保留。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.models import Artifact, StepStatus, TaskRecord, TaskStatus, TaskStep, TaskType, utc_now


SQLALCHEMY_DATABASE_URL = "sqlite:///./data/boxpilot.db"
# SQLite 默认限制连接线程；FastAPI 的后台任务会在不同线程访问连接，因此关闭该限制。
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 数据库依赖：请求结束后一定关闭 Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TaskStore(Protocol):
    """任务存储协议；调度器和路由只依赖这些方法，不关心 SQLite 细节。"""
    def create_task(
        self,
        task_type: TaskType,
        params: dict,
        *,
        attempt: int = 1,
        retry_of: str | None = None,
    ) -> TaskRecord: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def list_tasks(
        self,
        *,
        query: str = "",
        statuses: list[TaskStatus] | None = None,
        task_type: TaskType | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskRecord], int]: ...

    def count_tasks(self) -> dict[str, int]: ...

    def delete_task(self, task_id: str) -> bool: ...

    def purge_tasks(self, statuses: list[TaskStatus], older_than: datetime) -> list[str]: ...

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
    """第 1 周契约实现，后续由陈咏忻替换为 SQLite 版本。"""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._artifacts: dict[str, Artifact] = {}
        # 后台调度线程与 HTTP 查询线程会并发访问，所有读写都经过同一把可重入锁。
        self._lock = threading.RLock()

    def create_task(
        self,
        task_type: TaskType,
        params: dict,
        *,
        attempt: int = 1,
        retry_of: str | None = None,
    ) -> TaskRecord:
        # 重试任务使用新的任务 ID，并通过 attempt/retry_of 保留来源关系。
        task = TaskRecord(task_type=task_type, params=params, attempt=attempt, retry_of=retry_of)
        with self._lock:
            self._tasks[task.id] = task
        return task.model_copy(deep=True)

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.model_copy(deep=True) if task else None

    def list_tasks(
        self,
        *,
        query: str = "",
        statuses: list[TaskStatus] | None = None,
        task_type: TaskType | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskRecord], int]:
        """按筛选条件返回当前页和命中总数；搜索覆盖任务 ID、类型、参数和错误信息。"""
        with self._lock:
            matched = [task for task in self._tasks.values() if self._matches(task, query, statuses, task_type)]
            matched.sort(key=lambda item: item.created_at, reverse=True)
            start = (page - 1) * page_size
            visible = matched[start : start + page_size]
            return [task.model_copy(deep=True) for task in visible], len(matched)

    def count_tasks(self) -> dict[str, int]:
        """统计全部任务的状态分布，供列表页概览使用。"""
        with self._lock:
            counts = {status.value: 0 for status in TaskStatus}
            counts["total"] = len(self._tasks)
            for task in self._tasks.values():
                counts[task.status.value] += 1
            return counts

    def delete_task(self, task_id: str) -> bool:
        """删除任务记录及其产物索引；磁盘文件由调用方同步清理。"""
        with self._lock:
            task = self._tasks.pop(task_id, None)
            if task is None:
                return False
            for artifact in task.artifacts:
                self._artifacts.pop(artifact.id, None)
            return True

    def purge_tasks(self, statuses: list[TaskStatus], older_than: datetime) -> list[str]:
        """删除指定状态且创建时间早于截止时间的任务，返回被删除的任务 ID。"""
        allowed = set(statuses)
        with self._lock:
            targets = [
                task.id
                for task in self._tasks.values()
                if task.status in allowed and task.created_at < older_than
            ]
            for task_id in targets:
                self.delete_task(task_id)
            return targets

    @staticmethod
    def _matches(
        task: TaskRecord,
        query: str,
        statuses: list[TaskStatus] | None,
        task_type: TaskType | None,
    ) -> bool:
        if statuses and task.status not in statuses:
            return False
        if task_type is not None and task.task_type != task_type:
            return False
        needle = query.strip().lower()
        if not needle:
            return True
        haystack = " ".join(
            [
                task.id,
                task.task_type.value,
                json.dumps(task.params, ensure_ascii=False),
                task.error_code or "",
                task.error_message or "",
                " ".join(step.name for step in task.steps),
            ]
        ).lower()
        return needle in haystack

    def start_task(self, task_id: str) -> TaskRecord:
        """将任务标记为运行中，并清除上一次残留错误。"""
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
        """写入最终状态；失败时保留错误码和可读错误信息。"""
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
        """结束步骤时计算耗时，供前端展示和后续性能分析。"""
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
