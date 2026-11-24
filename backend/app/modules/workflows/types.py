"""Data types for the workflow runtime.

Pure dataclasses, no I/O. Used by decision.py (which is fully testable
without a DB) and loaded/persisted by engine.py (which wraps them).

WorkflowState is the frozen snapshot the loop hands to decide_next_node.
Every field is read-only once constructed. Same pattern as RunState from
Stage 2's decision.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Mapping

from app.modules.workflows.schemas import WorkflowGraph


@dataclass(frozen=True)
class NodeExec:
    """One completed node execution, as read from execution_steps."""
    node_id: str
    node_type: str          # "trigger" | "agent" | "tool" | "condition" | "terminate"
    status: str             # "ok" | "failed" | "escalated"
    output: Mapping[str, Any] | None
    started_at: float        # epoch seconds (for elapsed computation)


@dataclass(frozen=True)
class WorkflowState:
    """Frozen snapshot the loop hands to decide_next_node."""
    graph: WorkflowGraph
    # The last node whose execution completed, or None if nothing has run.
    last_node_id: str | None
    # Every completed node execution in order.
    node_executions: tuple[NodeExec, ...]
    # Aggregate counters across the workflow and all sub-runs.
    node_count: int
    agent_call_count: int
    tool_call_count: int
    cost_usd: Decimal
    elapsed_seconds: float


@dataclass(frozen=True)
class NodeDecision:
    """What the workflow should do next."""
    kind: Literal["run_node", "terminate"]
    node_id: str | None = None
    terminal_reason: str | None = None
    # For terminal decisions, optional detail (e.g. "no_branch_matched")
    terminal_detail: str | None = None


# Terminal reasons (deterministic, must match agents/models.py status enum)
TERMINAL_COMPLETED = "completed"
TERMINAL_FAILED = "failed"
TERMINAL_ESCALATED = "escalated"
TERMINAL_TIMED_OUT = "timed_out"
TERMINAL_BUDGET_EXCEEDED = "budget_exceeded"
TERMINAL_CANCELLED = "cancelled"


__all__ = [
    "NodeExec", "WorkflowState", "NodeDecision",
    "TERMINAL_COMPLETED", "TERMINAL_FAILED", "TERMINAL_ESCALATED",
    "TERMINAL_TIMED_OUT", "TERMINAL_BUDGET_EXCEEDED", "TERMINAL_CANCELLED",
]
