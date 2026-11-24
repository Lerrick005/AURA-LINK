"""audit_log: append-only record of authorization decisions and admin actions

Revision ID: 0005_audit_log
Revises: 0004_run_context_messages
Create Date: 2026-11-24

Design notes:
- Append-only: auralink_app gets SELECT + INSERT only. No UPDATE, no DELETE.
  Immutability is enforced at the DB layer, mirroring tool_calls.
- org_id denormalized for join-free RLS. Actor may be NULL for system actions
  (e.g. the maintenance job, or a cron-triggered workflow with no user actor).
- outcome distinguishes allowed / denied / error. Denials are logged *before*
  the 403 is raised, so a brute-force attempt is visible in the audit trail.
- request_id and ip let an operator correlate an audit row with a log line
  and an HTTP request. Both nullable: not every writer has them (the
  maintenance job has neither).
- meta JSONB: free-form context. Never put secrets here. redaction.py runs
  on the way in (service layer), not on the way out.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0005_audit_log"
down_revision = "0004_run_context_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),  # 45 = max IPv6 text
        sa.Column("meta", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("actor_type IN ('user','system','agent','platform')",
                           name="ck_audit_log_actor_type"),
        sa.CheckConstraint("outcome IN ('allowed','denied','error')",
                           name="ck_audit_log_outcome"),
    )

    op.create_index("ix_audit_log_org_created", "audit_log", ["org_id", "created_at"])
    op.create_index("ix_audit_log_org_resource",
                    "audit_log", ["org_id", "resource_type", "resource_id"])
    op.create_index("ix_audit_log_org_actor", "audit_log", ["org_id", "actor_id"])

    op.execute(sa.text("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(
        "CREATE POLICY tenant_isolation ON audit_log "
        "USING (org_id = current_setting('app.org_id', true))"
    ))
    # Append-only grants. No UPDATE, no DELETE for the runtime role.
    op.execute(sa.text("GRANT SELECT, INSERT ON audit_log TO auralink_app"))


def downgrade() -> None:
    op.drop_table("audit_log")