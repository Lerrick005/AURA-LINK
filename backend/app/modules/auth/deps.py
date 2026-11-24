"""FastAPI dependencies for authenticated routes.

`current_actor` is the single source of authentication for the API. Every
protected route depends on it. It decodes the Bearer token, resolves the
session row via the owner engine (org_id is not in the token claims), then
loads the Membership inside the tenant context to build the Actor.

Failure modes:
  - Missing/malformed Bearer -> 401 missing_bearer_token
  - Invalid/expired token    -> 401 invalid_token
  - Session revoked/missing  -> 401 session_invalid
  - No membership for org    -> 403 not_a_member
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.db import get_owner_sessionmaker, tenant_session
from app.modules.auth.models import Membership, Session as SessionRow
from app.modules.auth.tokens import decode_access_token
from app.modules.authorization.models import Role
from app.modules.authorization.policy import Actor


_bearer = HTTPBearer(auto_error=False)


async def current_actor(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Actor:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_bearer_token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(creds.credentials)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
        )

    user_id = claims.get("sub")
    session_id = claims.get("sid")
    if not user_id or not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
        )

    # org_id is NOT in the token. Read it from the session row via the owner
    # engine (cross-tenant, no RLS). Same pattern as auth/router.refresh.
    async with get_owner_sessionmaker()() as os_:
        sess = (await os_.execute(
            select(SessionRow).where(
                SessionRow.id == session_id,
                SessionRow.user_id == user_id,
            )
        )).scalar_one_or_none()
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session_invalid",
        )
    if getattr(sess, "revoked_at", None) is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session_revoked",
        )
    org_id = getattr(sess, "org_id", None)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session_missing_org",
        )

    # Load the user's role for this org under tenant context.
    async with tenant_session(org_id) as s:
        mem = (await s.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.org_id == org_id,
            )
        )).scalar_one_or_none()
    if mem is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not_a_member",
        )

    raw_role = getattr(mem, "role", None) or getattr(mem, "role_name", None)
    if isinstance(raw_role, Role):
        roles = frozenset([raw_role])
    elif isinstance(raw_role, str):
        try:
            roles = frozenset([Role(raw_role)])
        except ValueError:
            roles = frozenset()
    else:
        roles = frozenset()

    return Actor(
        user_id=user_id,
        org_id=org_id,
        roles=roles,
        session_id=session_id,
    )


__all__ = ["current_actor"]
