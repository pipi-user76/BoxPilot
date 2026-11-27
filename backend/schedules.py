"""定时任务调度：用 APScheduler 持久化 cron，并保留手动触发入口。"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

import tzlocal
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.cron import next_after, parse_cron
from backend.db import TaskStore
from backend.models import (
    ScheduleCreate,
    ScheduleRecord,
    TaskRecord,
    TaskType,
    utc_now,
)

# APScheduler 只序列化函数的模块路径，由全局引用指向当前进程的调度器。
_ACTIVE: "ScheduleRunner | None" = None


def _fire(schedule_id: str) -> None:
    runner = _ACTIVE
    if runner is not None:
        runner.fire(schedule_id)


def next_run_for(expression: str, now: datetime | None = None) -> datetime:
    """按五段 cron 计算下一次执行时间，按本机时区解释，统一返回 UTC。"""
    zone = tzlocal.get_localzone()
    return next_after(parse_cron(expression), now or utc_now(), zone).astimezone(timezone.utc)


class ScheduleRunner:
    """把定时任务写进 APScheduler 的持久化作业库，进程重启后自动恢复。"""

    def __init__(
        self,
        store: TaskStore,
        task_runner,
        poll_seconds: int | None = None,
        clock=utc_now,
        max_per_tick: int | None = None,
    ) -> None:
        self.store = store
        self.task_runner = task_runner
        self.clock = clock
        self.max_per_tick = max_per_tick or 5
        self._started = False
        jobstores = {}
        path = getattr(store, "path", None)
        if path is not None:
            jobstores["default"] = SQLAlchemyJobStore(url=f"sqlite:///{path}")
        self.scheduler = BackgroundScheduler(
            jobstores=jobstores,
            timezone=tzlocal.get_localzone(),
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
        )

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        global _ACTIVE
        _ACTIVE = self
        if not self._started:
            self.scheduler.start()
            self._started = True
        for record in self.store.list_schedules():
            self.sync(record)

    def stop(self) -> None:
        global _ACTIVE
        if self._started:
            self.scheduler.shutdown(wait=False)
            for jobstore in self.scheduler._jobstores.values():
                engine = getattr(jobstore, "engine", None)
                if engine is not None:
                    engine.dispose()
            self._started = False
        if _ACTIVE is self:
            _ACTIVE = None

    # -- 作业同步 ---------------------------------------------------------
    def sync(self, record: ScheduleRecord) -> None:
        """按记录状态新增、更新或移除 APScheduler 作业。"""
        try:
            self.scheduler.remove_job(record.id)
        except Exception:
            pass
        if not record.enabled:
            return
        trigger = CronTrigger.from_crontab(
            record.cron, timezone=tzlocal.get_localzone()
        )
        job = self.scheduler.add_job(
            _fire,
            trigger=trigger,
            args=[record.id],
            id=record.id,
            replace_existing=True,
        )
        if job.next_run_time is not None:
            self.store.set_schedule_enabled(record.id, True, job.next_run_time)

    def remove(self, schedule_id: str) -> None:
        try:
            self.scheduler.remove_job(schedule_id)
        except Exception:
            pass

    def next_run(self, record: ScheduleRecord) -> datetime:
        job = self.scheduler.get_job(record.id) if self._started else None
        if job is not None and job.next_run_time is not None:
            return job.next_run_time
        return next_run_for(record.cron, self.clock())

    # -- 触发 -------------------------------------------------------------
    def fire(self, schedule_id: str) -> TaskRecord | None:
        record = self.store.get_schedule(schedule_id)
        if record is None:
            return None
        return self._create_task(record)

    def tick(self, now: datetime) -> list[ScheduleRecord]:
        """不依赖 APScheduler 的兜底触发，也便于单元测试推进时间。"""
        fired: list[ScheduleRecord] = []
        for record in self.store.due_schedules(now, limit=self.max_per_tick):
            task = self._create_task(record, now)
            if task is not None:
                fired.append(record)
        return fired

    def _create_task(
        self, record: ScheduleRecord, now: datetime | None = None
    ) -> TaskRecord | None:
        moment = now or self.clock()
        params = dict(record.params)
        params["_schedule_id"] = record.id
        task = self.store.create_task(
            record.task_type, params, owner_id=record.owner_id
        )
        if task is None:
            return None
        upcoming = next_run_for(record.cron, moment)
        self.store.mark_schedule_run(record.id, moment, upcoming, task.id)
        threading.Thread(
            target=self.task_runner.run, args=(task.id,), daemon=True
        ).start()
        return task

    # -- 供接口调用 -------------------------------------------------------
    def create(self, payload: ScheduleCreate, owner_id: str | None) -> ScheduleRecord:
        now = self.clock()
        record = self.store.create_schedule(
            payload.task_type,
            payload.params,
            payload.cron,
            next_run_for(payload.cron, now),
            owner_id=owner_id,
            name=payload.name,
        )
        if self._started:
            self.sync(record)
        return record
