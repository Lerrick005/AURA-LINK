"""ORM classes must map every column that exists in the DB for their table.

Raw-SQL-managed tables (tool_calls, execution_steps, escalations, audit_log,
run_context) are excluded: they have no ORM class by design. See
migrations/README.md.
"""
import pytest
from sqlalchemy import text

from app.core.db import tenant_session
from app.modules.agents.models import (
    Agent, AgentRun, AgentToolGrant, AgentVersion, Tool,
)
from app.modules.auth.models import Membership, Organization, Session, User


ORM_TABLES = [
    (Agent, "agents"),
    (AgentVersion, "agent_versions"),
    (Tool, "tools"),
    (AgentToolGrant, "agent_tool_grants"),
    (AgentRun, "agent_runs"),
    (User, "users"),
    (Organization, "organizations"),
    (Membership, "memberships"),
    (Session, "sessions"),
]


async def _db_columns(session, table: str) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    )
    return {r[0] for r in rows.all()}


@pytest.mark.parametrize("model,table", ORM_TABLES)
async def test_orm_maps_every_db_column(world, model, table):
    async with tenant_session(world.a.org_id) as s:
        db_cols = await _db_columns(s, table)
    orm_cols = {c.name for c in model.__table__.columns}
    missing = db_cols - orm_cols
    assert not missing, (
        f"{table}: ORM class {model.__name__} is missing DB columns {sorted(missing)}. "
        f"Either add them to the model or document the table as raw-SQL-managed in "
        f"migrations/README.md."
    )
