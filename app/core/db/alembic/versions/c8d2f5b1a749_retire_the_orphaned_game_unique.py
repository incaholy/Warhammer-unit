"""retire the game unique that step six orphaned

Revision ID: c8d2f5b1a749
Revises: b7c1e4a9f336
Create Date: 2026-10-29 01:10:00.000000

K8's eighth revision (ROADMAP K8, decisions #54, #67 and #76; the chain's steps are
listed under "The revision chain" there, which is the only place their count lives).

The contract half of the sixth revision, and nothing else. That revision moved
`KTGameOperative`'s ownership leg off `kt_games` and onto `kt_game_sides`, which left
`uq_kt_game_owner_team_id = UNIQUE(owner_user_id, kill_team_id, id)` with no dependents:
it existed solely to be that leg's provably-unique parent. Measured on `kt_games` after
that revision -- `kt_games_pkey` carries three dependents, `uq_kt_game_owner_id` one, and
this unique zero.

It is a separate revision from the one that orphaned it on purpose. A drop-and-add is one
operation and must not be split; a drop and the UNRELATED drop it enables are two, and
`ARCHITECTURE.md` section 4 wants the second one named so a failure says which. It also
keeps the sixth revision's reverse honest: while this revision is un-applied, that unique
is there for the narrowed leg to point at.

This retires the FIRST of #76's four reasons for keeping `kt_games.kill_team_id`. The
decision stands on the other three -- `fk_kt_game_roster` carries no `ondelete` so a
roster deletion is REFUSED (#60), `KTGame.kill_team` feeds `kill_team_name` and
`faction_name` in both read models, and `_new_operative` reads the column. #76 says this
in its own row, with the revision named, so a reader who finds reason one false does not
doubt the rest.

What this revision deliberately does NOT do, although the plan once had it here:

  - It does not drop the six moved columns or the opponent pair. The service reads them
    at 13 call sites across six methods, and four API schemas publish them.
  - It does not set `side` NOT NULL on the child tables. Nothing writes that column yet,
    and making it NOT NULL fails 95 tests across four files -- measured, not estimated.

Both wait for K8's code half, which is the point of expand-migrate-contract rather than a
delay. A column cannot be dropped before the code stops reading it, and cannot be made
NOT NULL before the code starts writing it. They become the ninth revision.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d2f5b1a749"
down_revision: Union[str, Sequence[str], None] = "b7c1e4a9f336"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "uq_kt_game_owner_team_id"
_TABLE = "kt_games"
_COLUMNS = ["owner_user_id", "kill_team_id", "id"]


def upgrade() -> None:
    """Drop the unique nothing points at."""
    op.drop_constraint(_NAME, _TABLE, type_="unique")


def downgrade() -> None:
    """Put it back.

    CLEAN, and the only revision in this chain whose reverse creates rather than destroys.
    A unique holds no data of its own, and the columns it covers are all still here --
    `kill_team_id` because #76 keeps it, `id` and `owner_user_id` because they are the
    game's identity.

    It can be REFUSED in one case, which is worth naming because the create is what
    validates it: `(owner_user_id, kill_team_id, id)` contains the primary key, so it is
    unique for as long as `id` is, and no data can collide. The refusal would come from
    the constraint already existing, which only happens if something re-added it by hand.
    """
    op.create_unique_constraint(_NAME, _TABLE, _COLUMNS)

