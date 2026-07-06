"""add user is_admin

Revision ID: 44441c6a9671
Revises: a10ed1e91d13
Create Date: 2026-07-05 17:32:03.546750

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '44441c6a9671'
down_revision: Union[str, Sequence[str], None] = 'a10ed1e91d13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'is_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'is_admin')
