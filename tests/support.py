"""文件职责：为测试提供工作区内的临时目录。

Windows 沙箱下 tempfile.mkdtemp 创建的目录带有限制性权限，受限进程无法再写入子目录。
测试统一改用工作区内的 .tmp 目录，既避开该限制，也方便失败后保留现场排查。
"""

from __future__ import annotations

import logging
import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from backend.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".tmp"

# 测试只关心断言结果；预期失败会产生 WARNING 日志，这里压到 CRITICAL 保持输出干净。
settings.LOG_LEVEL = "CRITICAL"
logging.getLogger("boxpilot").setLevel(logging.CRITICAL)


def make_temp_dir() -> Path:
    """创建一个测试专用目录；调用方负责清理。"""
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / f"test-{uuid4().hex}"
    path.mkdir()
    return path


def remove_temp_dir(path: Path) -> None:
    """尽力删除测试目录，残留文件不应让测试本身失败。"""
    shutil.rmtree(path, ignore_errors=True)


@contextmanager
def workspace_temp_dir():
    """with 形式的临时目录，保持与 TemporaryDirectory 相同的使用习惯。"""
    path = make_temp_dir()
    try:
        yield path
    finally:
        remove_temp_dir(path)
