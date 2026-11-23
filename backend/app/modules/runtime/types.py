"""Runtime engine types: protocols, exceptions, config.

No logic here. This file defines the seams the engine plugs into and the
errors it can raise, so that engine.py stays focused on the state machine
and the test stubs can satisfy narrow protocols.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

from app.adapters.llm.base import LLMResponse, ToolCall  # noqa: F401
from app.modules.agents.schemas import ResolvedTool


class ToolResult:
    """Opaque result payload; the engine persists it redacted."""

    __slots__ = ("ok", "output", "error", "verified")

    def __init__(
        self,
        *,
        ok: bool,
        output: dict[str, Any] | None = None,
        error: BaseException | None = None,
        verified: bool | None = None,
    ) -> None:
        self.ok = ok
        self.output = output or {}
        self.error = error
        self.verified = verified


@runtime_checkable
class ToolExecutor(Protocol):
    """Executes one tool call. Must honor the idempotency key when mode=keyed."""

    async def __call__(
        self,
        *,
        tool: ResolvedTool,
        arguments: dict[str, Any],
        idempotency_key: str | None,
        timeout_seconds: float,
    ) -> ToolResult:
        ...


PostCheckFn = Callable[[dict[str, Any], dict[str, Any]], Awaitable[bool | None]]


@runtime_checkable
class ToolRegistry(Protocol):
    """Resolves an executor and optional post_check for a tool key."""

    def executor_for(self, tool_key: str) -> ToolExecutor:
        ...

    def post_check_for(self, tool_key: str) -> PostCheckFn | None:
        ...


class EngineError(Exception):
    """Base for all engine-domain errors."""


class StaleLeaseError(EngineError):
    """Lease epoch did not match at commit time. No side effects applied."""


class RunNotFound(EngineError):
    pass


class RunNotResumable(EngineError):
    """Run is in a terminal state or cannot be resumed for a specific reason."""


class BudgetExceededError(EngineError):
    """A non-wall-clock budget was exceeded (steps, tool_calls, cost)."""


class TimedOutError(EngineError):
    """Wall-clock limit exceeded. Distinct from budget for reporting."""


class IndeterminateSideEffectError(EngineError):
    """A tool with idempotency_mode=none was mid-execution on resume."""


class RepeatedRejectionError(EngineError):
    """Too many consecutive tool proposals rejected by policy."""


TerminalReason = Literal[
    "completed",
    "failed",
    "escalated",
    "timed_out",
    "budget_exceeded",
    "cancelled",
]


@dataclass(frozen=True)
class EngineConfig:
    """Runtime config the engine uses for limits and leases."""

    lease_ttl_seconds: int
    heartbeat_interval_seconds: int
    max_consecutive_rejections: int = 3
    pricing_per_million_usd: dict[str, tuple[Decimal, Decimal]] = field(
        default_factory=lambda: {
            "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
            "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
        }
    )

    def __post_init__(self) -> None:
        if self.heartbeat_interval_seconds * 3 > self.lease_ttl_seconds:
            raise ValueError(
                "heartbeat_interval_seconds * 3 must be <= lease_ttl_seconds "
                "(need three heartbeats per TTL for safe detection)"
            )

    def cost_for(self, model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        rates = self.pricing_per_million_usd.get(model)
        if rates is None:
            return Decimal("0")
        in_rate, out_rate = rates
        return (
            (Decimal(prompt_tokens) * in_rate + Decimal(completion_tokens) * out_rate)
            / Decimal(1_000_000)
        )


def compute_lease_ttl(max_tool_timeout_seconds: float) -> int:
    """Lease TTL must exceed max tool timeout by a safety margin."""
    return int(max_tool_timeout_seconds) + 30


def compute_idempotency_key(
    *,
    execution_id: str,
    agent_version_id: str,
    tool_id: str,
    arguments: dict[str, Any],
) -> str:
    """Deterministic idempotency key: stable across retries for the same call."""
    import hashlib
    import json
    material = json.dumps(
        {
            "execution_id": execution_id,
            "agent_version_id": agent_version_id,
            "tool_id": tool_id,
            "arguments": arguments,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "ek_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


# --- Step decision (runtime engine's pure decision output) -------------

TerminalToolStatus = Literal[
    "not_run",
    "ran_ok_verified",
    "ran_ok_unverified",
    "ran_failed",
]


StepKind = Literal[
    "reasoning",
    "policy_check",
    "tool_call",
    "verifying",
    "terminate",
]


@dataclass(frozen=True)
class StepDecision:
    """Pure output of decide_next_step. No I/O; the loop dispatches on kind."""
    kind: StepKind
    proposals: tuple[ToolCall, ...] = ()
    proposal_index: int = 0
    terminal_reason: TerminalReason | None = None
    post_nudge: bool = False
    escalation_reason: str | None = None


__all__ = [
    "ToolResult",
    "ToolExecutor",
    "PostCheckFn",
    "ToolRegistry",
    "EngineError",
    "StaleLeaseError",
    "RunNotFound",
    "RunNotResumable",
    "BudgetExceededError",
    "TimedOutError",
    "IndeterminateSideEffectError",
    "RepeatedRejectionError",
    "TerminalReason",
    "TerminalToolStatus",
    "StepKind",
    "StepDecision",
    "EngineConfig",
    "compute_lease_ttl",
    "compute_idempotency_key",
    "LLMResponse",
    "ToolCall",
]
