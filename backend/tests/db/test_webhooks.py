"""HTTP tests for the webhook receiver and secret rotation."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

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


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _ts() -> str:
    return str(int(time.time()))


_GRAPH = {
    "entry_node_id": "start",
    "nodes": [
        {"id": "start", "type": "trigger", "next": "end", "config": None, "branches": None},
        {"id": "end", "type": "terminate", "next": None, "config": {"reason": "completed"}, "branches": None},
    ],
    "edges": [],
}


async def _deploy_workflow(client, name: str) -> str:
    """Helper: create + version + deploy a workflow. Returns workflow id."""
    r = await client.post("/api/v1/workflows", json={"name": name})
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]

    r = await client.post(f"/api/v1/workflows/{wf_id}/versions", json={"graph": _GRAPH})
    assert r.status_code == 201, r.text
    ver_id = r.json()["id"]

    r = await client.post(f"/api/v1/workflows/{wf_id}/deploy?version_id={ver_id}")
    assert r.status_code == 200, r.text
    return wf_id


@pytest.mark.asyncio
async def test_rotate_webhook_secret_returns_secret(app_with_fake_auth):
    async with _client(app_with_fake_auth) as c:
        wf_id = await _deploy_workflow(c, "hook-rotate")
        r = await c.post(f"/api/v1/webhooks/workflows/{wf_id}/rotate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workflow_id"] == wf_id
    assert "webhook_secret" in body
    assert len(body["webhook_secret"]) > 20


@pytest.mark.asyncio
async def test_webhook_without_secret_set_is_404(app_with_fake_auth):
    async with _client(app_with_fake_auth) as c:
        wf_id = await _deploy_workflow(c, "hook-no-secret")
        body = json.dumps({"x": 1}).encode()
        r = await c.post(
            f"/api/v1/webhooks/workflows/{wf_id}",
            content=body,
            headers={
                "x-auralink-signature-256": _sign("ignored", body),
                "x-auralink-timestamp": _ts(),
                "content-type": "application/json",
            },
        )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "http_404"


@pytest.mark.asyncio
async def test_webhook_with_valid_signature_returns_202(app_with_fake_auth):
    async with _client(app_with_fake_auth) as c:
        wf_id = await _deploy_workflow(c, "hook-ok")
        r = await c.post(f"/api/v1/webhooks/workflows/{wf_id}/rotate")
        secret = r.json()["webhook_secret"]

        body = json.dumps({"task": "do thing"}).encode()
        with patch(
            "app.modules.webhooks.routes.run_workflow_background",
            new=AsyncMock(return_value=None),
        ):
            r = await c.post(
                f"/api/v1/webhooks/workflows/{wf_id}",
                content=body,
                headers={
                    "x-auralink-signature-256": _sign(secret, body),
                    "x-auralink-timestamp": _ts(),
                    "content-type": "application/json",
                },
            )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "accepted"
    assert r.json()["run_id"]


@pytest.mark.asyncio
async def test_webhook_with_bad_signature_is_401(app_with_fake_auth):
    async with _client(app_with_fake_auth) as c:
        wf_id = await _deploy_workflow(c, "hook-badsig")
        await c.post(f"/api/v1/webhooks/workflows/{wf_id}/rotate")

        body = json.dumps({"task": "x"}).encode()
        r = await c.post(
            f"/api/v1/webhooks/workflows/{wf_id}",
            content=body,
            headers={
                "x-auralink-signature-256": "sha256=" + "0" * 64,
                "x-auralink-timestamp": _ts(),
                "content-type": "application/json",
            },
        )
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_webhook_with_stale_timestamp_is_401(app_with_fake_auth):
    async with _client(app_with_fake_auth) as c:
        wf_id = await _deploy_workflow(c, "hook-old")
        r = await c.post(f"/api/v1/webhooks/workflows/{wf_id}/rotate")
        secret = r.json()["webhook_secret"]

        body = json.dumps({"task": "x"}).encode()
        old_ts = str(int(time.time()) - 600)  # 10 min old, tolerance is 5 min
        r = await c.post(
            f"/api/v1/webhooks/workflows/{wf_id}",
            content=body,
            headers={
                "x-auralink-signature-256": _sign(secret, body),
                "x-auralink-timestamp": old_ts,
                "content-type": "application/json",
            },
        )
    assert r.status_code == 401, r.text
