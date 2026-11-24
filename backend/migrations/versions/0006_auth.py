"""auth: users, organizations, memberships, sessions + RLS + role helper

Revision ID: 0006_auth
Revises: 0005_audit_log
Create Date: 2026-11-24

Notes:
- users is global (no org_id). organizations is the tenant root.
- memberships and sessions carry org_id and get RLS.
- A CHECK ensures roles is a JSONB array of strings.
- Sessions expire in 30 days; a maintenance job later purges.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0006_auth"
down_revision = "0005_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("roles", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"),
    )
    op.create_index("ix_memberships_org", "memberships", ["org_id"])
    op.create_index("ix_memberships_user", "memberships", ["user_id"])
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("refresh_token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sessions_org", "sessions", ["org_id"])
    op.create_index("ix_sessions_user", "sessions", ["user_id"])

    for table in ("memberships", "sessions"):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (org_id = current_setting('app.org_id', true))"
        ))
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO auralink_app"))

    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON users TO auralink_app"))
    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON organizations TO auralink_app"))


def downgrade() -> None:
    op.drop_table("sessions")
    op.drop_table("memberships")
    op.drop_table("organizations")
    op.drop_table("users")
