"""add army points_limit

Revision ID: a10ed1e91d13
Revises: 61c99176b574
Create Date: 2026-07-03 17:25:38.406055

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'a10ed1e91d13'
down_revision: Union[str, Sequence[str], None] = '61c99176b574'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('armies', sa.Column('points_limit', sa.Integer(), nullable=True))
    op.create_check_constraint(
        'ck_army_points_limit_non_negative',
        'armies',
        'points_limit IS NULL OR points_limit >= 0',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_army_points_limit_non_negative', 'armies', type_='check')
    op.drop_column('armies', 'points_limit')
