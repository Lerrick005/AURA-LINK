"""Wire a RuntimeEngine with real production dependencies."""
from __future__ import annotations

from app.core.db import get_sessionmaker
from app.core.llm_factory import get_llm_provider
from app.modules.runtime.engine import RuntimeEngine
from app.modules.runtime.registry import get_tool_registry
from app.modules.runtime.types import EngineConfig


def build_agent_engine(
    *,
    lease_ttl_seconds: int = 30,
    heartbeat_interval_seconds: int = 5,
) -> RuntimeEngine:
    config = EngineConfig(
        lease_ttl_seconds=lease_ttl_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    return RuntimeEngine(
        session_factory=get_sessionmaker(),
        llm=get_llm_provider(),
        config=config,
        tool_registry=get_tool_registry(),
    )


__all__ = ["build_agent_engine"]
