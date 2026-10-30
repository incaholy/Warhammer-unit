"""backfill side on the operatives and equipment that already exist

Revision ID: e4f7b1d8c635
Revises: d3e6a9c7b521
Create Date: 2026-10-29 00:30:00.000000

K8's fourth revision (ROADMAP K8, decision #67; the chain's steps are listed under
"The revision chain" there, which is the only place their count lives). Data only -- no
DDL, no model change, so `compare_metadata` is untouched by this revision.

Every row that already exists belongs to the creator's half, so every row becomes
`player`. That is not an assumption about the data, because the service could not have
produced anything else. `add_operative` refuses `source == 'roster'` with "a roster's
operatives arrive with the game", and refuses any operative whose `kill_team_id` differs
from the game's. So a game's operatives are all of the game's one team, which is the
creator's. `_SOURCES` gains `'opponent'` in #93, and #93 is not built.

This revision is also what makes two documents true. ROADMAP K8 and #77 both state that a
backfilled opponent side has "no operatives and no equipment", and both lean on it: it is
the reason a half-NULL composite leg on those rows is harmless rather than lucky. Nothing
established it until this revision, which gives every child row to the player and so
leaves the opponent rows empty.

Its own reverse is CLEAN. `side` stays nullable until the last revision, so writing NULL
back is a state the schema still permits.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f7b1d8c635"
down_revision: Union[str, Sequence[str], None] = "d3e6a9c7b521"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# `WHERE side IS NULL` makes the statement idempotent. Today no code writes `side`, so the
# guard matches every row and changes nothing about the result. It earns its place while
# this chain is unfinished: the later steps get written against a database that has already
# run this one, and a hand re-run must not overwrite a side somebody set.
#
# Equipment is the half that is CHECKED. `fk_kt_game_equipment_side` points
# `(game_id, side)` at `kt_game_sides`, and under MATCH SIMPLE that leg is skipped entirely
# while `side` is NULL -- so this UPDATE is the first thing to test it. It passes because
# the previous revision inserts a `player` row for EVERY game unconditionally. Had the
# player row been conditional the way the opponent row is, this statement would fail here,
# which is a better place to find out than a 500 on a read.
_OPERATIVES = "UPDATE kt_game_operatives SET side = 'player' WHERE side IS NULL"
_EQUIPMENT = "UPDATE kt_game_equipment SET side = 'player' WHERE side IS NULL"


def upgrade() -> None:
    """Give every existing child row to the player's side.

    `kt_game_operatives` carries no leg to `kt_game_sides` yet, so nothing validates its
    value here. Its ownership leg widens from three columns to four in the sixth revision,
    and that is the step this backfill exists to unblock: widening a leg over a column
    full of NULLs would make it unenforceable on arrival.

    `kt_game_events` is deliberately NOT touched. A NULL `side` on an event means "about
    the game" under #94, which is correct for most historical rows. The ones that are
    about a side need a `target_id` injected into their payload as well, and that is its
    own revision rather than a clause here.
    """
    op.execute(_OPERATIVES)
    op.execute(_EQUIPMENT)


def downgrade() -> None:
    """Return both columns to NULL.

    It nulls EVERY row rather than only the ones this revision set, for the same reason
    the previous revision deletes every side row: an update-for-every-row has no narrower
    reverse that is still correct. A row holding `'opponent'` would lose that value, and
    that loss belongs to reversing K8 rather than to this step -- the pre-K8 schema has no
    `side` column for an opponent's operative to live in, and the revision that adds the
    column is the one whose downgrade drops it.
    """
    op.execute("UPDATE kt_game_operatives SET side = NULL")
    op.execute("UPDATE kt_game_equipment SET side = NULL")
