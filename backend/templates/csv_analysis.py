"""文件职责：把上传 CSV 送入沙箱分析，并归档 JSON 摘要与中文 Markdown 报告。

CSV 输入由 POST /tasks/csv 保存到当前任务目录。单文件保留 v2 的完整图表与解读；
多文件按数据集分别分析，每个数据集生成所有可用图表。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.config import settings
from backend.llm import LLMClient
from backend.sandbox_client import SandboxAdapter
from backend.storage import ArtifactSpec
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext

logger = logging.getLogger(__name__)

PALETTE = ["#466F87", "#78B6C8", "#F2D98E", "#E48578", "#98AF1E"]
TABLE_LABELS = {
    "numeric_timeseries": "数值时序表",
    "cross_matrix": "二维交叉表",
    "numeric_snapshot": "数值快照表",
    "compare_checklist": "分类对比清单",
    "text_archive": "纯文本归档",
    "mixed": "混合表",
    "multi_dataset": "多数据集",
}
VIEW_LABELS = {
    "line": "折线图",
    "bar": "柱状图",
    "pie": "饼图",
    "heatmap": "热力图",
    "scatter": "散点图",
    "mindmap": "思维导图",
    "profile_bar": "列总览",
}
ROLE_LABELS = {
    "time": "时间/版本",
    "measure": "数值",
    "category": "分类",
    "enum": "枚举标签",
    "long_text": "长文本",
    "hierarchy": "层级",
}


class CsvAnalysisTemplate:
    """固定 CSV 分析流程：上传、执行、下载并生成画像与图表数据。"""

    name = "CSV_ANALYSIS"

    def __init__(
        self,
        sandbox: SandboxAdapter,
        timeout_s: int | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.timeout_s = timeout_s or settings.REPO_TEST_TIMEOUT_SECONDS
        self.llm = llm
        self.steps = (
            StepDefinition("校验 CSV 文件", self._validate_upload),
            StepDefinition("上传分析文件", self._upload_csv),
            StepDefinition("执行数据分析", self._run_analyzer),
            StepDefinition("生成分析报告", self._save_summary),
        )

    def _source_specs(self, context: TaskContext) -> list[dict]:
        params = context.params or {}
        sources = params.get("sources")
        if not sources:
            sources = [
                {
                    "name": "input.csv",
                    "artifact": params.get("csv_filename") or "input.csv",
                }
            ]
        root = context.artifact_store.root / context.task_id
        resolved = []
        for source in sources:
            artifact = str(source.get("artifact") or "")
            local_path = root / artifact
            if not artifact or not local_path.is_file() or local_path.stat().st_size == 0:
                raise StepExecutionError(
                    "FILE_NOT_FOUND",
                    f"uploaded CSV is missing or empty: {source.get('name') or artifact}",
                )
            resolved.append(
                {
                    "name": source.get("name") or Path(artifact).name,
                    "artifact": artifact,
                    "path": local_path,
                }
            )
        return resolved

    def _validate_upload(self, context: TaskContext) -> StepOutcome:
        """确认所有源文件存在且分析脚本位于当前代码树中。"""
        sources = self._source_specs(context)
        analyzer_path = Path(__file__).resolve().parent.parent / "csv_analyzer.py"
        if not analyzer_path.is_file():
            raise StepExecutionError("FILE_NOT_FOUND", f"CSV analyzer not found: {analyzer_path}")
        context.state.update(sources=sources, analyzer_path=analyzer_path)
        total_kb = max(1, round(sum(item["path"].stat().st_size for item in sources) / 1024))
        return StepOutcome(output=f"CSV 文件校验通过：{len(sources)} 个文件，共约 {total_kb} KB")

    def _upload_csv(self, context: TaskContext) -> StepOutcome:
        """上传全部 CSV 与固定分析脚本到任务工作目录。"""
        result = self.sandbox.upload_file(
            context.task_id, context.state["analyzer_path"], "analyze_csv.py"
        )
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])

        remotes = []
        single = len(context.state["sources"]) == 1
        for index, source in enumerate(context.state["sources"], start=1):
            remote = "input.csv" if single else f"input-{index:02d}.csv"
            result = self.sandbox.upload_file(context.task_id, source["path"], remote)
            if not result["success"]:
                error = result["error"]
                raise StepExecutionError(error["code"], error["message"])
            remotes.append(remote)
        context.state["remotes"] = remotes
        return StepOutcome(output=f"{len(remotes)} 个 CSV 文件和分析脚本已上传到沙箱")

    def _run_analyzer(self, context: TaskContext) -> StepOutcome:
        """逐个文件执行分析脚本，保留每个数据集的 summary。"""
        runs = []
        for index, remote in enumerate(context.state["remotes"], start=1):
            output_name = "summary.json" if len(context.state["remotes"]) == 1 else f"summary-{index:02d}.json"
            result = self.sandbox.exec_shell(
                f"python analyze_csv.py {remote} {output_name}",
                task_id=context.task_id,
                timeout_s=self.timeout_s,
            )
            if not result["success"]:
                error = result["error"]
                raise StepExecutionError(error["code"], error["message"])
            runs.append({"remote": remote, "summary": output_name, "output": result["data"].get("output") or ""})
        context.state["runs"] = runs
        return StepOutcome(output=f"已完成 {len(runs)} 个数据集分析")

    def _insight(self, summary: dict) -> str:
        if not self.llm:
            return ""
        kind = summary.get("table_kind")
        recommended = summary.get("recommended_view")
        columns = [
            {
                "name": column.get("name"),
                "role": column.get("role"),
                "type": column.get("type"),
                "unique": column.get("unique"),
                "missing": column.get("missing"),
                "min": column.get("min"),
                "max": column.get("max"),
                "mean": column.get("mean"),
            }
            for column in summary.get("column_summary", [])[:12]
        ]
        sample = summary.get("sample", [])[:8]
        messages = [
            {
                "role": "system",
                "content": (
                    "你是数据分析助手。根据给定的 CSV 画像，用中文写一段 150–220 字的解读，"
                    "包含：这张表适合回答什么问题、最值得看的 2–3 个信号、以及使用数据时的"
                    "一个注意事项。数值范围以 columns_detail 的 min/max 为准，sample_rows 只是前几行；"
                    "不要编造表中没有的数字，不要使用 Markdown 标题。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "rows": summary.get("rows"),
                        "columns": summary.get("columns"),
                        "table_kind": TABLE_LABELS.get(kind, kind),
                        "recommended_view": VIEW_LABELS.get(recommended, recommended),
                        "columns_detail": columns,
                        "sample_rows": sample,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            result = self.llm.chat(
                messages,
                temperature=0.3,
                max_tokens=700,
                timeout=60,
                retries=0,
            )
        except Exception as exc:  # noqa: BLE001 - insight is optional
            logger.warning("csv_insight_unavailable: %s", exc)
            return ""
        text = " ".join((result.text or "").split())
        return text[:600]

    @staticmethod
    def _dataset(source: dict, summary: dict) -> dict:
        recommended = summary.get("recommended_view", "profile_bar")
        charts = summary.get("charts", {}) or {}
        return {
            "name": source["name"],
            "artifact": source["artifact"],
            "rows": summary.get("rows"),
            "columns": summary.get("columns"),
            "duplicate_rows": summary.get("duplicate_rows"),
            "missing_cells": summary.get("missing_cells"),
            "completeness": summary.get("completeness"),
            "profile": summary.get("profile", {}),
            "table_kind": summary.get("table_kind"),
            "recommended_view": recommended,
            "view_options": summary.get("view_options", []),
            "charts": charts,
            "sample": summary.get("sample", []),
            "insight": summary.get("insight", ""),
        }

    def _save_summary(self, context: TaskContext) -> StepOutcome:
        """下载每个 summary.json，合并成 v3，生成解读与报告。"""
        task_dir = context.artifact_store.ensure_task_dir(context.task_id)
        multi = len(context.state["runs"]) > 1
        datasets = []
        for index, run in enumerate(context.state["runs"]):
            remote_name = run["summary"]
            local_name = remote_name if not multi else f"summary-{index + 1:02d}.json"
            local_path = task_dir / local_name
            result = self.sandbox.download_file(context.task_id, remote_name, local_path)
            if not result["success"]:
                error = result["error"]
                raise StepExecutionError(error["code"], error["message"])
            try:
                summary = json.loads(local_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StepExecutionError("INTERNAL_ERROR", f"invalid CSV summary: {exc}") from exc
            summary["insight"] = "" if multi else self._insight(summary)
            source = context.state["sources"][index]
            datasets.append(self._dataset(source, summary))
            if not multi:
                local_path.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        if multi:
            total_rows = sum(int(item.get("rows") or 0) for item in datasets)
            total_dupes = sum(int(item.get("duplicate_rows") or 0) for item in datasets)
            total_missing = sum(int(item.get("missing_cells") or 0) for item in datasets)
            completeness = (
                sum(float(item.get("completeness") or 0) for item in datasets) / len(datasets)
                if datasets
                else 100.0
            )
            summary = {
                "version": 3,
                "dataset_count": len(datasets),
                "datasets": datasets,
                "rows": total_rows,
                "columns": datasets[0].get("columns") if datasets else 0,
                "duplicate_rows": total_dupes,
                "missing_cells": total_missing,
                "completeness": round(completeness, 1),
                "table_kind": "multi_dataset",
                "recommended_view": "profile_bar",
                "view_options": [
                    {"id": "profile_bar", "label": VIEW_LABELS["profile_bar"], "enabled": True, "score": 1, "reason": "多数据集总览"}
                ],
                "charts": datasets[0].get("charts", {}) if datasets else {},
                "insight": "",
                "palette": PALETTE,
            }
        else:
            summary = dict(datasets[0]) if datasets else {}
            summary.update(
                {
                    "version": 3,
                    "dataset_count": 1,
                    "datasets": datasets,
                    "rows": datasets[0].get("rows") if datasets else 0,
                    "columns": datasets[0].get("columns") if datasets else 0,
                    "duplicate_rows": datasets[0].get("duplicate_rows") if datasets else 0,
                    "missing_cells": datasets[0].get("missing_cells") if datasets else 0,
                    "completeness": datasets[0].get("completeness") if datasets else 100.0,
                    "table_kind": datasets[0].get("table_kind") if datasets else "mixed",
                    "recommended_view": datasets[0].get("recommended_view") if datasets else "profile_bar",
                    "view_options": datasets[0].get("view_options") if datasets else [],
                    "charts": datasets[0].get("charts") if datasets else {},
                    "insight": datasets[0].get("insight") if datasets else "",
                    "palette": PALETTE,
                }
            )
        summary_path = task_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary_spec = ArtifactSpec(
            filename="summary.json",
            content_type="application/json",
            size_bytes=summary_path.stat().st_size,
        )

        report_lines = ["# CSV 分析报告", ""]
        if multi:
            report_lines.extend(
                [
                    f"- 数据集：{len(datasets)} 个",
                    f"- 合计规模：{summary['rows']} 行",
                    f"- 重复行：{summary['duplicate_rows']}",
                    f"- 缺失值：{summary['missing_cells']} 个",
                    "",
                    "## 数据集概览",
                    "",
                    "| 文件 | 行 | 列 | 表型 | 推荐视图 |",
                    "| --- | ---: | ---: | --- | --- |",
                ]
            )
            for item in datasets:
                report_lines.append(
                    f"| {item['name']} | {item.get('rows')} | {item.get('columns')} | "
                    f"{TABLE_LABELS.get(item.get('table_kind'), item.get('table_kind'))} | "
                    f"{VIEW_LABELS.get(item.get('recommended_view'), item.get('recommended_view'))} |"
                )
            report_lines.extend(["", "多数据集模式会为每个数据集生成全部可用图表。"])
        else:
            item = datasets[0]
            type_labels = {"integer": "整数", "number": "数值", "string": "文本"}
            report_lines.extend(
                [
                    f"- 文件：{item['name']}",
                    f"- 数据规模：{item.get('rows')} 行 × {item.get('columns')} 列",
                    f"- 重复行：{item.get('duplicate_rows')}",
                    f"- 缺失值：{item.get('missing_cells')} 个（完整度 {item.get('completeness')}%）",
                    f"- 表型：{TABLE_LABELS.get(item.get('table_kind'), item.get('table_kind'))}",
                    f"- 推荐视图：{VIEW_LABELS.get(item.get('recommended_view'), item.get('recommended_view'))}",
                    "",
                    "## 分析结论",
                    "",
                    item.get("insight") or "模型解读暂不可用；图表与列画像已根据本地规则生成。",
                    "",
                    "## 视图建议",
                    "",
                ]
            )
            enabled_views = [option.get("label") for option in item.get("view_options", []) if option.get("enabled")]
            disabled_views = [
                f"{option.get('label')}（{option.get('reason')}）"
                for option in item.get("view_options", [])
                if not option.get("enabled")
            ]
            report_lines.extend(
                [
                    f"- 可用：{'、'.join(enabled_views) or '仅列总览'}",
                    f"- 不适合：{'；'.join(disabled_views) or '无'}",
                    "",
                    "## 列概览",
                    "",
                    "| 列名 | 类型 | 角色 | 非空 | 缺失 | 唯一值 | 摘要 |",
                    "| --- | --- | --- | ---: | ---: | ---: | --- |",
                ]
            )
            for column in item.get("profile", {}).get("columns", []):
                label = type_labels.get(column.get("type"), column.get("type"))
                role = ROLE_LABELS.get(column.get("role"), column.get("role"))
                if column.get("type") in {"integer", "number"}:
                    detail = (
                        f"最小 {column.get('min')}，最大 {column.get('max')}，"
                        f"均值 {column.get('mean'):.2f}"
                    )
                else:
                    examples = "、".join(column.get("examples", []))
                    tops = "、".join(
                        f"{top['value']}({top['count']})" for top in column.get("top_values", [])
                    )
                    detail = f"示例：{examples}" + (f"；高频：{tops}" if tops else "")
                report_lines.append(
                    f"| {column.get('name')} | {label} | {role} | {column.get('count')} | "
                    f"{column.get('missing')} | {column.get('unique')} | {detail} |"
                )

        report_spec = context.artifact_store.write_text(
            context.task_id,
            "report.md",
            "\n".join(report_lines) + "\n",
        )
        context.state["summary"] = summary

        return StepOutcome(
            output=(
                f"分析完成：{summary.get('dataset_count')} 个数据集，"
                f"{summary.get('rows')} 行，表型 "
                f"{TABLE_LABELS.get(summary.get('table_kind'), summary.get('table_kind'))}"
            ),
            artifacts=[summary_spec, report_spec],
            result={
                "kind": "csv_analysis",
                "message": "CSV 分析完成",
                "dataset_count": summary.get("dataset_count", 1),
                "datasets": datasets,
                "rows": summary.get("rows"),
                "columns": summary.get("columns"),
                "duplicate_rows": summary.get("duplicate_rows"),
                "missing_values": summary.get("missing_cells"),
                "completeness": summary.get("completeness"),
                "table_kind": summary.get("table_kind"),
                "recommended_view": summary.get("recommended_view"),
                "profile": summary.get("profile", {}),
                "view_options": summary.get("view_options", []),
                "charts": summary.get("charts", {}),
                "insight": summary.get("insight", ""),
                "palette": PALETTE,
                "summary_artifact": "summary.json",
            },
        )
