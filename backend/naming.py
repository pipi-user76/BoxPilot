"""文件职责：统一任务标题与下载文件命名。

任务列表、批次卡片和压缩包都从这里取名字，避免出现同一批地址
在界面上分不清、下载后文件名互相覆盖的问题。
"""

from __future__ import annotations

import re
from datetime import timezone
from urllib.parse import urlparse

from backend.models import TaskRecord, TaskType

TYPE_LABELS: dict[TaskType, str] = {
    TaskType.WEB_DIGEST: "网页资料整理",
    TaskType.WEB_COMPARE: "网页对比",
    TaskType.CSV_ANALYSIS: "CSV 数据分析",
    TaskType.REPO_CHECK: "代码仓库体检",
    TaskType.WEB_ACCEPTANCE: "网页应用验收",
    TaskType.MOCK: "开发验证",
}

TYPE_SLUGS: dict[TaskType, str] = {
    TaskType.WEB_DIGEST: "web-digest",
    TaskType.WEB_COMPARE: "web-compare",
    TaskType.CSV_ANALYSIS: "csv-analysis",
    TaskType.REPO_CHECK: "repo-check",
    TaskType.WEB_ACCEPTANCE: "web-acceptance",
    TaskType.MOCK: "mock",
}

_SAFE_CHARS = re.compile(r"[^a-z0-9._-]+")


def _slug(value: str, fallback: str) -> str:
    text = _SAFE_CHARS.sub("-", value.strip().lower()).strip("-._")
    return (text[:60] or fallback).strip("-._") or fallback


def slug(value: str, fallback: str = "batch") -> str:
    """把任意文本压成安全的文件名片段，供批次名等复用。"""
    return _slug(value, fallback)


def _host_of(url: str) -> str:
    parsed = urlparse(url if "//" in url else f"https://{url}")
    return parsed.netloc or parsed.path


def target_label(task: TaskRecord) -> str:
    """从参数里取出可读的目标名，用于列表标题和压缩包名。"""
    params = task.params or {}
    if task.task_type in (TaskType.WEB_DIGEST, TaskType.WEB_ACCEPTANCE):
        return _slug(_host_of(str(params.get("url", ""))), "page")
    if task.task_type == TaskType.WEB_COMPARE:
        urls = [str(u) for u in (params.get("urls") or []) if str(u).strip()]
        hosts: list[str] = []
        for url in urls:
            host = _host_of(url)
            if host.startswith("www."):
                host = host[4:]
            if host and host not in hosts:
                hosts.append(host)
        if not hosts:
            return "compare"
        if len(hosts) == 1:
            return hosts[0]
        if len(hosts) == 2:
            return f"{hosts[0]} vs {hosts[1]}"
        return f"{hosts[0]} 等 {len(hosts)} 站"
    if task.task_type == TaskType.REPO_CHECK:
        repo = str(params.get("repo_url", "")).rstrip("/")
        name = repo.rsplit("/", 1)[-1].removesuffix(".git") if repo else ""
        return _slug(name, "repo")
    if task.task_type == TaskType.CSV_ANALYSIS:
        return _slug(str(params.get("filename", "")), "data")
    return "task"


def task_stem(task: TaskRecord) -> str:
    """形如 web-digest_books.toscrape.com_20261123-2159_e24e56ee。"""
    created = task.created_at
    if created.tzinfo is None:
        # 任务时间按 UTC 存储，命名需要换算到用户所在时区，否则文件名比界面早八小时。
        created = created.replace(tzinfo=timezone.utc)
    stamp = created.astimezone().strftime("%Y%m%d-%H%M")
    return f"{TYPE_SLUGS[task.task_type]}_{target_label(task)}_{stamp}_{task.id[:8]}"


_BAD_TITLE = re.compile(r"未找到|找不到|404|not\s*found|page\s*not\s*found|访问失败", re.IGNORECASE)
_HOSTLIKE = re.compile(
    r"https?://|www\.|\b[\w-]+\.(?:com|cn|org|net|io|dev|edu|gov)\b", re.IGNORECASE
)


def _summary(task: TaskRecord) -> dict:
    result = task.result if isinstance(task.result, dict) else {}
    summary = result.get("summary")
    return summary if isinstance(summary, dict) else {}


def _semantic_label(task: TaskRecord) -> str:
    summary = _summary(task)
    label = str(summary.get("label") or "").strip()
    if label and not _HOSTLIKE.search(label):
        return label[:24]
    page_title = str(summary.get("title") or "").split("|")[0].split("—")[0].strip()
    if page_title and not _BAD_TITLE.search(page_title) and not _HOSTLIKE.search(page_title):
        return page_title[:24]
    titles: list[str] = []
    for source in summary.get("sources") or []:
        if not isinstance(source, dict):
            continue
        title = str(source.get("title") or "").split("|")[0].split("—")[0].strip()
        if title and not _BAD_TITLE.search(title) and not _HOSTLIKE.search(title) and title not in titles:
            titles.append(title)
    if titles:
        if len(titles) == 1:
            return titles[0][:24]
        if len(titles) == 2:
            return f"{titles[0][:16]} vs {titles[1][:16]}"
        return f"{titles[0][:12]} / {titles[1][:12]} 等 {len(titles)} 站"
    if task.task_type in (TaskType.WEB_COMPARE, TaskType.WEB_DIGEST):
        return ""
    return target_label(task)


def display_name(task: TaskRecord) -> str:
    """优先使用模型主题或页面标题，不把 URL/域名当展示名。"""
    label = _semantic_label(task)
    return f"{TYPE_LABELS[task.task_type]} · {label}" if label else TYPE_LABELS[task.task_type]
