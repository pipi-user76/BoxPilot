"""文件职责：LLM 网关的重试、JSON 模式、嵌入与错误归类。"""

from __future__ import annotations

import unittest
from unittest import mock

from backend.config import settings
from backend.llm import LLMClient, LLMError


def _chat_body(text: str = "ok") -> dict:
    return {
        "model": "fake-model",
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }


class LLMTests(unittest.TestCase):
    def test_chat_reports_usage(self) -> None:
        calls: list[str] = []

        def transport(path: str, payload: dict, timeout: float) -> dict:
            calls.append(path)
            self.assertEqual(payload["model"], "fake-model")
            return _chat_body("hello")

        client = LLMClient(base_url="http://fake", api_key="k", model="fake-model", transport=transport)
        result = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result.text, "hello")
        self.assertEqual(result.usage["prompt_tokens"], 3)
        self.assertEqual(calls, ["/chat/completions"])

    def test_temperature_is_optional(self) -> None:
        seen: list[dict] = []

        def transport(path: str, payload: dict, timeout: float) -> dict:
            seen.append(payload)
            return _chat_body("ok")

        client = LLMClient(base_url="http://fake", api_key="k", model="fake-model", transport=transport)
        with mock.patch.object(settings, "LLM_TEMPERATURE", None):
            client.chat([{"role": "user", "content": "hi"}])
        client.chat([{"role": "user", "content": "hi"}], temperature=0.3)
        self.assertNotIn("temperature", seen[0])
        self.assertEqual(seen[1]["temperature"], 0.3)

    def test_retries_on_server_error(self) -> None:
        attempts = {"count": 0}

        def transport(path: str, payload: dict, timeout: float) -> dict:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise LLMError("LLM_HTTP_ERROR", "boom", status=500)
            return _chat_body("third")

        client = LLMClient(
            base_url="http://fake", api_key="k", model="m", transport=transport,
            max_retries=2, sleep=lambda _: None,
        )
        self.assertEqual(client.chat([{"role": "user", "content": "hi"}]).text, "third")
        self.assertEqual(attempts["count"], 3)

    def test_does_not_retry_client_error(self) -> None:
        attempts = {"count": 0}

        def transport(path: str, payload: dict, timeout: float) -> dict:
            attempts["count"] += 1
            raise LLMError("LLM_HTTP_ERROR", "bad", status=400)

        client = LLMClient(
            base_url="http://fake", api_key="k", model="m", transport=transport,
            max_retries=2, sleep=lambda _: None,
        )
        with self.assertRaises(LLMError):
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(attempts["count"], 1)

    def test_json_mode_rejects_non_object(self) -> None:
        client = LLMClient(
            base_url="http://fake", api_key="k", model="m",
            transport=lambda path, payload, timeout: _chat_body("[1, 2]"),
        )
        with self.assertRaises(LLMError) as caught:
            client.chat_json([{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "LLM_BAD_JSON")

    def test_embed_orders_by_index(self) -> None:
        def transport(path: str, payload: dict, timeout: float) -> dict:
            self.assertEqual(path, "/embeddings")
            return {
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            }

        client = LLMClient(base_url="http://fake", api_key="k", model="m", transport=transport)
        self.assertEqual(client.embed(["a", "b"]), [[1.0, 0.0], [0.0, 1.0]])

    def test_missing_key(self) -> None:
        client = LLMClient(base_url="http://fake", api_key="", model="m")
        self.assertFalse(client.available())
        with self.assertRaises(LLMError) as caught:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "LLM_NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()
