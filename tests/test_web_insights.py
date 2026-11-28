"""目标精读、自定义字段、网页对比与连续追问的单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.kb import KnowledgeBaseService
from backend.main import create_app
from backend.storage import ArtifactStore
from backend.templates.base import TaskContext
from backend.templates.base import StepExecutionError
from backend.templates.web_collect import WebDigestTemplate
from backend.templates.web_compare import WebCompareTemplate


class FakeSandbox:
    def __init__(self, text: str = "这是一个用于测试的网页正文，包含足够多的中文内容，用来通过正文长度校验。") -> None:
        self.text = text
        self.titles = {}

    def navigate(self, url, timeout_s=None):
        return {"success": True, "data": {"url": url, "title": url, "status": 200, "load_time_ms": 10}, "error": None}

    def get_page_markdown(self):
        return {"success": True, "data": {"title": "页面", "markdown": self.text}, "error": None}

    def get_page_text(self):
        return {"success": True, "data": {"text": self.text}, "error": None}

    def evaluate(self, expression):
        return {"success": True, "data": {"items": [], "selector": ""}, "error": None}

    def take_screenshot(self, path: Path, full_page: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return {"success": True, "data": {"path": str(path), "bytes": path.stat().st_size}, "error": None}


class FakeLLM:
    """按调用类型返回固定内容，避免真实网络。"""

    def __init__(self, reading: str = "- 要点一 (原文：第1段)\n- 要点二") -> None:
        self.reading = reading
        self.json_payload = {"rows": [{"报名截止": "2026-12-01", "地点": "无数据"}]}

    def available(self) -> bool:
        return True

    def chat(self, messages, **kwargs):
        return SimpleNamespace(text=self.reading)

    def chat_json(self, messages, **kwargs):
        return self.json_payload

    def chat_stream(self, messages, **kwargs):
        yield self.reading


def run_template(template, params, task_id="t-1"):
    root = Path(tempfile.mkdtemp())
    store = ArtifactStore(root, 10 * 1024 * 1024)
    context = TaskContext(task_id=task_id, params=params, artifact_store=store, state={})
    for step in template.steps:
        step.action(context)
    return context, store


class WebInsightsTests(unittest.TestCase):
    def test_goal_generates_reading_artifact(self):
        template = WebDigestTemplate(FakeSandbox(), llm=FakeLLM())
        context, store = run_template(
            template, {"url": "https://example.com", "goal": "notes"}
        )
        self.assertTrue(store.resolve("t-1", "reading.md").is_file())
        self.assertEqual(context.state["words"] > 0, True)

    def test_custom_fields_write_csv(self):
        template = WebDigestTemplate(FakeSandbox(), llm=FakeLLM())
        context, store = run_template(
            template,
            {
                "url": "https://example.com",
                "fields": [{"name": "报名截止", "description": "日期"}, {"name": "地点"}],
            },
        )
        csv_path = store.resolve("t-1", "fields.csv")
        rows = csv_path.read_text(encoding="utf-8")
        self.assertIn("无数据", rows)

    def test_compare_writes_natural_language_report(self):
        llm = FakeLLM(
            reading="## 总体判断\n两者定位不同 [1]。\n\n## 结论\nFlask 偏轻量，Django 偏一体化。"
        )
        template = WebCompareTemplate(FakeSandbox(), llm=llm)
        context, store = run_template(
            template,
            {"urls": ["https://a.example.com", "https://b.example.com"], "dimensions": ["性能"]},
        )
        report = store.resolve("t-1", "compare.md").read_text(encoding="utf-8")
        self.assertIn("# 网页对比分析", report)
        self.assertIn("Flask 偏轻量，Django 偏一体化。", report)
        self.assertIn("## 来源", report)
        self.assertEqual(context.state["compare"]["report"].splitlines()[0], "## 总体判断")

    def test_compare_degrades_to_source_summary_when_model_returns_nothing(self):
        template = WebCompareTemplate(FakeSandbox(), llm=FakeLLM(reading="   "))
        context, store = run_template(
            template,
            {"urls": ["https://a.example.com", "https://b.example.com"]},
        )
        self.assertTrue(context.state["compare"]["degraded"])
        report = store.resolve("t-1", "compare.md").read_text(encoding="utf-8")
        self.assertIn("自动摘要", report)


class ConversationTests(unittest.TestCase):
    def test_history_is_scoped_by_conversation(self):
        from backend.db import InMemoryTaskStore

        store = InMemoryTaskStore()
        kb = store.create_knowledge_base("测试库", "", "u1")

        class Index:
            def query(self, **kwargs):
                return [{"document_id": "d1", "title": "页面", "url": "u", "text": "片段", "score": 0.9}]

        class Emb:
            def available(self):
                return True

            def embed(self, texts):
                return [[0.1, 0.2] for _ in texts]

            def chat(self, messages, **kwargs):
                return SimpleNamespace(text="回答 [1]")

        service = KnowledgeBaseService(store, ArtifactStore(Path(tempfile.mkdtemp()), 10**7), Emb(), Index())
        service.ask(kb.id, "u1", "第一问", conversation_id="c1")
        service.ask(kb.id, "u1", "第二问", conversation_id="c2")
        first = service.history(kb.id, "u1", 10, "c1")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].question, "第一问")


if __name__ == "__main__":
    unittest.main()


class CompareApiTests(unittest.TestCase):
    """对比任务必须合并成一个任务，批量接口应直接拒绝。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="boxpilot-compare-"))
        self.client = TestClient(
            create_app(
                database_path=self.tmp / "tasks.db",
                artifact_store=ArtifactStore(self.tmp),
                auth_required=False,
            )
        )
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        for path in sorted(self.tmp.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.tmp.rmdir()

    def test_batch_rejects_compare(self) -> None:
        response = self.client.post(
            "/tasks/batch",
            json={"task_type": "WEB_COMPARE", "targets": ["https://a.example.com", "https://b.example.com"]},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "BATCH_UNSUPPORTED")
