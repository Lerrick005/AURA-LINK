"""The runtime decision function: pure, testable, no I/O.

decide_next_step() reads a frozen _RunState (loaded by the loop just before
calling) and returns a StepDecision. It never touches the DB, never calls
an LLM, never runs a tool. All it does is reason about what the run should
do next given (steps, counters, config, tri-state terminal-tool status).

Precedence rules (in order):
  1. Budget limits beat any pending work (budget_exceeded / timed_out).
  2. Consecutive-rejection cap beats any pending proposal.
  3. Terminal-tool obligation beats content-only completion.
  4. Otherwise, drive the normal cycle (reasoning -> proposals -> tool -> reasoning -> verifying).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.adapters.llm.base import ToolCall
from app.modules.agents.schemas import AgentConfig
from app.modules.runtime.types import (
    StepDecision,
    TerminalToolStatus,
    ESCALATION_REASON_REJECTION_CAP,
)


@dataclass(frozen=True)
class RunState:
    """Frozen snapshot the loop hands to decide_next_step.

    Every field is read-only once constructed. The loop builds this from
    a single short DB read before every iteration.
    """
    steps: tuple[Mapping[str, Any], ...]       # execution_steps, ordered by seq
    iteration_count: int
    tool_call_count: int
    cost_usd: float
    elapsed_seconds: float
    consecutive_rejections: int
    nudge_issued: bool
    config: AgentConfig
    terminal_tool_status: TerminalToolStatus
    pending_proposal_count: int = 0             # unprocessed proposals from last llm_response
    last_step_type: str | None = None           # convenience for the common read


def decide_next_step(state: RunState) -> StepDecision:
    """Pure: state in, decision out. No side effects."""

    # --- Precedence 1: budget and time beat everything else -------------
    if state.elapsed_seconds >= state.config.limits.max_seconds:
        return StepDecision(kind="terminate", terminal_reason="timed_out")
    if state.iteration_count >= state.config.limits.max_steps:
        return StepDecision(kind="terminate", terminal_reason="budget_exceeded")
    if state.tool_call_count >= state.config.limits.max_tool_calls:
        return StepDecision(kind="terminate", terminal_reason="budget_exceeded")
    if state.cost_usd >= float(state.config.limits.max_cost_usd):
        return StepDecision(kind="terminate", terminal_reason="budget_exceeded")

    # --- Precedence 2: repeated rejections ------------------------------
    if state.consecutive_rejections >= 3:
        return StepDecision(
            kind="terminate",
            terminal_reason="escalated",
            escalation_reason=ESCALATION_REASON_REJECTION_CAP,
        )

    # --- Precedence 3: terminal-tool obligation -------------------------
    expected = state.config.expected_terminal_tool
    if expected is not None:
        status = state.terminal_tool_status
        if status == "ran_ok_verified":
            # Requirement satisfied; verify final and terminate.
            return StepDecision(kind="verifying", post_nudge=state.nudge_issued)
        if status == "ran_ok_unverified":
            # Tool ran but verification did not confirm. If policy requires
            # verification for this risk class, escalate; otherwise verify.
            if state.config.require_verified_terminal:
                return StepDecision(
                    kind="terminate",
                    terminal_reason="escalated",
                    escalation_reason="unverified_terminal_tool",
                )
            return StepDecision(kind="verifying", post_nudge=state.nudge_issued)
        if status == "ran_failed":
            return StepDecision(kind="terminate", terminal_reason="failed")
        # status == "not_run": nudge once, then escalate.
        if not state.nudge_issued:
            # If a content-only response just arrived, do one more reasoning
            # turn with a system nudge appended (the loop handles nudge write).
            if state.last_step_type == "llm_response" and state.pending_proposal_count == 0:
                return StepDecision(kind="reasoning")
            # Otherwise keep going through the normal cycle.
            return StepDecision(kind=_next_normal(state))
        # Nudge already spent, still not run.
        return StepDecision(
            kind="terminate",
            terminal_reason="escalated",
            escalation_reason="expected_tool_not_executed",
        )

    # --- Precedence 4: normal cycle -------------------------------------
    return StepDecision(kind=_next_normal(state))


def _next_normal(state: RunState) -> str:
    """What to do next when no terminal obligation and no limit hit."""
    last = state.last_step_type

    # No steps yet: begin with reasoning.
    if last is None:
        return "reasoning"

    # Just finished an LLM call: either propose policy checks or (if no tool
    # calls) end. If content-only and no expected terminal tool, terminate
    # as completed below; if expected tool exists, the obligation block above
    # already returned.
    if last == "llm_response":
        if state.pending_proposal_count > 0:
            return "policy_check"
        # Content-only response with no expected-tool obligation:
        return "verifying"

    # A tool result is in: decide whether to loop back to reasoning or
    # verify. If we have any proposals still unprocessed, keep going.
    if last == "tool_result":
        return "reasoning"

    # After tool_proposed step (policy check ran, next step is tool_call):
    if last == "tool_proposed":
        return "tool_call"

    # After tool_rejected: loop back to reasoning with the rejection recorded.
    if last == "tool_rejected":
        return "reasoning"

    # After a resumed/loaded_config step: reasoning.
    if last in ("resumed", "loaded_config"):
        return "reasoning"

    # Fallback: keep reasoning. This should not trigger if the state machine
    # is correct, but returning "reasoning" is safe (worst case: extra loop).
    return "reasoning"


__all__ = ["RunState", "decide_next_step"]
