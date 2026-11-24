"""Every error path returns the same envelope shape with request_id."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.errors import Forbidden, NotFound
from app.main import app


client = TestClient(app, raise_server_exceptions=False)


def _assert_envelope(response, expected_code: str, expected_status: int):
    assert response.status_code == expected_status
    body = response.json()
    assert "error" in body
    err = body["error"]
    assert set(err.keys()) >= {"code", "message", "request_id"}
    assert err["code"] == expected_code
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["request_id"], str) and err["request_id"]
    assert response.headers.get("x-request-id") == err["request_id"]


def test_health_has_request_id_header():
    r = client.get("/api/v1/healthz")
    assert r.status_code == 200
    assert r.headers.get("x-request-id")


def test_404_is_typed():
    r = client.get("/api/v1/does-not-exist")
    _assert_envelope(r, "http_404", 404)


def test_validation_error_is_typed():
    r = client.post("/api/v1/auth/login", json={})
    _assert_envelope(r, "validation_error", 422)


from fastapi import APIRouter
_test_router = APIRouter()


@_test_router.get("/_test/forbidden")
async def _raise_forbidden():
    raise Forbidden("nope", details={"reason": "test"})


@_test_router.get("/_test/not_found")
async def _raise_not_found():
    raise NotFound()


@_test_router.get("/_test/boom")
async def _raise_boom():
    raise RuntimeError("kaboom")


app.include_router(_test_router, prefix="/api/v1")


def test_app_error_forbidden():
    r = client.get("/api/v1/_test/forbidden")
    _assert_envelope(r, "forbidden", 403)
    assert r.json()["error"]["details"] == {"reason": "test"}


def test_app_error_not_found():
    r = client.get("/api/v1/_test/not_found")
    _assert_envelope(r, "not_found", 404)


def test_unhandled_exception_is_500_with_request_id():
    r = client.get("/api/v1/_test/boom")
    _assert_envelope(r, "internal_error", 500)
    assert "kaboom" not in r.text


import pytest_asyncio
from app.core.db import dispose_engine


@pytest_asyncio.fixture(autouse=True)
async def _reset_engine_after_api_tests():
    """TestClient runs its own event loop internally. asyncpg connections bound
    to the previous loop must be disposed or subsequent tests see closed-loop
    errors on pool ping. Dispose after each TestClient test."""
    yield
    await dispose_engine()
