"""set null on the two user references K8 added

Revision ID: f2a5c8e1b473
Revises: e1f4b7d2a936
Create Date: 2026-10-29 01:40:00.000000

K8's eleventh revision (ROADMAP K8, decisions #70 and #74; the chain's steps are listed
under "The revision chain" there, which is the only place their count lives).

A FIX, not a step of the design. `kt_game_sides.occupant_user_id` (#74) and
`kt_game_events.actor_user_id` (#70) arrived in the third revision with no `ondelete`,
which is NO ACTION -- so each one REFUSED the deletion of a user it referenced, and
deleting a user had taken their games with them since K5:
`kt_games.owner_user_id` and `kt_rosters.owner_user_id` both carry ON DELETE CASCADE.
`test_deleting_a_user_takes_their_games_and_everything_on_them` is the invariant that
broke.

SET NULL on both, and neither is a compromise:

  `occupant_user_id`   the side SURVIVES, vacated. That is what NULL already means on
                       this column (#74), so the end state is one the schema and the
                       reads already describe. Deleting an account must not delete a
                       battle it was only a guest in -- which is the K9 case the column
                       exists for.
  `actor_user_id`      the log entry survives and still says WHO. #70 made
                       `actor_display_name` stored rather than joined precisely so a
                       later rename leaves old entries alone, and the same property
                       carries them past a deletion. Losing the id costs a reader
                       nothing; losing the entry would breach #8 and #58.

CASCADE was the wrong shape for both and is worth naming: it would delete a side, or a
log entry, because an account went away.

**Found by the Postgres parity tier, not by the default one.** The same deletion
SUCCEEDED against SQLite and was refused by Postgres, so every test passed on the tier
that runs by default. `ROADMAP` R6 exists for this: the two tiers disagree about
referential actions, and only one of them is what production runs.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a5c8e1b473"
down_revision: Union[str, Sequence[str], None] = "e1f4b7d2a936"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Postgres names an unnamed foreign key `<table>_<column>_fkey`, which is what the third
# revision left behind for both of these.
_LEGS = (
    ("kt_game_sides", "occupant_user_id", "kt_game_sides_occupant_user_id_fkey"),
    ("kt_game_events", "actor_user_id", "kt_game_events_actor_user_id_fkey"),
)


def upgrade() -> None:
    """Swap each NO ACTION leg for a SET NULL one."""
    for table, column, name in _LEGS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "users", [column], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    """Put the NO ACTION legs back.

    CLEAN in the sense that matters -- no data moves -- and it restores a defect, which is
    what a faithful reverse of a fix does. Reversing this makes deleting a user refused
    again wherever one of these columns names them.
    """
    for table, column, name in _LEGS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "users", [column], ["id"])
