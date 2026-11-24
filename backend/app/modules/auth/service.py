"""Auth service: login, refresh, logout, create org/user.

Sessions are rows. Refresh rotates. Logout revokes. Role changes take
effect on next refresh (hybrid c). Every successful login and every
refresh and every logout writes an audit row.
"""
from __future__ import annotations
from datetime import datetime, timezone
import secrets

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import tenant_session
from app.core.ids import uuid7str
from app.modules.audit.service import log_action
from app.modules.authorization.models import Action, Resource
from app.modules.auth import tokens
from app.modules.auth.models import Membership, Organization, Session, User
from app.modules.auth.passwords import hash_password, verify_password


class AuthError(Exception):
    pass


class InvalidCredentials(AuthError):
    pass


class InvalidRefresh(AuthError):
    pass


async def create_organization(session: AsyncSession | None = None, *, name: str) -> Organization:
    """Create an org. Uses admin_session (RLS bypass) because new tenants
    do not yet belong to any tenant context."""
    from app.core.db import admin_session
    org = Organization(id=uuid7str(), name=name)
    try:
        if session is not None:
            session.add(org)
            await session.flush()
            return org
        async with admin_session() as s:
            s.add(org)
            await s.flush()
            return org
    except IntegrityError as exc:
        raise AuthError(f"organization name already exists: {name!r}") from exc
async def create_user_with_membership(
    session: AsyncSession, *, email: str, password: str,
    org_id: str, roles: list[str], display_name: str | None = None,
) -> User:
    """Create a user and their first membership. MUST run inside
    tenant_session(org_id); the membership RLS check rejects it otherwise."""
    user = User(id=uuid7str(), email=email.lower().strip(),
                password_hash=hash_password(password), display_name=display_name)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AuthError(f"email already registered: {email!r}") from exc
    session.add(Membership(id=uuid7str(), user_id=user.id, org_id=org_id, roles=roles))
    await session.flush()
    return user
async def _load_membership(session: AsyncSession, *, user_id: str, org_id: str) -> Membership | None:
    result = await session.execute(
        select(Membership).where(Membership.user_id == user_id, Membership.org_id == org_id)
    )
    return result.scalar_one_or_none()


async def login(
    session: AsyncSession, *, email: str, password: str, org_id: str,
    ip: str | None = None, user_agent: str | None = None,
) -> tuple[User, Session, str, str]:
    """Return (user, session_row, access_token, refresh_token).

    Always runs argon2 verification, even for unknown users, to avoid
    user-enumeration via timing.
    """
    email_n = email.lower().strip()
    result = await session.execute(select(User).where(User.email == email_n))
    user = result.scalar_one_or_none()
    dummy_hash = "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    hash_to_check = user.password_hash if user else dummy_hash
    ok = verify_password(password, hash_to_check)
    if not user or not ok or not user.is_active:
        raise InvalidCredentials("invalid email or password")

    m = await _load_membership(session, user_id=user.id, org_id=org_id)
    if m is None:
        raise InvalidCredentials("invalid email or password")

    sess = Session(
        id=uuid7str(), user_id=user.id, org_id=org_id,
        refresh_token_hash="pending", expires_at=datetime.now(timezone.utc),
        created_ip=ip, user_agent=user_agent,
    )
    session.add(sess)
    await session.flush()

    access = tokens.encode_access_token(
        user_id=user.id, org_id=org_id, roles=list(m.roles), session_id=sess.id,
    )
    refresh, jti, expires = tokens.encode_refresh_token(user_id=user.id, session_id=sess.id)
    sess.refresh_token_hash = tokens.hash_jti(jti)
    sess.expires_at = expires
    await session.flush()

    await log_action(
        actor=None, org_id=org_id, action="login", resource="session",
        resource_id=sess.id, outcome="allowed", reason="login_ok", session=session,
    )
    return user, sess, access, refresh


async def refresh(
    session: AsyncSession, *, refresh_token: str,
) -> tuple[User, Session, str, str]:
    """Rotate: revoke old session, create new. Old refresh token dies."""
    try:
        claims = tokens.decode_refresh_token(refresh_token)
    except Exception:
        raise InvalidRefresh("invalid refresh token")

    sid = claims["sid"]
    jti = claims["jti"]
    result = await session.execute(select(Session).where(Session.id == sid))
    old = result.scalar_one_or_none()
    if old is None or old.revoked_at is not None:
        raise InvalidRefresh("session not found or revoked")
    if old.refresh_token_hash != tokens.hash_jti(jti):
        raise InvalidRefresh("refresh token mismatch")

    now = datetime.now(timezone.utc)
    if old.expires_at < now:
        raise InvalidRefresh("refresh token expired")

    user = await session.get(User, old.user_id)
    if user is None or not user.is_active:
        raise InvalidRefresh("user not found or inactive")

    m = await _load_membership(session, user_id=user.id, org_id=old.org_id)
    if m is None:
        raise InvalidRefresh("membership revoked")

    old.revoked_at = now
    new_sess = Session(
        id=uuid7str(), user_id=user.id, org_id=old.org_id,
        refresh_token_hash="pending", expires_at=now,
        created_ip=old.created_ip, user_agent=old.user_agent,
    )
    session.add(new_sess)
    await session.flush()

    access = tokens.encode_access_token(
        user_id=user.id, org_id=old.org_id, roles=list(m.roles), session_id=new_sess.id,
    )
    new_refresh, new_jti, new_exp = tokens.encode_refresh_token(
        user_id=user.id, session_id=new_sess.id,
    )
    new_sess.refresh_token_hash = tokens.hash_jti(new_jti)
    new_sess.expires_at = new_exp
    await session.flush()

    await log_action(
        actor=None, org_id=old.org_id, action="refresh", resource="session",
        resource_id=new_sess.id, outcome="allowed", reason="refresh_ok", session=session,
    )
    return user, new_sess, access, new_refresh


async def logout(session: AsyncSession, *, refresh_token: str) -> None:
    try:
        claims = tokens.decode_refresh_token(refresh_token)
    except Exception:
        return
    sid = claims["sid"]
    result = await session.execute(select(Session).where(Session.id == sid))
    sess = result.scalar_one_or_none()
    if sess is None or sess.revoked_at is not None:
        return
    sess.revoked_at = datetime.now(timezone.utc)
    await session.flush()
    await log_action(
        actor=None, org_id=sess.org_id, action="logout", resource="session",
        resource_id=sess.id, outcome="allowed", reason="logout_ok", session=session,
    )



