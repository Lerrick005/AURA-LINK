"""HTTP-level tests for the /runs read API."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text

from app.core.db import tenant_session
from app.core.ids import uuid7str
from app.main import create_app
from app.modules.auth.deps import current_actor
from app.modules.authorization.models import Role
from app.modules.authorization.policy import Actor


def _fake_actor(org_id: str) -> Actor:
    return Actor(
        user_id="test-user", org_id=org_id,
        roles=frozenset([Role.OPERATOR]), session_id="test-session",
    )


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
    )


@pytest.fixture
def app_with_fake_auth(world):
    app = create_app()
    app.dependency_overrides[current_actor] = lambda: _fake_actor(world.a.org_id)
    yield app
    app.dependency_overrides.clear()


async def _seed_run(org_id: str, *, status: str = "completed") -> str:
    """Seed an agent_runs row with 2 execution_steps (trigger + terminated)."""
    run_id = uuid7str()
    async with tenant_session(org_id) as s:
        await s.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, org_id, status, terminal_reason, input, output, cost_usd, tokens_in, tokens_out) "
                "VALUES (:id, :o, :st, :tr, CAST(:inp AS jsonb), CAST(:out AS jsonb), 0.05, 100, 50)"
            ),
            {
                "id": run_id, "o": org_id, "st": status,
                "tr": status if status in ("completed", "failed", "escalated") else None,
                "inp": '{"task":"test"}', "out": '{"result":"done"}',
            },
        )
        for seq, step_type, node_id in [(1, "trigger", "start"), (2, "terminated", None)]:
            await s.execute(
                text(
                    "INSERT INTO execution_steps "
                    "(id, org_id, execution_id, seq, type, status, node_id, input, output) "
                    "VALUES (:id, :o, :e, :seq, :t, 'ok', :n, '{}'::jsonb, '{}'::jsonb)"
                ),
                {
                    "id": uuid7str(), "o": org_id, "e": run_id,
                    "seq": seq, "t": step_type, "n": node_id,
                },
            )
    return run_id


@pytest.mark.asyncio
async def test_list_runs_returns_org_runs(app_with_fake_auth, world):
    run_id = await _seed_run(world.a.org_id)
    async with _client(app_with_fake_auth) as c:
        r = await c.get("/api/v1/runs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    ids = [item["id"] for item in body["items"]]
    assert run_id in ids


@pytest.mark.asyncio
async def test_list_runs_filters_by_status(app_with_fake_auth, world):
    await _seed_run(world.a.org_id, status="completed")
    await _seed_run(world.a.org_id, status="failed")
    async with _client(app_with_fake_auth) as c:
        r = await c.get("/api/v1/runs?status=completed")
    assert r.status_code == 200, r.text
    for item in r.json()["items"]:
        assert item["status"] == "completed"


@pytest.mark.asyncio
async def test_get_run_detail_includes_steps(app_with_fake_auth, world):
    run_id = await _seed_run(world.a.org_id)
    async with _client(app_with_fake_auth) as c:
        r = await c.get(f"/api/v1/runs/{run_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == run_id
    assert body["status"] == "completed"
    assert len(body["steps"]) == 2
    assert body["steps"][0]["type"] == "trigger"
    assert body["steps"][1]["type"] == "terminated"


@pytest.mark.asyncio
async def test_get_run_detail_404_for_other_org(app_with_fake_auth, world):
    # Seed a run in org B, but fake actor is org A. RLS + explicit WHERE
    # filter should return 404.
    other_run = await _seed_run(world.b.org_id)
    async with _client(app_with_fake_auth) as c:
        r = await c.get(f"/api/v1/runs/{other_run}")
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_list_runs_requires_auth():
    app = create_app()
    async with _client(app) as c:
        r = await c.get("/api/v1/runs")
    assert r.status_code == 401, r.text
