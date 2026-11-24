"""LLM provider factory. Cached singleton.

Constructed lazily on first call so tests can override settings before the
first request. Reads LLM_* fields from Settings; the OpenAICompatProvider
itself also reads them, but passing them explicitly keeps the factory the
single place where the provider is configured.
"""
from __future__ import annotations

from functools import lru_cache

from app.adapters.llm.base import LLMProvider
from app.adapters.llm.openai_compat import OpenAICompatProvider
from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    s = get_settings()
    api_key = s.LLM_API_KEY.get_secret_value() if s.LLM_API_KEY else None
    return OpenAICompatProvider(
        api_key=api_key,
        base_url=s.LLM_BASE_URL,
        timeout=float(s.LLM_REQUEST_TIMEOUT_SECONDS),
    )


__all__ = ["get_llm_provider"]
