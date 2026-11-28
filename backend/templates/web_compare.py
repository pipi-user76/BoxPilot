"""网页对比模板：抓取多篇网页，按维度生成对比表并标记冲突。"""

from __future__ import annotations

import json
import logging
import re
import pathlib
import tempfile
from typing import Any

from backend.llm import llm_client
from backend.config import settings
from backend.extract import extract_article
from backend.sandbox_client import SandboxAdapter
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext
from backend.templates.web_collect import _ITEM_SCRIPT, _absolutize_links, _count_words, _tidy_markdown

logger = logging.getLogger("boxpilot.compare")

PRESET_DIMENSIONS = ("适用场景", "优缺点", "性能", "限制", "版本")
MAX_SOURCES = 10
MAX_CHARS_PER_PAGE = 4000

COMPARE_SYSTEM = """你是严谨的技术调研助手。基于给定资料写一份简短对比报告，不要套表格，也不要输出 JSON。

第一行必须是：标题：<不超过 18 个字的对比主题>
标题要让人一眼知道在对比什么，例如“Python Web 框架文档对比”“两本爬虫练习站点对比”；不要出现网址、域名、http 或“plus”。

第二行空行，紧接着先写 `## 结论`：用两句话给出选择建议。这一节必须优先写完，后面的内容可以更短。
然后依次写：
## 来源速览
每个来源一行：[n] 一句话说明是什么、是否可用；打不开或 404 的来源直接标“不可用”并排除比较。
## 关键差异
2 到 3 条，每条一句话。
## 适用场景
一两句话说明各自适合谁。
## 风险与限制

要求：
1. 全文控制在 400 字以内，不要展开例子，不要抄原文。
2. 引用资料时用 [n] 标注来源序号。
3. 资料没覆盖就写“资料未覆盖”，不要编造。"""


