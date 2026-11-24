"""FastAPI dependencies: authenticate + authorize.

`current_actor` decodes the JWT and builds an Actor. No DB on the hot path.
`require(action, resource)` is the ONLY way to protect a route. It runs
`authorize()` and, if `audit_required`, writes an audit row before
returning the actor to the handler. We audit attempts, not just successes.
"""
from __future__ import annotations
from typing import Callable
from fastapi import Depends, Header, HTTPException, Request, status

from app.modules.authorization.models import Action, Resource, Role
from app.modules.authorization.policy import Actor, Decision, authorize


def _decode_access_token(token: str) -> dict:
    from app.modules.auth.tokens import decode_access_token
    return decode_access_token(token)


async def current_actor(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Actor:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="missing_bearer_token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = _decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="invalid_token")
    try:
        roles = frozenset(Role(r) for r in claims.get("roles", []))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="invalid_role_in_token")
    return Actor(
        user_id=claims["sub"],
        org_id=claims.get("org_id"),
        roles=roles,
        session_id=claims["sid"],
    )


async def _write_audit(
    actor: Actor, action: Action, resource: Resource,
    resource_org_id: str | None, decision: Decision, request: Request,
) -> None:
    from app.modules.audit.service import log_action
    await log_action(
        actor=actor, action=action, resource=resource,
        resource_org_id=resource_org_id, outcome="allowed",
        reason=decision.reason, request=request,
    )


def require(action: Action, resource: Resource) -> Callable:
    """FastAPI dependency factory. Usage:

        @router.post('/agents', dependencies=[Depends(require(Action.CREATE, Resource.AGENT))])
        async def create_agent(...):
            ...
    """
    async def _dep(
        request: Request,
        actor: Actor = Depends(current_actor),
    ) -> Actor:
        decision = authorize(actor, action, resource)
        if not decision.allowed:
            await _write_denied_audit(actor, action, resource, decision, request)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=decision.reason)
        if decision.audit_required:
            await _write_audit(actor, action, resource, None, decision, request)
        return actor
    return _dep


async def _write_denied_audit(
    actor: Actor, action: Action, resource: Resource,
    decision: Decision, request: Request,
) -> None:
    try:
        from app.modules.audit.service import log_action
        await log_action(
            actor=actor, action=action, resource=resource,
            resource_org_id=None, outcome="denied",
            reason=decision.reason, request=request,
        )
    except Exception:
        # Audit write failure must not mask the 403.
        pass