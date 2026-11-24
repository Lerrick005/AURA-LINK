"""TTL cleanup for run_context (F5).

Runs as the owner role (admin_session) because auralink_app has no DELETE
grant on run_context by design (see migration 0003). Only deletes rows
whose expires_at has passed AND whose parent agent_run is in a terminal
status. Active runs are never touched -- the engine needs them.

Usage:
    python -m app.modules.maintenance.cleanup            # actually delete
    python -m app.modules.maintenance.cleanup --dry-run  # count only
"""
from __future__ import annotations
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import text

from app.core.db import admin_session, dispose_engine
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)

# Statuses after which a run's run_context is safe to expire.
# Must match app/modules/agents/models.py:AgentRun status enum.
TERMINAL_STATUSES = (
    "completed", "failed", "escalated",
    "cancelled", "timed_out", "budget_exceeded",
)


async def cleanup_expired_run_context(*, dry_run: bool = False) -> dict[str, Any]:
    """Delete expired run_context rows whose parent run is terminal.

    Returns {"deleted": N} or {"would_delete": N} in dry_run mode.
    """
    terminal = list(TERMINAL_STATUSES)

    async with admin_session() as s:
        if dry_run:
            count = (await s.execute(
                text(
                    "SELECT count(*) FROM run_context rc "
                    "JOIN agent_runs ar ON ar.id = rc.execution_id "
                    "WHERE rc.expires_at < now() "
                    "  AND ar.status = ANY(:terminal)"
                ),
                {"terminal": terminal},
            )).scalar_one()
            result = {"would_delete": int(count)}
            log.info("cleanup_dry_run", extra=result)
            return result

        res = await s.execute(
            text(
                "DELETE FROM run_context rc "
                "USING agent_runs ar "
                "WHERE ar.id = rc.execution_id "
                "  AND rc.expires_at < now() "
                "  AND ar.status = ANY(:terminal)"
            ),
            {"terminal": terminal},
        )
        deleted = res.rowcount or 0
        result = {"deleted": int(deleted)}
        log.info("cleanup_deleted", extra=result)
        return result


async def _main() -> int:
    configure_logging()
    dry_run = "--dry-run" in sys.argv
    try:
        result = await cleanup_expired_run_context(dry_run=dry_run)
        print(json.dumps(result))
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
