"""Authorization matrix must be complete and correctly bounded."""
import pytest
from app.modules.authorization.models import Action, Resource, Role
from app.modules.authorization.policy import Actor, authorize
from app.modules.authorization.matrix import PERMISSIONS, allowed


def _actor(roles, org_id="org-1"):
    return Actor(user_id="u1", org_id=org_id,
                 roles=frozenset(roles), session_id="s1")


def test_every_role_has_a_matrix_entry():
    for role in Role:
        if role is Role.PLATFORM_OWNER:
            continue
        assert role in PERMISSIONS, f"{role} missing from PERMISSIONS"


def test_viewer_cannot_create_agent():
    d = authorize(_actor([Role.VIEWER]), Action.CREATE, Resource.AGENT)
    assert d.allowed is False
    assert d.reason.startswith("role_denied")


def test_operator_can_create_agent_and_audit_is_required():
    d = authorize(_actor([Role.OPERATOR]), Action.CREATE, Resource.AGENT)
    assert d.allowed is True
    assert d.audit_required is True


def test_supervisor_can_approve_escalation_but_not_create_agent():
    a = _actor([Role.SUPERVISOR])
    assert authorize(a, Action.APPROVE, Resource.ESCALATION).allowed is True
    assert authorize(a, Action.CREATE, Resource.AGENT).allowed is False


def test_viewer_read_does_not_require_audit():
    d = authorize(_actor([Role.VIEWER]), Action.READ, Resource.AGENT)
    assert d.allowed is True
    assert d.audit_required is False


def test_cross_org_is_denied_even_for_org_owner():
    a = _actor([Role.ORG_OWNER], org_id="org-1")
    d = authorize(a, Action.READ, Resource.AGENT, resource_org_id="org-2")
    assert d.allowed is False
    assert d.reason == "cross_org_denied"


def test_platform_owner_can_act_cross_org():
    a = Actor(user_id="p1", org_id=None,
              roles=frozenset([Role.PLATFORM_OWNER]), session_id="s1")
    d = authorize(a, Action.UPDATE, Resource.AGENT, resource_org_id="org-2")
    assert d.allowed is True
    assert d.audit_required is True


def test_actor_with_no_roles_is_denied():
    d = authorize(_actor([]), Action.READ, Resource.AGENT)
    assert d.allowed is False
    assert d.reason == "actor_has_no_roles"


def test_no_org_actor_is_denied_unless_platform_owner():
    a = Actor(user_id="u1", org_id=None, roles=frozenset([Role.ORG_OWNER]), session_id="s1")
    d = authorize(a, Action.READ, Resource.AGENT)
    assert d.allowed is False
    assert d.reason == "actor_has_no_org"


@pytest.mark.parametrize("role", [Role.VIEWER, Role.OPERATOR, Role.SUPERVISOR, Role.ORG_ADMIN, Role.ORG_OWNER])
def test_no_role_can_delete_audit_log(role):
    a = _actor([role])
    d = authorize(a, Action.DELETE, Resource.AUDIT_LOG)
    assert d.allowed is False