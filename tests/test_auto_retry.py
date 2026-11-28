"""Automatic retry tests for infrastructure failures."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.db import InMemoryTaskStore
from backend.models import TaskStatus, TaskType
from backend.scheduler import TaskRunner
from backend.storage import ArtifactStore
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome


class FakeSandbox:
    def ensure_task_workspace(self, task_id):
        return None

    def task_workdir(self, task_id):
        return f"/tmp/{task_id}"


class FlakyTemplate:
    name = "flaky"

    def __init__(self, code="SANDBOX_UNAVAILABLE", fail_times=1):
        self.code = code
        self.fail_times = fail_times
        self.calls = 0

    @property
    def steps(self):
        return [StepDefinition("唯一一步", self.run)]

    def run(self, context):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise StepExecutionError(self.code, "沙箱暂时不可用")
        return StepOutcome(output="done")


class AutoRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = InMemoryTaskStore()
        self.artifacts = ArtifactStore(self.tmp / "artifacts", 1024 * 1024)
        self.runner = TaskRunner(self.store, self.artifacts, FakeSandbox())

    def test_infrastructure_failure_retries_once(self):
        template = FlakyTemplate()
        self.runner.register(TaskType.MOCK, template)
        task = self.store.create_task(TaskType.MOCK, {}, owner_id="u1")
        self.runner.run(task.id)

        tasks, _ = self.store.list_tasks()
        records = [item for item in tasks if item.task_type == TaskType.MOCK]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.id, task.id)
        self.assertEqual(record.status, TaskStatus.SUCCESS)
        self.assertEqual(record.attempt, 2)
        self.assertEqual(record.retry_of, None)
        self.assertEqual(template.calls, 2)

    def test_business_failure_is_not_retried(self):
        template = FlakyTemplate(code="VALIDATION_ERROR", fail_times=1)
        self.runner.register(TaskType.MOCK, template)
        task = self.store.create_task(TaskType.MOCK, {}, owner_id="u1")
        self.runner.run(task.id)

        items, _ = self.store.list_tasks()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
