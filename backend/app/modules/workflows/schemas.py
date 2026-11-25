"""Pydantic models for the workflow graph.

See WORKFLOW_ENGINE.md for the full spec. This module defines the
validate-time shape. Runtime state (which node we are on, run_context)
lives elsewhere.

Design:
- One Node model with optional type-specific fields, not a discriminated
  union. Simpler for JSONB round-trip and Pydantic v2 strict mode. Validation
  is done in graph.py, not in Pydantic field validators, because some rules
  cross nodes (unique ids, DAG, referential integrity).
- Edge is a simple (from, to) pair. Both fields are node ids.
- WorkflowGraph is immutable after validation. The engine treats it as a
  frozen snapshot.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NodeType(StrEnum):
    TRIGGER = "trigger"
    AGENT = "agent"
    TOOL = "tool"
    CONDITION = "condition"
    TERMINATE = "terminate"


class Branch(BaseModel):
    """One branch of a condition node."""
    model_config = ConfigDict(extra="forbid")
    when: Any  # JSONLogic expression; validated at runtime by the evaluator
    next: str  # target node id


class NodeConfig(BaseModel):
    """Type-specific configuration. Fields are optional; which ones are
    required depends on the node type (validated in graph.py)."""
    model_config = ConfigDict(extra="forbid")

    # For agent nodes:
    agent_id: str | None = None
    instructions_override: str | None = None
    max_seconds: int | None = None
    max_cost_usd: float | None = None

    # For tool nodes:
    tool_key: str | None = None
    arguments_template: dict[str, Any] | None = None

    # For terminate nodes:
    reason: str | None = None
    detail: str | None = None


class Node(BaseModel):
    """One node in the workflow graph."""
    model_config = ConfigDict(extra="forbid")
    id: str
    type: NodeType
    next: str | None = None           # for trigger/agent/tool; None for condition/terminate
    branches: list[Branch] | None = None  # for condition nodes
    config: NodeConfig | None = None


class WorkflowGraph(BaseModel):
    """The complete graph definition."""
    model_config = ConfigDict(extra="forbid")
    entry_node_id: str
    nodes: list[Node] = Field(min_length=1)
    edges: list[tuple[str, str]] = Field(default_factory=list)


__all__ = [
    "NodeType", "Branch", "NodeConfig", "Node", "WorkflowGraph",
]
