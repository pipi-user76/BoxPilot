"""文件职责：管理每个任务独立的本地产物目录，并阻止路径逃逸。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from backend.validation import safe_relative_path, validate_filename, validate_task_id


@dataclass(frozen=True)
class ArtifactSpec:
    filename: str
    content_type: str
    size_bytes: int


class ArtifactStore:
    """任务产物存储；数据库只保存相对路径，下载时再安全解析。"""

    def __init__(self, root: Path, max_bytes: int = 20 * 1024 * 1024):
        self.root = root.resolve()
        self.max_bytes = max_bytes

    def ensure_task_dir(self, task_id: str) -> Path:
        """创建任务目录前校验 task_id，防止 `../` 跳出 artifacts 根目录。"""
        validate_task_id(task_id)
        path = (self.root / task_id).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("task directory escapes artifact root")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_text(self, task_id: str, filename: str, content: str) -> ArtifactSpec:
        payload = content.encode("utf-8")
        return self.write_bytes(task_id, filename, payload, "text/plain; charset=utf-8")

    def write_bytes(
        self,
        task_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> ArtifactSpec:
        """写入单文件产物，并限制单文件大小，避免异常任务占满磁盘。"""
        validate_filename(filename)
        if len(content) > self.max_bytes:
            raise ValueError("artifact exceeds configured size limit")
        task_dir = self.ensure_task_dir(task_id)
        target = task_dir / filename
        target.write_bytes(content)
        return ArtifactSpec(filename=filename, content_type=content_type, size_bytes=target.stat().st_size)

    def resolve(self, task_id: str, relative_path: str) -> Path:
        """把数据库中的相对路径解析为真实文件，同时再次校验目录边界。"""
        validate_task_id(task_id)
        relative = safe_relative_path(relative_path)
        path = (self.root / task_id / relative).resolve()
        task_root = (self.root / task_id).resolve()
        if task_root not in path.parents:
            raise ValueError("artifact path escapes task directory")
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path

    def delete_task_dir(self, task_id: str) -> bool:
        """删除单个任务的产物目录；路径必须严格位于产物根目录下一层。"""
        validate_task_id(task_id)
        task_dir = (self.root / task_id).resolve()
        if task_dir.parent != self.root or not task_dir.is_dir():
            return False
        shutil.rmtree(task_dir)
        return True
