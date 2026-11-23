"""run_context table and tool_calls.status CHECK

Revision ID: 0003_run_context
Revises: 0002_runtime
Create Date: 2026-11-23

Design notes:
- run_context holds per-run durable scratch state that must survive a crash.
  One row per execution. RLS policy uses denormalized org_id, no joins.
- TTL: expires_at defaults to now() + 30 days. A maintenance job deletes rows
  whose expires_at has passed AND whose run is in a terminal status. Never
  deleted for active runs -- the engine needs them.
- tool_calls.status CHECK is added now so the engine's no-rerun rule can key
  on the literal string 'stuck' and so that any illegal status write fails at
  the DB, not in a downstream test.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_run_context"
down_revision: str | None = "0002_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSONB = postgresql.JSONB(astext_type=sa.Text())

_TOOL_CALL_STATUSES = (
    "proposed",
    "executing",
    "completed",
    "failed",
    "rejected",
    "cancelled",
    "stuck",
)
_STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in _TOOL_CALL_STATUSES) + ")"


def upgrade() -> None:
    # --- run_context ----------------------------------------------------
    op.create_table(
        "run_context",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_version_id", sa.String(36), nullable=False),
        sa.Column(
            "scratch",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("resume_from_step_id", sa.String(36), nullable=True),
        sa.Column(
            "consecutive_rejections",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_terminal_reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now() + interval '30 days'"),
            nullable=False,
        ),
        sa.UniqueConstraint("execution_id", name="uq_run_context_execution_id"),
    )
    op.create_index("ix_run_context_org_id", "run_context", ["org_id"])
    op.create_index("ix_run_context_expires_at", "run_context", ["expires_at"])

    op.execute("ALTER TABLE run_context ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE run_context FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON run_context "
        "USING (org_id = current_setting('app.org_id', true))"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON run_context TO auralink_app")
    # DELETE intentionally withheld from auralink_app -- only maintenance role.

    # --- tool_calls.status CHECK ---------------------------------------
    op.execute(
        "ALTER TABLE tool_calls ADD CONSTRAINT ck_tool_calls_status CHECK ("
        + _STATUS_CHECK
        + ")"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tool_calls DROP CONSTRAINT ck_tool_calls_status")
    op.drop_table("run_context")
