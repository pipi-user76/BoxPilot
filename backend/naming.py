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
    TaskType.CSV_ANALYSIS: "CSV 数据分析",
    TaskType.REPO_CHECK: "代码仓库体检",
    TaskType.WEB_ACCEPTANCE: "网页应用验收",
    TaskType.MOCK: "开发验证",
}

TYPE_SLUGS: dict[TaskType, str] = {
    TaskType.WEB_DIGEST: "web-digest",
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


def display_name(task: TaskRecord) -> str:
    """形如“网页资料整理 · books.toscrape.com”，列表和详情共用。"""
    return f"{TYPE_LABELS[task.task_type]} · {target_label(task)}"
