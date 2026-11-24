"""Background runner for agent runs.

Extracted from routes.py so integration tests can patch this function
to a no-op, avoiding real LLM calls in HTTP tests.
"""
from __future__ import annotations

import logging

from app.modules.agents.engine_factory import build_agent_engine

log = logging.getLogger(__name__)


async def run_agent_background(*, run_id: str, org_id: str) -> None:
    try:
        engine = build_agent_engine()
        await engine.run_once(run_id=run_id, org_id=org_id)
    except Exception:
        log.exception("agent_run_failed", extra={"run_id": run_id, "org_id": org_id})
