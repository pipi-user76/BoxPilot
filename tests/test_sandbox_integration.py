from __future__ import annotations

import os
import unittest

from app.adapters.sandbox import SandboxAdapter


@unittest.skipUnless(os.getenv("BOXPILOT_RUN_SANDBOX_TESTS") == "1", "set BOXPILOT_RUN_SANDBOX_TESTS=1")
class SandboxIntegrationTests(unittest.TestCase):
    def test_shell_file_and_browser(self) -> None:
        adapter = SandboxAdapter()
        self.assertTrue(adapter.health()["success"])
        self.assertTrue(adapter.ensure_task_workspace("integration-task")["success"])
        self.assertTrue(adapter.write_text_file("integration-task", "check.txt", "ok")["success"])
        self.assertEqual(adapter.read_text_file("integration-task", "check.txt")["data"]["content"], "ok")
        self.assertTrue(adapter.exec_shell("printf 'ok'", task_id="integration-task", timeout_s=20)["success"])
        self.assertTrue(adapter.navigate("http://127.0.0.1:8080", timeout_s=20)["success"])


if __name__ == "__main__":
    unittest.main()
