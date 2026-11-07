"""文件职责：验证 CSV 上传接口、固定分析流程和标准库分析脚本。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.csv_analyzer import analyze_csv
from backend.main import create_app
from backend.storage import ArtifactStore

try:
    from tests.support import make_temp_dir, remove_temp_dir, workspace_temp_dir
except ImportError:  # 兼容以 tests 目录为顶层的 unittest discover
    from support import make_temp_dir, remove_temp_dir, workspace_temp_dir


class CsvSandbox:
    """CSV 模板测试替身，模拟上传、执行和结果下载。"""

    def __init__(self) -> None:
        self.summary = {
            "rows": 3,
            "columns": 2,
            "column_names": ["value", "group"],
            "duplicate_rows": 0,
            "column_summary": {
                "value": {"type": "integer", "count": 3, "missing": 0, "mean": 2.0},
                "group": {"type": "string", "count": 3, "missing": 0, "unique": 2},
            },
        }

    def ensure_task_workspace(self, task_id: str) -> dict:
        return {"success": True, "data": {"path": f"/home/gem/boxpilot/{task_id}"}, "error": None}

    def task_workdir(self, task_id: str) -> str:
        return f"/home/gem/boxpilot/{task_id}"

    def upload_file(self, task_id: str, local_path: Path, relative_path: str) -> dict:
        return {"success": True, "data": {"path": relative_path}, "error": None}

    def exec_shell(self, command: str, task_id: str, timeout_s: int = 120, check_exit: bool = True) -> dict:
        return {"success": True, "data": {"output": '{"success": true}', "exit_code": 0}, "error": None}

    def download_file(self, task_id: str, relative_path: str, local_path: Path) -> dict:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(json.dumps(self.summary, ensure_ascii=False), encoding="utf-8")
        return {"success": True, "data": {"path": str(local_path), "bytes": local_path.stat().st_size}, "error": None}

    def health(self) -> dict:
        return {"success": True, "data": {"base_url": "fake"}, "error": None}


class CsvApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(remove_temp_dir, self.temp_dir)
        artifact_store = ArtifactStore(self.temp_dir)
        context = TestClient(create_app(artifact_store=artifact_store, adapter=CsvSandbox()))
        self.addCleanup(context.__exit__, None, None, None)
        self.client = context.__enter__()

    def test_csv_upload_runs_template_and_downloads_artifacts(self) -> None:
        """正常 CSV 应创建任务并生成 summary.json、report.txt。"""
        response = self.client.post(
            "/tasks/csv",
            files={"file": ("sample.csv", b"value,group\n1,a\n2,b\n3,a\n", "text/csv")},
        )
        self.assertEqual(response.status_code, 202)
        task = self.client.get(f"/tasks/{response.json()['id']}").json()
        self.assertEqual(task["status"], "SUCCESS")
        self.assertEqual([item["filename"] for item in task["artifacts"]], ["summary.json", "report.txt"])

        summary_artifact = next(item for item in task["artifacts"] if item["filename"] == "summary.json")
        summary = self.client.get(f"/artifacts/{summary_artifact['id']}/download").json()
        self.assertEqual(summary["rows"], 3)
        self.assertEqual(summary["columns"], 2)
        self.assertEqual(task["result"]["summary"]["kind"], "csv_analysis")
        self.assertEqual(task["result"]["summary"]["rows"], 3)
        self.assertEqual(task["steps"][0]["name"], "校验 CSV 文件")

    def test_csv_upload_rejects_non_csv_file(self) -> None:
        """扩展名不是 .csv 时，不应创建任务。"""
        response = self.client.post(
            "/tasks/csv",
            files={"file": ("notes.txt", b"not csv", "text/plain")},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "VALIDATION_ERROR")

    def test_json_csv_task_requires_upload_endpoint(self) -> None:
        """CSV 类型不能绕过文件上传接口直接通过 JSON 创建。"""
        response = self.client.post("/tasks", json={"task_type": "CSV_ANALYSIS", "params": {}})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "CSV_UPLOAD_REQUIRED")


class CsvAnalyzerTests(unittest.TestCase):
    def test_analyzer_counts_missing_and_duplicate_rows(self) -> None:
        """直接验证标准库分析脚本的核心统计口径。"""
        with workspace_temp_dir() as temp_dir:
            source = Path(temp_dir) / "sample.csv"
            source.write_text(
                "name,score,group\nA,10,x\nB,20,x\nB,20,x\nC,,y\n",
                encoding="utf-8",
            )
            summary = analyze_csv(source)
        self.assertEqual(summary["rows"], 4)
        self.assertEqual(summary["columns"], 3)
        self.assertEqual(summary["duplicate_rows"], 1)
        self.assertEqual(summary["column_summary"]["score"]["missing"], 1)
        self.assertEqual(summary["column_summary"]["score"]["count"], 3)

    def test_analyzer_rejects_empty_csv(self) -> None:
        """空文件应抛出明确错误，而不是生成空摘要。"""
        with workspace_temp_dir() as temp_dir:
            source = Path(temp_dir) / "empty.csv"
            source.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty"):
                analyze_csv(source)


if __name__ == "__main__":
    unittest.main()
