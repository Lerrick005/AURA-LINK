"""Provider-agnostic LLM interface.

This module defines the only contract any LLM adapter must satisfy.
Runtime code depends on ``LLMProvider`` (Protocol) and the dataclasses below,
never on a specific vendor SDK.

Design notes (see ADR-0003):
- One primary provider at MVP; the interface exists so a swap is a config change,
  not a rewrite.
- Token usage and cost are first-class fields, not afterthoughts, so the runtime
  can enforce budget gates outside the model.
- Tool calls are modeled as proposals. A ToolCall is not an action -- it is a
  request that the tool registry will validate and either execute or reject.
- The provider NEVER executes a tool. That is the runtime's job (see ADR-0006).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


Role = Literal["system", "user", "assistant", "tool"]


class ToolSpec(BaseModel):
    """The metadata a provider needs to advertise a tool to the model."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema, validated by the tool registry


class ToolCall(BaseModel):
    """A model's proposal to call a tool. NOT an executed action."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]


class TokenUsage(BaseModel):
    """Provider-reported token counts. Used for cost accounting and budget gates."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMMessage(BaseModel):
    """One message in the conversation sent to the provider."""

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str | None = None
    # Populated for assistant turns that propose tool calls.
    tool_calls: tuple[ToolCall, ...] = ()
    # Populated for role="tool" turns responding to a specific tool call.
    tool_call_id: str | None = None
    name: str | None = None  # for role="tool", the tool name


class LLMRequest(BaseModel):
    """Everything the runtime passes to a provider for one completion."""

    model_config = ConfigDict(frozen=True)

    model: str
    messages: Sequence[LLMMessage]
    tools: Sequence[ToolSpec] = ()
    temperature: float = 0.0
    max_output_tokens: int = 1024
    # Opaque passthrough for provider-specific knobs; adapters may ignore.
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """A provider's response. Tool calls are proposals; finish_reason is set."""

    model_config = ConfigDict(frozen=True)

    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter", "other"]
    usage: TokenUsage
    model: str
    provider: str
    # Opaque provider-specific response id for tracing / dedupe.
    provider_request_id: str | None = None


class LLMStreamChunk(TypedDict):
    """A single streamed delta. MVP: adapters may return non-streaming responses.

    Streaming is deferred to Milestone 2's tail; this TypedDict exists so the
    interface does not need to change later.'
    """

    delta_content: str
    done: bool


@runtime_checkable
class LLMProvider(Protocol):
    """The only contract any LLM adapter must satisfy.

    Implementations MUST:
    - Never execute tools.
    - Return usage on every response (the runtime enforces budget from it).
    - Raise ``LLMError`` subclasses for anything that is not a successful response.
    - Be safe to call from async request handlers (no blocking I/O).
    """

    name: str  # short identifier, e.g. "openai_compat"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run one completion. Return a fully-populated LLMResponse."""
        ...

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        """Optional streaming. Providers that do not stream may yield one chunk.

        Default fallback: adapters that do not implement streaming should raise
        ``NotImplementedError`` in a subclass, or use the helper below.
        """
        ...


class LLMError(Exception):
    """Base for all provider errors. The runtime catches these and classifies."""

    def __init__(self, message: str, *, provider: str, retryable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class LLMRetryableError(LLMError):
    """Transient: timeouts, 429, 5xx. Safe to retry within the budget."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=True)


class LLMFatalError(LLMError):
    """Non-transient: auth, invalid request, content policy. Retry wastes budget."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=False)


async def non_streaming_fallback(
    provider: LLMProvider, request: LLMRequest
) -> AsyncIterator[LLMStreamChunk]:
    """Helper for adapters that do not support streaming.

    Runs ``complete()`` and yields a single chunk. Call from adapters that want
    a uniform stream interface but do not yet implement real streaming.
    """
    response = await provider.complete(request)
    yield LLMStreamChunk(delta_content=response.content or "", done=True)
