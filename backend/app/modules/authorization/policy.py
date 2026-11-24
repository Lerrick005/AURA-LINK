"""The single authorization entry point. Nothing else decides access.

`authorize(actor, action, resource, resource_org_id=None)` returns a
Decision. It never raises for denial. It is pure: no DB, no I/O.

Cross-tenant rule: PLATFORM_OWNER bypasses the matrix for cross-org
reads and admin actions. It does NOT bypass RLS. Authorization decides
"may act", RLS decides "may see". They are different controls.

Org-scoping rule: if `resource_org_id` is provided and does not match
`actor.org_id`, deny unless the actor is PLATFORM_OWNER.
"""
from __future__ import annotations
from dataclasses import dataclass
from app.modules.authorization.matrix import allowed
from app.modules.authorization.models import Action, Resource, Role


@dataclass(frozen=True)
class Actor:
    """The authenticated principal. Built from a JWT by `current_actor`."""
    user_id: str
    org_id: str | None
    roles: frozenset[Role]
    session_id: str


@dataclass(frozen=True)
class Decision:
    """Authorization result. Explicit, loggable, non-bool.

    `audit_required` is True for any allow of a sensitive action.
    The caller MUST write an audit row before performing the action.
    """
    allowed: bool
    reason: str
    audit_required: bool = False


_AUDIT_ALWAYS: frozenset[Action] = frozenset({
    Action.CREATE, Action.UPDATE, Action.DELETE,
    Action.DEPLOY, Action.PAUSE, Action.RESUME,
    Action.APPROVE, Action.RUN, Action.CANCEL,
    Action.INVITE, Action.ROLE_CHANGE, Action.REVOKE,
})


def _is_platform_owner(actor: Actor) -> bool:
    return Role.PLATFORM_OWNER in actor.roles


def authorize(
    actor: Actor,
    action: Action,
    resource: Resource,
    *,
    resource_org_id: str | None = None,
) -> Decision:
    if _is_platform_owner(actor):
        audit = action in _AUDIT_ALWAYS
        return Decision(True, "platform_owner", audit_required=audit)

    if actor.org_id is None:
        return Decision(False, "actor_has_no_org")

    if resource_org_id is not None and resource_org_id != actor.org_id:
        return Decision(False, "cross_org_denied")

    if not actor.roles:
        return Decision(False, "actor_has_no_roles")

    if not allowed(actor.roles, resource, action):
        return Decision(False, f"role_denied:{action.value}:{resource.value}")

    audit = action in _AUDIT_ALWAYS
    return Decision(True, "role_allowed", audit_required=audit)