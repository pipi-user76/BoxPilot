"""文件职责：网页资料整理模板，提取去噪正文、纯文本和整页截图。

固定步骤为 URL 校验、打开页面、正文提取（Readability 去噪，失败时退回可见文本）、
整页截图和写入元数据报告。正文为空会使任务失败，避免生成没有有效内容的伪成功产物。
"""

from __future__ import annotations

import json

from backend.config import settings
from backend.sandbox_client import SandboxAdapter
from backend.storage import ArtifactSpec
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext
from backend.validation import ValidationFailure, validate_http_url


class WebDigestTemplate:
    """提取网页正文和整页截图，并生成一份可审查的元数据报告。"""

    name = "WEB_DIGEST"

    def __init__(self, sandbox: SandboxAdapter, timeout_s: int | None = None) -> None:
        self.sandbox = sandbox
        self.timeout_s = timeout_s or settings.WEB_ACCEPTANCE_TIMEOUT_SECONDS
        self.steps = (
            StepDefinition("校验网页地址", self._validate_url),
            StepDefinition("打开网页", self._open_page),
            StepDefinition("提取正文并去噪", self._extract_article),
            StepDefinition("截取整页截图", self._capture_screenshot),
            StepDefinition("生成整理报告", self._write_report),
        )

    def _validate_url(self, context: TaskContext) -> StepOutcome:
        """URL 必须是合法的 HTTP/HTTPS 绝对地址。"""
        url = str(context.params.get("url", "")).strip()
        try:
            validate_http_url(url)
        except ValidationFailure as exc:
            raise StepExecutionError("VALIDATION_ERROR", str(exc)) from exc
        context.state["url"] = url
        return StepOutcome(output=f"网页地址校验通过：{url}")

    def _open_page(self, context: TaskContext) -> StepOutcome:
        """打开页面并记录标题、HTTP 状态和加载耗时。"""
        result = self.sandbox.navigate(context.state["url"], timeout_s=self.timeout_s)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        data = result["data"] or {}
        context.state.update(
            title=str(data.get("title") or "").strip(),
            http_status=data.get("status"),
            load_time_ms=data.get("load_time_ms"),
        )
        return StepOutcome(output=f"页面已打开，标题：{context.state['title'] or '未获取到标题'}")

    def _extract_article(self, context: TaskContext) -> StepOutcome:
        """优先用 Readability 提取正文并转 Markdown，失败时退回页面可见文本。"""
        mode = "readability"
        markdown = ""
        result = self.sandbox.get_page_markdown()
        if result["success"]:
            data = result["data"] or {}
            markdown = str(data.get("markdown") or "").strip()
            title = str(data.get("title") or "").strip()
            if title and not context.state.get("title"):
                context.state["title"] = title

        if not markdown:
            # 部分页面（例如纯脚本渲染页面）Readability 提取不到内容，退回可见文本。
            mode = "text"
            fallback = self.sandbox.get_page_text()
            if not fallback["success"]:
                error = fallback["error"]
                raise StepExecutionError(error["code"], error["message"])
            markdown = str(fallback["data"].get("text") or "").strip()

        if not markdown:
            raise StepExecutionError("ARTIFACT_EMPTY", "page body is empty")

        article_spec = context.artifact_store.write_text(context.task_id, "article.md", markdown + "\n")

        # 纯文本优先使用浏览器可见文本；读取失败时保留 Markdown 原文，保证产物完整。
        text = markdown
        text_result = self.sandbox.get_page_text()
        if text_result["success"]:
            candidate = str(text_result["data"].get("text") or "").strip()
            if candidate:
                text = candidate
        page_spec = context.artifact_store.write_text(context.task_id, "page.txt", text + "\n")

        context.state.update(
            article=markdown,
            page_text=text,
            extraction=mode,
            article_spec=article_spec,
            page_text_spec=page_spec,
        )
        return StepOutcome(
            output=(
                f"正文提取完成（{'Readability 去噪' if mode == 'readability' else '可见文本回退'}）："
                f"Markdown {len(markdown)} 字符，纯文本 {len(text)} 字符"
            ),
            artifacts=[article_spec, page_spec],
        )

    def _capture_screenshot(self, context: TaskContext) -> StepOutcome:
        """保存整页截图，页面较长时也能在预览中看到完整内容。"""
        task_dir = context.artifact_store.ensure_task_dir(context.task_id)
        screenshot_path = task_dir / "screenshot.png"
        result = self.sandbox.take_screenshot(screenshot_path, full_page=True)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        spec = ArtifactSpec(
            filename="screenshot.png",
            content_type="image/png",
            size_bytes=screenshot_path.stat().st_size,
        )
        context.state.update(screenshot_spec=spec, full_page=True)
        size_kb = max(1, round(spec.size_bytes / 1024))
        return StepOutcome(output=f"整页截图已保存，大小约 {size_kb} KB", artifacts=[spec])

    def _write_report(self, context: TaskContext) -> StepOutcome:
        """生成轻量元数据报告，正文仍以 article.md 和 page.txt 为准。"""
        markdown_length = len(context.state["article"])
        text_length = len(context.state["page_text"])
        report = {
            "url": context.state["url"],
            "title": context.state["title"],
            "http_status": context.state["http_status"],
            "load_time_ms": context.state["load_time_ms"],
            "text_length": text_length,
            "markdown_length": markdown_length,
            "extraction": context.state["extraction"],
            "full_page": context.state["full_page"],
            "artifacts": ["article.md", "page.txt", "screenshot.png"],
        }
        spec = context.artifact_store.write_text(
            context.task_id,
            "web-digest.json",
            json.dumps(report, ensure_ascii=False, indent=2),
        )
        return StepOutcome(
            output="网页资料整理完成",
            artifacts=[spec],
            result={
                "kind": "web_digest",
                "message": "网页资料整理完成",
                "title": context.state["title"],
                "url": context.state["url"],
                "http_status": context.state["http_status"],
                "text_length": text_length,
                "markdown_length": markdown_length,
                "extraction": context.state["extraction"],
                "full_page": True,
                "screenshot_bytes": context.state["screenshot_spec"].size_bytes,
            },
        )
