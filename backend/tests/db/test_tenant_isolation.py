import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import get_sessionmaker, tenant_session


@pytest.mark.parametrize(
    "table,attr",
    [("agents", "agent_id"), ("agent_versions", "version_id"),
     ("agent_runs", "run_id"), ("tool_calls", "call_id")],
)
async def test_tenant_sees_only_its_own_rows(world, table, attr):
    row_id = getattr(world.a, attr)
    q = text(f"SELECT count(*) FROM {table} WHERE id = :i")
    async with tenant_session(world.a.org_id) as s:
        assert (await s.execute(q, {"i": row_id})).scalar_one() == 1
    async with tenant_session(world.b.org_id) as s:
        assert (await s.execute(q, {"i": row_id})).scalar_one() == 0


async def test_other_tenant_cannot_update_tool_call(world):
    async with tenant_session(world.b.org_id) as s:
        res = await s.execute(
            text("UPDATE tool_calls SET status = 'failed' WHERE id = :i"),
            {"i": world.a.call_id},
        )
        assert res.rowcount == 0
    async with tenant_session(world.a.org_id) as s:
        status = (await s.execute(
            text("SELECT status FROM tool_calls WHERE id = :i"), {"i": world.a.call_id},
        )).scalar_one()
    assert status == "executing"


async def test_insert_into_another_orgs_rows_is_rejected(world, insert_tool_call):
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(world.b.org_id) as s:
            await insert_tool_call(
                s, org_id=world.a.org_id, run_id=world.a.run_id,
                version_id=world.a.version_id, tool_id=world.a.tool_id,
            )


async def test_illegal_tool_call_status_rejected_by_db(world):
    with pytest.raises(DBAPIError, match="ck_tool_calls_status"):
        async with tenant_session(world.a.org_id) as s:
            await s.execute(
                text("UPDATE tool_calls SET status = 'bogus' WHERE id = :i"),
                {"i": world.a.call_id},
            )


async def test_duplicate_idempotency_key_rejected(world, insert_tool_call):
    async with tenant_session(world.a.org_id) as s:
        key = (await s.execute(
            text("SELECT idempotency_key FROM tool_calls WHERE id = :i"), {"i": world.a.call_id},
        )).scalar_one()
    with pytest.raises(DBAPIError, match="uq_tool_calls_idempotency"):
        async with tenant_session(world.a.org_id) as s:
            await insert_tool_call(
                s, org_id=world.a.org_id, run_id=world.a.run_id,
                version_id=world.a.version_id, tool_id=world.a.tool_id, idem=key,
            )


async def test_runtime_role_cannot_delete_tool_calls(world):
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.a.org_id) as s:
            await s.execute(text("DELETE FROM tool_calls WHERE id = :i"), {"i": world.a.call_id})


async def test_no_tenant_context_sees_nothing(world):
    async with get_sessionmaker()() as s:
        n = (await s.execute(text("SELECT count(*) FROM agent_runs"))).scalar_one()
    assert n == 0


async def test_tenant_session_requires_org_id():
    with pytest.raises(ValueError):
        async with tenant_session(""):
            pass


async def test_uuid_object_and_string_are_equivalent(world):
    """tenant_session must treat uuid.UUID and str identically.

    Guards against RLS comparison footgun: app.org_id GUC is text, org_id
    columns are VARCHAR(36). If uuid7str() format and str(uuid.UUID(...))
    format differ, one form silently returns 0 rows via RLS while the other
    works. This test locks in equivalence.
    """
    import uuid
    org_str = world.a.org_id
    org_uuid = uuid.UUID(org_str)
    async with tenant_session(org_str) as s:
        by_str = (await s.execute(
            text("SELECT count(*) FROM agents WHERE org_id = :o"), {"o": org_str}
        )).scalar_one()
    async with tenant_session(org_uuid) as s:
        by_uuid = (await s.execute(
            text("SELECT count(*) FROM agents WHERE org_id = :o"), {"o": str(org_uuid)}
        )).scalar_one()
    async with tenant_session(org_uuid) as s:
        # Also assert the raw count using the GUC value the session set
        via_guc = (await s.execute(
            text("SELECT count(*) FROM agents WHERE org_id = current_setting('app.org_id', true)")
        )).scalar_one()
    assert by_str == by_uuid == via_guc, (
        f"RLS mismatch: str={by_str}, uuid={by_uuid}, guc={via_guc}. "
        f"Check app/core/ids.py uuid7str() format vs str(uuid.UUID(...))."
    )
    assert by_str > 0, "fixture should have at least one agent"
