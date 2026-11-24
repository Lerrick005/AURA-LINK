"""Audit log: append-only, tenant-isolated, redacts, writes denied too."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import tenant_session
from app.modules.audit.service import log_action
from app.modules.authorization.models import Action, Resource, Role
from app.modules.authorization.policy import Actor


def _actor(org_id="org-a", user_id="u1", roles=(Role.OPERATOR,)):
    return Actor(user_id=user_id, org_id=org_id,
                 roles=frozenset(roles), session_id="s1")


async def _count(session, org_id):
    async with tenant_session(org_id) as s:
        return (await s.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()


async def test_log_action_writes_one_row(world):
    actor = _actor(org_id=world.a.org_id)
    await log_action(actor=actor, action=Action.CREATE, resource=Resource.AGENT,
                     outcome="allowed", reason="role_allowed")
    n = await _count(None, world.a.org_id)
    assert n == 1


async def test_log_action_redacts_sensitive_meta(world):
    actor = _actor(org_id=world.a.org_id)
    await log_action(
        actor=actor, action=Action.UPDATE, resource=Resource.AGENT,
        outcome="allowed", reason="role_allowed",
        meta={"password": "hunter2", "nested": {"api_key": "k-123", "ok": "fine"}},
    )
    async with tenant_session(world.a.org_id) as s:
        row = (await s.execute(text(
            "SELECT meta FROM audit_log ORDER BY created_at DESC LIMIT 1"
        ))).mappings().one()
    assert row["meta"]["password"].startswith("<redacted")
    assert row["meta"]["nested"]["ok"] == "fine"


async def test_audit_rows_are_tenant_isolated(world):
    await log_action(actor=_actor(org_id=world.a.org_id),
                     action=Action.READ, resource=Resource.AGENT,
                     outcome="allowed", reason="role_allowed")
    # b sees none of a's rows
    async with tenant_session(world.b.org_id) as s:
        n = (await s.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()
    assert n == 0


async def test_runtime_role_cannot_update_audit(world):
    await log_action(actor=_actor(org_id=world.a.org_id),
                     action=Action.READ, resource=Resource.AGENT,
                     outcome="allowed", reason="role_allowed")
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.a.org_id) as s:
            await s.execute(text("UPDATE audit_log SET outcome='denied'"))


async def test_runtime_role_cannot_delete_audit(world):
    await log_action(actor=_actor(org_id=world.a.org_id),
                     action=Action.READ, resource=Resource.AGENT,
                     outcome="allowed", reason="role_allowed")
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.a.org_id) as s:
            await s.execute(text("DELETE FROM audit_log"))


async def test_denied_outcome_is_writable(world):
    await log_action(actor=_actor(org_id=world.a.org_id),
                     action=Action.DELETE, resource=Resource.AGENT,
                     outcome="denied", reason="role_denied:delete:agent")
    async with tenant_session(world.a.org_id) as s:
        outcomes = (await s.execute(text(
            "SELECT outcome FROM audit_log"
        ))).scalars().all()
    assert "denied" in outcomes


async def test_system_action_requires_explicit_org():
    with pytest.raises(ValueError):
        await log_action(actor=None, action=Action.UPDATE,
                         resource=Resource.AGENT, outcome="error")


async def test_system_action_with_org_works(world):
    await log_action(actor=None, org_id=world.a.org_id,
                     action=Action.UPDATE, resource=Resource.AGENT,
                     outcome="error", reason="cleanup")
    n = await _count(None, world.a.org_id)
    assert n == 1
