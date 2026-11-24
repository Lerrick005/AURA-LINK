"""JWT issuance and verification. HS256, one secret from Settings.

Access token claims: sub (user_id), org_id, roles, sid (session_id), exp, iat, typ=access.
Refresh token claims: sub, sid, jti, exp, iat, typ=refresh.

Refresh tokens are also stored hashed in the sessions table. Possession of
the token is not sufficient; the row must exist and not be revoked. Rotation
on every use means a stolen refresh token is only useful until the next
legitimate refresh.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib
import secrets
import jwt

from app.core.config import get_settings


_ALG = "HS256"
_ACCESS_TYP = "access"
_REFRESH_TYP = "refresh"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def encode_access_token(
    *, user_id: str, org_id: str | None, roles: list[str], session_id: str,
    ttl_seconds: int | None = None,
) -> str:
    s = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else s.ACCESS_TOKEN_TTL_SECONDS
    now = _now()
    payload: dict[str, Any] = {
        "sub": user_id,
        "org_id": org_id,
        "roles": roles,
        "sid": session_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "typ": _ACCESS_TYP,
    }
    return jwt.encode(payload, s.JWT_SECRET.get_secret_value(), algorithm=_ALG)


def encode_refresh_token(*, user_id: str, session_id: str) -> tuple[str, str, datetime]:
    """Returns (token, jti, expires_at). Caller stores sha256(jti) in the session row."""
    s = get_settings()
    ttl = s.REFRESH_TOKEN_TTL_SECONDS
    now = _now()
    jti = secrets.token_urlsafe(32)
    payload = {
        "sub": user_id,
        "sid": session_id,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "typ": _REFRESH_TYP,
    }
    token = jwt.encode(payload, s.JWT_SECRET.get_secret_value(), algorithm=_ALG)
    return token, jti, now + timedelta(seconds=ttl)


def decode_access_token(token: str) -> dict:
    s = get_settings()
    claims = jwt.decode(
        token, s.JWT_SECRET.get_secret_value(), algorithms=[_ALG],
        options={"require": ["sub", "exp", "iat", "typ", "sid"]},
    )
    if claims.get("typ") != _ACCESS_TYP:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


def decode_refresh_token(token: str) -> dict:
    s = get_settings()
    claims = jwt.decode(
        token, s.JWT_SECRET.get_secret_value(), algorithms=[_ALG],
        options={"require": ["sub", "exp", "iat", "typ", "sid", "jti"]},
    )
    if claims.get("typ") != _REFRESH_TYP:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


def hash_jti(jti: str) -> str:
    """Store sha256(jti), never the jti itself. A DB leak doesn't yield tokens."""
    return hashlib.sha256(jti.encode()).hexdigest()

