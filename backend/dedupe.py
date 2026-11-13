"""文件职责：计算任务幂等键。

相同任务类型与相同参数（键顺序无关）在窗口内只创建一次任务，
避免用户重复点击或前端重试把同一动作执行多遍。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.models import TaskType


def canonical_params(params: dict[str, Any]) -> str:
    """规范化参数：排序键、去掉空白字符串，再用紧凑 JSON 序列化。"""
    cleaned = {
        str(key): (value.strip() if isinstance(value, str) else value)
        for key, value in params.items()
    }
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_dedupe_key(task_type: TaskType, params: dict[str, Any]) -> str:
    """SHA-256 十六进制摘要，作为创建任务的幂等键。"""
    payload = f"{task_type.value}\n{canonical_params(params)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]
