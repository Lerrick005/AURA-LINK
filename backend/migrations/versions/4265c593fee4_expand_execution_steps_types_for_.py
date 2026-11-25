"""expand execution_steps types for workflow nodes

Revision ID: 4265c593fee4
Revises: 0007_workflows
Create Date: 2026-11-24 22:42:33.212529
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4265c593fee4'
down_revision: str | None = '0007_workflows'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STEP_TYPES = (
    "loaded_config", "llm_request", "llm_response", "tool_proposed",
    "tool_rejected", "tool_executing", "tool_result", "verified",
    "escalated", "resumed", "terminated",
)
_NEW_STEP_TYPES = _OLD_STEP_TYPES + ("trigger", "agent", "terminate", "condition", "tool")

def _check_expr(types):
    return "type IN (" + ", ".join(f"'{t}'" for t in types) + ")"

def upgrade() -> None:
    op.execute("ALTER TABLE execution_steps DROP CONSTRAINT ck_execution_steps_type")
    op.execute(
        f"ALTER TABLE execution_steps ADD CONSTRAINT ck_execution_steps_type "
        f"CHECK ({_check_expr(_NEW_STEP_TYPES)})"
    )

def downgrade() -> None:
    op.execute("ALTER TABLE execution_steps DROP CONSTRAINT ck_execution_steps_type")
    op.execute(
        f"ALTER TABLE execution_steps ADD CONSTRAINT ck_execution_steps_type "
        f"CHECK ({_check_expr(_OLD_STEP_TYPES)})"
    )
