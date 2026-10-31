"""drop the moved columns and make side NOT NULL

Revision ID: e1f4b7d2a936
Revises: d9e3a6c2f815
Create Date: 2026-10-29 01:30:00.000000

K8's tenth and last revision (ROADMAP K8, decisions #67, #77, #94 and #99; the chain's
steps are listed under "The revision chain" there, which is the only place their count
lives).

The CONTRACT step. Everything before it was additive or reversible; this one is where the
slice stops being undoable, which is why it is last and why it waited for the code to
stop reading what it removes.

What goes, and why each is safe to go now:

  the six moved columns   `command_points`, `victory_points`, `rules`, `ploys`,
                          `ploys_used` and `choices`. The service reads and writes them
                          on `kt_game_sides`, and both read models serve them from there.
  the opponent pair       `opponent_name` and `opponent_victory_points`. #77 backfilled
                          both onto an opponent side row, the read reshape removed them
                          from every schema, and #98 took `opponent_name` off creation.
  three CHECKs            `ck_kt_game_command_points`, `ck_kt_game_opponent_vp` and
                          `ck_kt_game_opponent_name`, each guarding a column that leaves.
                          The first was nearly missed: the slice's own notes named the
                          other two, and it was found by asking the model and the
                          database which constraints mention a departing column.

And `side` becomes NOT NULL on `kt_game_operatives` and `kt_game_equipment`, which is
what finally makes two constraints real rather than dormant. Under MATCH SIMPLE a NULL
anywhere in `fk_kt_game_operative_side` skips the whole check, and a NULL in
`uq_kt_game_equipment` makes it refuse nothing, because Postgres treats two NULLs as
distinct. Both gaps are asserted by tests that say they MUST be inverted here.

`kt_game_events.side` stays NULLABLE, deliberately and permanently. NULL is a VALUE there
(#94): it means the entry is about the game rather than a side.

#99 is what lets this revision land. A `game_updated` entry written before the redesign
can carry `opponent_name`, and after this `setattr` for it raises a bare `ValueError` --
not a `CodedError`, so an unexplained 500. `_undo_fields` now checks every payload field
against its target's columns first and refuses with a typed error instead.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f4b7d2a936"
down_revision: Union[str, Sequence[str], None] = "d9e3a6c2f815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHECKS = (
    "ck_kt_game_command_points",
    "ck_kt_game_opponent_vp",
    "ck_kt_game_opponent_name",
)
_MOVED = ("command_points", "victory_points", "rules", "ploys", "ploys_used", "choices")
_OPPONENT = ("opponent_name", "opponent_victory_points")


def upgrade() -> None:
    """Remove what moved, and close the two gaps the move left open."""
    for name in _CHECKS:
        op.drop_constraint(name, "kt_games", type_="check")
    for column in (*_MOVED, *_OPPONENT):
        op.drop_column("kt_games", column)

    # Last, so the drops above are not holding a lock while these rewrite two tables.
    for table in ("kt_game_operatives", "kt_game_equipment"):
        op.alter_column(table, "side", existing_type=sa.String(length=16), nullable=False)


def downgrade() -> None:
    """LOSSY BY CONSTRUCTION, which #77 says of this slice and means of this revision.

    The columns come back EMPTY. Their data lives on `kt_game_sides` and this reverse does
    not fetch it, deliberately: `kt_games` holds one row per game and `kt_game_sides` two,
    so there is no answer to "whose numbers go back" that is not a choice -- the player's,
    silently discarding the opponent's, or an error. A reverse that quietly picked one
    would look like it had worked.

    So this restores the SHAPE and not the data. An operator reversing K8 has the side
    rows until they reverse the revisions that created them, and can move what they want
    back with a statement that states its own choice.

    The three CHECKs return with the columns. `command_points` comes back NOT NULL with a
    server default of 0, which is how it was declared, so existing rows satisfy it.
    """
    for table in ("kt_game_operatives", "kt_game_equipment"):
        op.alter_column(table, "side", existing_type=sa.String(length=16), nullable=True)

    op.add_column("kt_games", sa.Column("command_points", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("kt_games", sa.Column("victory_points", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("kt_games", sa.Column("rules", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("kt_games", sa.Column("ploys", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("kt_games", sa.Column("ploys_used", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("kt_games", sa.Column("choices", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("kt_games", sa.Column("opponent_name", sa.String(length=128), nullable=True))
    op.add_column(
        "kt_games",
        sa.Column("opponent_victory_points", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_check_constraint("ck_kt_game_command_points", "kt_games", "command_points >= 0")
    op.create_check_constraint("ck_kt_game_opponent_vp", "kt_games", "opponent_victory_points >= 0")
    op.create_check_constraint(
        "ck_kt_game_opponent_name",
        "kt_games",
        "opponent_name IS NULL OR length(trim(opponent_name)) > 0",
    )
