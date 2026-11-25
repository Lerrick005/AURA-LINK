"""LLM provider factory.

Returns a ``LazyLLMProvider`` that defers constructing the real OpenAI-
compatible client until the first ``complete()`` or ``stream()`` call.

Why lazy:
  - A workflow whose nodes are all trigger/terminate/condition/tool needs
    no LLM at all. Eager construction in ``build_workflow_engine()`` would
    force every workflow to have LLM_API_KEY configured, which is a scaling
    and reliability problem, not just a dev-ergonomics one.
  - The failure mode is correct: workflows that never call complete() run
    fine; workflows that do raise ``LLMFatalError`` at the exact call site,
    not at import time.
"""
from __future__ import annotations

from functools import lru_cache
from typing import AsyncIterator

from app.adapters.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
)
from app.adapters.llm.openai_compat import OpenAICompatProvider
from app.core.config import get_settings


class LazyLLMProvider:
    """Provider facade that constructs the real adapter on first use.

    Satisfies the ``LLMProvider`` Protocol: exposes ``name``, ``complete``,
    and ``stream``. Thread-safe by construction (the first call may race
    two constructions; only one is kept, the other is discarded — no side
    effects either way).
    """

    name = "lazy"

    def __init__(self) -> None:
        self._real: LLMProvider | None = None

    def _resolve(self) -> LLMProvider:
        if self._real is None:
            s = get_settings()
            api_key = s.LLM_API_KEY.get_secret_value() if s.LLM_API_KEY else None
            self._real = OpenAICompatProvider(
                api_key=api_key,
                base_url=s.LLM_BASE_URL,
                timeout=float(s.LLM_REQUEST_TIMEOUT_SECONDS),
            )
        return self._real

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return await self._resolve().complete(request)

    async def stream(
        self, request: LLMRequest
    ) -> AsyncIterator[LLMStreamChunk]:
        async for chunk in self._resolve().stream(request):
            yield chunk


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    return LazyLLMProvider()


__all__ = ["get_llm_provider", "LazyLLMProvider"]