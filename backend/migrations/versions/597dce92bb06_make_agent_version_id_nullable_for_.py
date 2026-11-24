from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '597dce92bb06'
down_revision: Union[str, None] = '4265c593fee4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.alter_column('agent_runs', 'agent_version_id', existing_type=sa.String(36), nullable=True)
    op.alter_column('execution_steps', 'agent_version_id', existing_type=sa.String(36), nullable=True)

def downgrade() -> None:
    # Note: Downgrading will fail if there are workflow runs in the DB.
    # In a real production environment, you would clean up or backfill before downgrading.
    op.alter_column('execution_steps', 'agent_version_id', existing_type=sa.String(36), nullable=False)
    op.alter_column('agent_runs', 'agent_version_id', existing_type=sa.String(36), nullable=False)
