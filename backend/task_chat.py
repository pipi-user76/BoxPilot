"""Persisted task-scoped chat grounded in a task's artifacts."""

from __future__ import annotations

import json
import re

from backend.llm import LLMError, llm_client
from backend.models import TaskChatMessage, TaskRecord

CONTEXT_LIMIT = 6000
HISTORY_LIMIT = 8
MAX_ANSWER = 1200

SYSTEM = (
    "你是 BoxPilot 的分析助手，只能依据给出的任务资料回答。"
    "直接用自然语言回答用户关心的问题，不要输出 JSON，也不要套固定字段。"
    "资料没有覆盖的内容，直接说“资料里没有提到”，不要编造。"
    "引用来源时使用资料中的编号，例如 [1]。"
)


VIEW_LABELS = {
    "line": "折线图",
    "bar": "柱状图",
    "pie": "饼图",
    "heatmap": "热力图",
    "scatter": "散点图",
    "mindmap": "思维导图",
    "profile_bar": "列总览",
}


def _view_label(value: str | None) -> str:
    return VIEW_LABELS.get(str(value or ""), str(value or "列总览"))


class TaskChatService:
    def __init__(self, store, artifact_store, llm=None) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.llm = llm or llm_client

    def _read(self, task_id: str, filename: str) -> str:
        try:
            path = self.artifact_store.resolve(task_id, filename)
        except FileNotFoundError:
            return ""
        try:
            return path.read_text(encoding="utf-8")[:CONTEXT_LIMIT]
        except OSError:
            return ""

    def _context(self, task: TaskRecord) -> str:
        parts: list[str] = []
        summary = (task.result or {}).get("summary") or {}
        if summary.get("kind") == "csv_analysis":
            datasets = summary.get("datasets") or []
            if len(datasets) > 1:
                parts.append(
                    "多数据集画像：\n"
                    + json.dumps(
                        [
                            {
                                "name": item.get("name"),
                                "rows": item.get("rows"),
                                "columns": item.get("columns"),
                                "table_kind": item.get("table_kind"),
                                "recommended_view": item.get("recommended_view"),
                                "profile_columns": (item.get("profile") or {}).get("columns"),
                            }
                            for item in datasets[:5]
                        ],
                        ensure_ascii=False,
                    )[:4000]
                )
            profile = summary.get("profile") or {}
            enabled = [
                item.get("label")
                for item in summary.get("view_options", [])
                if item.get("enabled") and item.get("id") != "wordcloud"
            ]
            parts.append(
                "表格画像：\n"
                + json.dumps(
                    {
                        "rows": summary.get("rows"),
                        "columns": summary.get("columns"),
                        "table_kind": summary.get("table_kind"),
                        "recommended_view": summary.get("recommended_view"),
                        "completeness": summary.get("completeness"),
                        "enabled_views": enabled,
                        "columns": profile.get("columns"),
                    },
                    ensure_ascii=False,
                )[:3500]
            )
            if summary.get("insight"):
                parts.append("初步解读：\n" + str(summary["insight"]))
            report = self._read(task.id, "report.md")
            if report:
                parts.append("分析报告：\n" + report)
        urls = task.params.get("urls") or []
        if urls:
            parts.append("对比来源：\n" + "\n".join(f"[{i + 1}] {url}" for i, url in enumerate(urls)))
        if summary and summary.get("kind") != "csv_analysis":
            parts.append("任务结论：\n" + json.dumps(summary, ensure_ascii=False)[:1500])
        compare = self._read(task.id, "compare.md")
        if compare:
            parts.append("对比报告：\n" + compare)
        digest = self._read(task.id, "web-digest.json")
        if digest:
            parts.append("整理数据：\n" + digest)
        return "\n\n".join(parts)[:CONTEXT_LIMIT]

    def _seed(self, task: TaskRecord) -> TaskChatMessage:
        summary = (task.result or {}).get("summary") or {}
        if summary.get("kind") == "csv_analysis":
            kind_labels = {
                "numeric_timeseries": "数值时序表",
                "cross_matrix": "二维交叉表",
                "numeric_snapshot": "数值快照表",
                "compare_checklist": "分类对比清单",
                "text_archive": "纯文本归档",
                "mixed": "混合表",
            }
            views = {
                item.get("label"): item.get("reason")
                for item in summary.get("view_options", [])
                if item.get("enabled") and item.get("id") not in ("profile_bar", "wordcloud")
            }
            datasets = summary.get("datasets") or []
            if len(datasets) > 1:
                total_lines = sum(int(item.get("rows") or 0) for item in datasets)
                text = f"{len(datasets)} 个数据集，共 {total_lines} 行。可以问某个数据集的趋势，或让我生成图表。"
                return self.store.add_task_message(task.id, "assistant", text)
            text = (
                f"{kind_labels.get(summary.get('table_kind'), '混合表')}，"
                f"{summary.get('rows')} 行 × {summary.get('columns')} 列。"
                "可以问趋势、列含义，或让我进一步分析。"
            )
            return self.store.add_task_message(task.id, "assistant", text)

        count = summary.get("source_count") or len(task.params.get("urls") or [])
        briefs = {item["index"]: item["brief"] for item in (summary.get("briefs") or [])}
        sources = (summary.get("sources") or [])[:10]
        if not sources:
            sources = [
                {"index": index, "url": url, "title": url}
                for index, url in enumerate(task.params.get("urls") or [], start=1)
            ][:10]
        listing = "\n".join(
            f"[{item.get('index', i)}] {briefs.get(item.get('index')) or item.get('title') or item.get('url')}"
            for i, item in enumerate(sources, start=1)
        )
        conclusion = str(summary.get("conclusion") or "").strip()
        if conclusion and listing:
            text = (
                f"这份对比覆盖了 {count} 个来源：\n{listing}\n\n"
                f"结论：{conclusion}\n\n"
                "可以继续追问具体差异。"
            )
        elif listing:
            text = (
                f"这份对比任务覆盖了 {count} 个来源：\n{listing}\n\n"
                "可以追问具体差异，或让我展开某一部分。"
            )
        else:
            text = (
                f"这份对比任务覆盖了 {count} 个来源，上方结论是报告摘要。"
                "可以继续追问细节，或让我展开某一部分。"
            )
        return self.store.add_task_message(task.id, "assistant", text)

    def history(self, task: TaskRecord) -> list[TaskChatMessage]:
        items = self.store.list_task_messages(task.id, 50)
        if items:
            return items
        if task.status.value in {"PENDING", "RUNNING"}:
            return []
        self._seed(task)
        return self.store.list_task_messages(task.id, 50)

    def ask(self, task: TaskRecord, question: str) -> TaskChatMessage:
        history = self.history(task)
        if history and history[0].role == "assistant":
            history = history[1:]  # 开场白由系统生成，不参与模型上下文
        messages = [{"role": "system", "content": SYSTEM + "\n\n" + self._context(task)}]
        for item in history[-HISTORY_LIMIT:]:
            messages.append({"role": item.role, "content": item.content[:800]})
        messages.append({"role": "user", "content": question})

        text = ""
        try:
            for budget in (700, 1600):
                result = self.llm.chat(messages, max_tokens=budget, timeout=60, retries=0)
                text = (result.text or "").strip()
                if text:
                    break
        except LLMError:
            raise
        if not text:
            summary = (task.result or {}).get("summary") or {}
            conclusion = str(summary.get("conclusion") or "").strip()
            text = (
                f"根据这份报告：{conclusion}" if conclusion
                else "这份资料里没有足够信息回答这个问题，可以打开上方来源查看原文。"
            )
        text = text[:MAX_ANSWER]
        citations = sorted({int(value) for value in re.findall(r"\[(\d{1,2})\]", text)})[:4]
        if citations:
            text += "\n\n引用：" + " ".join(f"[{index}]" for index in citations)

        self.store.add_task_message(task.id, "user", question)
        return self.store.add_task_message(task.id, "assistant", text)
