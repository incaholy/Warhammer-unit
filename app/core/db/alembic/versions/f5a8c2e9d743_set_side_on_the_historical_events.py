"""set side on the event rows that already exist

Revision ID: f5a8c2e9d743
Revises: e4f7b1d8c635
Create Date: 2026-10-29 00:40:00.000000

K8's fifth revision (ROADMAP K8, decisions #94 and #96; the chain's steps are listed
under "The revision chain" there, which is the only place their count lives). Data only --
no DDL, no model change, so `compare_metadata` is untouched by this revision.

It sets SCOPE and nothing else. An earlier plan for this step also injected a
`target_id` into historical payloads so that `_event_target` would resolve a side row.
That is refused, in #94's own words: "`target_id` names the row that CHANGED, and a side
is the SCOPE an event belongs to". When one of these events was written the row that
changed WAS the game, so writing a side row's id into it claims a row changed that did
not exist, and collapses the one distinction #94 drew.

The repair `undo` actually needs is not a migration. `_undo_fields` resolves ONE target
and `setattr`s EVERY payload field onto it, while `_UPDATABLE`'s nine fields land in
three different places after #67: four on the player side, two on the opponent side, and
`status`, `initiative` and `markers` still on the game. One `PATCH` setting `status` and
`command_points` together produced ONE event spanning two of those, and no value of
`target_id` expresses it. `_undo_fields` MUST route per field instead, and that change
MUST land with the revision that drops the columns.

`rules` and `ploys` are in no settable set, so they never reached a payload. Four of the
six moved columns are at risk, not six, which is the four ROADMAP K8 names.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5a8c2e9d743"
down_revision: Union[str, Sequence[str], None] = "e4f7b1d8c635"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The eight types whose scope is the creator's half whatever their payload says. Listed
# rather than matched with `LIKE 'operative\_%'`, because a prefix would also claim a
# type added later, and a later type is exactly the one nobody checked.
#
# Matching on TYPE rather than probing the target row is deliberate, and the deleted-row
# case is why. `equipment_removed` carries the id of a row it had just deleted, so a
# probe of `kt_game_equipment` finds nothing and the event would be left unscoped --
# wrongly, since removing a piece of the player's equipment is plainly the player's half.
#
# The `EXISTS` guard is what keeps `fk_kt_game_event_side` satisfiable: that leg points
# `(game_id, side)` at `kt_game_sides` with no `ondelete`, so a side this game has no row
# for is REFUSED rather than ignored. The guard cannot fail for `player` today, because
# the second revision gives every game one -- it is here so that the statement states its
# own precondition rather than inheriting it from a revision three steps back.
_PLAYER_TYPES = """
UPDATE kt_game_events AS e
SET side = 'player'
WHERE e.side IS NULL
  AND e.type IN (
      'operative_added', 'operative_transformed', 'operative_updated',
      'operative_activated', 'equipment_added', 'equipment_revealed',
      'equipment_removed', 'ploy_used'
  )
  AND EXISTS (
      SELECT 1 FROM kt_game_sides AS s
      WHERE s.game_id = e.game_id AND s.side = 'player'
  )
"""

# `game_updated` is the one type whose scope depends on its payload, because it is the
# only one built from a caller-chosen subset of `_UPDATABLE`.
#
# The axis that creates ambiguity is player against OPPONENT, not player against game.
# `status`, `initiative` and `markers` stay on `kt_games` and belong to no side, so they
# do not compete for the column: a payload holding `command_points` and `status` is
# scoped `player`, because exactly one SIDE appears in it. A payload holding
# `command_points` and `opponent_name` names two sides and one column, so it stays NULL,
# which #94 already reads as "about the GAME" -- the honest description of a write that
# moved both halves at once. A payload holding only game-level keys stays NULL for the
# same reason it is true: no side appears in it.
#
# The alternative to NULL was splitting such a row in two. That renumbers `sequence`,
# invalidates `undone_by`, and turns one past action into two undos against a log with
# no redo (#58) -- a migration rewriting what a player did, to record it more tidily.
#
# `jsonb_exists_any` rather than the `?|` operator it implements: `?` is a parameter
# marker in some drivers, and a statement that depends on which driver reads it is a
# statement that works until the day it does not.
#
# The `opponent` branch carries its own `EXISTS`, and unlike the `player` one this guard
# CAN fire. The second revision creates an opponent row only where the game still records
# an opponent, so a game whose `opponent_name` was set and later cleared has an event
# naming that key and no row to point at. Such an event stays NULL.
_GAME_UPDATED = """
UPDATE kt_game_events AS e
SET side = CASE
    WHEN jsonb_exists_any(e.payload,
             ARRAY['command_points', 'victory_points', 'ploys_used', 'choices'])
     AND NOT jsonb_exists_any(e.payload,
             ARRAY['opponent_name', 'opponent_victory_points'])
     AND EXISTS (
         SELECT 1 FROM kt_game_sides AS s
         WHERE s.game_id = e.game_id AND s.side = 'player'
     )
        THEN 'player'
    WHEN jsonb_exists_any(e.payload,
             ARRAY['opponent_name', 'opponent_victory_points'])
     AND NOT jsonb_exists_any(e.payload,
             ARRAY['command_points', 'victory_points', 'ploys_used', 'choices'])
     AND EXISTS (
         SELECT 1 FROM kt_game_sides AS s
         WHERE s.game_id = e.game_id AND s.side = 'opponent'
     )
        THEN 'opponent'
    ELSE NULL
END
WHERE e.type = 'game_updated'
  AND e.side IS NULL
"""


def upgrade() -> None:
    """Scope every historical event that belongs to one half.

    Two types are left NULL on purpose, and neither is an omission.

    `advanced` moves `turning_point`, `phase`, `status` and `initiative`. All four stay
    on `kt_games` under #76 and #67, so the event is about the game and NULL says so.

    `undone` is unreachable by the code that would care. `_newest_standing_event` filters
    `type != UNDO_EVENT`, so `_undo_fields` is never called on one, and its `target_id`
    names an EVENT rather than a row -- nothing a side column would help resolve.
    Deriving its scope from the event it undoes is a further rule, and no decision has
    taken it.
    """
    op.execute(_PLAYER_TYPES)
    op.execute(_GAME_UPDATED)


def downgrade() -> None:
    """Unscope every event.

    Clean, and clean permanently rather than only at this point in the chain: `side` on
    `kt_game_events` is nullable by #94 and stays nullable, because NULL is a value there
    rather than a gap. The last revision sets NOT NULL on the two child tables, not on
    this one.
    """
    op.execute("UPDATE kt_game_events SET side = NULL")
