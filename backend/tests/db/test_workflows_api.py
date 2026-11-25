"""HTTP-level integration tests for the workflow API.

Uses FastAPI dependency_overrides to inject a fake Actor, bypassing the
auth middleware for these tests. Auth itself is covered separately.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.main import create_app
from app.modules.auth.deps import current_actor
from app.modules.authorization.models import Role
from app.modules.authorization.policy import Actor


def _fake_actor(org_id: str) -> Actor:
    return Actor(
        user_id="test-user",
        org_id=org_id,
        roles=frozenset([Role.OPERATOR]),
        session_id="test-session",
    )


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


@pytest.fixture
def app_with_fake_auth(world):
    app = create_app()
    app.dependency_overrides[current_actor] = lambda: _fake_actor(world.a.org_id)
    yield app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_workflow(app_with_fake_auth):
    async with _client(app_with_fake_auth) as c:
        r = await c.post("/api/v1/workflows", json={"name": "t1", "description": "d"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "t1"
    assert body["status"] == "draft"
    assert body["current_version_id"] is None


@pytest.mark.asyncio
async def test_full_lifecycle_create_version_deploy(app_with_fake_auth):
    graph = {
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "trigger", "next": "end", "config": None, "branches": None},
            {"id": "end", "type": "terminate", "next": None, "config": {"reason": "completed"}, "branches": None},
        ],
        "edges": [],
    }
    async with _client(app_with_fake_auth) as c:
        r = await c.post("/api/v1/workflows", json={"name": "t2"})
        assert r.status_code == 201, r.text
        wf_id = r.json()["id"]

        r = await c.post(f"/api/v1/workflows/{wf_id}/versions", json={"graph": graph})
        assert r.status_code == 201, r.text
        ver_id = r.json()["id"]

        r = await c.post(f"/api/v1/workflows/{wf_id}/deploy?version_id={ver_id}")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"
        assert r.json()["current_version_id"] == ver_id


@pytest.mark.asyncio
async def test_invalid_graph_returns_422(app_with_fake_auth):
    # Missing trigger node -> validate_graph returns errors
    graph = {
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "terminate", "next": None, "config": {"reason": "completed"}, "branches": None},
        ],
        "edges": [],
    }
    async with _client(app_with_fake_auth) as c:
        r = await c.post("/api/v1/workflows", json={"name": "t3"})
        wf_id = r.json()["id"]
        r = await c.post(f"/api/v1/workflows/{wf_id}/versions", json={"graph": graph})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "http_422"  # see ADR-008 note in commit


@pytest.mark.asyncio
async def test_trigger_workflow_returns_202(app_with_fake_auth):
    graph = {
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "trigger", "next": "end", "config": None, "branches": None},
            {"id": "end", "type": "terminate", "next": None, "config": {"reason": "completed"}, "branches": None},
        ],
        "edges": [],
    }
    async with _client(app_with_fake_auth) as c:
        r = await c.post("/api/v1/workflows", json={"name": "t4"})
        wf_id = r.json()["id"]
        r = await c.post(f"/api/v1/workflows/{wf_id}/versions", json={"graph": graph})
        ver_id = r.json()["id"]
        await c.post(f"/api/v1/workflows/{wf_id}/deploy?version_id={ver_id}")

        r = await c.post(
            f"/api/v1/workflows/{wf_id}/runs",
            json={"input_data": {"task": "test"}},
        )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "accepted"
    assert r.json()["run_id"]


@pytest.mark.asyncio
async def test_unauthenticated_request_is_401():
    # No dependency override — use the real current_actor which requires Bearer
    app = create_app()
    async with _client(app) as c:
        r = await c.post("/api/v1/workflows", json={"name": "t5"})
    assert r.status_code == 401, r.text
