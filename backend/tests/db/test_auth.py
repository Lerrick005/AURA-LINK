"""Auth: login, refresh rotation, logout revokes, argon2 format."""
import uuid
import pytest
from sqlalchemy import text

from app.core.db import tenant_session
from app.modules.auth import service as auth_service
from app.modules.auth.passwords import hash_password, verify_password


def _org_name() -> str:
    return f"org-{uuid.uuid4().hex[:12]}"


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


async def _seed(email=None, pw="correct horse battery"):
    """Create org (admin) then user+membership (tenant). Returns (org, user, email)."""
    from app.modules.auth.service import create_organization
    if email is None:
        email = _email()
    org = await create_organization(name=_org_name())
    async with tenant_session(org.id) as s:
        user = await auth_service.create_user_with_membership(
            s, email=email, password=pw, org_id=org.id,
            roles=["org_owner"], display_name="Alice",
        )
    return org, user, email


async def test_hash_is_argon2id_and_verifies():
    h = hash_password("a-real-password-123")
    assert h.startswith("$argon2id$")
    assert verify_password("a-real-password-123", h) is True
    assert verify_password("wrong", h) is False


async def test_login_returns_tokens_and_writes_session(world):
    org, user, email = await _seed()
    async with tenant_session(org.id) as s:
        _, sess, access, ref = await auth_service.login(
            s, email=email, password="correct horse battery", org_id=org.id,
        )
    assert access and ref
    assert sess.org_id == org.id
    assert sess.revoked_at is None


async def test_wrong_password_401(world):
    org, user, email = await _seed()
    async with tenant_session(org.id) as s:
        with pytest.raises(auth_service.InvalidCredentials):
            await auth_service.login(
                s, email=email, password="wrong", org_id=org.id,
            )


async def test_unknown_user_401(world):
    org, user, email = await _seed()
    async with tenant_session(org.id) as s:
        with pytest.raises(auth_service.InvalidCredentials):
            await auth_service.login(
                s, email="nobody-" + uuid.uuid4().hex[:8] + "@example.com",
                password="x", org_id=org.id,
            )


async def test_refresh_rotates_and_old_is_revoked(world):
    org, user, email = await _seed()
    async with tenant_session(org.id) as s:
        _, old_sess, _, old_ref = await auth_service.login(
            s, email=email, password="correct horse battery", org_id=org.id,
        )
    old_sid = old_sess.id
    async with tenant_session(org.id) as s:
        _, new_sess, _, new_ref = await auth_service.refresh(s, refresh_token=old_ref)
    assert new_sess.id != old_sid
    async with tenant_session(org.id) as s:
        row = (await s.execute(
            text("SELECT revoked_at FROM sessions WHERE id = :i"), {"i": old_sid},
        )).scalar_one()
    assert row is not None
    async with tenant_session(org.id) as s:
        with pytest.raises(auth_service.InvalidRefresh):
            await auth_service.refresh(s, refresh_token=old_ref)


async def test_logout_revokes_session(world):
    org, user, email = await _seed()
    async with tenant_session(org.id) as s:
        _, sess, _, ref = await auth_service.login(
            s, email=email, password="correct horse battery", org_id=org.id,
        )
    async with tenant_session(org.id) as s:
        await auth_service.logout(s, refresh_token=ref)
    async with tenant_session(org.id) as s:
        with pytest.raises(auth_service.InvalidRefresh):
            await auth_service.refresh(s, refresh_token=ref)
