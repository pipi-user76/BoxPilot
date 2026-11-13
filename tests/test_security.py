"""文件职责：验证安全加固的四条边界——命令构造、URL/SSRF、幂等、限流与超时。

这些用例不访问真实沙箱和公网：DNS 解析、Shell 客户端都用替身，
保证测试在离线环境也能稳定复现安全判定。
"""

from __future__ import annotations

import socket
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.command import CommandBuildError, build_command, quote_arg
from backend.config import settings
from backend.db import InMemoryTaskStore
from backend.main import create_app
from backend.sandbox_client import SandboxAdapter
from backend.storage import ArtifactStore
from backend.validation import ValidationFailure, validate_http_url, validate_public_url

try:
    from tests.support import make_temp_dir, remove_temp_dir
except ImportError:  # 直接运行本文件时
    from support import make_temp_dir, remove_temp_dir


class FakeSandbox:
    """最小沙箱替身：只满足 MOCK 模板的命令与目录调用。"""

    def ensure_task_workspace(self, task_id: str) -> dict:
        return {"success": True, "data": {"task_dir": task_id}, "error": None}

    def exec_shell(self, command: str, task_id: str, timeout_s: int | None = None, check_exit: bool = True) -> dict:
        return {
            "success": True,
            "data": {"output": "boxpilot mock ok", "exit_code": 0, "status": "finished"},
            "error": None,
        }

    def health(self) -> dict:
        return {"success": True, "data": {"status": "ok"}, "error": None}


PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
PRIVATE_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]


class CommandTests(unittest.TestCase):
    def test_quote_arg_escapes_shell_metacharacters(self) -> None:
        self.assertEqual(quote_arg("a;rm -rf /"), "'a;rm -rf /'")

    def test_quote_arg_rejects_control_characters_and_long_values(self) -> None:
        with self.assertRaises(CommandBuildError):
            quote_arg("line\nbreak")
        with self.assertRaises(CommandBuildError):
            quote_arg("x" * (settings.COMMAND_MAX_LENGTH + 1))

    def test_build_command_appends_terminator(self) -> None:
        command = build_command("git clone", quote_arg("https://example.com/a"), terminator=True)
        self.assertTrue(command.endswith(" --"))


class UrlGuardTests(unittest.TestCase):
    def test_rejects_non_standard_ports(self) -> None:
        with self.assertRaises(ValidationFailure):
            validate_http_url("http://example.com:8080/")

    def test_rejects_metadata_and_container_hosts(self) -> None:
        for url in (
            "http://host.docker.internal/",
            "http://metadata.google.internal/",
            "http://127.0.0.1/",
        ):
            with self.subTest(url=url), self.assertRaises(ValidationFailure):
                validate_public_url(url)

    @patch("backend.validation.socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_accepts_public_address(self, _dns) -> None:
        self.assertEqual(validate_public_url("https://example.com/page"), "https://example.com/page")

    @patch("backend.validation.socket.getaddrinfo", return_value=PRIVATE_DNS)
    def test_rejects_private_resolution(self, _dns) -> None:
        with self.assertRaises(ValidationFailure):
            validate_public_url("https://example.com/")


class SandboxTimeoutTests(unittest.TestCase):
    def test_exit_code_137_maps_to_step_killed(self) -> None:
        class StubShell:
            def exec_command(self, **kwargs):
                class Data:
                    status = "finished"
                    output = "killed"
                    exit_code = 137

                class Response:
                    success = True
                    message = None
                    data = Data()

                return Response()

        class StubClient:
            shell = StubShell()

        adapter = object.__new__(SandboxAdapter)
        adapter.client = StubClient()
        adapter.default_timeout_s = 5  # type: ignore[attr-defined]
        result = adapter._exec_raw("true", exec_dir="/tmp")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "STEP_KILLED")
        self.assertEqual(result["error"]["exit_code"], 137)


class ApiSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.store = InMemoryTaskStore()
        self.app = create_app(
            task_store=self.store,
            artifact_store=ArtifactStore(Path(self.temp_dir), max_bytes=1024 * 1024),
            adapter=FakeSandbox(),
        )
        self.client = TestClient(self.app)
        # 进入上下文才会触发 lifespan，把 task_runner / stores 挂到 app.state。
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        remove_temp_dir(self.temp_dir)

    def test_duplicate_submission_returns_existing_task(self) -> None:
        payload = {"task_type": "MOCK", "params": {"note": "same"}}
        first = self.client.post("/tasks", json=payload)
        second = self.client.post("/tasks", json=payload)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertFalse(first.json()["deduplicated"])
        self.assertTrue(second.json()["deduplicated"])

    def test_different_params_create_new_task(self) -> None:
        first = self.client.post("/tasks", json={"task_type": "MOCK", "params": {"note": "a"}})
        second = self.client.post("/tasks", json={"task_type": "MOCK", "params": {"note": "b"}})
        self.assertNotEqual(first.json()["id"], second.json()["id"])

    def test_rate_limit_returns_429(self) -> None:
        with patch.object(settings, "RATE_LIMIT_PER_MINUTE", 2):
            statuses = [
                self.client.post("/tasks", json={"task_type": "MOCK", "params": {"note": str(index)}}).status_code
                for index in range(3)
            ]
        self.assertEqual(statuses[:2], [202, 202])
        self.assertEqual(statuses[2], 429)

    def test_rejects_private_url_in_web_digest(self) -> None:
        response = self.client.post(
            "/tasks",
            json={"task_type": "WEB_DIGEST", "params": {"url": "http://127.0.0.1:8080/"}},
        )
        self.assertEqual(response.status_code, 202)
        detail = self.client.get(f"/tasks/{response.json()['id']}").json()
        self.assertEqual(detail["status"], "FAILED")
        self.assertEqual(detail["error_code"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
