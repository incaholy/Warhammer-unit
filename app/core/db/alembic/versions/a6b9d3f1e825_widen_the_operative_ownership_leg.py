"""widen the operative ownership leg from the game to the side

Revision ID: a6b9d3f1e825
Revises: f5a8c2e9d743
Create Date: 2026-10-29 00:50:00.000000

K8's sixth revision (ROADMAP K8, decisions #54 and #67; the chain's steps are listed
under "The revision chain" there, which is the only place their count lives).

ONE constraint swap, which is ONE operation, plus the simple key that swap turns out to
require. `ARCHITECTURE.md` section 4 refuses splitting a drop from the add that replaces
it, because the window between them has no protection at all and `downgrade` can stop
inside it. The equipment unique is a SECOND swap on a SECOND table, so it is its own
revision rather than a second half of this one -- the same section's "a slice that
changes four tables is several revisions".

The simple `game_id` key is part of this operation rather than a tidy-up. The composite
leg WAS this table's only link to `kt_games`, and `KTGame.operatives` derived its join
condition from it, so moving the leg left the ORM with no foreign key between the two
tables and `NoForeignKeysError` on first use. Nothing in the DDL showed it:
`compare_metadata` agreed before and after, and the Postgres parity tier passed, because
the break is in the ORM mapping rather than the schema. The default tier caught it.
`kt_game_equipment` and `kt_game_events` already carry this key for the same reason.

What widens, and why three columns stopped being enough. The old leg said "this
operative belongs to a game its owner owns, and names a datacard of THAT GAME's kill
team". Once a game has two teams, "the game's team" is no longer a question with one
answer, and the leg would quietly keep validating the creator's team for both halves.
Four columns say "of THAT SIDE's team" instead, which is the guarantee #67 is for.

The parent is `uq_kt_game_side_owner_team_id` on `kt_game_sides`. The referenced columns
are written in the leg's own order rather than the constraint's; Postgres pairs them
positionally and accepts it, which was checked rather than assumed.

This is the table's only path to `kt_games`, since no simple `game_id` key sits beside
it, so a game deletion now cascades in two hops: game to side, side to operative. The
game's operatives still go, which was checked rather than assumed.

A side deletion reaching its operatives is new, and it is the weakest of the three
children's rules rather than a new hazard. `fk_kt_game_event_side` and
`fk_kt_game_equipment_side` carry no `ondelete`, so a side that an event or a piece of
equipment still names is REFUSED deletion outright -- #94's "deleting would be refused by
this key". This cascade fires only for a side nothing else references.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6b9d3f1e825"
down_revision: Union[str, Sequence[str], None] = "f5a8c2e9d743"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "fk_kt_game_operative_game"
_NEW = "fk_kt_game_operative_side"


def upgrade() -> None:
    """Swap the three-column leg for the four-column one.

    The add can only succeed because the fourth and fifth revisions ran first. Every
    operative needs a matching `(owner_user_id, kill_team_id, game_id, side)` row in
    `kt_game_sides`, and it has one: the second revision copied the game's
    `owner_user_id` and `kill_team_id` onto its `player` row, and the fourth set every
    operative's `side` to `'player'`. Had either been skipped this statement would fail
    rather than silently widen nothing.

    `side` is still nullable here, and that is the one gap this revision cannot close.
    Under MATCH SIMPLE the whole four-column check is skipped when `side` is NULL, so a
    row written by code that does not yet set it would be unprotected. Nothing writes
    `side` today, which is why the last revision sets NOT NULL -- and why it MUST land in
    the same deploy as the code that writes the column.
    """
    op.drop_constraint(_OLD, "kt_game_operatives", type_="foreignkey")
    op.create_foreign_key(
        _NEW,
        "kt_game_operatives",
        "kt_game_sides",
        ["owner_user_id", "kill_team_id", "game_id", "side"],
        ["owner_user_id", "kill_team_id", "game_id", "side"],
        ondelete="CASCADE",
    )
    # Unnamed, so Postgres names it `kt_game_operatives_game_id_fkey` -- matching the two
    # sibling tables, whose keys come from `Field(foreign_key=...)` the same way.
    op.create_foreign_key(
        None,
        "kt_game_operatives",
        "kt_games",
        ["game_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Narrow the leg back to the game.

    Clean: `kt_games` still carries `owner_user_id` and `kill_team_id`, and
    `uq_kt_game_owner_team_id` is still there to be pointed at -- the last revision is
    what drops that unique, and only because this revision is what stops using it.

    Unlike the equipment unique's reverse, this one cannot be REFUSED by existing data.
    Narrowing drops a column from the check rather than adding one, so every row that
    satisfied the four-column leg satisfies the three-column leg it came from.

    The simple key goes too, by its Postgres-assigned name. It has to: leaving it would
    make `compare_metadata` disagree with a model whose leg points back at `kt_games`.
    """
    op.drop_constraint("kt_game_operatives_game_id_fkey", "kt_game_operatives", type_="foreignkey")
    op.drop_constraint(_NEW, "kt_game_operatives", type_="foreignkey")
    op.create_foreign_key(
        _OLD,
        "kt_game_operatives",
        "kt_games",
        ["owner_user_id", "kill_team_id", "game_id"],
        ["owner_user_id", "kill_team_id", "id"],
        ondelete="CASCADE",
    )
