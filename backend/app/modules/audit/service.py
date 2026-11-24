"""Audit log write path. The only writer of the audit_log table.

Design (hybrid c):
- log_action() opens its own tenant_session by default. Suitable for
  callers that run *before* the route body (the require() dependency).
- log_action(session=...) accepts a caller's session for atomicity when
  the audit row and the action must commit together.
- Never raises on success. On failure, logs and (by default) swallows:
  audit must not break a request that is otherwise valid. The one place
  this is wrong is failed-auth logging, where the caller already
  handles the exception path explicitly.
"""
from __future__ import annotations
import json
from typing import Any
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import tenant_session
from app.core.ids import uuid7str
from app.core.logging import get_logger
from app.core.redaction import redact_audit_meta  # if not present, fallback below
from app.modules.authorization.models import Action, Resource
from app.modules.authorization.policy import Actor

log = get_logger(__name__)


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()[:45]
    if request.client:
        return request.client.host[:45]
    return None


def _actor_fields(actor: Actor | None) -> tuple[str | None, str]:
    if actor is None:
        return None, "system"
    if "platform_owner" in {r.value for r in actor.roles}:
        return actor.user_id, "platform"
    return actor.user_id, "user"


def _safe_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not meta:
        return {}
    try:
        return redact_audit_meta(meta)
    except Exception:
        # If redaction fails, drop the meta rather than risk leaking.
        log.error("audit_redaction_failed")
        return {}


async def _insert(
    session: AsyncSession,
    *,
    org_id: str,
    actor: Actor | None,
    action: Action | str,
    resource: Resource | str,
    resource_id: str | None,
    outcome: str,
    reason: str | None,
    request: Request | None,
    meta: dict[str, Any] | None,
) -> None:
    actor_id, actor_type = _actor_fields(actor)
    request_id = None
    if request is not None:
        request_id = request.headers.get("x-request-id")

    await session.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO audit_log "
            "(id, org_id, actor_id, actor_type, action, resource_type, resource_id, "
            " outcome, reason, request_id, ip, meta) "
            "VALUES (:id, :org_id, :actor_id, :actor_type, :action, :rtype, :rid, "
            "        :outcome, :reason, :req_id, :ip, :meta)"
        ),
        {
            "id": uuid7str(),
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "action": action.value if hasattr(action, "value") else str(action),
            "rtype": resource.value if hasattr(resource, "value") else str(resource),
            "rid": resource_id,
            "outcome": outcome,
            "reason": reason,
            "req_id": request_id,
            "ip": _client_ip(request),
            "meta": json.dumps(_safe_meta(meta)),
        },
    )


async def log_action(
    *,
    actor: Actor | None,
    action: Action | str,
    resource: Resource | str,
    org_id: str | None = None,
    resource_id: str | None = None,
    outcome: str,
    reason: str | None = None,
    request: Request | None = None,
    meta: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Write one audit row. Never raises on failure (by design).

    org_id resolution:
    - explicit org_id wins
    - else actor.org_id
    - else ValueError (system actions must name their org explicitly)
    """
    target_org = org_id or (actor.org_id if actor else None)
    if not target_org:
        raise ValueError("log_action requires org_id or an actor with org_id")

    try:
        if session is not None:
            await _insert(
                session,
                org_id=target_org, actor=actor, action=action, resource=resource,
                resource_id=resource_id, outcome=outcome, reason=reason,
                request=request, meta=meta,
            )
        else:
            async with tenant_session(target_org) as s:
                await _insert(
                    s,
                    org_id=target_org, actor=actor, action=action, resource=resource,
                    resource_id=resource_id, outcome=outcome, reason=reason,
                    request=request, meta=meta,
                )
    except Exception as exc:
        log.error("audit_write_failed",
                  extra={"action": str(action), "outcome": outcome,
                         "error": str(exc)})