"""文件职责：把页面 HTML 提取成正文 Markdown。

优先 trafilatura（按正文密度剔除导航/侧栏），失败时回退到沙箱 Readability
和可见文本；模板层只关心返回的 markdown 与 mode。
"""

from __future__ import annotations

import re
from typing import Any

import trafilatura

from backend.config import settings

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[A-Za-z0-9_]+")


def content_length(text: str) -> int:
    """粗略统计正文长度：中文按字、英文按词。"""
    return len(_CJK.findall(text)) + len(_WORD.findall(text))


def extract_article(
    html: str,
    url: str,
    fallback_markdown: str = "",
    fallback_text: str = "",
) -> dict[str, Any]:
    """返回 {markdown, mode, chars}；mode 为 trafilatura/readability/text/none。"""
    minimum = max(20, settings.EXTRACT_MIN_CHARS)
    engine = (settings.EXTRACT_ENGINE or "").strip().lower()

    if html and engine == "trafilatura":
        try:
            extracted = trafilatura.extract(
                html,
                url=url or None,
                output_format="markdown",
                include_links=True,
                include_tables=True,
                favor_recall=True,
                deduplicate=True,
            )
        except Exception:
            extracted = None
        markdown = (extracted or "").strip()
        if content_length(markdown) >= minimum:
            return {"markdown": markdown, "mode": "trafilatura", "chars": len(markdown)}

    markdown = (fallback_markdown or "").strip()
    if markdown:
        return {"markdown": markdown, "mode": "readability", "chars": len(markdown)}

    text = (fallback_text or "").strip()
    if text:
        return {"markdown": text, "mode": "text", "chars": len(text)}

    return {"markdown": "", "mode": "none", "chars": 0}