class WebCompareTemplate:
    name = "网页对比"

    def __init__(self, sandbox: SandboxAdapter, llm=None, timeout_s: int | None = None) -> None:
        self.sandbox = sandbox
        self.llm = llm if llm is not None else llm_client
        self.timeout_s = timeout_s
        self.steps = (
            StepDefinition("校验对比链接", self._validate),
            StepDefinition("抓取网页正文", self._collect),
            StepDefinition("生成对比表", self._compare, timeout_seconds=480),
            StepDefinition("写入对比报告", self._write),
        )

    # ------------------------------------------------------------------ steps
    def _validate(self, context: TaskContext) -> StepOutcome:
        raw = context.params.get("urls") or []
        if isinstance(raw, str):
            raw = [line for line in re.split(r"[\r\n,，]+", raw) if line.strip()]
        urls: list[str] = []
        for item in raw:
            url = str(item).strip()
            if not url:
                continue
            if not re.match(r"^https?://", url, re.I):
                raise StepExecutionError("INVALID_URL", f"链接格式不正确：{url}")
            if url not in urls:
                urls.append(url)
        if len(urls) < 2:
            raise StepExecutionError("INVALID_URL", "至少需要两个不同的网页链接。")
        if len(urls) > MAX_SOURCES:
            raise StepExecutionError("TOO_MANY_SOURCES", f"一次最多对比 {MAX_SOURCES} 个链接。")

        dims = [str(d).strip() for d in (context.params.get("dimensions") or []) if str(d).strip()]
        dims = list(dict.fromkeys(dims))
        if not dims:
            dims = list(PRESET_DIMENSIONS[:4])
        context.state["urls"] = urls
        context.state["dimensions"] = dims
        return StepOutcome(output=f"已校验 {len(urls)} 个链接，对比维度：{'、'.join(dims)}")

    def _collect(self, context: TaskContext) -> StepOutcome:
        pages: list[dict[str, Any]] = []
        for index, url in enumerate(context.state["urls"], start=1):
            opened = self.sandbox.navigate(url, timeout_s=self.timeout_s)
            if not opened["success"]:
                pages.append(
                    {
                        "url": url,
                        "index": index,
                        "title": url,
                        "text": "",
                        "markdown": "",
                        "words": 0,
                        "items": [],
                        "selector": "",
                        "screenshot": b"",
                        "extract_mode": "none",
                        "unavailable": True,
                    }
                )
                continue
            text = self.sandbox.get_page_text()
            body = ((text.get("data") or {}).get("text") or "") if text["success"] else ""
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            if len(body) < 20:
                pages.append(
                    {
                        "url": url,
                        "index": index,
                        "title": url,
                        "text": "",
                        "markdown": "",
                        "words": 0,
                        "items": [],
                        "selector": "",
                        "screenshot": b"",
                        "extract_mode": "none",
                        "unavailable": True,
                    }
                )
                continue
            title = str((opened.get("data") or {}).get("title") or "").strip() or url
            markdown = ""
            extract_mode = "none"
            html_result = (
                self.sandbox.get_page_html(settings.EXTRACT_MAX_HTML)
                if hasattr(self.sandbox, "get_page_html")
                else {"success": False, "data": {}, "error": None}
            )
            html = str((html_result.get("data") or {}).get("html") or "") if html_result["success"] else ""
            if html:
                extracted = extract_article(html, url)
                markdown = extracted["markdown"]
                extract_mode = extracted["mode"]
            if not markdown:
                extracted_page = self.sandbox.get_page_markdown()
                if extracted_page["success"]:
                    markdown = str((extracted_page.get("data") or {}).get("markdown") or "").strip()
                    extract_mode = "readability" if markdown else "none"
            if markdown:
                markdown = _tidy_markdown(_absolutize_links(markdown, url))
            else:
                markdown = body
                extract_mode = "text"
            items: list[dict[str, str]] = []
            selector = ""
            found = self.sandbox.evaluate(_ITEM_SCRIPT)
            if found["success"] and isinstance(found.get("data"), dict):
                raw_items = [
                    item
                    for item in found["data"].get("items", [])
                    if isinstance(item, dict) and (item.get("title") or item.get("link"))
                ]
                if len(raw_items) >= 3:
                    items = raw_items
                    selector = str(found["data"].get("selector") or "")
            screenshot = b""
            shot_dir = pathlib.Path(tempfile.gettempdir()) / "boxpilot-compare"
            shot = self.sandbox.take_screenshot(shot_dir / f"{context.task_id}-{index}.png")
            if shot["success"]:
                shot_path = pathlib.Path(shot["data"]["path"])
                screenshot = shot_path.read_bytes()
                shot_path.unlink(missing_ok=True)
            pages.append(
                {
                    "url": url,
                    "index": index,
                    "title": title,
                    "text": body[:MAX_CHARS_PER_PAGE],
                    "markdown": markdown[:MAX_CHARS_PER_PAGE],
                    "words": _count_words(markdown),
                    "items": items,
                    "selector": selector,
                    "screenshot": screenshot,
                    "extract_mode": extract_mode,
                }
            )
        usable = [page for page in pages if not page.get("unavailable")]
        if len(usable) < 2:
            raise StepExecutionError(
                "PAGE_OPEN_FAILED",
                "至少要有两个网页能正常打开才能做对比，请检查链接后重试。",
            )
        context.state["pages"] = pages
        item_total = sum(len(page["items"]) for page in pages)
        return StepOutcome(output=f"已抓取 {len(usable)}/{len(pages)} 个网页的正文，识别 {item_total} 个条目")

    def _compare(self, context: TaskContext) -> StepOutcome:
        pages = [page for page in context.state["pages"] if not page.get("unavailable")]
        dims = context.state["dimensions"]
        if not self.llm.available():
            raise StepExecutionError(
                "LLM_NOT_CONFIGURED",
                "多页对比需要调用大模型做归纳分析，请先在 .env 配置 LLM_API_KEY 再重试。",
            )

        # 单次请求只带每页压缩后的摘要，避免长上下文导致网关直接断连。
        blocks = []
        for page in pages:
            digest = re.sub(r"\n{3,}", "\n\n", page["markdown"]).strip()[:900]
            blocks.append(f"[{page['index']}] {page['title']}（{page['url']}）\n{digest}")
        messages = [
            {"role": "system", "content": COMPARE_SYSTEM},
            {
                "role": "user",
                "content": f"关注维度：{'、'.join(dims)}\n\n资料：\n" + "\n\n".join(blocks),
            },
        ]

        label, report, last_error = "", "", ""
        for budget in (2400, 1600, 1000):
            try:
                streamed = "".join(self.llm.chat_stream(messages, max_tokens=budget))
                candidate = self._split_label(streamed)
            except Exception as exc:  # pragma: no cover - 依赖外部服务
                last_error = str(exc)
                logger.warning("compare_synthesis_failed", extra={"task_id": context.task_id, "budget": budget, "error": last_error[:200]})
                continue
            if candidate[1].strip():
                label, report = candidate
                break

        if not report.strip():
            # 模型连续失败时退化为“来源摘要”，并在报告与界面上明确标注，不静默冒充分析。
            report = self._fallback_report(pages, dims)
            context.state["compare"] = {"report": report, "label": "", "degraded": True, "detail": last_error[:300]}
            return StepOutcome(output="模型分析不可用，已生成来源摘要")

        if not label:
            first = self._short_title(pages[0]["title"])
            label = f"{first} 等 {len(pages)} 站对比" if len(pages) > 1 else first
        context.state["compare"] = {"report": report, "label": label, "degraded": False}
        return StepOutcome(output=f"已生成对比分析，{len(report)} 字")

    def _fallback_report(self, pages: list[dict[str, Any]], dims: list[str]) -> str:
        lines = [
            "# 网页对比分析（自动摘要）",
            "",
            "> 模型分析暂不可用，以下为各来源的标题与首段摘要，仅供快速浏览。",
            "",
            "## 来源速览",
        ]
        for page in pages:
            snippet = re.sub(r"\s+", " ", page["markdown"]).strip()[:160]
            lines.append(f"[{page['index']}] {page['title']}：{snippet}…")
        lines += [
            "",
            "## 对比维度",
            "、".join(dims) if dims else "未指定",
            "",
            "## 结论",
            f"本次收录 {len(pages)} 个来源，模型分析未完成，请逐条阅读摘要后自行判断。",
        ]
        return "\n".join(lines)

    def _write(self, context: TaskContext) -> StepOutcome:
        payload = dict(context.state["compare"])
        pages = context.state["pages"]
        payload["sources"] = [
            {
                "index": p["index"],
                "url": p["url"],
                "title": p["title"],
                "words": p.get("words", 0),
                "items": len(p.get("items") or []),
                "extract_mode": p.get("extract_mode", "none"),
                "unavailable": bool(p.get("unavailable")),
            }
            for p in pages
        ]
        payload["dimensions"] = context.state["dimensions"]

        store = context.artifact_store
        extra_specs = []
        for page in pages:
            if page.get("unavailable"):
                continue
            header = "\n".join(
                [
                    f"> 来源：{page['url']}",
                    f"> 标题：{page['title']}",
                    f"> 抓取方式：正文提取（{page.get('extract_mode', 'none')}）",
                    f"> 正文字数：{page.get('words', 0)}",
                    f"> 条目数：{len(page.get('items') or [])}",
                    "",
                    "---",
                    "",
                ]
            )
            extra_specs.append(store.write_bytes(
                context.task_id,
                f"source-{page['index']}.md",
                (header + (page.get("markdown") or page.get("text") or "")).encode("utf-8"),
                "text/markdown; charset=utf-8",
            ))
            if page.get("items"):
                extra_specs.append(store.write_bytes(
                    context.task_id,
                    f"source-{page['index']}.items.json",
                    json.dumps(
                        {
                            "url": page["url"],
                            "title": page["title"],
                            "selector": page.get("selector") or "",
                            "items": page["items"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ).encode("utf-8"),
                    "application/json; charset=utf-8",
                ))
            if page.get("screenshot"):
                extra_specs.append(store.write_bytes(
                    context.task_id,
                    f"source-{page['index']}.png",
                    page["screenshot"],
                    "image/png",
                ))
        md = self._markdown(payload)
        md_spec = store.write_bytes(
            context.task_id, "compare.md", md.encode("utf-8"), "text/markdown; charset=utf-8"
        )
        json_spec = store.write_bytes(
            context.task_id,
            "compare.json",
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
        )
        conclusion = self._conclusion(payload.get("report", ""))
        short = [name for name in (self._short_title(page["title"]) for page in pages) if name]
        if not short:
            fallback = f"网页对比 · {len(pages)} 站"
        elif len(short) == 1:
            fallback = f"{short[0]} 等 {len(pages)} 站"
        elif len(short) == 2:
            fallback = f"{short[0]} vs {short[1]}"
        else:
            fallback = f"{short[0]} / {short[1]} 等 {len(pages)} 站"
        summary = {
            "kind": "web_compare",
            "label": context.state.get("compare", {}).get("label") or fallback,
            "degraded": bool(context.state.get("compare", {}).get("degraded")),
            "source_count": len(pages),
            "dimensions": context.state["dimensions"],
            "conclusion": conclusion,
            "sources": [
                {"index": p["index"], "url": p["url"], "title": p["title"]} for p in pages
            ],
            "briefs": self._briefs(payload.get("report", ""), pages),
        }
        return StepOutcome(
            output="对比分析已生成",
            artifacts=[*extra_specs, md_spec, json_spec],
            result=summary,
        )

    @staticmethod
    def _short_title(title: str) -> str:
        text = str(title or "").strip()
        lowered = text.lower()
        if any(mark in lowered for mark in ("未找到", "404", "not found", "page not found")):
            return ""
        for sep in (" | ", " — ", " - ", "：", ":"):
            if sep in text:
                head = text.split(sep)[0].strip()
                if head:
                    return head[:18]
        return text[:18] or "网页对比"

    @staticmethod
    def _split_label(raw: str) -> tuple[str, str]:
        text = str(raw or "").strip()
        lines = text.splitlines()
        if not lines:
            return "", ""
        first = lines[0].strip()
        for prefix in ("标题：", "标题:"):
            if first.startswith(prefix):
                label = first[len(prefix):].strip()[:24]
                body = "\n".join(lines[1:]).strip()
                return label, body or text
        return "", text

    # ------------------------------------------------------------------ helpers

    @staticmethod
    @staticmethod
    def _briefs(report: str, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        briefs: dict[int, str] = {}
        marker = re.search(r"^#{1,6}\s*来源速览\s*$", report, re.M)
        if marker:
            tail = report[marker.end():]
            end = re.search(r"^#{1,6}\s*", tail, re.M)
            block = tail[: end.start()] if end else tail
            for line in block.splitlines():
                found = re.match(r"\s*(?:[-*]\s*)?\[(\d{1,2})\]\s*(.+)", line)
                if found:
                    briefs[int(found.group(1))] = found.group(2).strip()[:60]
        return [
            {
                "index": page["index"],
                "brief": briefs.get(page["index"]) or page["title"][:40],
            }
            for page in pages
        ]

    @staticmethod
    def _conclusion(report: str) -> str:
        """取“结论”小节或靠后正文段落，作为列表和对话开场的一句话概括。"""
        text = report.strip()
        if not text:
            return ""
        parts = re.split(r"^#{1,6}\s*(.+?)\s*$", text, flags=re.M)
        sections = {
            parts[i].strip(): parts[i + 1].strip()
            for i in range(1, len(parts) - 1, 2)
        }
        for title in ("结论", "风险与限制", "各自更适合的场景", "关键差异", "总体判断"):
            body = sections.get(title)
            if not body:
                continue
            for paragraph in re.split(r"\n\s*\n", body):
                cleaned = paragraph.replace("**", "").strip()
                if len(cleaned) >= 10:
                    return " ".join(cleaned.split())[:200]
        for line in text.splitlines():
            cleaned = line.replace("**", "").strip()
            if not cleaned or cleaned.startswith("#") or cleaned.startswith("标题："):
                continue
            if len(cleaned) >= 10:
                return " ".join(cleaned.split())[:200]
        return ""

    @staticmethod
    def _markdown(payload: dict[str, Any]) -> str:
        lines = ["# 网页对比分析", "", payload.get("report", "").strip(), "", "## 来源"]
        for source in payload.get("sources") or []:
            lines.append(f"{source['index']}. {source.get('title') or source['url']} — {source['url']}")
        return "\n".join(lines)
