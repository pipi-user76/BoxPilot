"""文件职责：网页资料整理模板，提取去噪正文、整页截图和内容统计。

固定步骤为 URL 校验、打开页面、正文提取（trafilatura 优先，失败时退回 Readability/可见文本）、
整页截图和写入元数据报告。只保留 article.md 一份正文，避免同时输出 Markdown 与纯文本。
正文为空会使任务失败，避免生成没有有效内容的伪成功产物。
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from backend.config import settings
from backend.extract import extract_article
from backend.llm import LLMError, llm_client
from backend.sandbox_client import SandboxAdapter
from backend.storage import ArtifactSpec
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext
from backend.templates.url_guard import validate_public_url_step

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]*")
_HEADING = re.compile(r"(?m)^#{1,6}\s")
_LINK = re.compile(r"\[[^\]]+\]\([^)]+\)")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MD_LINK = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)(\))")
_URL_IN_LINK = re.compile(r"\((?:https?://)[^)]+\)")
_ABSOLUTE_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#", "data:")

# 在页面里寻找“重复出现、每条带链接”的容器，抽出标题、价格、库存、评分和图片。
# 纯浏览器端逻辑，不依赖站点专用选择器，列表页和商品页都能用。
_ITEM_SCRIPT = r"""
(() => {
  const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const abs = (value) => { try { return new URL(value, location.href).href; } catch (e) { return value || ''; } };
  const priceRe = /(?:[$£€¥]|USD|CNY|EUR|RMB)\s?\d[\d,.]*/;
  const clsOf = (node) => String(node.className || '').trim().split(/\s+/).slice(0, 2).join('.');
  const sigOf = (el) => {
    const parent = el.parentElement;
    const prefix = parent ? parent.tagName + (clsOf(parent) ? '.' + clsOf(parent) : '') : '';
    return prefix + '>' + el.tagName + (clsOf(el) ? '.' + clsOf(el) : '');
  };
  const groups = new Map();
  const nodes = Array.from(document.querySelectorAll('article, li, [class*="product"], [class*="item"], [class*="card"]'));
  for (const el of nodes) {
    const sig = sigOf(el);
    if (!groups.has(sig)) groups.set(sig, []);
    groups.get(sig).push(el);
  }
  // 同时看“重复次数”和“是否像商品/条目”：带价格且有一定文字的块优先，
  // 这样导航菜单（同样是重复 li、也带链接）不会盖过真正的商品网格。
  let best = null;
  for (const [sig, els] of groups) {
    if (els.length < 3) continue;
    const linked = els.filter((item) => item.querySelector('a[href]')).length;
    if (linked < els.length * 0.8) continue;
    let rich = 0;
    for (const el of els) {
      const text = norm(el.innerText);
      if (priceRe.test(text) && text.length > 20) rich += 1;
    }
    const score = rich * 10 + els.length;
    if (!best || score > best.score) best = { sig: sig, els: els, rich: rich, score: score };
  }
  if (!best) return JSON.stringify({ detected: false, selector: '', items: [] });
  const items = best.els.map((el) => {
    // 列表页常把标题截断成“A Light in the ...”，完整标题放在 a[title]。
    const link = el.querySelector('a[href][title]') || el.querySelector('h1 a[href], h2 a[href], h3 a[href], h4 a[href]') || el.querySelector('a[href]');
    const img = el.querySelector('img');
    const heading = el.querySelector('h1,h2,h3,h4,h5');
    const text = norm(el.innerText);
    const title = norm(link && link.getAttribute('title')) || norm(heading && heading.textContent) || norm(link && link.textContent);
    const price = (text.match(priceRe) || [''])[0];
    const stockMatch = text.match(/in stock|out of stock|有货|缺货|库存紧张/i);
    const ratingMatch = String((el.querySelector('[class*="star-rating"]') || {}).className || '').match(/star-rating\s+(\w+)/i);
    return {
      title: title,
      price: norm(price),
      stock: stockMatch ? norm(stockMatch[0]) : '',
      rating: ratingMatch ? ratingMatch[1] : '',
      link: link ? abs(link.getAttribute('href')) : '',
      image: img ? abs(img.getAttribute('src') || '') : '',
      text: text.slice(0, 200)
    };
  }).filter((item) => item.title || item.link);
  return JSON.stringify({ detected: items.length >= 3, selector: best.sig, items: items });
})()
"""


def _absolutize_links(markdown: str, base_url: str) -> str:
    """把 Readability 产出的相对链接补成绝对地址，方便直接点开和二次使用。"""

    def replace(match: re.Match[str]) -> str:
        target = match.group(2)
        if target.startswith(_ABSOLUTE_PREFIXES):
            return match.group(0)
        return f"{match.group(1)}{urljoin(base_url, target)}{match.group(3)}"

    return _MD_LINK.sub(replace, markdown)


def _tidy_markdown(markdown: str) -> str:
    """压缩多余空行，去掉行尾空白，保持正文紧凑可读。"""
    lines = [line.rstrip() for line in markdown.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _count_words(markdown: str) -> int:
    """统计中英文词数；链接地址不算正文，避免绝对化后字数虚高。"""
    plain = _URL_IN_LINK.sub("()", markdown)
    return len(_CJK.findall(plain)) + len(_WORD.findall(plain))


GOAL_PRESETS = {
    "notes": "整理成学习笔记：提炼概念、关键结论和可执行要点，按主题分节。",
    "steps": "整理成步骤教程：按操作顺序列出前置条件、每一步做什么、如何验证。",
    "compare": "整理成优缺点对比：分别列出优点、缺点、适用场景。",
    "specs": "整理成参数清单：用表格列出名称、数值、单位、说明。",
    "risks": "整理风险与局限：列出限制、坑点、前置条件和使用注意。",
}

GOAL_LABELS = {
    "notes": "学习笔记",
    "steps": "步骤教程",
    "compare": "优缺点对比",
    "specs": "参数清单",
    "risks": "风险局限整理",
}

READING_SYSTEM = """你是严谨的阅读助手。只依据给定正文完成用户指定的阅读目标，不要补充正文没有的信息。
用 Markdown 输出，包含三级标题分节；关键结论后用 (原文：第N段) 标注依据。
正文超过 10000 字时只使用前 10000 字，并在开头说明这一点。"""

FIELDS_SYSTEM = """你是结构化抽取器。按用户给出的字段，从正文中抽取数据。
只输出 JSON：{"rows":[{"字段名":"值"}]}。找不到的字段填 "无数据"，不要编造。
一页有多条记录时输出多行；只有一条时输出一行。"""


class WebDigestTemplate:
    """提取网页正文和整页截图，并生成一份可审查的元数据报告。"""

    name = "WEB_DIGEST"

    def __init__(self, sandbox: SandboxAdapter, timeout_s: int | None = None, llm=None) -> None:
        self.sandbox = sandbox
        self.timeout_s = timeout_s or settings.WEB_ACCEPTANCE_TIMEOUT_SECONDS
        self.llm = llm if llm is not None else llm_client
        self.steps = (
            StepDefinition("校验网页地址", validate_public_url_step),
            StepDefinition("打开网页", self._open_page),
            StepDefinition("提取正文与条目", self._extract_article),
            StepDefinition("截取整页截图", self._capture_screenshot),
            StepDefinition("生成整理报告", self._write_report),
        )

    def _validate_url(self, context: TaskContext) -> StepOutcome:
        """保留旧方法名，实际委托给共用的公网地址校验。"""
        return validate_public_url_step(context)

    def _open_page(self, context: TaskContext) -> StepOutcome:
        """打开页面并记录标题、HTTP 状态和加载耗时。"""
        result = self.sandbox.navigate(context.state["url"], timeout_s=self.timeout_s)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        data = result["data"] or {}
        load_time = data.get("load_time_ms")
        context.state.update(
            title=str(data.get("title") or "").strip(),
            http_status=data.get("status"),
            load_time_ms=round(load_time, 1) if isinstance(load_time, (int, float)) else None,
        )
        return StepOutcome(output=f"页面已打开，标题：{context.state['title'] or '未获取到标题'}")

    def _extract_article(self, context: TaskContext) -> StepOutcome:
        """先尝试结构化抽取；列表页产出条目表，普通页面退回正文整理。"""
        items, selector = self._extract_items()
        if items:
            return self._write_structured(context, items, selector)

        mode = "readability"
        markdown = ""
        html_result = (
            self.sandbox.get_page_html(settings.EXTRACT_MAX_HTML)
            if hasattr(self.sandbox, "get_page_html")
            else {"success": False, "data": {}, "error": None}
        )
        html = ""
        if html_result["success"]:
            html = str((html_result.get("data") or {}).get("html") or "")
        if html:
            extracted = extract_article(html, str(context.state.get("url") or ""))
            markdown = extracted["markdown"]
            mode = extracted["mode"]
        if not markdown:
            result = self.sandbox.get_page_markdown()
            if result["success"]:
                data = result["data"] or {}
                markdown = str(data.get("markdown") or "").strip()
                title = str(data.get("title") or "").strip()
                if title and not context.state.get("title"):
                    context.state["title"] = title
                mode = "readability"

        if not markdown:
            # 部分页面（例如纯脚本渲染页面）Readability 提取不到内容，退回可见文本。
            mode = "text"
            fallback = self.sandbox.get_page_text()
            if not fallback["success"]:
                error = fallback["error"]
                raise StepExecutionError(error["code"], error["message"])
            markdown = str(fallback["data"].get("text") or "").strip()
        context.state["extract_mode"] = mode

        if not markdown:
            raise StepExecutionError("ARTIFACT_EMPTY", "page body is empty")

        # 脚本渲染或需要登录的页面，常只留下导航文字或一张图片链接。
        # 这类内容虽非空，但对用户没有价值，按“正文过少”失败，避免产出空报告。
        cjk = len(_CJK.findall(markdown))
        latin = len(_WORD.findall(markdown))
        if cjk + latin < 20 and len(markdown) < 400:
            raise StepExecutionError(
                "CONTENT_EMPTY",
                "页面正文过少，可能是脚本渲染、需要登录或尚未加载完成。"
                "请换一个有正文的页面，或等页面完全打开后再试。",
            )

        base_url = str(context.state.get("url") or "")
        body = _tidy_markdown(_absolutize_links(markdown, base_url))
        words = _count_words(body)
        mode_label = {
            "trafilatura": "trafilatura 正文提取",
            "readability": "Readability 去噪",
        }.get(mode, "可见文本回退")
        article_spec = context.artifact_store.write_text(
            context.task_id,
            "article.md",
            self._header(context, words, mode_label) + body + "\n",
        )

        context.state.update(article=body, extraction=mode, article_spec=article_spec, words=words)
        return StepOutcome(
            output=f"正文提取完成（{mode_label}）：{words} 个词",
            artifacts=[article_spec],
        )

    def _extract_items(self) -> tuple[list[dict[str, str]], str]:
        """在页面里找重复条目块；少于 3 条视为普通文章页。"""
        result = self.sandbox.evaluate(_ITEM_SCRIPT)
        if not result["success"]:
            return [], ""
        data = result["data"] or {}
        if not isinstance(data, dict):
            return [], ""
        items = [
            item
            for item in data.get("items", [])
            if isinstance(item, dict) and (item.get("title") or item.get("link"))
        ]
        if len(items) < 3:
            return [], ""
        return items, str(data.get("selector") or "")

    def _header(self, context: TaskContext, words: int, mode: str, extra: list[str] | None = None) -> str:
        """生成正文开头的来源信息块，方便离线阅读时知道内容从哪来。"""
        lines = [
            f"> 来源：{context.state['url']}",
            f"> 标题：{context.state.get('title') or context.state['url']}",
            f"> 抓取时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
            f"> 提取方式：{mode}",
        ]
        if extra:
            lines.extend(extra)
        lines.extend([f"> 正文字数：{words}", "", "---", ""])
        return "\n".join(lines)

    def _write_structured(self, context: TaskContext, items: list[dict[str, str]], selector: str) -> StepOutcome:
        """把列表页抽成条目表，同时输出 JSON 和 CSV，正文只保留表格。"""
        columns = ["title", "price", "stock", "rating", "link", "image"]
        title = str(context.state.get("title") or "").strip() or context.state["url"]
        rows = []
        for index, item in enumerate(items, start=1):
            name = str(item.get("title") or "").replace("|", "\\|")
            link = str(item.get("link") or "")
            label = f"[{name}]({link})" if link else name
            rows.append(
                f"| {index} | {label} | {item.get('price') or ''} | "
                f"{item.get('stock') or ''} | {item.get('rating') or ''} |"
            )
        body = "\n".join(
            [
                f"# {title}",
                "",
                f"共识别 {len(items)} 个条目（页面结构：`{selector or '未记录'}`）。",
                "",
                "| # | 标题 | 价格 | 库存 | 评分 |",
                "|---|---|---|---|---|",
                *rows,
            ]
        )
        words = _count_words(body)
        article = (
            self._header(context, words, "结构化条目抽取", [f"> 条目数：{len(items)}"]) + body + "\n"
        )
        article_spec = context.artifact_store.write_text(context.task_id, "article.md", article)
        payload = {
            "url": context.state["url"],
            "title": title,
            "selector": selector,
            "count": len(items),
            "fields": columns,
            "items": items,
        }
        json_spec = context.artifact_store.write_text(
            context.task_id, "items.json", json.dumps(payload, ensure_ascii=False, indent=2)
        )
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)
        csv_spec = context.artifact_store.write_text(context.task_id, "items.csv", buffer.getvalue())

        context.state.update(
            article=body,
            extraction="structured",
            words=words,
            item_count=len(items),
            selector=selector,
            article_spec=article_spec,
        )
        return StepOutcome(
            output=f"识别到 {len(items)} 个条目，已生成条目表、JSON 和 CSV",
            artifacts=[article_spec, json_spec, csv_spec],
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

    def _goal_instruction(self, context: TaskContext) -> str:
        raw = str(context.params.get("goal") or "").strip()
        if not raw:
            return ""
        return GOAL_PRESETS.get(raw, raw)[:500]

    def _declared_fields(self, context: TaskContext) -> list[tuple[str, str]]:
        raw = context.params.get("fields") or []
        fields: list[tuple[str, str]] = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                desc = str(item.get("description") or item.get("desc") or "").strip()
            else:
                name, _, desc = str(item).partition(":")
                name, desc = name.strip(), desc.strip()
            if name:
                fields.append((name[:40], desc[:120]))
        return fields[:8]

    def _extract_fields(
        self, fields: list[tuple[str, str]], markdown: str, context: TaskContext
    ) -> list[dict[str, str]]:
        listing = "\n".join(f"- {name}：{desc or '按名称理解'}" for name, desc in fields)
        data = self.llm.chat_json(
            [
                {"role": "system", "content": FIELDS_SYSTEM},
                {"role": "user", "content": f"字段：\n{listing}\n\n正文：\n{markdown[:10000]}"},
            ]
        )
        rows = [row for row in (data.get("rows") or []) if isinstance(row, dict)]
        normalized = []
        for row in rows:
            normalized.append({name: str(row.get(name) or "无数据") for name, _ in fields})

        payload = json.dumps(normalized, ensure_ascii=False, indent=2)
        spec = context.artifact_store.write_bytes(
            context.task_id, "fields.json", payload.encode("utf-8"), "application/json; charset=utf-8"
        )
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=[name for name, _ in fields])
        writer.writeheader()
        writer.writerows(normalized)
        csv_spec = context.artifact_store.write_bytes(
            context.task_id, "fields.csv", buffer.getvalue().encode("utf-8"), "text/csv; charset=utf-8"
        )
        context.state["field_specs"] = [
            ArtifactSpec("fields.json", spec.content_type, spec.size_bytes),
            ArtifactSpec("fields.csv", csv_spec.content_type, csv_spec.size_bytes),
        ]
        return normalized

    def _write_report(self, context: TaskContext) -> StepOutcome:
        """生成轻量元数据报告；正文本身在 article.md，截图在 screenshot.png。"""
        markdown = context.state["article"]
        words = _count_words(markdown)
        reading_minutes = max(1, round(words / 300))
        screenshot_bytes = context.state["screenshot_spec"].size_bytes

        extra_artifacts: list[ArtifactSpec] = []
        notes: list[str] = []
        goal_label = ""
        reading_words = 0
        field_rows: list[dict[str, str]] = []
        fields = self._declared_fields(context)
        llm_ready = bool(self.llm and getattr(self.llm, "available", lambda: False)())

        goal = self._goal_instruction(context)
        if goal:
            goal_label = GOAL_LABELS.get(str(context.params.get("goal") or ""), goal)[:40]
            if not llm_ready:
                notes.append("未配置模型，已跳过精读")
            else:
                try:
                    result = self.llm.chat(
                        [
                            {"role": "system", "content": READING_SYSTEM},
                            {
                                "role": "user",
                                "content": f"阅读目标：{goal}\n\n正文（最多 10000 字）：\n{markdown[:10000]}",
                            },
                        ],
                        max_tokens=1600,
                    )
                    reading = self._header(context, words, "目标精读") + result.text.strip() + "\n"
                    spec = context.artifact_store.write_bytes(
                        context.task_id, "reading.md", reading.encode("utf-8"), "text/markdown; charset=utf-8"
                    )
                    extra_artifacts.append(ArtifactSpec("reading.md", spec.content_type, spec.size_bytes))
                    reading_words = _count_words(result.text)
                    notes.append("已生成目标精读")
                except LLMError as exc:
                    notes.append(f"精读生成失败：{exc.code}")

        if fields and llm_ready:
            try:
                field_rows = self._extract_fields(fields, markdown, context)
            except LLMError as exc:
                notes.append(f"字段抽取失败：{exc.code}")

        report = {
            "url": context.state["url"],
            "title": context.state["title"],
            "http_status": context.state["http_status"],
            "load_time_ms": context.state["load_time_ms"],
            "extraction": context.state["extraction"],
            "full_page": context.state["full_page"],
            "structured": {
                "detected": bool(context.state.get("item_count")),
                "count": context.state.get("item_count", 0),
                "selector": context.state.get("selector", ""),
                "fields": ["title", "price", "stock", "rating", "link", "image"]
                if context.state.get("item_count")
                else [],
            },
            "crawler": {
                "engine": "AIO Sandbox Chromium",
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "final_url": context.state["url"],
                "http_status": context.state["http_status"],
                "load_time_ms": context.state["load_time_ms"],
                "items": context.state.get("item_count", 0),
            },
            "content": {
                "characters": len(markdown),
                "words": words,
                "reading_time_minutes": reading_minutes,
                "headings": len(_HEADING.findall(markdown)),
                "links": len(_LINK.findall(markdown)),
                "images": len(_IMAGE.findall(markdown)),
            },
            "screenshot_bytes": screenshot_bytes,
            "reading": {"goal": goal_label, "words": reading_words},
            "fields": {"declared": len(fields), "rows": len(field_rows)},
            "notes": notes,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        spec = context.artifact_store.write_text(
            context.task_id,
            "web-digest.json",
            json.dumps(report, ensure_ascii=False, indent=2),
        )
        artifacts = [spec, *extra_artifacts, *context.state.get("field_specs", [])]
        return StepOutcome(
            output=f"网页资料整理完成：{words} 词，约 {reading_minutes} 分钟阅读",
            artifacts=artifacts,
            result={
                "kind": "web_digest",
                "message": "网页资料整理完成",
                "title": context.state["title"],
                "url": context.state["url"],
                "http_status": context.state["http_status"],
                "words": words,
                "reading_time_minutes": reading_minutes,
                "extraction": context.state["extraction"],
                "full_page": True,
                "screenshot_bytes": screenshot_bytes,
                "item_count": context.state.get("item_count", 0),
                "goal": goal_label,
                "reading_words": reading_words,
                "field_count": len(fields),
                "field_rows": len(field_rows),
            },
        )
