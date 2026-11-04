"""文件职责：对真实 AIO Sandbox 执行 Shell、文件、浏览器和截图冒烟检查。

运行该脚本会访问已启动的沙箱服务，并在 artifacts/smoke 下保存截图。脚本任一步
失败都会以非零状态退出，适合每次改完适配层后手工或 CI 执行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.sandbox_client import SandboxAdapter


def main() -> int:
    # 每次使用随机任务 ID，避免复用旧目录导致测试结果被历史文件污染。
    parser = argparse.ArgumentParser(description="Verify AIO Sandbox Shell, file, and browser APIs.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()

    task_id = f"smoke-{uuid4().hex[:8]}"
    adapter = SandboxAdapter(base_url=args.base_url)
    output_dir = Path("artifacts/smoke")
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, object] = {"task_id": task_id, "checks": {}}
    checks: dict[str, dict] = {}
    report["checks"] = checks

    workspace = adapter.ensure_task_workspace(task_id)
    # 工作目录创建失败时立即停止，后续文件测试没有意义。
    checks["workspace"] = workspace
    if not workspace["success"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    checks["file_write"] = adapter.write_text_file(task_id, "smoke.txt", "BoxPilot sandbox file check")
    checks["file_read"] = adapter.read_text_file(task_id, "smoke.txt")
    checks["shell"] = adapter.exec_shell("printf 'BoxPilot shell check'", task_id=task_id, timeout_s=20)
    checks["navigation"] = adapter.navigate("http://127.0.0.1:8080", timeout_s=20)

    # 页面文本只记录长度，避免把整页内容输出到终端。
    page_text = adapter.get_page_text()
    checks["page_text"] = {
        "success": page_text["success"],
        "data": {"length": len(page_text["data"]["text"])} if page_text["success"] else None,
        "error": page_text["error"],
    }
    checks["screenshot"] = adapter.take_screenshot(output_dir / "smoke.png", full_page=False)

    success = all(check["success"] for check in checks.values())
    report["success"] = success
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
