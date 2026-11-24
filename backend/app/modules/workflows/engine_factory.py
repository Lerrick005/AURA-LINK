"""Wire a WorkflowEngine with real production dependencies.

Both API route handlers and future background workers use this. Keeping
construction in one place means swapping the LLM provider or tool registry
does not require touching every call site.
"""
from __future__ import annotations

from app.core.db import get_sessionmaker
from app.core.llm_factory import get_llm_provider
from app.modules.runtime.engine import RuntimeEngine
from app.modules.runtime.registry import get_tool_registry
from app.modules.runtime.types import EngineConfig
from app.modules.workflows.engine import WorkflowEngine


def build_workflow_engine(
    *,
    lease_ttl_seconds: int = 30,
    heartbeat_interval_seconds: int = 5,
) -> WorkflowEngine:
    """Construct a fully-wired WorkflowEngine.

    Note: agent_engine and workflow_engine share one LLMProvider and one
    tool_registry instance — they are stateless and safe to share.
    """
    config = EngineConfig(
        lease_ttl_seconds=lease_ttl_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    sm = get_sessionmaker()
    llm = get_llm_provider()
    registry = get_tool_registry()

    agent_engine = RuntimeEngine(
        session_factory=sm, llm=llm, config=config, tool_registry=registry,
    )
    return WorkflowEngine(
        session_factory=sm, llm=llm, config=config,
        tool_registry=registry, agent_engine=agent_engine,
    )


__all__ = ["build_workflow_engine"]
