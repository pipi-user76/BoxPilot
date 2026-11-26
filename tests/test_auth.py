"""认证与数据隔离回归：未登录拦截、登录态、归属过滤与越权访问。"""

from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.storage import ArtifactStore

try:
    from tests.support import make_temp_dir, remove_temp_dir
except ImportError:  # 兼容以 tests 目录为顶层的 unittest discover
    from support import make_temp_dir, remove_temp_dir


class StubSandbox:
    def ensure_task_workspace(self, task_id: str) -> dict:
        return {"success": True, "data": {"path": f"/home/gem/boxpilot/{task_id}"}, "error": None}

    def task_workdir(self, task_id: str) -> str:
        return f"/home/gem/boxpilot/{task_id}"

    def exec_shell(self, command: str, task_id: str, timeout_s: int = 120) -> dict:
        return {"success": True, "data": {"output": "boxpilot mock ok", "exit_code": 0}, "error": None}

    def health(self) -> dict:
        return {"success": True, "data": {"base_url": "fake"}, "error": None}


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.app = create_app(
            database_path=self.temp_dir / "tasks.db",
            artifact_store=ArtifactStore(self.temp_dir / "artifacts"),
            adapter=StubSandbox(),
        )
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        remove_temp_dir(self.temp_dir)

    def login(self, username: str, password: str):
        response = self.client.post("/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["user"]

    def logout(self) -> None:
        self.client.post("/auth/logout")
        self.client.cookies.clear()

    def test_unauthenticated_requests_are_blocked(self) -> None:
        self.assertEqual(self.client.get("/tasks").status_code, 401)
        page = self.client.get("/ui/task_list.html", follow_redirects=False)
        self.assertEqual(page.status_code, 303)
        self.assertIn("/ui/login.html", page.headers["location"])
        self.assertTrue(self.client.get("/health").status_code == 200)

    def test_bootstrap_register_and_login(self) -> None:
        self.assertTrue(self.client.get("/auth/bootstrap").json()["needs_admin"])
        created = self.client.post("/auth/register", json={"username": "alice", "password": "secret123"})
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["user"]["is_admin"])
        self.assertFalse(self.client.get("/auth/bootstrap").json()["needs_admin"])
        # 第二个账号不能自助注册，必须由管理员添加。
        self.client.cookies.clear()
        again = self.client.post("/auth/register", json={"username": "bob", "password": "secret123"})
        self.assertEqual(again.status_code, 403)

        self.assertEqual(self.client.post("/auth/login", json={"username": "alice", "password": "wrong"}).status_code, 401)
        self.login("alice", "secret123")
        me = self.client.get("/auth/me").json()
        self.assertEqual(me["username"], "alice")

    def test_tasks_are_isolated_between_users(self) -> None:
        self.client.post("/auth/register", json={"username": "alice", "password": "secret123"})
        self.login("alice", "secret123")
        task = self.client.post("/tasks", json={"task_type": "MOCK", "params": {}}).json()
        detail = self.client.get(f"/tasks/{task['id']}").json()
        artifact_id = detail["artifacts"][0]["id"]

        # 管理员创建普通用户 bob。
        bob = self.client.post("/auth/users", json={"username": "bob", "password": "secret123"})
        self.assertEqual(bob.status_code, 201)
        self.assertFalse(bob.json()["is_admin"])

        self.logout()
        self.login("bob", "secret123")
        self.assertEqual(self.client.get("/tasks").json()["total"], 0)
        self.assertEqual(self.client.get(f"/tasks/{task['id']}").status_code, 404)
        self.assertEqual(self.client.post(f"/tasks/{task['id']}/cancel").status_code, 404)
        self.assertEqual(self.client.get(f"/artifacts/{artifact_id}/download").status_code, 404)
        self.assertEqual(
            self.client.get(f"/tasks/artifacts/archive?task_ids={task['id']}").status_code, 404
        )

        # 管理员仍然能看到并下载。
        self.logout()
        self.login("alice", "secret123")
        self.assertEqual(self.client.get("/tasks").json()["total"], 1)
        self.assertEqual(self.client.get(f"/artifacts/{artifact_id}/download").status_code, 200)

    def test_logout_invalidates_access(self) -> None:
        self.client.post("/auth/register", json={"username": "alice", "password": "secret123"})
        self.logout()
        self.assertEqual(self.client.get("/tasks").status_code, 401)

    def test_login_assets_are_public(self) -> None:
        # 登录页自己的 css/js 必须在未登录时可加载，否则页面会白屏。
        css = self.client.get("/ui/login.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("text/css", css.headers["content-type"])
        hero = self.client.get("/ui/hero.js")
        self.assertEqual(hero.status_code, 200)
        self.assertIn("javascript", hero.headers["content-type"])

    def test_admin_manages_accounts(self) -> None:
        self.client.post("/auth/register", json={"username": "alice", "password": "secret123"})
        self.login("alice", "secret123")
        created = self.client.post(
            "/auth/users",
            json={"username": "carol", "password": "secret123", "is_admin": True},
        )
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["is_admin"])

        users = self.client.get("/auth/users").json()
        self.assertEqual([user["username"] for user in users], ["alice", "carol"])

        # 普通用户不能查看或创建账号。
        self.client.post("/auth/users", json={"username": "bob", "password": "secret123"})
        self.logout()
        self.login("bob", "secret123")
        self.assertEqual(self.client.get("/auth/users").status_code, 403)
        self.assertEqual(
            self.client.post("/auth/users", json={"username": "eve", "password": "secret123"}).status_code,
            403,
        )

        # 新账号可以直接登录。
        self.logout()
        self.login("carol", "secret123")

    def test_duplicate_username_rejected(self) -> None:
        self.client.post("/auth/register", json={"username": "alice", "password": "secret123"})
        duplicate = self.client.post("/auth/users", json={"username": "ALICE", "password": "secret123"})
        self.assertEqual(duplicate.status_code, 409)


if __name__ == "__main__":
    unittest.main()
