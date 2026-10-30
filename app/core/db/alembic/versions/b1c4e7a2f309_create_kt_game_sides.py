"""create kt_game_sides and the parent unique it points at

Revision ID: b1c4e7a2f309
Revises: e6d56ea7d09e
Create Date: 2026-10-29 00:00:00.000000

The first of K8's revisions (ROADMAP K8, decisions #67, #68, #69, #74, #77).

Purely additive: nothing reads this table yet and no existing column changes, so the
old code runs unaffected against the result. The unique on `kt_games` and the table
are ONE operation rather than two revisions -- the table's composite leg cannot be
created without the parent it points at, and splitting a thing from the constraint it
requires is the split ARCHITECTURE.md section 4 refuses.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b1c4e7a2f309"
down_revision: Union[str, Sequence[str], None] = "e6d56ea7d09e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    """Add the side table, and the unique its own leg into the game needs."""
    # `kt_game_sides` reaches its game through (owner_user_id, game_id), and Postgres
    # needs a declared unique covering exactly those target columns -- a subset of
    # `uq_kt_game_owner_team_id` will not do. It cannot use that unique anyway: a side
    # carries its OWN kill_team_id, which differs from the game's for the opponent and
    # is NULL for one of #77's backfilled rows, so a leg through it would constrain the
    # wrong relationship.
    op.create_unique_constraint(
        "uq_kt_game_owner_id", "kt_games", ["owner_user_id", "id"]
    )

    op.create_table(
        "kt_game_sides",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("side", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("kill_team_id", sa.Uuid(), nullable=True),
        sa.Column("occupant_user_id", sa.Uuid(), nullable=True),
        sa.Column("roster_id", sa.Uuid(), nullable=True),
        sa.Column(
            "display_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True
        ),
        sa.Column("command_points", sa.Integer(), nullable=False),
        sa.Column("victory_points", _JSON, nullable=False),
        sa.Column("rules", _JSON, nullable=False),
        sa.Column("ploys", _JSON, nullable=False),
        sa.Column("ploys_used", _JSON, nullable=False),
        sa.Column("choices", _JSON, nullable=False),
        sa.CheckConstraint("command_points >= 0", name="ck_kt_game_side_command_points"),
        sa.CheckConstraint(
            "side IN ('player', 'opponent')", name="ck_kt_game_side_side"
        ),
        sa.ForeignKeyConstraint(["kill_team_id"], ["kt_kill_teams.id"]),
        sa.ForeignKeyConstraint(["occupant_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "game_id"],
            ["kt_games.owner_user_id", "kt_games.id"],
            name="fk_kt_game_side_game",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "side", name="uq_kt_game_side"),
        sa.UniqueConstraint(
            "owner_user_id",
            "game_id",
            "side",
            "kill_team_id",
            name="uq_kt_game_side_owner_team_id",
        ),
    )
    op.create_index(
        op.f("ix_kt_game_sides_game_id"), "kt_game_sides", ["game_id"], unique=False
    )
    op.create_index(
        op.f("ix_kt_game_sides_kill_team_id"),
        "kt_game_sides",
        ["kill_team_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_kt_game_sides_occupant_user_id"),
        "kt_game_sides",
        ["occupant_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_kt_game_sides_owner_user_id"),
        "kt_game_sides",
        ["owner_user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Reverses cleanly: the table holds no data this revision did not create."""
    op.drop_index(op.f("ix_kt_game_sides_owner_user_id"), table_name="kt_game_sides")
    op.drop_index(op.f("ix_kt_game_sides_occupant_user_id"), table_name="kt_game_sides")
    op.drop_index(op.f("ix_kt_game_sides_kill_team_id"), table_name="kt_game_sides")
    op.drop_index(op.f("ix_kt_game_sides_game_id"), table_name="kt_game_sides")
    op.drop_table("kt_game_sides")
    op.drop_constraint("uq_kt_game_owner_id", "kt_games", type_="unique")
