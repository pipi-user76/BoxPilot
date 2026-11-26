"""命令行创建账号：python -m scripts.create_user <用户名> <密码> [--admin]。

首次部署也可以直接打开 /ui/login.html 点“创建管理员”；这个脚本用于之后批量建号，
或在无法访问网页时兜底。
"""

from __future__ import annotations

import argparse
import sys

from backend.auth import hash_password
from backend.auth_store import SQLiteAuthStore
from backend.config import settings
from backend.sqlite_store import SQLiteTaskStore


def main() -> int:
    parser = argparse.ArgumentParser(description="创建 BoxPilot 账号")
    parser.add_argument("username")
    parser.add_argument("password")
    parser.add_argument("--admin", action="store_true", help="创建管理员账号")
    args = parser.parse_args()

    if len(args.password) < 8:
        print("密码至少 8 位", file=sys.stderr)
        return 2

    store = SQLiteTaskStore(settings.DATABASE_PATH)
    store.init_db()
    users = SQLiteAuthStore(settings.DATABASE_PATH)
    try:
        user = users.create_user(args.username, hash_password(args.password), is_admin=args.admin)
    except ValueError:
        print(f"用户名已存在：{args.username}", file=sys.stderr)
        return 1
    role = "管理员" if user.is_admin else "普通用户"
    print(f"已创建{role}：{user.username}（{user.id}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
