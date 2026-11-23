"""runtime schema: execution_steps, tool_calls, escalations; lease columns on agent_runs

Revision ID: 0002_runtime
Revises: 0001_initial
Create Date: 2026-11-23

Design notes:
- org_id is denormalized onto every new table so RLS policies are direct,
  no joins through agent_runs to find the tenant.
- execution_steps.type CHECK constraint lists the 11 legal step types.
  'terminated' is the sole terminal step; the engine enforces that every
  run's final step has type='terminated'.
- execution_steps and tool_calls are append-only (SELECT/INSERT granted to
  auralink_app; no UPDATE/DELETE). tool_calls has one UPDATE path for
  status transitions (executing -> completed / failed) gated by idempotency
  semantics, granted explicitly.
- No raw prompts in execution_steps.input/output. Prompts belong in a
  separate llm_calls table with shorter retention; that lands in File 8.
- tool_calls.executed_by is NULL (engine) or 'human:<user_id>' (human action
  completing a pending escalation). CHECK enforces the shape.
- tool_calls.idempotency_mode_snapshot copies the tool's mode at proposal
  time so the engine can enforce the no-rerun rule for non-idempotent tools
  without joining tools.
- agent_runs gains lease columns (holder, expires_at, heartbeat_at, epoch).
  epoch is a fencing token: every commit checks it matches. A stale worker's
  commit fails, no side effects applied.
- escalations records every human handoff with the pending tool call (if any),
  a frozen context snapshot, and status. Approval FK added in File 8 when
  approvals table exists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_runtime"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


_STEP_TYPES = (
    "loaded_config",
    "llm_request",
    "llm_response",
    "tool_proposed",
    "tool_rejected",
    "tool_executing",
    "tool_result",
    "verified",
    "escalated",
    "resumed",
    "terminated",
)

_STEP_TYPE_CHECK = "type IN (" + ", ".join(f"'{t}'" for t in _STEP_TYPES) + ")"


def upgrade() -> None:
    # --- agent_runs lease columns --------------------------------------
    op.add_column("agent_runs", sa.Column("lease_holder", sa.String(64), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("lease_epoch", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("current_step_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- execution_steps ------------------------------------------------
    op.create_table(
        "execution_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_version_id", sa.String(36), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.UniqueConstraint("execution_id", "seq", name="uq_execution_steps_execution_seq"),
    )
    op.create_index("ix_execution_steps_org_id", "execution_steps", ["org_id"])
    op.create_index("ix_execution_steps_execution_id", "execution_steps", ["execution_id"])
    op.create_index("ix_execution_steps_agent_version", "execution_steps", ["agent_version_id"])
    op.create_index("ix_execution_steps_org_started", "execution_steps", ["org_id", "started_at"])
    op.execute(f"ALTER TABLE execution_steps ADD CONSTRAINT ck_execution_steps_type CHECK ({_STEP_TYPE_CHECK})")

    # --- tool_calls -----------------------------------------------------
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("execution_steps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_version_id", sa.String(36), nullable=False),
        sa.Column("tool_id", sa.String(36), sa.ForeignKey("tools.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tool_key_snapshot", sa.String(100), nullable=False),
        sa.Column("risk_class_snapshot", sa.String(32), nullable=False),
        sa.Column("idempotency_mode_snapshot", sa.String(32), nullable=False),
        sa.Column("input", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("input_redacted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("output_redacted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("approval_id", sa.String(36), nullable=True),
        sa.Column("escalation_id", sa.String(36), nullable=True),
        sa.Column("executed_by", sa.String(64), nullable=True),
        sa.Column("verified", sa.Boolean, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.CheckConstraint(
            "executed_by IS NULL OR executed_by LIKE 'human:%'",
            name="ck_tool_calls_executed_by",
        ),
    )
    op.create_index("ix_tool_calls_org_id", "tool_calls", ["org_id"])
    op.create_index("ix_tool_calls_execution_id", "tool_calls", ["execution_id"])
    op.create_index("ix_tool_calls_org_started", "tool_calls", ["org_id", "started_at"])
    op.execute(
        "CREATE UNIQUE INDEX uq_tool_calls_idempotency "
        "ON tool_calls (execution_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )

    # --- escalations ----------------------------------------------------
    op.create_table(
        "escalations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_version_id", sa.String(36), nullable=False),
        sa.Column("tool_call_id", sa.String(36), sa.ForeignKey("tool_calls.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("context", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("assignee_id", sa.String(36), nullable=True),
        sa.Column("resolution", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_escalations_org_id", "escalations", ["org_id"])
    op.create_index("ix_escalations_org_status", "escalations", ["org_id", "status"])
    op.create_index("ix_escalations_execution_id", "escalations", ["execution_id"])

    # --- RLS ------------------------------------------------------------
    for table in ("execution_steps", "tool_calls", "escalations"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (org_id = current_setting('app.org_id', true))"
        )

    # --- Grants ---------------------------------------------------------
    # execution_steps is append-only from the runtime's point of view.
    op.execute("GRANT SELECT, INSERT ON execution_steps TO auralink_app")
    # tool_calls: insert + a controlled UPDATE path (status transitions).
    # Delete forbidden at MVP.
    op.execute("GRANT SELECT, INSERT, UPDATE ON tool_calls TO auralink_app")
    # escalations: operators update status/assignee/resolution.
    op.execute("GRANT SELECT, INSERT, UPDATE ON escalations TO auralink_app")


def downgrade() -> None:
    op.drop_table("escalations")
    op.drop_table("tool_calls")
    op.drop_table("execution_steps")
    for col in (
        "cancelled_at",
        "current_step_id",
        "lease_epoch",
        "lease_heartbeat_at",
        "lease_expires_at",
        "lease_holder",
    ):
        op.drop_column("agent_runs", col)
