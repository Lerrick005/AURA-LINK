"""Background runner for workflow runs.

Extracted from workflows/routes.py so both the manual trigger and webhook
trigger share one implementation and tests can patch it.
"""
from __future__ import annotations

import logging

from app.modules.workflows.engine_factory import build_workflow_engine

log = logging.getLogger(__name__)


async def run_workflow_background(*, run_id: str, org_id: str) -> None:
    try:
        engine = build_workflow_engine()
        await engine.run_once(run_id=run_id, org_id=org_id)
    except Exception:
        log.exception("workflow_run_failed", extra={"run_id": run_id, "org_id": org_id})
