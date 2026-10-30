"""backfill kt_game_sides from the games that already exist

Revision ID: c2d5f8b3a417
Revises: b1c4e7a2f309
Create Date: 2026-10-29 00:10:00.000000

K8's second revision (ROADMAP K8, decision #77; the chain's steps are listed under
"The revision chain" there, which is the only place their count lives). Data only -- no
DDL, no model change, so `compare_metadata` is untouched by this revision.

Its own reverse is CLEAN, which is worth stating because #77 calls K8's reverse
"lossy by construction". That is true of the LAST revision, which drops `opponent_name`
and `opponent_victory_points`. Here the source columns are all still on `kt_games`, so
deleting the rows loses nothing. One big migration would have made the whole slice
irreversible; a chain isolates the lossiness to its last step.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d5f8b3a417"
down_revision: Union[str, Sequence[str], None] = "b1c4e7a2f309"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# One `player` row per game, carrying the six columns the last revision will drop.
# `kill_team_id` and `roster_id` are copied rather than moved: #76 keeps `kt_games`'
# pair as the creator's, so both tables declare them with different meanings.
#
# `occupant_user_id` is the owner: whoever created a game was playing its player side.
# `display_name` stays
# NULL -- the game never named a side, #92's `PUT` is what sets one, and the team's name
# is already reachable through `kill_team_id`. `markers` is NOT copied; it stays shared
# on the game (#67). `created_at`/`updated_at` carry a server default.
_PLAYER_SIDES = """
INSERT INTO kt_game_sides (
    id, game_id, owner_user_id, side, kill_team_id, occupant_user_id, roster_id,
    display_name, command_points, victory_points, rules, ploys, ploys_used, choices
)
SELECT
    gen_random_uuid(), g.id, g.owner_user_id, 'player', g.kill_team_id, g.owner_user_id,
    g.roster_id, NULL, g.command_points, g.victory_points, g.rules, g.ploys,
    g.ploys_used, g.choices
FROM kt_games AS g
"""

# An `opponent` row ONLY where the game recorded something about one. `opponent_name` is
# nullable, but `opponent_victory_points` is NOT NULL DEFAULT 0 -- so "holds something"
# has to mean `> 0` for it, or every game would get a row and #77's "ONLY where" would
# mean nothing.
#
# `kill_team_id`, `occupant_user_id` and `roster_id` are NULL: a historical game never
# recorded the opponent's team, nobody held the side, and no roster was named. The four
# snapshot documents are empty because no snapshot of that team was ever taken, and
# `command_points` is 0 because the game had no opponent CP to carry.
#
# The score becomes `{"unrecorded": N}` (#77): the column is keyed by source and this
# game recorded none, so the honest key names the absence rather than claiming a real
# scoring source, which is #28's "numbers that looked authoritative". Where the opponent
# was named but scored nothing the document stays EMPTY -- the key exists to label a
# number, and there is no number to label.
_OPPONENT_SIDES = """
INSERT INTO kt_game_sides (
    id, game_id, owner_user_id, side, kill_team_id, occupant_user_id, roster_id,
    display_name, command_points, victory_points, rules, ploys, ploys_used, choices
)
SELECT
    gen_random_uuid(), g.id, g.owner_user_id, 'opponent', NULL, NULL, NULL,
    g.opponent_name, 0,
    CASE
        WHEN g.opponent_victory_points > 0
            THEN jsonb_build_object('unrecorded', g.opponent_victory_points)
        ELSE '{}'::jsonb
    END,
    '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb
FROM kt_games AS g
WHERE g.opponent_name IS NOT NULL OR g.opponent_victory_points > 0
"""


def upgrade() -> None:
    """Give every existing game its sides."""
    op.execute(_PLAYER_SIDES)
    op.execute(_OPPONENT_SIDES)


def downgrade() -> None:
    """Remove every side row.

    Clean at this point in the chain: `kt_games` still holds the columns these rows were
    built from, so nothing is unrecoverable. It deletes ALL rows rather than only the
    ones this revision inserted, because an insert-for-every-game has no narrower
    reverse -- and putting side data back onto `kt_games` is the LAST revision's problem,
    not this one's, since that is the revision which removed the columns to put it in.
    """
    op.execute("DELETE FROM kt_game_sides")
