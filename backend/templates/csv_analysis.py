"""文件职责：把上传 CSV 送入沙箱分析，并归档 JSON 摘要与文本报告。

CSV 输入由 POST /tasks/csv 保存到当前任务目录的 input.csv。本模板负责上传分析脚本、
执行固定命令、下载 summary.json，并生成人类可读的 report.txt。
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.config import settings
from backend.sandbox_client import SandboxAdapter
from backend.storage import ArtifactSpec
from backend.templates.base import StepDefinition, StepExecutionError, StepOutcome, TaskContext


class CsvAnalysisTemplate:
    """固定 CSV 分析流程：上传、执行、下载并校验结果。"""

    name = "CSV_ANALYSIS"

    def __init__(self, sandbox: SandboxAdapter, timeout_s: int | None = None) -> None:
        self.sandbox = sandbox
        self.timeout_s = timeout_s or settings.REPO_TEST_TIMEOUT_SECONDS
        self.steps = (
            StepDefinition("校验 CSV 文件", self._validate_upload),
            StepDefinition("上传分析文件", self._upload_csv),
            StepDefinition("执行数据分析", self._run_analyzer),
            StepDefinition("生成分析报告", self._save_summary),
        )

    def _validate_upload(self, context: TaskContext) -> StepOutcome:
        """确认 input.csv 存在且分析脚本位于当前代码树中。"""
        input_path = context.artifact_store.root / context.task_id / "input.csv"
        analyzer_path = Path(__file__).resolve().parent.parent / "csv_analyzer.py"
        if not input_path.is_file() or input_path.stat().st_size == 0:
            raise StepExecutionError("FILE_NOT_FOUND", "uploaded input.csv is missing or empty")
        if not analyzer_path.is_file():
            raise StepExecutionError("FILE_NOT_FOUND", f"CSV analyzer not found: {analyzer_path}")
        context.state.update(input_path=input_path, analyzer_path=analyzer_path)
        size_kb = max(1, round(input_path.stat().st_size / 1024))
        return StepOutcome(output=f"CSV 文件校验通过，大小约 {size_kb} KB")

    def _upload_csv(self, context: TaskContext) -> StepOutcome:
        """上传原始 CSV 和固定分析脚本到任务工作目录。"""
        for local_path, remote_path in (
            (context.state["input_path"], "input.csv"),
            (context.state["analyzer_path"], "analyze_csv.py"),
        ):
            result = self.sandbox.upload_file(context.task_id, local_path, remote_path)
            if not result["success"]:
                error = result["error"]
                raise StepExecutionError(error["code"], error["message"])
        return StepOutcome(output="CSV 文件和分析脚本已上传到沙箱")

    def _run_analyzer(self, context: TaskContext) -> StepOutcome:
        """执行固定命令；非零退出码会让任务失败并保留错误输出。"""
        result = self.sandbox.exec_shell(
            "python analyze_csv.py input.csv summary.json",
            task_id=context.task_id,
            timeout_s=self.timeout_s,
        )
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])
        context.state["analyzer_output"] = result["data"].get("output") or ""
        return StepOutcome(output="数据分析命令执行完成")

    def _save_summary(self, context: TaskContext) -> StepOutcome:
        """下载 summary.json，确认可解析后再登记 JSON 和文本产物。"""
        task_dir = context.artifact_store.ensure_task_dir(context.task_id)
        summary_path = task_dir / "summary.json"
        result = self.sandbox.download_file(context.task_id, "summary.json", summary_path)
        if not result["success"]:
            error = result["error"]
            raise StepExecutionError(error["code"], error["message"])

        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StepExecutionError("INTERNAL_ERROR", f"invalid CSV summary: {exc}") from exc

        summary_spec = ArtifactSpec(
            filename="summary.json",
            content_type="application/json",
            size_bytes=summary_path.stat().st_size,
        )
        report_lines = [
            "BoxPilot CSV Analysis Report",
            f"Rows: {summary.get('rows')}",
            f"Columns: {summary.get('columns')}",
            f"Duplicate rows: {summary.get('duplicate_rows')}",
            "",
            "Column summary:",
        ]
        for name, column in summary.get("column_summary", {}).items():
            report_lines.append(f"- {name}: {json.dumps(column, ensure_ascii=False)}")
        report_spec = context.artifact_store.write_text(
            context.task_id,
            "report.txt",
            "\n".join(report_lines) + "\n",
        )
        context.state["summary"] = summary
        missing_values = sum(int(column.get("missing", 0)) for column in summary.get("column_summary", {}).values())
        return StepOutcome(
            output=(
                f"分析完成：{summary.get('rows')} 行、{summary.get('columns')} 列，"
                f"{summary.get('duplicate_rows')} 个重复行，{missing_values} 个缺失值"
            ),
            artifacts=[summary_spec, report_spec],
            result={
                "kind": "csv_analysis",
                "message": "CSV 分析完成",
                "rows": summary.get("rows"),
                "columns": summary.get("columns"),
                "duplicate_rows": summary.get("duplicate_rows"),
                "missing_values": missing_values,
            },
        )
