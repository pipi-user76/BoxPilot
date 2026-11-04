from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from agent_sandbox import Sandbox

from app.core.config import settings
from app.core.validation import safe_relative_path, validate_http_url, validate_task_id


_BLOCKED_COMMAND_PATTERNS = (
    re.compile(r"(^|[;&|]\s*)(sudo|su)(\s|$)"),
    re.compile(r"(^|[;&|]\s*)rm(\s|$)"),
    re.compile(r"(^|[;&|]\s*)(shutdown|reboot|halt|poweroff|mount|umount|nsenter|unshare)(\s|$)"),
    re.compile(r"\bchmod\s+[0-7]{3,4}\b"),
    re.compile(r"\bchown\b"),
    re.compile(r"\b(docker|podman)\b"),
    re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sh|bash)\b"),
    re.compile(r"\bdd\s+if="),
)


class SandboxAdapter:
    def __init__(
        self,
        base_url: str | None = None,
        work_root: str | None = None,
        default_timeout_s: int | None = None,
    ) -> None:
        self.base_url = base_url or settings.SANDBOX_URL
        self.work_root = work_root or settings.SANDBOX_WORK_ROOT
        self.default_timeout_s = default_timeout_s or settings.SANDBOX_TIMEOUT_SECONDS
        self.client = Sandbox(base_url=self.base_url)

    def health(self) -> dict[str, Any]:
        try:
            response = self.client.sandbox.get_context()
            if not response.success:
                return self._failure("SANDBOX_UNAVAILABLE", response.message or "sandbox health check failed")
            context = response.data if isinstance(response.data, str) else str(response.data)
            return {
                "success": True,
                "data": {"base_url": self.base_url, "context": context[:2000]},
                "error": None,
            }
        except Exception as exc:  # SDK can surface transport and validation errors
            return self._exception_failure(exc)

    def task_workdir(self, task_id: str) -> str:
        validate_task_id(task_id)
        return str(PurePosixPath(self.work_root) / task_id)

    def ensure_task_workspace(self, task_id: str) -> dict[str, Any]:
        workdir = self.task_workdir(task_id)
        command = f"mkdir -p {shlex.quote(workdir)} && test -w {shlex.quote(workdir)}"
        return self._exec_raw(command, exec_dir="/home/gem", timeout_s=15)

    def exec_shell(
        self,
        command: str,
        task_id: str,
        timeout_s: int | None = None,
        check_exit: bool = True,
    ) -> dict[str, Any]:
        if not command.strip():
            return self._failure("VALIDATION_ERROR", "command must not be empty")
        if self._is_blocked(command):
            return self._failure("COMMAND_BLOCKED", "command matches the restricted-command policy")

        workspace = self.ensure_task_workspace(task_id)
        if not workspace["success"]:
            return workspace
        return self._exec_raw(
            command,
            exec_dir=self.task_workdir(task_id),
            timeout_s=timeout_s,
            check_exit=check_exit,
        )

    def write_text_file(self, task_id: str, relative_path: str, content: str) -> dict[str, Any]:
        try:
            relative = safe_relative_path(relative_path)
            remote_path = PurePosixPath(self.task_workdir(task_id)) / relative
            parent = remote_path.parent
            mkdir = self._exec_raw(f"mkdir -p {shlex.quote(str(parent))}", exec_dir="/home/gem", timeout_s=15)
            if not mkdir["success"]:
                return mkdir
            response = self.client.file.write_file(file=str(remote_path), content=content, encoding="utf-8")
            if not response.success:
                return self._failure("INTERNAL_ERROR", response.message or "file write failed")
            return {"success": True, "data": {"path": str(remote_path)}, "error": None}
        except Exception as exc:
            return self._exception_failure(exc)

    def read_text_file(self, task_id: str, relative_path: str) -> dict[str, Any]:
        try:
            relative = safe_relative_path(relative_path)
            remote_path = PurePosixPath(self.task_workdir(task_id)) / relative
            response = self.client.file.read_file(file=str(remote_path))
            if not response.success or response.data is None:
                return self._failure("FILE_NOT_FOUND", response.message or "file read failed")
            return {"success": True, "data": {"content": response.data.content, "path": str(remote_path)}, "error": None}
        except Exception as exc:
            failure = self._exception_failure(exc)
            if failure["error"]["code"] == "INTERNAL_ERROR":
                failure["error"] = {"code": "FILE_NOT_FOUND", "message": failure["error"]["message"]}
            return failure

    def upload_file(self, task_id: str, local_path: str | Path, relative_path: str) -> dict[str, Any]:
        try:
            source = Path(local_path)
            if not source.is_file():
                return self._failure("FILE_NOT_FOUND", f"local file not found: {source}")
            relative = safe_relative_path(relative_path)
            remote_path = PurePosixPath(self.task_workdir(task_id)) / relative
            parent = remote_path.parent
            mkdir = self._exec_raw(f"mkdir -p {shlex.quote(str(parent))}", exec_dir="/home/gem", timeout_s=15)
            if not mkdir["success"]:
                return mkdir
            payload = source.read_bytes()
            response = self.client.file.upload_file(
                file=(source.name, payload, "application/octet-stream"),
                path=str(remote_path),
            )
            if not response.success:
                return self._failure("INTERNAL_ERROR", response.message or "file upload failed")
            return {"success": True, "data": {"path": str(remote_path), "bytes": len(payload)}, "error": None}
        except Exception as exc:
            return self._exception_failure(exc)

    def download_file(self, task_id: str, relative_path: str, local_path: str | Path) -> dict[str, Any]:
        try:
            relative = safe_relative_path(relative_path)
            remote_path = PurePosixPath(self.task_workdir(task_id)) / relative
            target = Path(local_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as stream:
                for chunk in self.client.file.download_file(path=str(remote_path)):
                    stream.write(chunk)
            size = target.stat().st_size
            if size <= 0:
                return self._failure("ARTIFACT_EMPTY", "downloaded file is empty")
            return {"success": True, "data": {"path": str(target), "bytes": size}, "error": None}
        except Exception as exc:
            return self._exception_failure(exc)

    def navigate(self, url: str, timeout_s: int | None = None) -> dict[str, Any]:
        try:
            validate_http_url(url)
            response = self.client.browser_page.navigate(
                url=url,
                wait_until="domcontentloaded",
                timeout=timeout_s or self.default_timeout_s,
            )
            if not response.success:
                return self._failure("COMMAND_FAILED", response.message or "browser navigation failed")
            return {"success": True, "data": response.data or {}, "error": None}
        except Exception as exc:
            return self._exception_failure(exc)

    def get_page_text(self) -> dict[str, Any]:
        try:
            response = self.client.browser_page.get_text()
            if not response.success:
                return self._failure("COMMAND_FAILED", response.message or "page text extraction failed")
            text = response.data if isinstance(response.data, str) else str(response.data or "")
            return {"success": True, "data": {"text": text}, "error": None}
        except Exception as exc:
            return self._exception_failure(exc)

    def take_screenshot(self, save_path: str | Path, full_page: bool = False) -> dict[str, Any]:
        try:
            target = Path(save_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as stream:
                for chunk in self.client.browser_page.screenshot(full_page=full_page, format="png"):
                    stream.write(chunk)
            size = target.stat().st_size
            if size <= 0:
                return self._failure("ARTIFACT_EMPTY", "screenshot output is empty")
            return {"success": True, "data": {"path": str(target), "bytes": size}, "error": None}
        except Exception as exc:
            return self._exception_failure(exc)

    def _exec_raw(
        self,
        command: str,
        exec_dir: str,
        timeout_s: int | None = None,
        check_exit: bool = True,
    ) -> dict[str, Any]:
        timeout = timeout_s or self.default_timeout_s
        try:
            response = self.client.shell.exec_command(
                command=command,
                exec_dir=exec_dir,
                timeout=timeout,
                hard_timeout=timeout,
                strict=True,
                truncate=True,
            )
            if not response.success or response.data is None:
                return self._failure("COMMAND_FAILED", response.message or "shell command failed")

            data = response.data
            status = getattr(data, "status", None)
            status_value = getattr(status, "value", status)
            output = getattr(data, "output", None) or ""
            exit_code = getattr(data, "exit_code", None)
            if status_value in {"hard_timeout", "no_change_timeout", "timeout"}:
                return self._failure("TIMEOUT", f"command timed out: {status_value}")
            if check_exit and exit_code not in {None, 0}:
                return self._failure("COMMAND_FAILED", f"command exited with code {exit_code}: {output[-1000:]}")
            return {
                "success": True,
                "data": {"output": output, "exit_code": exit_code, "status": str(status_value)},
                "error": None,
            }
        except Exception as exc:
            return self._exception_failure(exc)

    @staticmethod
    def _is_blocked(command: str) -> bool:
        return any(pattern.search(command) for pattern in _BLOCKED_COMMAND_PATTERNS)

    @staticmethod
    def _failure(code: str, message: str) -> dict[str, Any]:
        return {"success": False, "data": None, "error": {"code": code, "message": message}}

    def _exception_failure(self, exc: Exception) -> dict[str, Any]:
        message = str(exc)
        lowered = message.lower()
        if isinstance(exc, httpx.TimeoutException) or "timeout" in lowered or "timed out" in lowered:
            return self._failure("TIMEOUT", message)
        if isinstance(exc, httpx.ConnectError) or "connection refused" in lowered or "connect" in lowered:
            return self._failure("SANDBOX_UNAVAILABLE", message)
        if "permission" in lowered or "not found" in lowered or "no such file" in lowered:
            return self._failure("FILE_NOT_FOUND", message)
        return self._failure("INTERNAL_ERROR", message)


sandbox_adapter = SandboxAdapter()
