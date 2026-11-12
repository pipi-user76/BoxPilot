"""文件职责：覆盖任务取消、失败重试、分页搜索、批量清理和产物预览。

这些用例直接检查新增的进阶能力，不依赖真实沙箱：模板替身只负责模拟成功、失败
和阻塞步骤，便于稳定地验证状态机与接口契约。
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import settings
from backend.logging_config import JsonFormatter
from backend.main import create_app
from backend.models import Artifact, TaskStatus, TaskType
from backend.storage import ArtifactStore
from backend.templates.base import StepDefinition, StepExecutionError

try:
    from tests.support import make_temp_dir, remove_temp_dir
except ImportError:  # 兼容以 tests 目录为顶层的 unittest discover
    from support import make_temp_dir, remove_temp_dir


class StubSandbox:
    """满足模板依赖的最小沙箱替身；本文件的模板不会真正调用它。"""

    def ensure_task_workspace(self, task_id: str) -> dict:
        return {"success": True, "data": {"path": f"/home/gem/boxpilot/{task_id}"}, "error": None}

    def task_workdir(self, task_id: str) -> str:
        return f"/home/gem/boxpilot/{task_id}"

    def exec_shell(self, command: str, task_id: str, timeout_s: int = 120, check_exit: bool = True) -> dict:
        return {"success": True, "data": {"output": "ok", "exit_code": 0}, "error": None}

    def health(self) -> dict:
        return {"success": True, "data": {"base_url": "fake"}, "error": None}


class RecordingTemplate:
    """三步模板；第一步可阻塞，用来制造“运行中取消”的窗口。"""

    name = "MOCK"

    def __init__(self, started: threading.Event | None = None, release: threading.Event | None = None):
        self.executed: list[str] = []
        self.started = started
        self.release = release
        self.steps = (
            StepDefinition("第一步", self._first),
            StepDefinition("第二步", self._second),
            StepDefinition("第三步", self._third),
        )

    def _first(self, context):
        self.executed.append("第一步")
        if self.started is not None:
            self.started.set()
        if self.release is not None and not self.release.wait(timeout=5):
            raise AssertionError("测试未释放阻塞步骤")
        return StepOutcomeStub("第一步完成")

    def _second(self, context):
        self.executed.append("第二步")
        return StepOutcomeStub("第二步完成")

    def _third(self, context):
        self.executed.append("第三步")
        return StepOutcomeStub("第三步完成")


class FailingTemplate:
    """固定失败模板，用于验证重试只能针对已结束的失败任务。"""

    name = "MOCK"

    def __init__(self) -> None:
        self.steps = (StepDefinition("必失败步骤", self._fail),)

    def _fail(self, context):
        raise StepExecutionError("DEMO_FAILURE", "演示失败")


class HangingTemplate:
    """步骤故意卡住，用于验证调度器的超时兜底。"""

    name = "MOCK"

    def __init__(self) -> None:
        self.steps = (StepDefinition("卡住步骤", self._hang, timeout_seconds=1),)

    def _hang(self, context):
        time.sleep(5)
        return StepOutcomeStub("迟到一步")


class StepOutcomeStub:
    """轻量步骤结果，字段与 StepOutcome 保持一致。"""

    def __init__(self, output: str):
        self.output = output
        self.artifacts = []
        self.result = {}


class AdvancedApiTests(unittest.TestCase):
    def make_client(self, templates, sandbox: StubSandbox | None = None) -> TestClient:
        self.temp_dir = make_temp_dir()
        self.addCleanup(remove_temp_dir, self.temp_dir)
        self.artifact_store = ArtifactStore(self.temp_dir)
        self.app = create_app(
            artifact_store=self.artifact_store,
            adapter=sandbox or StubSandbox(),
            templates=templates,
        )
        context = TestClient(self.app)
        self.addCleanup(context.__exit__, None, None, None)
        self.client = context.__enter__()
        return self.client

    def test_cancel_pending_task_marks_it_cancelled(self) -> None:
        """等待中的任务可以立即取消，不需要启动执行线程。"""
        self.make_client([(TaskType.MOCK, RecordingTemplate())])
        task = self.app.state.task_store.create_task(TaskType.MOCK, {})

        response = self.client.post(f"/tasks/{task.id}/cancel")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "CANCELLED")
        self.assertEqual(response.json()["error_code"], "TASK_CANCELLED")
        self.assertEqual(self.client.get(f"/tasks/{task.id}").json()["status"], "CANCELLED")

    def test_cancel_running_task_stops_before_next_step(self) -> None:
        """运行中任务在步骤边界停止，已完成的步骤和产物保留。"""
        started = threading.Event()
        release = threading.Event()
        template = RecordingTemplate(started=started, release=release)
        self.make_client([(TaskType.MOCK, template)])
        task = self.app.state.task_store.create_task(TaskType.MOCK, {})
        worker = threading.Thread(target=self.app.state.task_runner.run, args=(task.id,))
        worker.start()
        self.assertTrue(started.wait(5), "第一步没有按预期开始")

        response = self.client.post(f"/tasks/{task.id}/cancel")
        release.set()
        worker.join(5)

        self.assertEqual(response.status_code, 202)
        final = self.client.get(f"/tasks/{task.id}").json()
        self.assertEqual(final["status"], "CANCELLED")
        self.assertEqual(final["result"]["completed_steps"], ["第一步"])
        self.assertEqual(template.executed, ["第一步"])

    def test_hanging_step_times_out_instead_of_running_forever(self) -> None:
        """步骤卡死时按超时收尾，任务不会永远停在运行中。"""
        self.make_client([(TaskType.MOCK, HangingTemplate())])

        created = self.client.post("/tasks", json={"task_type": "MOCK", "params": {}}).json()
        task = self.client.get(f"/tasks/{created['id']}").json()

        self.assertEqual(task["status"], "TIMEOUT")
        self.assertEqual(task["error_code"], "STEP_TIMEOUT")
        self.assertEqual(task["steps"][0]["error_code"], "STEP_TIMEOUT")
        self.assertIn("已停止等待", task["error_message"])

    def test_cancel_finished_task_returns_conflict(self) -> None:
        """已结束任务不能再取消，返回 409。"""
        self.make_client([(TaskType.MOCK, RecordingTemplate())])
        task = self.app.state.task_store.create_task(TaskType.MOCK, {})
        self.app.state.task_store.finish_task(task.id, TaskStatus.SUCCESS, {}, None, None)

        response = self.client.post(f"/tasks/{task.id}/cancel")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "TASK_NOT_CANCELLABLE")

    def test_retry_failed_task_creates_new_attempt(self) -> None:
        """重试生成新任务并保留 attempt/retry_of 关系，原记录不覆盖。"""
        self.make_client([(TaskType.MOCK, FailingTemplate())])
        created = self.client.post("/tasks", json={"task_type": "MOCK", "params": {}}).json()
        self.assertEqual(self.client.get(f"/tasks/{created['id']}").json()["status"], "FAILED")

        response = self.client.post(f"/tasks/{created['id']}/retry")

        self.assertEqual(response.status_code, 202)
        retried = response.json()
        self.assertNotEqual(retried["id"], created["id"])
        self.assertEqual(retried["attempt"], 2)
        self.assertEqual(retried["retry_of"], created["id"])
        self.assertEqual(self.client.get(f"/tasks/{created['id']}").json()["status"], "FAILED")
        self.assertEqual(self.client.get(f"/tasks/{retried['id']}").json()["status"], "FAILED")

    def test_retry_successful_task_returns_conflict(self) -> None:
        """成功任务不允许重试。"""
        self.make_client([(TaskType.MOCK, RecordingTemplate())])
        created = self.client.post("/tasks", json={"task_type": "MOCK", "params": {}}).json()
        self.assertEqual(self.client.get(f"/tasks/{created['id']}").json()["status"], "SUCCESS")

        response = self.client.post(f"/tasks/{created['id']}/retry")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "TASK_NOT_RETRYABLE")

    def test_retry_csv_task_without_input_returns_conflict(self) -> None:
        """输入文件已被清理时，不应创建注定失败的重试任务。"""
        self.make_client([(TaskType.MOCK, RecordingTemplate())])
        store = self.app.state.task_store
        task = store.create_task(TaskType.CSV_ANALYSIS, {})
        store.finish_task(task.id, TaskStatus.FAILED, None, "DEMO_FAILURE", "演示失败")

        response = self.client.post(f"/tasks/{task.id}/retry")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "INPUT_NOT_FOUND")
        self.assertEqual(store.count_tasks()["total"], 1)

    def test_task_list_supports_pagination_search_and_counts(self) -> None:
        """列表返回分页元数据，搜索和状态筛选互不影响全量统计。"""
        self.make_client([(TaskType.MOCK, RecordingTemplate())])
        store = self.app.state.task_store
        for index in range(3):
            store.create_task(TaskType.MOCK, {"index": index})
        done = store.create_task(TaskType.WEB_DIGEST, {"url": "https://example.com"})
        store.finish_task(done.id, TaskStatus.SUCCESS, {}, None, None)

        first = self.client.get("/tasks", params={"page": 1, "page_size": 2}).json()
        self.assertEqual(first["total"], 4)
        self.assertEqual(first["pages"], 2)
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(first["counts"]["total"], 4)
        self.assertEqual(first["counts"]["SUCCESS"], 1)

        searched = self.client.get("/tasks", params={"q": "WEB_DIGEST"}).json()
        self.assertEqual(searched["total"], 1)
        self.assertEqual(searched["items"][0]["task_type"], "WEB_DIGEST")

        filtered = self.client.get("/tasks", params={"status": "PENDING,SUCCESS"}).json()
        self.assertEqual(filtered["total"], 4)
        self.assertEqual(filtered["counts"]["total"], 4)

        invalid = self.client.get("/tasks", params={"status": "NOPE"})
        self.assertEqual(invalid.status_code, 422)

    def test_batch_delete_skips_active_tasks_and_removes_artifacts(self) -> None:
        """批量清理只删除终态任务，并同步删除产物目录。"""
        self.make_client([(TaskType.MOCK, RecordingTemplate())])
        store = self.app.state.task_store
        done = store.create_task(TaskType.MOCK, {})
        store.finish_task(done.id, TaskStatus.SUCCESS, {}, None, None)
        self.artifact_store.write_text(done.id, "result.txt", "ok")
        pending = store.create_task(TaskType.MOCK, {})

        response = self.client.post("/tasks/batch-delete", json={"task_ids": [done.id, pending.id, "missing"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted"], 1)
        self.assertEqual(response.json()["skipped"], [pending.id, "missing"])
        self.assertIsNone(store.get_task(done.id))
        self.assertFalse((self.temp_dir / done.id).exists())

    def test_cleanup_rejects_active_statuses(self) -> None:
        """清理接口不允许传入 PENDING/RUNNING，避免误删正在执行的任务。"""
        self.make_client([(TaskType.MOCK, RecordingTemplate())])

        response = self.client.post("/tasks/cleanup", json={"statuses": ["RUNNING"], "older_than_days": 0})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "VALIDATION_ERROR")

    def test_preview_text_image_and_binary(self) -> None:
        """文本返回内容，图片返回预览地址，二进制只提示下载。"""
        self.make_client([(TaskType.MOCK, RecordingTemplate())])
        store = self.app.state.task_store
        task = store.create_task(TaskType.MOCK, {})
        store.finish_task(task.id, TaskStatus.SUCCESS, {}, None, None)
        text_spec = self.artifact_store.write_text(task.id, "report.txt", "第一行\n第二行")
        image_spec = self.artifact_store.write_bytes(task.id, "shot.png", b"\x89PNG\r\n\x1a\nfake", "image/png")
        binary_spec = self.artifact_store.write_bytes(task.id, "data.bin", b"\x00\x01\x02", "application/octet-stream")
        ids = {}
        for spec, content_type in (
            (text_spec, "text/plain; charset=utf-8"),
            (image_spec, "image/png"),
            (binary_spec, "application/octet-stream"),
        ):
            artifact = store.add_artifact(
                Artifact(
                    task_id=task.id,
                    filename=spec.filename,
                    relative_path=spec.filename,
                    content_type=content_type,
                    size_bytes=spec.size_bytes,
                )
            )
            ids[spec.filename] = artifact.id

        text = self.client.get(f"/artifacts/{ids['report.txt']}/preview").json()
        self.assertEqual(text["kind"], "text")
        self.assertIn("第二行", text["text"])
        self.assertFalse(text["truncated"])

        image = self.client.get(f"/artifacts/{ids['shot.png']}/preview").json()
        self.assertEqual(image["kind"], "image")
        self.assertIsNone(image["text"])
        self.assertIn("inline=1", image["url"])

        binary = self.client.get(f"/artifacts/{ids['data.bin']}/preview").json()
        self.assertEqual(binary["kind"], "binary")

    def test_inline_download_uses_inline_disposition(self) -> None:
        """inline=1 供图片预览使用，默认下载仍为附件。"""
        self.make_client([(TaskType.MOCK, RecordingTemplate())])
        store = self.app.state.task_store
        task = store.create_task(TaskType.MOCK, {})
        spec = self.artifact_store.write_bytes(task.id, "shot.png", b"\x89PNG\r\n\x1a\nfake", "image/png")
        artifact = store.add_artifact(
            Artifact(
                task_id=task.id,
                filename=spec.filename,
                relative_path=spec.filename,
                content_type="image/png",
                size_bytes=spec.size_bytes,
            )
        )

        inline = self.client.get(f"/artifacts/{artifact.id}/download", params={"inline": 1})
        attachment = self.client.get(f"/artifacts/{artifact.id}/download")

        self.assertTrue(inline.headers["content-disposition"].startswith("inline"))
        self.assertTrue(attachment.headers["content-disposition"].startswith("attachment"))


class StructuredLoggingTests(unittest.TestCase):
    def test_json_formatter_keeps_business_fields(self) -> None:
        """格式化器保留 event 与 extra 字段，并输出单行 JSON。"""
        import logging

        record = logging.LogRecord(
            name="boxpilot.scheduler",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="step_succeeded",
            args=(),
            exc_info=None,
        )
        record.task_id = "task-1"
        record.step = "第一步"
        record.duration_ms = 12

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["event"], "step_succeeded")
        self.assertEqual(payload["logger"], "boxpilot.scheduler")
        self.assertEqual(payload["task_id"], "task-1")
        self.assertEqual(payload["step"], "第一步")
        self.assertEqual(payload["duration_ms"], 12)
        self.assertNotIn("msg", payload)

    def test_log_file_is_configured_once(self) -> None:
        """重复初始化日志不会叠加文件 handler。"""
        from backend.logging_config import configure_logging
        import logging

        before = len(logging.getLogger().handlers)
        configure_logging()
        configure_logging()

        self.assertEqual(len(logging.getLogger().handlers), before)
        self.assertTrue(Path(settings.LOG_FILE).parent.exists())


if __name__ == "__main__":
    unittest.main()
