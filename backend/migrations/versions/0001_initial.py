"""initial schema: agents, versions, tools, grants, runs + RLS + builtin seed

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-23

Design notes:
- All primary keys are VARCHAR(36) UUIDv7 strings (app-generated).
- Every table with org_id gets ENABLE + FORCE ROW LEVEL SECURITY and a policy
  using current_setting('app.org_id', true). FORCE is required because
  the owner role would otherwise bypass RLS.
- tools.org_id is nullable for builtins. Policy allows NULL rows for everyone
  (builtin visibility) plus rows matching the current org (per-tenant custom).
- auralink_app gets SELECT/INSERT/UPDATE/DELETE on each table but never DDL.
- Three builtin tools are seeded with org_id NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


_TENANT_TABLES = (
    "agents",
    "agent_versions",
    "agent_tool_grants",
    "agent_runs",
)


def _enable_rls(table: str) -> None:
    """ENABLE + FORCE RLS, and create the tenant_isolation policy."""
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (org_id = current_setting('app.org_id', true))"
        )
    )


def _grant_to_app(table: str) -> None:
    op.execute(
        sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO auralink_app"
        )
    )


def upgrade() -> None:
    # --- agents ---------------------------------------------------------
    op.create_table(
        "agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "name", name="uq_agents_org_name"),
    )
    op.create_index("ix_agents_org_id", "agents", ["org_id"])
    op.create_index("ix_agents_org_status", "agents", ["org_id", "status"])

    # --- agent_versions -------------------------------------------------
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )
    op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])
    op.create_index("ix_agent_versions_org_id", "agent_versions", ["org_id"])
    op.create_index("ix_agent_versions_org_agent", "agent_versions", ["org_id", "agent_id"])

    # --- tools ----------------------------------------------------------
    op.create_table(
        "tools",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=True),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("input_schema", JSONB, nullable=False),
        sa.Column("output_schema", JSONB, nullable=False),
        sa.Column("risk_class", sa.String(32), nullable=False),
        sa.Column("approval_policy", sa.String(32), nullable=False, server_default="auto"),
        sa.Column("timeout_ms", sa.Integer, nullable=False, server_default="15000"),
        sa.Column("idempotency_mode", sa.String(32), nullable=False, server_default="keyed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "key", name="uq_tools_org_key"),
    )
    op.create_index("ix_tools_org_id", "tools", ["org_id"])
    op.create_index("ix_tools_org_risk", "tools", ["org_id", "risk_class"])

    # --- agent_tool_grants ---------------------------------------------
    op.create_table(
        "agent_tool_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_version_id", sa.String(36), sa.ForeignKey("agent_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_id", sa.String(36), sa.ForeignKey("tools.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("agent_version_id", "tool_id", name="uq_grants_version_tool"),
    )
    op.create_index("ix_grants_agent_version_id", "agent_tool_grants", ["agent_version_id"])
    op.create_index("ix_grants_tool_id", "agent_tool_grants", ["tool_id"])
    op.create_index("ix_grants_org_id", "agent_tool_grants", ["org_id"])

    # --- agent_runs -----------------------------------------------------
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("agent_version_id", sa.String(36), sa.ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("terminal_reason", sa.Text, nullable=True),
        sa.Column("input", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("iteration_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tool_call_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
    )
    op.create_index("ix_agent_runs_org_id", "agent_runs", ["org_id"])
    op.create_index("ix_agent_runs_agent_version_id", "agent_runs", ["agent_version_id"])
    op.create_index("ix_agent_runs_org_status", "agent_runs", ["org_id", "status"])
    op.create_index("ix_agent_runs_org_started", "agent_runs", ["org_id", "started_at"])

    # --- RLS on tenant tables ------------------------------------------
    for table in _TENANT_TABLES:
        _enable_rls(table)

    # tools: nullable org_id for builtins. Custom tools per-tenant; builtins visible to all.
    op.execute(sa.text("ALTER TABLE tools ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE tools FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation ON tools "
            "USING (org_id IS NULL OR org_id = current_setting('app.org_id', true))"
        )
    )

    # --- Grants to runtime role ----------------------------------------
    for table in ("agents", "agent_versions", "tools", "agent_tool_grants", "agent_runs"):
        _grant_to_app(table)

    # --- Seed builtin tools (org_id NULL) ------------------------------
    # Use op.execute("...") with a raw string, NOT sa.text(...).
    # sa.text() parses ":name" as a bind parameter, and JSON literals like
    # {"minimum":1,"maximum":20} contain ":" followed by digits, which
    # SQLAlchemy would misread as bind params named "1" / "2".
    _BUILTIN_TOOLS = (
        (
            "00000000-0000-7000-8000-000000000001",
            "knowledge.search",
            "Knowledge search",
            "Search the tenant knowledge base. Returns passages with citations.",
            '{"type":"object","properties":{"query":{"type":"string"},"top_k":{"type":"integer","minimum":1,"maximum":20}},"required":["query"]}',
            '{"type":"object","properties":{"passages":{"type":"array"}},"required":["passages"]}',
            "read_only",
            "auto",
            10000,
            "none",
        ),
        (
            "00000000-0000-7000-8000-000000000002",
            "channel.reply",
            "Send channel reply",
            "Send a message to the originating conversation on the active channel.",
            '{"type":"object","properties":{"text":{"type":"string","minLength":1,"maxLength":4000}},"required":["text"]}',
            '{"type":"object","properties":{"message_id":{"type":"string"},"delivered":{"type":"boolean"}},"required":["message_id","delivered"]}',
            "external_communication",
            "always",
            15000,
            "keyed",
        ),
        (
            "00000000-0000-7000-8000-000000000003",
            "handoff.request",
            "Request human handoff",
            "Escalate the conversation to a human operator with context and reasoning.",
            '{"type":"object","properties":{"reason":{"type":"string"},"priority":{"type":"string","enum":["low","normal","high"]}},"required":["reason"]}',
            '{"type":"object","properties":{"escalation_id":{"type":"string"}},"required":["escalation_id"]}',
            "reversible_write",
            "auto",
            5000,
            "keyed",
        ),
    )
    for _id, _key, _name, _desc, _in, _out, _risk, _approval, _timeout, _idem in _BUILTIN_TOOLS:
        _sql = (
            "INSERT INTO tools "
            "(id, org_id, key, name, description, input_schema, output_schema, "
            " risk_class, approval_policy, timeout_ms, idempotency_mode) VALUES ("
            f"'{_id}', NULL, '{_key}', '{_name}', '{_desc}', "
            f"'{_in}'::jsonb, '{_out}'::jsonb, "
            f"'{_risk}', '{_approval}', {_timeout}, '{_idem}'"
            ")"
        )
        # exec_driver_sql bypasses SQLAlchemy text() parsing entirely.
        # This is required for literal JSON: text() misreads '":1"' as a
        # positional bind parameter and asyncpg rejects it.
        op.get_bind().exec_driver_sql(_sql)

def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("agent_tool_grants")
    op.drop_table("tools")
    op.drop_table("agent_versions")
    op.drop_table("agents")
