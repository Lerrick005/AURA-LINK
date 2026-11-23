"""OpenAI-compatible LLM adapter.

This is the ONLY place in Auralink that imports from the ``openai`` SDK.
Runtime and modules depend on ``app.adapters.llm.base``, never on this file.

Design notes:
- Retry is hand-rolled (bounded attempts, jittered exponential backoff) so the
  policy is testable and visible. Only LLMRetryableError retries; LLMFatalError
  raises immediately so we do not burn budget on auth failures.
- Timeout defaults to Settings.LLM_REQUEST_TIMEOUT_SECONDS (60s), not the SDK's
  600s default. A runtime step has its own wall-clock budget; 600s per call
  would let a single call consume it.
- The SDK's error tree is mapped to our hierarchy at this boundary. Nothing
  downstream should ever need to import ``openai``.
- Streaming is deferred. ``stream()`` falls back to one chunk from ``complete()``.
  Real streaming lands in the Milestone 2 tail; faking it now would lie.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from typing import Any

import openai
from openai import AsyncOpenAI

from app.adapters.llm.base import (
    LLMError,
    LLMFatalError,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMRetryableError,
    LLMStreamChunk,
    TokenUsage,
    ToolCall,
    ToolSpec,
    non_streaming_fallback,
)
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


_RETRYABLE_OPENAI_ERRORS: tuple[type[BaseException], ...] = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)

_FATAL_OPENAI_ERRORS: tuple[type[BaseException], ...] = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.BadRequestError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)


def _classify(exc: BaseException) -> LLMError:
    """Map an SDK exception to our hierarchy. Unknown exceptions are fatal."""
    provider = "openai_compat"
    if isinstance(exc, _RETRYABLE_OPENAI_ERRORS):
        return LLMRetryableError(str(exc), provider=provider)
    if isinstance(exc, _FATAL_OPENAI_ERRORS):
        return LLMFatalError(str(exc), provider=provider)
    return LLMFatalError(f"unclassified SDK error: {type(exc).__name__}: {exc}", provider=provider)


class OpenAICompatProvider:
    """LLMProvider over an OpenAI-compatible HTTP API.

    ``http_client`` is injectable for tests. Production passes None and the
    SDK builds a default client. Tests pass an httpx.AsyncClient wired to an
    httpx.MockTransport, so no network traffic leaves the process.
    """

    name = "openai_compat"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        http_client: Any | None = None,
    ) -> None:
        s = get_settings()
        resolved_key = api_key if api_key is not None else (
            s.LLM_API_KEY.get_secret_value() if s.LLM_API_KEY else None
        )
        if not resolved_key:
            raise LLMFatalError(
                "no API key configured; set LLM_API_KEY or pass api_key",
                provider=self.name,
            )
        self._client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url if base_url is not None else s.LLM_BASE_URL,
            timeout=timeout if timeout is not None else float(s.LLM_REQUEST_TIMEOUT_SECONDS),
            http_client=http_client,
        )
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap

    async def complete(self, request: LLMRequest) -> LLMResponse:
        params = self._to_sdk_params(request)
        last_err: LLMError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = await self._client.chat.completions.create(**params)
                return self._from_sdk_response(resp, request)
            except Exception as exc:
                err = _classify(exc)
                log.warning(
                    "llm_call_failed",
                    extra={
                        "provider": self.name,
                        "attempt": attempt,
                        "retryable": err.retryable,
                        "error_type": type(exc).__name__,
                    },
                )
                if not err.retryable or attempt == self._max_attempts:
                    raise err from exc
                last_err = err
                delay = min(self._backoff_base * (2 ** (attempt - 1)), self._backoff_cap)
                await asyncio.sleep(random.uniform(0, delay))
        # Defensive: loop exits only on return or raise, but static analyzers
        # want an explicit raise.
        raise last_err or LLMFatalError("unreachable", provider=self.name)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        async for chunk in non_streaming_fallback(self, request):
            yield chunk

    # --- translation -----------------------------------------------------

    def _to_sdk_params(self, request: LLMRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": request.model,
            "messages": [self._message_to_sdk(m) for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.tools:
            params["tools"] = [self._tool_to_sdk(t) for t in request.tools]
            params["tool_choice"] = "auto"
        return params

    @staticmethod
    def _message_to_sdk(m: LLMMessage) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": m.role}
        if m.content is not None:
            msg["content"] = m.content
        if m.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": __import__("json").dumps(tc.arguments),
                    },
                }
                for tc in m.tool_calls
            ]
        if m.tool_call_id is not None:
            msg["tool_call_id"] = m.tool_call_id
        if m.name is not None:
            msg["name"] = m.name
        return msg

    @staticmethod
    def _tool_to_sdk(t: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }

    def _from_sdk_response(self, resp: Any, request: LLMRequest) -> LLMResponse:
        choice = resp.choices[0]
        msg = choice.message
        tool_calls: tuple[ToolCall, ...] = ()
        if getattr(msg, "tool_calls", None):
            import json
            tool_calls = tuple(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments or "{}"),
                )
                for tc in msg.tool_calls
            )
        finish_raw = choice.finish_reason or "stop"
        finish_reason = (
            finish_raw
            if finish_raw in ("stop", "length", "tool_calls", "content_filter")
            else "other"
        )
        usage = resp.usage
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=TokenUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ),
            model=getattr(resp, "model", request.model),
            provider=self.name,
            provider_request_id=getattr(resp, "id", None),
        )


assert isinstance(OpenAICompatProvider, type)
