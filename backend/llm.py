"""文件职责：OpenAI 兼容的最小 LLM 网关。

只依赖标准库，便于在沙箱、测试和离线环境复用：
- `chat` 统一超时、重试、错误码与 token 用量；
- `chat_json` 强制 JSON 输出并解析，失败抛 `LLM_BAD_JSON`；
- `embed` 供向量检索使用。

未配置 API Key 时 `available()` 返回 False，调用方应降级而不是崩溃。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.config import settings

logger = logging.getLogger("boxpilot.llm")

# 传输层可注入，测试不需要真实网络；签名：(path, payload, timeout) -> dict。
Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]


class LLMError(RuntimeError):
    """LLM 调用失败；code 用于前端和日志归类。"""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class ChatResult:
    text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = (base_url or settings.LLM_BASE_URL).rstrip("/")
        self.api_key = settings.LLM_API_KEY if api_key is None else api_key
        self.model = model or settings.LLM_MODEL
        self.embedding_model = embedding_model or settings.LLM_EMBEDDING_MODEL
        self.timeout = float(timeout or settings.LLM_TIMEOUT_SECONDS)
        self.max_retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries
        self._transport = transport
        self._sleep = sleep

    def available(self) -> bool:
        return bool(self.api_key)

    # ---------- 对话 ----------

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        if not self.api_key:
            raise LLMError("LLM_NOT_CONFIGURED", "未配置 LLM_API_KEY")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": settings.LLM_MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens,
        }
        effective_temperature = settings.LLM_TEMPERATURE if temperature is None else temperature
        # 推理类模型（如 kimi-k3）不接受 temperature，未配置时不要发送该字段。
        if effective_temperature is not None:
            payload["temperature"] = effective_temperature
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = self._post("/chat/completions", payload)
        try:
            choice = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM_BAD_RESPONSE", "模型返回结构不完整") from exc
        usage = body.get("usage") or {}
        result = ChatResult(
            text=str(choice).strip(),
            model=str(body.get("model") or self.model),
            usage={key: int(value) for key, value in usage.items() if isinstance(value, int)},
            raw=body,
        )
        logger.info(
            "llm_chat",
            extra={
                "model": result.model,
                "prompt_tokens": result.usage.get("prompt_tokens", 0),
                "completion_tokens": result.usage.get("completion_tokens", 0),
            },
        )
        return result

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        result = self.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise LLMError("LLM_BAD_JSON", "模型没有返回合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise LLMError("LLM_BAD_JSON", "模型返回的 JSON 不是对象")
        return parsed

    # ---------- 向量 ----------

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise LLMError("LLM_NOT_CONFIGURED", "未配置 LLM_API_KEY")
        if not texts:
            return []
        body = self._post("/embeddings", {"model": self.embedding_model, "input": texts})
        try:
            rows = sorted(body["data"], key=lambda item: item.get("index", 0))
            return [[float(value) for value in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("LLM_BAD_RESPONSE", "嵌入接口返回结构不完整") from exc

    # ---------- 传输与重试 ----------

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        delay = 1.0
        last_error: LLMError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._request(path, payload)
            except LLMError as exc:
                last_error = exc
                retryable = exc.code in {"LLM_TIMEOUT", "LLM_RATE_LIMITED"} or (
                    exc.status is not None and exc.status >= 500
                )
                if not retryable or attempt >= self.max_retries:
                    raise
                logger.warning("llm_retry", extra={"path": path, "attempt": attempt + 1, "code": exc.code})
                self._sleep(delay)
                delay *= 2
        raise last_error or LLMError("LLM_FAILED", "模型调用失败")

    def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._transport is not None:
            try:
                return self._transport(path, payload, self.timeout)
            except TimeoutError as exc:
                raise LLMError("LLM_TIMEOUT", "模型调用超时") from exc
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            code = "LLM_RATE_LIMITED" if exc.code == 429 else "LLM_HTTP_ERROR"
            raise LLMError(code, f"模型服务返回 {exc.code}", status=exc.code) from exc
        except TimeoutError as exc:
            raise LLMError("LLM_TIMEOUT", "模型调用超时") from exc
        except urllib.error.URLError as exc:
            raise LLMError("LLM_UNAVAILABLE", f"无法连接模型服务：{exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError("LLM_BAD_RESPONSE", "模型服务返回的不是 JSON") from exc


llm_client = LLMClient()
