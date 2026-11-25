"""The workflow decision function: pure, testable, no I/O.

decide_next_node() reads a frozen WorkflowState and returns a NodeDecision.
It never touches the DB, never calls an LLM, never executes a node. It
only reasons about what the workflow should do next.

Precedence (highest first):
  1. Budget limits (nodes, time, cost) — beat any pending work.
  2. If nothing has run yet — dispatch entry_node_id.
  3. If last node was 'terminate' — terminate (defensive; shouldn't reach here).
  4. Dispatch next:
     - trigger/agent/tool: next_id from node definition.
     - condition: evaluated at runtime by engine (3.1d); here we just
       return run_node for the condition itself so the engine can evaluate.
     - if next_id is None and node is not terminate — this is a bug in
       the graph (validated at save time). Escalate.

This function is intentionally simple. Complexity belongs in the node
handlers, not the decision logic. If it ever grows branches for specific
node types, that is a smell — those branches belong in the engine.
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.workflows.types import (
    NodeDecision,
    WorkflowState,
    TERMINAL_BUDGET_EXCEEDED,
    TERMINAL_ESCALATED,
    TERMINAL_TIMED_OUT,
)


# Hard limits — match EngineConfig defaults.
# The engine reads its config's values, but decision.py needs the same
# defaults so unit tests without an engine can use them.
DEFAULT_MAX_NODES = 100
DEFAULT_MAX_SECONDS = 300
DEFAULT_MAX_COST_USD = Decimal("5.00")


def decide_next_node(
    state: WorkflowState,
    *,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    max_cost_usd: Decimal = DEFAULT_MAX_COST_USD,
) -> NodeDecision:
    """Pure: state in, decision out. No side effects."""

    # --- Precedence 1: budget/time beats everything else ----------------
    if state.elapsed_seconds >= max_seconds:
        return NodeDecision(kind="terminate", terminal_reason=TERMINAL_TIMED_OUT)
    if state.node_count >= max_nodes:
        return NodeDecision(
            kind="terminate",
            terminal_reason=TERMINAL_BUDGET_EXCEEDED,
            terminal_detail="max_nodes_executed",
        )
    if state.cost_usd >= max_cost_usd:
        return NodeDecision(
            kind="terminate",
            terminal_reason=TERMINAL_BUDGET_EXCEEDED,
            terminal_detail="max_cost_usd",
        )

    # --- Precedence 2: nothing has run yet — dispatch entry --------------
    if state.last_node_id is None:
        return NodeDecision(kind="run_node", node_id=state.graph.entry_node_id)

    # --- Precedence 3: last node was terminate (defensive) --------------
    last_exec = state.node_executions[-1] if state.node_executions else None
    if last_exec is not None and last_exec.node_type == "terminate":
        # If we get here, the engine already wrote a terminate step but
        # didn't terminate. Return terminate to be safe.
        return NodeDecision(
            kind="terminate",
            terminal_reason=last_exec.output.get("reason", "completed")
            if last_exec.output else "completed",
        )

    # --- Precedence 4: dispatch next node -------------------------------
    last_node = next(
        (n for n in state.graph.nodes if n.id == state.last_node_id),
        None,
    )
    if last_node is None:
        # Graph corruption — the node we just executed is not in the graph.
        return NodeDecision(
            kind="terminate",
            terminal_reason=TERMINAL_ESCALATED,
            terminal_detail=f"last_node_id={state.last_node_id!r} not in graph",
        )

    if last_node.type.value == "terminate":
        # Already handled above; defensive.
        return NodeDecision(
            kind="terminate",
            terminal_reason=last_node.config.reason if last_node.config else "completed",
        )

    if last_node.type.value == "condition":
        # The engine evaluated the condition and wrote its chosen next
        # as the last execution's output. The decision layer trusts that.
        # For 3.1d, condition evaluation writes a step whose output has
        # next_node_id set. If it's absent, that's a bug in the node handler.
        chosen = last_exec.output.get("chosen_next_node_id") if last_exec and last_exec.output else None
        if chosen is None:
            return NodeDecision(
                kind="terminate",
                terminal_reason=TERMINAL_ESCALATED,
                terminal_detail="condition node did not record chosen_next_node_id",
            )
        return NodeDecision(kind="run_node", node_id=chosen)

    # trigger / agent / tool: standard next pointer
    next_id = last_node.next
    if next_id is None:
        return NodeDecision(
            kind="terminate",
            terminal_reason=TERMINAL_ESCALATED,
            terminal_detail=f"node {last_node.id!r} has no next",
        )
    return NodeDecision(kind="run_node", node_id=next_id)


__all__ = [
    "decide_next_node",
    "DEFAULT_MAX_NODES", "DEFAULT_MAX_SECONDS", "DEFAULT_MAX_COST_USD",
]
