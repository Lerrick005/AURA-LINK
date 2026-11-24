"""F5: run_context TTL cleanup deletes only expired+terminal rows.

The cleanup runs as admin (owner role) because auralink_app has no DELETE
grant on run_context. We use admin_session() to seed, delete, and verify,
since tenant_session() cannot see rows across the row's lifetime here.
"""
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

from app.core.db import admin_session, tenant_session
from app.core.ids import uuid7str
from app.modules.maintenance.cleanup import cleanup_expired_run_context


async def _seed_run_with_context(*, org_id, status: str, expired: bool) -> tuple[str, str]:
    """Insert an agent_runs row + run_context row. Return (run_id, ctx_id)."""
    run_id = uuid7str()
    ctx_id = uuid7str()
    # We need an agent_version to satisfy the FK. Insert a minimal one under admin.
    agent_id = uuid7str()
    version_id = uuid7str()
    expires = (
        datetime.now(timezone.utc) - timedelta(days=1)
        if expired else datetime.now(timezone.utc) + timedelta(days=30)
    )
    async with admin_session() as s:
        await s.execute(text(
            "INSERT INTO agents (id, org_id, name, status) "
            "VALUES (:id, :o, :n, 'active')"
        ), {"id": agent_id, "o": org_id, "n": f"agent-{__import__('uuid').uuid4().hex[:12]}"})
        await s.execute(text(
            "INSERT INTO agent_versions (id, agent_id, org_id, version, config) "
            "VALUES (:id, :a, :o, 1, '{}'::jsonb)"
        ), {"id": version_id, "a": agent_id, "o": org_id})
        await s.execute(text(
            "INSERT INTO agent_runs (id, org_id, agent_version_id, status) "
            "VALUES (:id, :o, :v, :st)"
        ), {"id": run_id, "o": org_id, "v": version_id, "st": status})
        await s.execute(text(
            "INSERT INTO run_context (id, org_id, execution_id, agent_version_id, expires_at) "
            "VALUES (:id, :o, :e, :v, :x)"
        ), {"id": ctx_id, "o": org_id, "e": run_id, "v": version_id, "x": expires})
    return run_id, ctx_id


async def _exists(ctx_id: str) -> bool:
    async with admin_session() as s:
        n = (await s.execute(
            text("SELECT count(*) FROM run_context WHERE id = :i"), {"i": ctx_id}
        )).scalar_one()
    return int(n) > 0


async def test_deletes_expired_terminal_run_context(world):
    org = world.a.org_id
    run_id, ctx_id = await _seed_run_with_context(
        org_id=org, status="completed", expired=True,
    )
    result = await cleanup_expired_run_context()
    assert result["deleted"] >= 1
    assert not await _exists(ctx_id)


async def test_keeps_expired_active_run_context(world):
    org = world.a.org_id
    run_id, ctx_id = await _seed_run_with_context(
        org_id=org, status="running", expired=True,
    )
    await cleanup_expired_run_context()
    assert await _exists(ctx_id)


async def test_keeps_unexpired_terminal_run_context(world):
    org = world.a.org_id
    run_id, ctx_id = await _seed_run_with_context(
        org_id=org, status="completed", expired=False,
    )
    await cleanup_expired_run_context()
    assert await _exists(ctx_id)


async def test_dry_run_counts_without_deleting(world):
    org = world.a.org_id
    run_id, ctx_id = await _seed_run_with_context(
        org_id=org, status="completed", expired=True,
    )
    result = await cleanup_expired_run_context(dry_run=True)
    assert result["would_delete"] >= 1
    assert await _exists(ctx_id)  # still there



