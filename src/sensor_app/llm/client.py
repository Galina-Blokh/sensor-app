"""OpenAI-compatible chat client (``POST …/chat/completions``) over ``httpx``.

Works with any host that matches that JSON API (OpenAI, Groq, vLLM, …).
Includes retries/backoff and optional :class:`PrimaryWithFallbackBackend` for a second model/host.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from sensor_app.llm.exceptions import LLMError, LLMTimeoutError, LLMUpstreamError
from sensor_app.llm.types import ChatResult

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMBackend(Protocol):
    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        max_completion_tokens: int = 512,
        request_id: str | None = None,
    ) -> ChatResult:
        """Return assistant text; implementations apply timeouts and retries."""
        ...


def _retryable_status(code: int) -> bool:
    return code == 429 or code >= 500


def should_try_fallback(exc: LLMError) -> bool:
    """Whether a failed **primary** chat call may be retried on a secondary (local/cheap) model."""
    if isinstance(exc, LLMTimeoutError):
        return True
    if isinstance(exc, LLMUpstreamError):
        code = exc.status_code
        if code is None:
            return True
        if code in (400, 401, 403, 404, 422):
            return False
        return True
    return False


class PrimaryWithFallbackBackend:
    """Try **primary** backend first; on transient failure, call **secondary** once (same messages)."""

    def __init__(self, primary: LLMBackend, secondary: LLMBackend) -> None:
        self._primary = primary
        self._secondary = secondary

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        max_completion_tokens: int = 512,
        request_id: str | None = None,
    ) -> ChatResult:
        try:
            return await self._primary.chat_completion(
                messages,
                max_completion_tokens=max_completion_tokens,
                request_id=request_id,
            )
        except LLMError as e:
            if not should_try_fallback(e):
                raise
            logger.warning(
                "llm_fallback_invoked",
                extra={
                    "request_id": request_id or "-",
                    "primary_error": type(e).__name__,
                    "primary_status": getattr(e, "status_code", None),
                },
            )
            return await self._secondary.chat_completion(
                messages,
                max_completion_tokens=max_completion_tokens,
                request_id=request_id,
            )


class OpenAICompatibleChatBackend:
    """POST ``/chat/completions`` JSON API (OpenAI and many local proxies)."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model: str,
        max_retries: int,
        backoff_base_ms: int,
    ) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._max_retries = max(0, max_retries)
        self._backoff_base_ms = max(1, backoff_base_ms)

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        max_completion_tokens: int = 512,
        request_id: str | None = None,
    ) -> ChatResult:
        url = f"{self._base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if request_id:
            headers["X-Request-ID"] = request_id
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_completion_tokens,
        }
        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    return _parse_chat_json(resp.json(), configured_model=self._model)
                if _retryable_status(resp.status_code) and attempt < self._max_retries:
                    delay = (self._backoff_base_ms / 1000.0) * (2**attempt)
                    logger.warning(
                        "llm_retry",
                        extra={
                            "outcome": "retry",
                            "status": resp.status_code,
                            "attempt": attempt + 1,
                            "request_id": request_id or "-",
                        },
                    )
                    await asyncio.sleep(delay)
                    continue
                raise LLMUpstreamError(
                    f"LLM HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    retryable=_retryable_status(resp.status_code),
                )
            except httpx.TimeoutException as e:
                last_err = e
                if attempt < self._max_retries:
                    delay = (self._backoff_base_ms / 1000.0) * (2**attempt)
                    logger.warning(
                        "llm_retry",
                        extra={
                            "outcome": "timeout_retry",
                            "attempt": attempt + 1,
                            "request_id": request_id or "-",
                        },
                    )
                    await asyncio.sleep(delay)
                    continue
                raise LLMTimeoutError() from e
            except LLMError:
                raise
            except httpx.HTTPError as e:
                raise LLMUpstreamError(f"LLM transport error: {e}") from e
        assert last_err is not None
        raise LLMTimeoutError() from last_err


def _parse_chat_json(data: Any, *, configured_model: str) -> ChatResult:
    try:
        choices = data["choices"]
        msg = choices[0]["message"]
        content = msg.get("content") or ""
        text = content if isinstance(content, str) else str(content)
        usage = data.get("usage") or {}
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        api_model = data.get("model")
        resolved = (
            api_model.strip()
            if isinstance(api_model, str) and api_model.strip()
            else configured_model
        )
        return ChatResult(
            text=text.strip(),
            prompt_tokens=int(pt) if isinstance(pt, int) else None,
            completion_tokens=int(ct) if isinstance(ct, int) else None,
            model=resolved,
        )
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise LLMUpstreamError("LLM response shape unexpected") from e


def extract_json_object(raw: str) -> dict[str, Any]:
    """Parse first JSON object from model output (strips optional ``` fences)."""
    s = raw.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("no JSON object in model output")
    blob = s[start : end + 1]
    out = json.loads(blob)
    if not isinstance(out, dict):
        raise ValueError("JSON root must be an object")
    return out
