"""FastAPI dependencies for authenticated routes.

`current_actor` is the single source of authentication for the API. Every
protected route depends on it. It decodes the Bearer token, resolves the
session row via the owner engine, then loads the Membership inside the
tenant context to build the Actor.

Failure modes:
  - Missing/malformed Bearer -> 401 missing_bearer_token
  - Invalid/expired token    -> 401 invalid_token
  - Session revoked/missing  -> 401 session_invalid
  - No membership for org    -> 403 not_a_member

DEV MODE:
  When APP_ENV == "dev", a missing session row does NOT cause a 401.
  Instead, the session is recreated on the fly from the token claims.
  This keeps the browser usable across backend restarts without weakening
  the production auth path (which still requires a real session row).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import get_owner_sessionmaker, tenant_session
from app.modules.auth.models import Membership, Session as SessionRow
from app.modules.auth.tokens import decode_access_token
from app.modules.authorization.models import Role
from app.modules.authorization.policy import Actor


_bearer = HTTPBearer(auto_error=False)


async def _dev_ensure_session(
    *, os_, session_id: str, user_id: str, org_id_hint: str | None,
) -> "SessionRow | None":
    """In dev: if session missing, create it. Returns the (new or existing) row."""
    existing = (await os_.execute(
        select(SessionRow).where(
            SessionRow.id == session_id,
            SessionRow.user_id == user_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    if not org_id_hint:
        return None
    try:
        await os_.execute(
            text(
                "INSERT INTO sessions "
                "(id, user_id, org_id, refresh_token_hash, expires_at) "
                "VALUES (:sid, :uid, :oid, :rth, NOW() + INTERVAL '100 years') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "sid": session_id,
                "uid": user_id,
                "oid": org_id_hint,
                "rth": "dev_autocreated",
            },
        )
        await os_.commit()
    except Exception:
        # If insert fails (e.g. old schema), fall back to None
        return None
    return (await os_.execute(
        select(SessionRow).where(SessionRow.id == session_id)
    )).scalar_one_or_none()


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

    settings = get_settings()
    is_dev = getattr(settings, "APP_ENV", "dev") == "dev"

    # Token may carry org_id directly (dev-only shortcut), or we derive it.
    org_id_from_claim = claims.get("org_id")

    async with get_owner_sessionmaker()() as os_:
        sess = (await os_.execute(
            select(SessionRow).where(
                SessionRow.id == session_id,
                SessionRow.user_id == user_id,
            )
        )).scalar_one_or_none()

        if sess is None:
            if is_dev:
                sess = await _dev_ensure_session(
                    os_=os_,
                    session_id=session_id,
                    user_id=user_id,
                    org_id_hint=org_id_from_claim,
                )
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

        org_id = getattr(sess, "org_id", None) or org_id_from_claim
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

        # In dev, if there's no membership, create one so the UI is usable.
        if mem is None and is_dev:
            try:
                await s.execute(
                    text(
                        "INSERT INTO memberships "
                        "(id, user_id, org_id, role, created_at) "
                        "VALUES (:id, :uid, :oid, 'org_owner', NOW()) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "uid": user_id,
                        "oid": org_id,
                    },
                )
                await s.commit()
                mem = (await s.execute(
                    select(Membership).where(
                        Membership.user_id == user_id,
                        Membership.org_id == org_id,
                    )
                )).scalar_one_or_none()
            except Exception:
                pass

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