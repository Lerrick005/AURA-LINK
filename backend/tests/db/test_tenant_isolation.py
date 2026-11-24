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
