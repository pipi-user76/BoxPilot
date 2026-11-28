"""共享任务存储协议及内存测试实现。"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Protocol

from backend.models import (
    TaskChatMessage,
    TagRecord,
    AnnotationRecord,
    Artifact,
    KbCitation,
    KbDocumentRecord,
    KbMessageRecord,
    KnowledgeBaseRecord,
    NotificationRecord,
    ScheduleRecord,
    StepStatus,
    TaskRecord,
    TaskStatus,
    TaskStep,
    TaskType,
    utc_now,
)


class TaskStore(Protocol):
    def create_tag(self, owner_id: str, name: str) -> TagRecord: ...
    def list_tags(self, owner_id: str) -> list[TagRecord]: ...
    def set_task_tags(self, task_id: str, owner_id: str, names: list[str]) -> list[str]: ...
    def get_task_tags(self, task_id: str) -> list[str]: ...
    def set_document_tags(self, document_id: str, owner_id: str, names: list[str]) -> list[str]: ...
    def get_document_tags(self, document_id: str) -> list[str]: ...
    def add_annotation(self, owner_id: str, *, task_id: str | None = None, document_id: str | None = None,
                       artifact_name: str = "article.md", block_index: int = 0, start_offset: int = 0,
                       end_offset: int = 0, quote: str = "", note: str = "") -> AnnotationRecord: ...
    def list_annotations(self, *, task_id: str | None = None, document_id: str | None = None,
                         owner_id: str | None = None) -> list[AnnotationRecord]: ...
    def update_annotation(self, annotation_id: str, owner_id: str, note: str) -> AnnotationRecord | None: ...
    def delete_annotation(self, annotation_id: str, owner_id: str) -> bool: ...

    """任务存储协议；调度器和路由只依赖这些方法，不关心 SQLite 细节。"""
    def create_task(
        self,
        task_type: TaskType,
        params: dict,
        *,
        attempt: int = 1,
        retry_of: str | None = None,
        owner_id: str | None = None,
        dedupe_key: str | None = None,
        dedupe_since: datetime | None = None,
    ) -> TaskRecord: ...

    def find_by_dedupe(self, dedupe_key: str, since: datetime, owner_id: str | None = None) -> TaskRecord | None: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def list_tasks(
        self,
        *,
        query: str = "",
        statuses: list[TaskStatus] | None = None,
        task_type: TaskType | None = None,
        task_types: list[str] | None = None,
        page: int = 1,
        page_size: int = 20,
        owner_id: str | None = None,
    ) -> tuple[list[TaskRecord], int]: ...

    def count_tasks(
        self,
        owner_id: str | None = None,
        task_type: str | None = None,
        task_types: list[str] | None = None,
    ) -> dict[str, int]: ...

    def delete_task(self, task_id: str) -> bool: ...

    def purge_tasks(
        self, statuses: list[TaskStatus], older_than: datetime, owner_id: str | None = None
    ) -> list[str]: ...

    def reset_task_for_retry(self, task_id: str, attempt: int) -> TaskRecord: ...

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

    def add_task_message(self, task_id: str, role: str, content: str) -> TaskChatMessage: ...

    def list_task_messages(self, task_id: str, limit: int = 50) -> list[TaskChatMessage]: ...

    def create_schedule(
        self,
        task_type: TaskType,
        params: dict,
        cron: str,
        next_run_at: datetime,
        *,
        owner_id: str | None = None,
        name: str = "",
    ) -> ScheduleRecord: ...

    def list_schedules(self, owner_id: str | None = None) -> list[ScheduleRecord]: ...

    def get_schedule(self, schedule_id: str) -> ScheduleRecord | None: ...

    def update_schedule(
        self,
        schedule_id: str,
        *,
        name: str,
        task_type: TaskType,
        params: dict,
        cron: str,
        next_run_at: datetime,
    ) -> ScheduleRecord | None: ...

    def set_schedule_enabled(
        self, schedule_id: str, enabled: bool, next_run_at: datetime | None = None
    ) -> ScheduleRecord | None: ...

    def delete_schedule(self, schedule_id: str) -> bool: ...

    def due_schedules(self, now: datetime, limit: int = 5) -> list[ScheduleRecord]: ...

    def mark_schedule_run(
        self, schedule_id: str, ran_at: datetime, next_run_at: datetime, task_id: str
    ) -> None: ...

    def schedule_runs(self, schedule_id: str, limit: int = 20) -> list[TaskRecord]: ...

    def create_notification(
        self, user_id: str, task_id: str, task_type: TaskType, kind: str, title: str, body: str = ""
    ) -> NotificationRecord: ...

    def list_notifications(
        self, user_id: str, *, limit: int = 20, unread_only: bool = False
    ) -> list[NotificationRecord]: ...

    def unread_notifications(self, user_id: str) -> int: ...

    def mark_notification_read(self, notification_id: str, user_id: str) -> bool: ...

    def mark_all_notifications_read(self, user_id: str) -> int: ...

    def create_knowledge_base(self, name: str, note: str, owner_id: str) -> KnowledgeBaseRecord: ...

    def rename_knowledge_base(self, kb_id: str, owner_id: str, name: str) -> bool: ...

    def list_knowledge_bases(self, owner_id: str) -> list[KnowledgeBaseRecord]: ...

    def get_knowledge_base(self, kb_id: str) -> KnowledgeBaseRecord | None: ...

    def delete_knowledge_base(self, kb_id: str) -> bool: ...

    def add_kb_document(
        self,
        kb_id: str,
        *,
        title: str,
        url: str,
        content: str,
        task_id: str | None = None,
        artifact_name: str | None = None,
        kind: str = "text",
    ) -> KbDocumentRecord: ...

    def list_kb_documents(self, kb_id: str) -> list[KbDocumentRecord]: ...

    def list_task_kb_documents(self, task_id: str, owner_id: str) -> list[tuple[str, str, str]]: ...

    def find_kb_document(
        self, kb_id: str, task_id: str, artifact_name: str
    ) -> KbDocumentRecord | None: ...

    def mark_kb_documents_orphaned(self, task_id: str) -> int: ...

    def get_kb_document(self, document_id: str) -> KbDocumentRecord | None: ...

    def set_kb_chunk_count(self, document_id: str, count: int) -> None: ...

    def update_kb_document_title(self, document_id: str, title: str) -> bool: ...

    def delete_kb_document(self, document_id: str) -> bool: ...

    def add_kb_message(
        self, kb_id: str, question: str, answer: str, citations: list[KbCitation],
        conversation_id: str = "default",
    ) -> KbMessageRecord: ...

    def list_kb_conversations(self, kb_id: str, limit: int = 50) -> list[dict]:
        ...

    def list_kb_messages(
        self, kb_id: str, limit: int = 50, conversation_id: str | None = None
    ) -> list[KbMessageRecord]: ...

    def replace_kb_chunks(self, kb_id: str, document_id: str, chunks: list[dict]) -> int: ...

    def delete_kb_chunks(self, document_id: str) -> None: ...

    def search_kb_chunks(
        self, kb_id: str, query: str, limit: int = 20,
        document_ids: list[str] | None = None, kinds: list[str] | None = None,
    ) -> list[dict]: ...

    def upsert_kb_conversation(
        self, kb_id: str, owner_id: str, conversation_id: str, title: str
    ) -> None: ...

    def rename_kb_conversation(
        self, kb_id: str, owner_id: str, conversation_id: str, title: str
    ) -> bool: ...

    def delete_kb_conversation(
        self, kb_id: str, owner_id: str, conversation_id: str
    ) -> bool: ...


class InMemoryTaskStore:
    """独立测试可显式注入的内存实现。"""

    name = "memory"

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._artifacts: dict[str, Artifact] = {}
        self._messages: dict[str, list[TaskChatMessage]] = {}
        self._schedules: dict[str, ScheduleRecord] = {}
        self._notifications: dict[str, NotificationRecord] = {}
        self._knowledge_bases: dict[str, KnowledgeBaseRecord] = {}
        self._kb_documents: dict[str, KbDocumentRecord] = {}
        self._kb_messages: dict[str, KbMessageRecord] = {}
        self._kb_chunks: dict[str, list[dict]] = {}
        self._kb_conversation_titles: dict[str, dict] = {}
        self._tags: dict[str, object] = {}
        self._task_tags: dict[str, list[str]] = {}
        self._document_tags: dict[str, list[str]] = {}
        self._annotations: dict[str, object] = {}
        # 后台调度线程与 HTTP 查询线程会并发访问，所有读写都经过同一把可重入锁。
        self._lock = threading.RLock()

    def create_task(
        self,
        task_type: TaskType,
        params: dict,
        *,
        attempt: int = 1,
        retry_of: str | None = None,
        owner_id: str | None = None,
        dedupe_key: str | None = None,
        dedupe_since: datetime | None = None,
    ) -> TaskRecord:
        # 重试任务使用新的任务 ID，并通过 attempt/retry_of 保留来源关系。
        task = TaskRecord(
            task_type=task_type,
            params=params,
            attempt=attempt,
            retry_of=retry_of,
            owner_id=owner_id,
            dedupe_key=dedupe_key,
        )
        with self._lock:
            if dedupe_key and dedupe_since is not None:
                existing = self.find_by_dedupe(dedupe_key, dedupe_since, owner_id)
                if existing is not None:
                    return existing.model_copy(update={"deduplicated": True})
            self._tasks[task.id] = task
        return task.model_copy(deep=True)

    def find_by_dedupe(
        self, dedupe_key: str, since: datetime, owner_id: str | None = None
    ) -> TaskRecord | None:
        """返回窗口内同键的最近任务；失败任务不参与去重，允许用户重跑。"""
        if not dedupe_key:
            return None
        with self._lock:
            matches = [
                task
                for task in self._tasks.values()
                if task.dedupe_key == dedupe_key
                and task.owner_id == owner_id
                and task.created_at >= since
                and task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.TIMEOUT}
            ]
        if not matches:
            return None
        latest = max(matches, key=lambda item: item.created_at)
        return latest.model_copy(deep=True)

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
        task_types: list[str] | None = None,
        page: int = 1,
        page_size: int = 20,
        owner_id: str | None = None,
    ) -> tuple[list[TaskRecord], int]:
        """按筛选条件返回当前页和命中总数；搜索覆盖任务 ID、类型、参数和错误信息。"""
        with self._lock:
            matched = [
                task
                for task in self._tasks.values()
                if (owner_id is None or task.owner_id == owner_id)
                and self._matches(task, query, statuses, task_type)
                and (not task_types or task.task_type.value in task_types)
            ]
            matched.sort(key=lambda item: item.created_at, reverse=True)
            start = (page - 1) * page_size
            visible = matched[start : start + page_size]
            return [task.model_copy(deep=True) for task in visible], len(matched)

    def count_tasks(
  self,
  owner_id: str | None = None,
  task_type: str | None = None,
  task_types: list[str] | None = None,
  ) -> dict[str, int]:
        """统计任务状态分布，供列表页概览使用；普通用户只统计自己的任务。"""
        with self._lock:
            tasks = [
                task
                for task in self._tasks.values()
                if (owner_id is None or task.owner_id == owner_id)
                and (task_type is None or task.task_type.value == task_type)
                and (not task_types or task.task_type.value in task_types)
            ]
            counts = {status.value: 0 for status in TaskStatus}
            counts["total"] = len(tasks)
            for task in tasks:
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

    def purge_tasks(
        self, statuses: list[TaskStatus], older_than: datetime, owner_id: str | None = None
    ) -> list[str]:
        """删除指定状态且创建时间早于截止时间的任务，返回被删除的任务 ID。"""
        allowed = set(statuses)
        with self._lock:
            targets = [
                task.id
                for task in self._tasks.values()
                if task.status in allowed
                and task.created_at < older_than
                and (owner_id is None or task.owner_id == owner_id)
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

    def reset_task_for_retry(self, task_id: str, attempt: int) -> TaskRecord:
        """把同一条任务重置为等待状态，用于原地重跑。"""
        with self._lock:
            task = self._tasks[task_id]
            updated = task.model_copy(
                update={
                    "status": TaskStatus.PENDING,
                    "attempt": attempt,
                    "retry_of": None,
                    "result": None,
                    "error_code": None,
                    "error_message": None,
                    "started_at": None,
                    "finished_at": None,
                    "steps": [],
                    "artifacts": [],
                },
                deep=True,
            )
            self._tasks[task_id] = updated
            self._artifacts = {
                artifact_id: artifact
                for artifact_id, artifact in self._artifacts.items()
                if artifact.task_id != task_id
            }
            return updated.model_copy(deep=True)

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

    def add_task_message(self, task_id: str, role: str, content: str) -> TaskChatMessage:
        message = TaskChatMessage(
            id=uuid.uuid4().hex,
            task_id=task_id,
            role=role,
            content=content,
            created_at=utc_now(),
        )
        with self._lock:
            self._messages.setdefault(task_id, []).append(message.model_copy(deep=True))
        return message

    def list_task_messages(self, task_id: str, limit: int = 50) -> list[TaskChatMessage]:
        with self._lock:
            items = self._messages.get(task_id, [])[-limit:]
            return [item.model_copy(deep=True) for item in items]

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

    def create_schedule(
        self,
        task_type: TaskType,
        params: dict,
        cron: str,
        next_run_at: datetime,
        *,
        owner_id: str | None = None,
        name: str = "",
    ) -> ScheduleRecord:
        record = ScheduleRecord(
            name=name,
            task_type=task_type,
            params=params,
            cron=cron,
            owner_id=owner_id,
            next_run_at=next_run_at,
        )
        with self._lock:
            self._schedules[record.id] = record
            return record.model_copy(deep=True)

    def list_schedules(self, owner_id: str | None = None) -> list[ScheduleRecord]:
        with self._lock:
            rows = [
                item
                for item in self._schedules.values()
                if owner_id is None or item.owner_id == owner_id
            ]
        rows.sort(key=lambda item: item.next_run_at)
        return [item.model_copy(deep=True) for item in rows]

    def set_schedule_enabled(
        self, schedule_id: str, enabled: bool, next_run_at: datetime | None = None
    ) -> ScheduleRecord | None:
        with self._lock:
            record = self._schedules.get(schedule_id)
            if record is None:
                return None
            record.enabled = enabled
            if next_run_at is not None:
                record.next_run_at = next_run_at
            return record.model_copy(deep=True)

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            return self._schedules.pop(schedule_id, None) is not None

    def due_schedules(self, now: datetime, limit: int = 5) -> list[ScheduleRecord]:
        with self._lock:
            rows = [
                item.model_copy(deep=True)
                for item in self._schedules.values()
                if item.enabled and item.next_run_at <= now
            ]
        rows.sort(key=lambda item: item.next_run_at)
        return rows[:limit]

    def mark_schedule_run(
        self, schedule_id: str, ran_at: datetime, next_run_at: datetime, task_id: str
    ) -> None:
        with self._lock:
            record = self._schedules.get(schedule_id)
            if record is None:
                return
            record.last_run_at = ran_at
            record.last_task_id = task_id
            record.next_run_at = next_run_at

    def schedule_runs(self, schedule_id: str, limit: int = 20) -> list[TaskRecord]:
        with self._lock:
            rows = [
                item.model_copy(deep=True)
                for item in self._tasks.values()
                if item.params.get("_schedule_id") == schedule_id
            ]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows[:limit]

    def create_notification(
        self, user_id: str, task_id: str, task_type: TaskType, kind: str, title: str, body: str = ""
    ) -> NotificationRecord:
        record = NotificationRecord(
            user_id=user_id, task_id=task_id, task_type=task_type,
            kind=kind, title=title, body=body,
        )
        with self._lock:
            self._notifications[record.id] = record
        return record

    def list_notifications(
        self, user_id: str, *, limit: int = 20, unread_only: bool = False
    ) -> list[NotificationRecord]:
        with self._lock:
            rows = [
                item.model_copy(deep=True)
                for item in self._notifications.values()
                if item.user_id == user_id and (not unread_only or item.read_at is None)
            ]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows[:limit]

    def unread_notifications(self, user_id: str) -> int:
        with self._lock:
            return sum(1 for item in self._notifications.values()
                       if item.user_id == user_id and item.read_at is None)

    def mark_notification_read(self, notification_id: str, user_id: str) -> bool:
        with self._lock:
            record = self._notifications.get(notification_id)
            if record is None or record.user_id != user_id:
                return False
            if record.read_at is None:
                record.read_at = utc_now()
            return True

    def mark_all_notifications_read(self, user_id: str) -> int:
        changed = 0
        with self._lock:
            for item in self._notifications.values():
                if item.user_id == user_id and item.read_at is None:
                    item.read_at = utc_now()
                    changed += 1
        return changed
    def get_schedule(self, schedule_id: str) -> ScheduleRecord | None:
        with self._lock:
            record = self._schedules.get(schedule_id)
            return record.model_copy(deep=True) if record is not None else None
    def update_schedule(
        self, schedule_id, *, name, task_type, params, cron, next_run_at
    ):
        with self._lock:
            record = self._schedules.get(schedule_id)
            if record is None:
                return None
            record.name = name
            record.task_type = task_type
            record.params = params
            record.cron = cron
            record.next_run_at = next_run_at
            return record.model_copy(deep=True)

    # ---------- 知识库（私有，按 owner_id 隔离） ----------

    def create_knowledge_base(self, name, note, owner_id):
        record = KnowledgeBaseRecord(name=name, note=note, owner_id=owner_id)
        with self._lock:
            self._knowledge_bases[record.id] = record
            return record.model_copy(deep=True)

    def rename_knowledge_base(self, kb_id, owner_id, name):
        with self._lock:
            record = self._knowledge_bases.get(kb_id)
            if record is None or record.owner_id != owner_id:
                return False
            record.name = name
            return True

    def list_knowledge_bases(self, owner_id):
        with self._lock:
            items = [kb for kb in self._knowledge_bases.values() if kb.owner_id == owner_id]
            return [kb.model_copy(deep=True) for kb in sorted(items, key=lambda kb: kb.created_at, reverse=True)]

    def get_knowledge_base(self, kb_id):
        with self._lock:
            record = self._knowledge_bases.get(kb_id)
            return record.model_copy(deep=True) if record is not None else None

    def delete_knowledge_base(self, kb_id):
        with self._lock:
            if kb_id not in self._knowledge_bases:
                return False
            self._knowledge_bases.pop(kb_id, None)
            for doc_id in [d.id for d in self._kb_documents.values() if d.kb_id == kb_id]:
                self._kb_documents.pop(doc_id, None)
            for msg_id in [m.id for m in self._kb_messages.values() if m.kb_id == kb_id]:
                self._kb_messages.pop(msg_id, None)
            return True

    def add_kb_document(self, kb_id, *, title, url, content, task_id=None, artifact_name=None, kind="text"):
        record = KbDocumentRecord(
            kb_id=kb_id, title=title, url=url, content=content, task_id=task_id,
            artifact_name=artifact_name, kind=kind,
        )
        with self._lock:
            self._kb_documents[record.id] = record
            return record.model_copy(deep=True)

    def find_kb_document(self, kb_id, task_id, artifact_name):
        with self._lock:
            for record in self._kb_documents.values():
                if (
                    record.kb_id == kb_id and record.task_id == task_id
                    and record.artifact_name == artifact_name
                ):
                    return record.model_copy(deep=True)
        return None

    def mark_kb_documents_orphaned(self, task_id):
        count = 0
        with self._lock:
            for key, record in list(self._kb_documents.items()):
                if record.task_id == task_id:
                    self._kb_documents[key] = record.model_copy(update={"orphaned": True})
                    count += 1
        return count

    def list_kb_documents(self, kb_id):
        with self._lock:
            items = [d for d in self._kb_documents.values() if d.kb_id == kb_id]
            return [d.model_copy(deep=True) for d in sorted(items, key=lambda d: d.created_at, reverse=True)]

    def list_task_kb_documents(self, task_id, owner_id):
        with self._lock:
            rows = []
            for doc in self._kb_documents.values():
                if doc.task_id != task_id:
                    continue
                kb = self._knowledge_bases.get(doc.kb_id)
                if kb is None or kb.owner_id != owner_id:
                    continue
                rows.append((doc.kb_id, kb.name, doc.id))
            return rows

    def get_kb_document(self, document_id):
        with self._lock:
            record = self._kb_documents.get(document_id)
            return record.model_copy(deep=True) if record is not None else None

    def set_kb_chunk_count(self, document_id, count):
        with self._lock:
            record = self._kb_documents.get(document_id)
            if record is not None:
                record.chunk_count = count

    def update_kb_document_title(self, document_id, title):
        with self._lock:
            record = self._kb_documents.get(document_id)
            if record is None:
                return False
            record.title = title
            return True

    def delete_kb_document(self, document_id):
        with self._lock:
            return self._kb_documents.pop(document_id, None) is not None

    def add_kb_message(self, kb_id, question, answer, citations, conversation_id="default"):
        record = KbMessageRecord(
            kb_id=kb_id,
            question=question,
            answer=answer,
            citations=list(citations),
            conversation_id=conversation_id or "default",
        )
        with self._lock:
            self._kb_messages[record.id] = record
            return record.model_copy(deep=True)

    def replace_kb_chunks(self, kb_id, document_id, chunks):
        with self._lock:
            self._kb_chunks[document_id] = [
                {"heading": item.get("heading", ""), "text": item["text"]} for item in chunks
            ]
            return len(chunks)

    def delete_kb_chunks(self, document_id):
        with self._lock:
            self._kb_chunks.pop(document_id, None)

    def search_kb_chunks(self, kb_id, query, limit=20, document_ids=None, kinds=None):
        import re
        tokens = [token for token in re.findall(r"\w+", query) if len(token) > 1][:8]
        if not tokens:
            return []
        hits = []
        with self._lock:
            for document_id, chunks in self._kb_chunks.items():
                doc = self._kb_documents.get(document_id)
                if doc is None or doc.kb_id != kb_id:
                    continue
                if document_ids and document_id not in document_ids:
                    continue
                if kinds and doc.kind not in kinds:
                    continue
                for chunk in chunks:
                    haystack = f"{chunk['heading']} {chunk['text']}".lower()
                    score = sum(haystack.count(token.lower()) for token in tokens)
                    if score:
                        hits.append({
                            "text": chunk["text"], "heading": chunk["heading"],
                            "document_id": document_id, "title": doc.title, "url": doc.url,
                            "rank": float(score),
                        })
        hits.sort(key=lambda item: item["rank"], reverse=True)
        return hits[:limit]

    def upsert_kb_conversation(self, kb_id, owner_id, conversation_id, title):
        with self._lock:
            self._kb_conversation_titles[conversation_id] = {
                "kb_id": kb_id, "owner_id": owner_id, "title": (title or "新对话")[:80],
                "updated_at": utc_now(),
            }

    def rename_kb_conversation(self, kb_id, owner_id, conversation_id, title):
        with self._lock:
            row = self._kb_conversation_titles.get(conversation_id)
            if not row or row["kb_id"] != kb_id or row["owner_id"] != owner_id:
                return False
            row["title"] = (title or "新对话")[:80]
            row["updated_at"] = utc_now()
            return True

    def delete_kb_conversation(self, kb_id, owner_id, conversation_id):
        with self._lock:
            row = self._kb_conversation_titles.get(conversation_id)
            if not row or row["kb_id"] != kb_id or row["owner_id"] != owner_id:
                return False
            self._kb_conversation_titles.pop(conversation_id, None)
            for key in [k for k, m in self._kb_messages.items() if m.kb_id == kb_id and m.conversation_id == conversation_id]:
                self._kb_messages.pop(key, None)
            return True

    def list_kb_conversations(self, kb_id, limit=50):
        groups = {}
        for msg in self._kb_messages.values():
            if msg.kb_id != kb_id:
                continue
            item = groups.setdefault(msg.conversation_id, {"id": msg.conversation_id, "title": msg.question, "count": 0, "updated_at": msg.created_at})
            item["count"] += 1
            if msg.created_at >= item["updated_at"]:
                item["updated_at"] = msg.created_at
            if msg.created_at <= item.get("_first", msg.created_at):
                item["_first"] = msg.created_at
                item["title"] = msg.question
        for cid, info in self._kb_conversation_titles.items():
            if info["kb_id"] != kb_id:
                continue
            item = groups.setdefault(cid, {"id": cid, "title": info["title"], "count": 0, "updated_at": info["updated_at"]})
            item["title"] = info["title"]
            if item["count"] == 0:
                item["updated_at"] = info["updated_at"]
        rows = sorted(groups.values(), key=lambda item: item["updated_at"], reverse=True)
        for row in rows:
            row.pop("_first", None)
        return rows[:limit]

    def list_kb_messages(self, kb_id, limit=50, conversation_id=None):
        with self._lock:
            items = [m for m in self._kb_messages.values() if m.kb_id == kb_id]
            if conversation_id:
                items = [m for m in items if m.conversation_id == conversation_id]
            items.sort(key=lambda m: m.created_at, reverse=True)
            return [m.model_copy(deep=True) for m in items[:limit]]

    # ---------- 标签与批注（内存实现） ----------

    def create_tag(self, owner_id, name):
        from backend.models import TagRecord

        with self._lock:
            for tag in self._tags.values():
                if tag.owner_id == owner_id and tag.name == name:
                    return tag
            tag = TagRecord(owner_id=owner_id, name=name[:24])
            self._tags[tag.id] = tag
            return tag

    def list_tags(self, owner_id):
        with self._lock:
            return sorted([t for t in self._tags.values() if t.owner_id == owner_id], key=lambda t: t.name)

    def set_task_tags(self, task_id, owner_id, names):
        with self._lock:
            cleaned = list(dict.fromkeys([n.strip()[:24] for n in names if n.strip()]))[:8]
            for name in cleaned:
                self.create_tag(owner_id, name)
            self._task_tags[task_id] = cleaned
            return list(cleaned)

    def get_task_tags(self, task_id):
        with self._lock:
            return list(self._task_tags.get(task_id, []))

    def set_document_tags(self, document_id, owner_id, names):
        with self._lock:
            cleaned = list(dict.fromkeys([n.strip()[:24] for n in names if n.strip()]))[:8]
            for name in cleaned:
                self.create_tag(owner_id, name)
            self._document_tags[document_id] = cleaned
            return list(cleaned)

    def get_document_tags(self, document_id):
        with self._lock:
            return list(self._document_tags.get(document_id, []))

    def add_annotation(self, owner_id, *, task_id=None, document_id=None, artifact_name="article.md",
                       block_index=0, start_offset=0, end_offset=0, quote="", note=""):
        from backend.models import AnnotationRecord

        with self._lock:
            record = AnnotationRecord(
                owner_id=owner_id, task_id=task_id, document_id=document_id, artifact_name=artifact_name,
                block_index=block_index, start_offset=start_offset, end_offset=end_offset, quote=quote, note=note,
            )
            self._annotations[record.id] = record
            return record

    def list_annotations(self, *, task_id=None, document_id=None, owner_id=None):
        with self._lock:
            items = list(self._annotations.values())
            if task_id:
                items = [a for a in items if a.task_id == task_id]
            if document_id:
                items = [a for a in items if a.document_id == document_id]
            if owner_id:
                items = [a for a in items if a.owner_id == owner_id]
            return sorted(items, key=lambda a: a.created_at)

    def update_annotation(self, annotation_id, owner_id, note):
        with self._lock:
            item = self._annotations.get(annotation_id)
            if not item or item.owner_id != owner_id:
                return None
            updated = item.model_copy(update={"note": note})
            self._annotations[annotation_id] = updated
            return updated

    def delete_annotation(self, annotation_id, owner_id):
        with self._lock:
            item = self._annotations.get(annotation_id)
            if not item or item.owner_id != owner_id:
                return False
            self._annotations.pop(annotation_id, None)
            return True
