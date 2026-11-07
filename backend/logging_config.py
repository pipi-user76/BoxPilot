"""文件职责：提供结构化 JSON 日志，供任务排查和后续日志采集使用。

日志同时写入标准输出和滚动文件（默认 logs/boxpilot.jsonl）。每条记录都带 event
字段，任务相关的记录还会带 task_id、step、duration_ms 等字段，便于按任务过滤。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from backend.config import settings


# LogRecord 自带字段不进入额外字段，避免日志被路径、线程名等固定信息淹没。
_RESERVED_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class JsonFormatter(logging.Formatter):
    """把日志记录序列化成一行 JSON，保留调用方通过 extra 传入的业务字段。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_FIELDS or key.startswith("_"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
            else:
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def configure_logging(level: str | None = None, log_file: Path | None = None) -> None:
    """幂等初始化日志；重复调用不会叠加 handler 或重复写文件。"""
    global _configured
    if _configured:
        return

    # 根日志器保持 WARNING，避免 httpx 等第三方库把每次请求都写进结构化日志；
    # 业务日志通过 boxpilot 命名空间单独放开到配置级别。
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    logging.getLogger("boxpilot").setLevel((level or settings.LOG_LEVEL).upper())
    formatter = JsonFormatter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    target = log_file if log_file is not None else settings.LOG_FILE
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            target,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        # 日志目录不可写时保留标准输出，不能因为日志问题让服务启动失败。
        root.warning("log_file_unavailable", extra={"path": str(target), "reason": str(exc)})

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """返回带统一前缀的 logger，便于按 logger 字段过滤业务日志。"""
    return logging.getLogger(f"boxpilot.{name}")
