"""workflows: workflows + workflow_versions tables, run/step FK columns

Revision ID: 0007_workflows
Revises: 0006_auth
Create Date: 2026-11-24

Design notes:
- workflows is the tenant-scoped definition. workflow_versions is the
  immutable graph snapshot. Deploying flips workflows.current_version_id,
  same pattern as agents.current_version_id from Stage 2.
- graph is JSONB. Pydantic validates shape at the service boundary
  (WORKFLOW_ENGINE.md describes the format). DB stores it opaquely.
- RLS on both tables, matching every other tenant table. The versions
  table needs its own org_id column even though workflow_id would suffice,
  so the RLS policy can be join-free.
- agent_runs.workflow_version_id is NULL for plain agent runs (Stage 2)
  and set for workflow runs (Stage 3+). No backfill needed - both columns
  are new and nullable.
- execution_steps.node_id is NULL for plain steps and set for steps that
  belong to a specific workflow node. Same compatibility story.
- The FK from workflow_versions.workflow_id -> workflows.id is CASCADE
  on delete: deleting a workflow removes its versions. That is correct
  since a version has no meaning without its parent workflow.
- Deleting a workflows row that is referenced by an agent_runs row
  would violate the run's integrity. We do not add a FK on
  agent_runs.workflow_version_id because runs should outlive their
  workflow (audit trail). The FK is logical, not DB-enforced.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0007_workflows"
down_revision = "0006_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- workflows ---------------------------------------------------------
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "name", name="uq_workflows_org_name"),
    )
    op.create_index("ix_workflows_org_status", "workflows", ["org_id", "status"])
    op.create_index("ix_workflows_org_created", "workflows", ["org_id", "created_at"])

    # --- workflow_versions ------------------------------------------------
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workflow_id", sa.String(36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("graph", JSONB(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workflow_id", "version",
                            name="uq_workflow_versions_workflow_version"),
    )
    op.create_index("ix_workflow_versions_org_workflow",
                    "workflow_versions", ["org_id", "workflow_id"])

    # --- RLS on both tables -----------------------------------------------
    for table in ("workflows", "workflow_versions"):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (org_id = current_setting('app.org_id', true))"
        ))
        op.execute(sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO auralink_app"
        ))

    # --- Extend existing tables ------------------------------------------
    op.add_column(
        "agent_runs",
        sa.Column("workflow_version_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "execution_steps",
        sa.Column("node_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_steps", "node_id")
    op.drop_column("agent_runs", "workflow_version_id")
    op.drop_table("workflow_versions")
    op.drop_table("workflows")

