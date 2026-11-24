"""add webhook_secret to workflows

Revision ID: ae5633de25d8
Revises: 597dce92bb06
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "ae5633de25d8"
down_revision: Union[str, None] = "597dce92bb06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("webhook_secret", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflows", "webhook_secret")
