"""文件职责：OpenAI 兼容的最小 LLM 网关。

只依赖标准库，便于在沙箱、测试和离线环境复用：
- `chat` 统一超时、重试、错误码与 token 用量；
- `chat_json` 强制 JSON 输出并解析，失败抛 `LLM_BAD_JSON`；
- `embed` 供向量检索使用。

未配置 API Key 时 `available()` 返回 False，调用方应降级而不是崩溃。
"""

from __future__ import annotations

import http.client

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator, Callable

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



def _loads_json_object(text: str) -> dict[str, Any] | None:
    """尽量从模型输出里取出一个 JSON 对象：容忍代码围栏与前后说明文字。"""
    raw = (text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if raw.startswith("```"):
        body = raw.strip("`")
        if body.lower().startswith("json"):
            body = body[4:]
        candidates.append(body.strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


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
        timeout: float | None = None,
        retries: int | None = None,
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
        try:
            body = self._post("/chat/completions", payload, timeout=timeout, retries=retries)
        except Exception as exc:
            code = exc.code if isinstance(exc, LLMError) else "LLM_UNAVAILABLE"
            fallback = (settings.LLM_FALLBACK_MODEL or "").strip()
            if (
                self._transport is None
                and fallback
                and fallback != self.model
                and code in {"LLM_UNAVAILABLE", "LLM_HTTP_ERROR"}
            ):
                logger.warning("llm_fallback model=%s from=%s reason=%s", fallback, self.model, code)
                return LLMClient(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    model=fallback,
                    embedding_model=self.embedding_model,
                    timeout=self.timeout,
                    max_retries=self.max_retries,
                    sleep=self._sleep,
                ).chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    timeout=timeout,
                    retries=retries,
                )
            if isinstance(exc, LLMError):
                raise
            raise LLMError(code, str(exc) or "模型服务不可用") from exc
        try:
            choice = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM_BAD_RESPONSE", "模型返回结构不完整") from exc
        if not str(choice or "").strip():
            fallback = (settings.LLM_FALLBACK_MODEL or "").strip()
            if self._transport is None and fallback and fallback != self.model:
                logger.warning("llm_fallback model=%s from=%s reason=empty", fallback, self.model)
                return LLMClient(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    model=fallback,
                    embedding_model=self.embedding_model,
                    timeout=self.timeout,
                    max_retries=self.max_retries,
                    sleep=self._sleep,
                ).chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    timeout=timeout,
                    retries=retries,
                )
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
        parsed = _loads_json_object(result.text)
        if parsed is None:
            raise LLMError("LLM_BAD_JSON", "模型没有返回合法 JSON")
        return parsed

    # ---------- 向量 ----------

    def embed(
        self, texts: list[str], *, timeout: float | None = None, retries: int | None = None
    ) -> list[list[float]]:
        if not self.api_key:
            raise LLMError("LLM_NOT_CONFIGURED", "未配置 LLM_API_KEY")
        if not texts:
            return []
        body = self._post(
            "/embeddings",
            {"model": self.embedding_model, "input": texts},
            timeout=timeout,
            retries=retries,
        )
        try:
            rows = sorted(body["data"], key=lambda item: item.get("index", 0))
            return [[float(value) for value in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("LLM_BAD_RESPONSE", "嵌入接口返回结构不完整") from exc

    def chat_stream(
        self, messages: list[dict[str, str]], *, max_tokens: int = 4000
    ) -> Iterator[str]:
        for event in self.chat_stream_events(messages, max_tokens=max_tokens):
            if event["type"] == "content":
                yield event["text"]

    def chat_stream_events(
        self, messages: list[dict[str, str]], *, max_tokens: int = 4000
    ) -> Iterator[dict[str, str]]:
        if not self.api_key:
            raise LLMError("LLM_NOT_CONFIGURED", "未配置 LLM_API_KEY")
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if settings.LLM_TEMPERATURE is not None:
            payload["temperature"] = settings.LLM_TEMPERATURE
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    piece = choices[0].get("delta") or {}
                    if piece.get("reasoning_content"):
                        yield {"type": "reasoning", "text": piece["reasoning_content"]}
                    if piece.get("content"):
                        yield {"type": "content", "text": piece["content"]}
        except urllib.error.HTTPError as exc:
            code = "LLM_RATE_LIMITED" if exc.code == 429 else "LLM_HTTP_ERROR"
            raise LLMError(code, f"模型服务返回 {exc.code}", status=exc.code) from exc
        except TimeoutError as exc:
            raise LLMError("LLM_TIMEOUT", "模型调用超时") from exc
        except urllib.error.URLError as exc:
            raise LLMError("LLM_UNAVAILABLE", f"无法连接模型服务：{exc.reason}") from exc
        except (http.client.RemoteDisconnected, ConnectionError) as exc:
            raise LLMError("LLM_UNAVAILABLE", f"模型连接被中断：{exc}") from exc
        except (ConnectionError, OSError) as exc:
            raise LLMError("LLM_UNAVAILABLE", f"模型连接被中断：{exc}") from exc

    # ---------- 传输与重试 ----------

    def _post(
        self, path: str, payload: dict[str, Any], *, timeout: float | None = None,
        retries: int | None = None,
    ) -> dict[str, Any]:
        delay = 1.0
        last_error: LLMError | None = None
        total = self.max_retries if retries is None else retries
        for attempt in range(total + 1):
            try:
                return self._request(path, payload, timeout=timeout)
            except LLMError as exc:
                last_error = exc
                retryable = exc.code in {"LLM_TIMEOUT", "LLM_RATE_LIMITED", "LLM_UNAVAILABLE"} or (
                    exc.status is not None and exc.status >= 500
                )
                if not retryable or attempt >= total:
                    raise
                logger.warning("llm_retry", extra={"path": path, "attempt": attempt + 1, "code": exc.code})
                self._sleep(delay)
                delay *= 2
        raise last_error or LLMError("LLM_FAILED", "模型调用失败")

    def _request(
        self, path: str, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        effective = self.timeout if timeout is None else timeout
        if self._transport is not None:
            try:
                return self._transport(path, payload, effective)
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
            with urllib.request.urlopen(request, timeout=effective) as response:
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
