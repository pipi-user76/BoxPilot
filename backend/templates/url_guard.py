"""文件职责：网页类任务共用的地址校验步骤。

除了语法检查，还会解析域名并拒绝回环、内网、链路本地和云元数据地址。
应用层校验挡不住浏览器内的跳转，部署时仍需给沙箱配置出站白名单。
"""

from __future__ import annotations

from backend.templates.base import StepExecutionError, StepOutcome, TaskContext
from backend.validation import ValidationFailure, validate_public_url


def guard_url(context: TaskContext) -> str:
    """校验 params.url，写入 context.state["url"] 并返回原值。"""
    url = str(context.params.get("url", "")).strip()
    try:
        validate_public_url(url)
    except ValidationFailure as exc:
        raise StepExecutionError(
            "VALIDATION_ERROR",
            f"网页地址不可用：{exc}。请改用公网可访问的 http 或 https 地址。",
        ) from exc
    context.state["url"] = url
    return url


def validate_public_url_step(context: TaskContext) -> StepOutcome:
    """网页整理的第一步：只校验地址，不做网络请求。"""
    url = guard_url(context)
    return StepOutcome(output=f"网页地址校验通过：{url}")
