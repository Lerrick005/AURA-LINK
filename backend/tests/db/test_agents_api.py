"""HTTP-level tests for /agents API."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

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


_AGENT_CONFIG = {
    "instructions": "You are a helpful test agent.",
    "model": "gpt-4o-mini",
}


async def _grant_tool(org_id: str, version_id: str, tool_id: str) -> None:
    """deploy_version refuses a version with no tool grants."""
    async with tenant_session(org_id) as s:
        await s.execute(
            text(
                "INSERT INTO agent_tool_grants "
                "(id, agent_version_id, tool_id, org_id, config) "
                "VALUES (:id, :av, :tid, :org, '{}'::jsonb)"
            ),
            {"id": uuid7str(), "av": version_id, "tid": tool_id, "org": org_id},
        )


@pytest.mark.asyncio
async def test_create_agent(app_with_fake_auth):
    async with _client(app_with_fake_auth) as c:
        r = await c.post("/api/v1/agents", json={"name": "test-agent"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "test-agent"
    assert body["status"] == "draft"
    assert body["current_version_id"] is None


@pytest.mark.asyncio
async def test_duplicate_agent_name_returns_409(app_with_fake_auth):
    async with _client(app_with_fake_auth) as c:
        await c.post("/api/v1/agents", json={"name": "dupe"})
        r = await c.post("/api/v1/agents", json={"name": "dupe"})
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_full_lifecycle_agent_version_deploy(app_with_fake_auth, world):
    async with _client(app_with_fake_auth) as c:
        r = await c.post("/api/v1/agents", json={"name": "lifecycle"})
        assert r.status_code == 201, r.text
        agent_id = r.json()["id"]

        r = await c.post(
            f"/api/v1/agents/{agent_id}/versions",
            json={"config": _AGENT_CONFIG},
        )
        assert r.status_code == 201, r.text
        ver_id = r.json()["id"]

        await _grant_tool(world.a.org_id, ver_id, world.a.tool_id)

        r = await c.post(f"/api/v1/agents/{agent_id}/deploy?version_id={ver_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active"
    assert body["current_version_id"] == ver_id


@pytest.mark.asyncio
async def test_trigger_deployed_agent_returns_202(app_with_fake_auth, world):
    async with _client(app_with_fake_auth) as c:
        r = await c.post("/api/v1/agents", json={"name": "runner"})
        agent_id = r.json()["id"]

        r = await c.post(
            f"/api/v1/agents/{agent_id}/versions",
            json={"config": _AGENT_CONFIG},
        )
        ver_id = r.json()["id"]
        await _grant_tool(world.a.org_id, ver_id, world.a.tool_id)
        await c.post(f"/api/v1/agents/{agent_id}/deploy?version_id={ver_id}")

        # Patch the background runner to avoid a real LLM call
        with patch(
            "app.modules.agents.routes.run_agent_background",
            new=AsyncMock(return_value=None),
        ):
            r = await c.post(
                f"/api/v1/agents/{agent_id}/runs",
                json={"input_data": {"task": "test"}},
            )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "accepted"
    assert r.json()["run_id"]


@pytest.mark.asyncio
async def test_get_agent_404_for_missing(app_with_fake_auth):
    async with _client(app_with_fake_auth) as c:
        r = await c.get("/api/v1/agents/does-not-exist")
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_unauthenticated_agent_request_is_401():
    app = create_app()
    async with _client(app) as c:
        r = await c.post("/api/v1/agents", json={"name": "x"})
    assert r.status_code == 401, r.text
