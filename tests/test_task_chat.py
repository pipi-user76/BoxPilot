"""Tests for the task-scoped chat service."""

from __future__ import annotations

import unittest

from backend.db import InMemoryTaskStore
from backend.llm import ChatResult
from backend.models import TaskRecord, TaskStatus, TaskType
from backend.task_chat import TaskChatService


class FakeArtifacts:
    def resolve(self, task_id, filename):  # noqa: ARG002
        raise FileNotFoundError(filename)


class FakeLLM:
    def __init__(self, text="结论是 42 [1]"):
        self.text = text
        self.prompts = []

    def chat(self, messages, max_tokens=None, **kwargs):  # noqa: ARG002
        self.prompts.append(messages)
        return ChatResult(text=self.text, model="fake", usage={}, raw={})


def make_task() -> TaskRecord:
    return TaskRecord(
        id="task-1",
        task_type=TaskType.WEB_COMPARE,
        status=TaskStatus.SUCCESS,
        params={"urls": ["https://a.example", "https://b.example"]},
        result={
            "summary": {
                "kind": "web_compare",
                "source_count": 2,
                "conclusion": "两者数据组织不同。",
            }
        },
        owner_id="u1",
    )


class TaskChatTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryTaskStore()
        self.task = self.store.create_task(
            TaskType.WEB_COMPARE,
            {"urls": ["https://a.example", "https://b.example"]},
            owner_id="u1",
        )
        self.store.finish_task(
            self.task.id,
            TaskStatus.SUCCESS,
            {"summary": {"kind": "web_compare", "source_count": 2, "conclusion": "两者数据组织不同。"}},
            None,
            None,
        )
        self.task = self.store.get_task(self.task.id)

    def test_seed_message_summarises_task(self):
        service = TaskChatService(self.store, FakeArtifacts(), FakeLLM())
        history = service.history(self.task)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].role, "assistant")
        self.assertIn("2 个来源", history[0].content)
        self.assertTrue(history[0].content.startswith("这份对比覆盖了 2 个来源"))
        self.assertIn("结论：", history[0].content)
        self.assertIn("[1]", history[0].content)

    def test_ask_uses_context_and_persists(self):
        llm = FakeLLM()
        service = TaskChatService(self.store, FakeArtifacts(), llm)
        reply = service.ask(self.task, "核心差别是什么？")
        self.assertIn("42", reply.content)
        self.assertIn("引用：[1]", reply.content)
        prompt = llm.prompts[0][0]["content"]
        self.assertIn("https://a.example", prompt)
        history = service.history(self.task)
        self.assertEqual([item.role for item in history], ["assistant", "user", "assistant"])

    def test_answer_keeps_plain_text(self):
        llm = FakeLLM(text="资料里没有提到这一点。")
        service = TaskChatService(self.store, FakeArtifacts(), llm)
        reply = service.ask(self.task, "再解释一下")
        self.assertIn("资料里没有提到", reply.content)
        self.assertNotIn("引用：", reply.content)


if __name__ == "__main__":
    unittest.main()
