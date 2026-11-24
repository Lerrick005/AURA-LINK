"""The permission matrix. Roles x resources x actions.

This is the ONLY place a role-to-permission mapping lives. If you find
yourself wanting to check a role elsewhere, add a permission here and
call authorize() instead.

Design notes:
- ORG_OWNER is a superset of ORG_ADMIN, which is a superset of OPERATOR,
  which is a superset of VIEWER. We spell it out so a reviewer can read
  the matrix and know exactly what each role can do.
- SUPERVISOR can read everything and approve/deny escalations, but cannot
  mutate agents, tools, or config. Supervision is read + adjudicate.
- Audit log is write-only for everyone except ORG_OWNER and PLATFORM_OWNER.
  Nobody can delete it (the DB enforces append-only).
"""
from __future__ import annotations
from app.modules.authorization.models import Action, Resource, Role

R, C, U, D = Action.READ, Action.CREATE, Action.UPDATE, Action.DELETE

_VIEWER: frozenset[tuple[Resource, Action]] = frozenset({
    (Resource.ORG, R), (Resource.USER, R), (Resource.AGENT, R),
    (Resource.AGENT_VERSION, R), (Resource.TOOL, R), (Resource.GRANT, R),
    (Resource.RUN, R), (Resource.EXECUTION_STEP, R), (Resource.TOOL_CALL, R),
    (Resource.ESCALATION, R), (Resource.SESSION, R),
})

_OPERATOR: frozenset[tuple[Resource, Action]] = _VIEWER | frozenset({
    (Resource.AGENT, C), (Resource.AGENT, U),
    (Resource.AGENT_VERSION, C), (Resource.GRANT, C), (Resource.GRANT, D),
    (Resource.AGENT, Action.DEPLOY), (Resource.AGENT, Action.PAUSE),
    (Resource.AGENT, Action.RESUME),
    (Resource.RUN, Action.RUN), (Resource.RUN, Action.CANCEL),
})

_SUPERVISOR: frozenset[tuple[Resource, Action]] = _VIEWER | frozenset({
    (Resource.ESCALATION, Action.APPROVE),
    (Resource.RUN, Action.CANCEL),
})

_ORG_ADMIN: frozenset[tuple[Resource, Action]] = _OPERATOR | _SUPERVISOR | frozenset({
    (Resource.USER, C), (Resource.USER, U), (Resource.USER, D),
    (Resource.MEMBERSHIP, R), (Resource.MEMBERSHIP, Action.INVITE),
    (Resource.MEMBERSHIP, Action.ROLE_CHANGE), (Resource.MEMBERSHIP, D),
    (Resource.TOOL, C), (Resource.TOOL, U), (Resource.TOOL, D),
    (Resource.API_KEY, R), (Resource.API_KEY, C), (Resource.API_KEY, D),
    (Resource.AUDIT_LOG, R),
})

_ORG_OWNER: frozenset[tuple[Resource, Action]] = _ORG_ADMIN | frozenset({
    (Resource.ORG, U),
    (Resource.SESSION, Action.REVOKE),
})

PERMISSIONS: dict[Role, frozenset[tuple[Resource, Action]]] = {
    Role.VIEWER: _VIEWER,
    Role.OPERATOR: _OPERATOR,
    Role.SUPERVISOR: _SUPERVISOR,
    Role.ORG_ADMIN: _ORG_ADMIN,
    Role.ORG_OWNER: _ORG_OWNER,
}


def allowed(roles: frozenset[Role], resource: Resource, action: Action) -> bool:
    """Return True if ANY of the actor's roles grants (resource, action)."""
    for role in roles:
        perms = PERMISSIONS.get(role)
        if perms is None:
            continue
        if (resource, action) in perms:
            return True
    return False