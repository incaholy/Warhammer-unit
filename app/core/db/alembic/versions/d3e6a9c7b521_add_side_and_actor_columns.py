"""add side to the child tables, and the actor columns to the event log

Revision ID: d3e6a9c7b521
Revises: c2d5f8b3a417
Create Date: 2026-10-29 00:20:00.000000

K8's third revision (ROADMAP K8, decisions #67, #70, #94; the chain's steps are listed
under "The revision chain" there, which is the only place their count lives).

Purely additive: five nullable columns and two composite foreign keys. Nothing reads
them yet and no existing column changes, so the current code runs unaffected.

`kt_game_operatives.side` gets no constraint here. Its ownership leg widens from three
columns to four, and a widening is a drop-and-add -- one operation, so it belongs whole
to a later revision rather than half to this one. `kt_game_equipment` and
`kt_game_events` are different: neither ever had a composite leg, so theirs is a pure
addition and lands with the column it constrains.

While `side` is NULL both new legs are skipped under MATCH SIMPLE. On the children that
is correct for an unpopulated column, and the backfill in the next revision fills it.
On the event log it is permanent and deliberate: NULL means the event targets the game,
so there is no side row to point at.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3e6a9c7b521"
down_revision: Union[str, Sequence[str], None] = "c2d5f8b3a417"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the columns, and the two legs that are additions rather than widenings."""
    op.add_column(
        "kt_game_operatives",
        sa.Column("side", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True),
    )

    op.add_column(
        "kt_game_equipment",
        sa.Column("side", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True),
    )
    op.create_foreign_key(
        "fk_kt_game_equipment_side",
        "kt_game_equipment",
        "kt_game_sides",
        ["game_id", "side"],
        ["game_id", "side"],
    )

    op.add_column(
        "kt_game_events",
        sa.Column("side", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True),
    )
    op.add_column(
        "kt_game_events",
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "kt_game_events",
        sa.Column(
            "actor_display_name",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_kt_game_events_actor_user_id"),
        "kt_game_events",
        ["actor_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        None, "kt_game_events", "users", ["actor_user_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_kt_game_event_side",
        "kt_game_events",
        "kt_game_sides",
        ["game_id", "side"],
        ["game_id", "side"],
    )


def downgrade() -> None:
    """Reverses cleanly: every column this revision added is still empty of meaning.

    The columns may hold values if later revisions ran and were reversed first, but
    nothing outside them depends on those values -- the data they describe still sits on
    `kt_games` until the last revision drops it.
    """
    op.drop_constraint("fk_kt_game_event_side", "kt_game_events", type_="foreignkey")
    op.drop_constraint(
        "kt_game_events_actor_user_id_fkey", "kt_game_events", type_="foreignkey"
    )
    op.drop_index(op.f("ix_kt_game_events_actor_user_id"), table_name="kt_game_events")
    op.drop_column("kt_game_events", "actor_display_name")
    op.drop_column("kt_game_events", "actor_user_id")
    op.drop_column("kt_game_events", "side")

    op.drop_constraint(
        "fk_kt_game_equipment_side", "kt_game_equipment", type_="foreignkey"
    )
    op.drop_column("kt_game_equipment", "side")

    op.drop_column("kt_game_operatives", "side")
