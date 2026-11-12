"""文件职责：网页应用验收模板。

输入 URL 和预期标题，执行页面访问、标题比对与截图。页面访问或截图失败属于任务失败；
标题不匹配属于有效验收结果，任务仍然成功并生成包含 failed 结论的报告。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.config import settings
from backend.sandbox_client import SandboxAdapter
from backend.storage import ArtifactSpec
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext
from backend.validation import ValidationFailure, validate_http_url


class WebAcceptTemplate:
    """按预期标题检查网页，并保留截图和结构化验收报告。"""

    name = "WEB_ACCEPTANCE"

    def __init__(self, sandbox: SandboxAdapter, timeout_s: int | None = None) -> None:
        self.sandbox = sandbox
        self.timeout_s = timeout_s or settings.WEB_ACCEPTANCE_TIMEOUT_SECONDS
        self.steps = (
            StepDefinition("校验验收参数", self._validate_input),
            StepDefinition("打开目标页面", self._open_page),
            StepDefinition("截取页面截图", self._capture_screenshot),
            StepDefinition("生成验收报告", self._write_report),
        )

    def _validate_input(self, context: TaskContext) -> StepOutcome:
        """URL 和预期标题都是必填字段，并在这里完成 URL 安全校验。"""
        url = str(context.params.get("url", "")).strip()
        expected_title = str(context.params.get("expected_title", "")).strip()
        if not expected_title:
            raise StepExecutionError("VALIDATION_ERROR", "expected_title is required")
        try:
            validate_http_url(url)
        except ValidationFailure as exc:
            raise StepExecutionError("VALIDATION_ERROR", str(exc)) from exc
        context.state.update(url=url, expected_title=expected_title)
        return StepOutcome(output=f"目标地址校验通过：{url}")

    def _open_page(self, context: TaskContext) -> StepOutcome:
        """打开页面并保存导航元数据，标题不匹配暂不抛出错误。"""
        result = self.sandbox.navigate(context.state["url"], timeout_s=self.timeout_s)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        data = result["data"] or {}
        context.state.update(
            actual_title=str(data.get("title") or "").strip(),
            http_status=data.get("status"),
            load_time_ms=round(data["load_time_ms"], 1)
            if isinstance(data.get("load_time_ms"), (int, float))
            else None,
        )
        return StepOutcome(output=f"页面已打开，实际标题：{context.state['actual_title'] or '未获取到标题'}")

    def _capture_screenshot(self, context: TaskContext) -> StepOutcome:
        """截图写入任务目录，并记录非空产物供调度器统一登记。"""
        task_dir = context.artifact_store.ensure_task_dir(context.task_id)
        screenshot_path = task_dir / "page.png"
        result = self.sandbox.take_screenshot(screenshot_path, full_page=False)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        context.state["screenshot_spec"] = ArtifactSpec(
            filename="page.png",
            content_type="image/png",
            size_bytes=screenshot_path.stat().st_size,
        )
        size_kb = max(1, round(screenshot_path.stat().st_size / 1024))
        return StepOutcome(output=f"页面截图已保存，大小约 {size_kb} KB")

    def _write_report(self, context: TaskContext) -> StepOutcome:
        """标题使用空白归一和大小写无关比较，结果写入 acceptance.json。"""
        expected = " ".join(context.state["expected_title"].split()).casefold()
        actual = " ".join(context.state["actual_title"].split()).casefold()
        passed = bool(actual) and actual == expected
        report = {
            "url": context.state["url"],
            "expected_title": context.state["expected_title"],
            "actual_title": context.state["actual_title"],
            "passed": passed,
            "http_status": context.state["http_status"],
            "load_time_ms": context.state["load_time_ms"],
            "screenshot_bytes": context.state["screenshot_spec"].size_bytes,
            "failure_reason": None if passed else "页面标题与预期不一致",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        report_spec = context.artifact_store.write_text(
            context.task_id,
            "acceptance.json",
            json.dumps(report, ensure_ascii=False, indent=2),
        )
        return StepOutcome(
            output="标题匹配，验收通过" if passed else "标题不匹配，验收未通过",
            artifacts=[report_spec, context.state["screenshot_spec"]],
            result={
                "kind": "web_acceptance",
                "message": "标题匹配" if passed else "标题不匹配",
                "passed": passed,
                "expected_title": context.state["expected_title"],
                "actual_title": context.state["actual_title"],
                "http_status": context.state["http_status"],
                "screenshot_bytes": context.state["screenshot_spec"].size_bytes,
            },
        )
