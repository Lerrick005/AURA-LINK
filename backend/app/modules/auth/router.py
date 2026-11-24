"""Auth endpoints: /auth/login, /auth/refresh, /auth/logout."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.db import tenant_session
from app.modules.auth.schemas import LoginRequest, LoginResponse, TokenPair, UserOut
from app.modules.auth.service import (
    InvalidCredentials, InvalidRefresh, login, logout, refresh,
)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login_endpoint(body: LoginRequest, request: Request, response: Response):
    org_id = request.headers.get("x-org-id")
    if not org_id:
        raise HTTPException(status_code=400, detail="x-org-id header required")
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    ua = request.headers.get("user-agent")
    try:
        async with tenant_session(org_id) as s:
            user, sess, access, ref = await login(
                s, email=body.email, password=body.password, org_id=org_id, ip=ip, user_agent=ua,
            )
    except InvalidCredentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    response.set_cookie(
        key="refresh_token", value=ref, httponly=True, secure=True, samesite="strict",
        max_age=30 * 24 * 3600, path="/auth",
    )
    return LoginResponse(
        user=UserOut(id=user.id, email=user.email, display_name=user.display_name),
        org_id=org_id, roles=list(sess.__dict__.get("_roles", [])),
        tokens=TokenPair(access_token=access, expires_in=15 * 60),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh_endpoint(request: Request, response: Response):
    ref = request.cookies.get("refresh_token")
    if not ref:
        raise HTTPException(status_code=401, detail="no_refresh_token")
    try:
        import jwt
        claims = jwt.decode(ref, options={"verify_signature": False})
        org_id = None
        # org_id resolution: refresh row carries it; we trust the row, not the token
        async with tenant_session("00000000-0000-0000-0000-000000000000") as s:
            pass
    except Exception:
        pass
    # The refresh flow needs to load the session without knowing org up front.
    # We use the session id from the token, then read its org_id from the row.
    from sqlalchemy import select
    from app.modules.auth.models import Session as SessionRow
    import jwt
    try:
        claims = jwt.decode(ref, options={"verify_signature": False})
    except Exception:
        raise HTTPException(status_code=401, detail="invalid_refresh_token")
    sid = claims.get("sid")
    if not sid:
        raise HTTPException(status_code=401, detail="invalid_refresh_token")
    # Peek the session via owner engine to learn org_id, then use tenant_session
    from app.core.db import get_owner_sessionmaker
    async with get_owner_sessionmaker()() as os_:
        row = (await os_.execute(select(SessionRow).where(SessionRow.id == sid))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="invalid_refresh_token")
    try:
        async with tenant_session(row.org_id) as s:
            user, sess, access, new_ref = await refresh(s, refresh_token=ref)
    except InvalidRefresh:
        raise HTTPException(status_code=401, detail="invalid_refresh_token")
    response.set_cookie(
        key="refresh_token", value=new_ref, httponly=True, secure=True, samesite="strict",
        max_age=30 * 24 * 3600, path="/auth",
    )
    return TokenPair(access_token=access, expires_in=15 * 60)


@router.post("/logout", status_code=204)
async def logout_endpoint(request: Request, response: Response):
    ref = request.cookies.get("refresh_token")
    response.delete_cookie("refresh_token", path="/auth")
    if not ref:
        return Response(status_code=204)
    import jwt
    try:
        claims = jwt.decode(ref, options={"verify_signature": False})
        sid = claims.get("sid")
    except Exception:
        return Response(status_code=204)
    from sqlalchemy import select
    from app.modules.auth.models import Session as SessionRow
    from app.core.db import get_owner_sessionmaker
    async with get_owner_sessionmaker()() as os_:
        row = (await os_.execute(select(SessionRow).where(SessionRow.id == sid))).scalar_one_or_none()
    if row is None:
        return Response(status_code=204)
    try:
        async with tenant_session(row.org_id) as s:
            await logout(s, refresh_token=ref)
    except Exception:
        pass
    return Response(status_code=204)
