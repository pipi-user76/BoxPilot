from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.validation import safe_relative_path, validate_filename, validate_task_id


@dataclass(frozen=True)
class ArtifactSpec:
    filename: str
    content_type: str
    size_bytes: int


class ArtifactStore:
    def __init__(self, root: Path, max_bytes: int = 20 * 1024 * 1024):
        self.root = root.resolve()
        self.max_bytes = max_bytes

    def ensure_task_dir(self, task_id: str) -> Path:
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
        validate_filename(filename)
        if len(content) > self.max_bytes:
            raise ValueError("artifact exceeds configured size limit")
        task_dir = self.ensure_task_dir(task_id)
        target = task_dir / filename
        target.write_bytes(content)
        return ArtifactSpec(filename=filename, content_type=content_type, size_bytes=target.stat().st_size)

    def resolve(self, task_id: str, relative_path: str) -> Path:
        validate_task_id(task_id)
        relative = safe_relative_path(relative_path)
        path = (self.root / task_id / relative).resolve()
        task_root = (self.root / task_id).resolve()
        if task_root not in path.parents:
            raise ValueError("artifact path escapes task directory")
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path
