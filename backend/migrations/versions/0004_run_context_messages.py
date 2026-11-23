"""run_context.messages: accumulated LLM conversation

Revision ID: 0004_run_context_messages
Revises: 0003_run_context
Create Date: 2026-11-23

The runtime engine stores the accumulated conversation in run_context so
each reasoning iteration reads one short row instead of reconstructing
from execution_steps. This closes the Q3 amendment: messages live on
run_context, not derived from steps.

Shape: JSONB array of {role, content, tool_calls?, tool_call_id?, name?}.
Empty for a fresh run until the first reasoning step populates it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_run_context_messages"
down_revision: str | None = "0003_run_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "run_context",
        sa.Column(
            "messages",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("run_context", "messages")
