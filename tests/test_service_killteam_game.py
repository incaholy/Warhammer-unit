"""Service tests for kill team games (`KTGameService`).

The ones that carry weight are the three mechanisms new to this codebase: the snapshot
(a game stops reading the catalog), the event log with `undo`, and `version` answering a
stale write. Everything else is bookkeeping, and the tests say which is which -- what is
REFUSED is bookkeeping, what is merely recorded is a rule the players apply (#1).
"""

import itertools
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, inspect, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import select

from app.core.db.columns import not_nullable_fields
from app.core.db.models_killteam import (
    KTGame,
    KTGameEquipment,
    KTGameEvent,
    KTGameOperative,
    KTGameSide,
)
from app.core.services.errors import ConflictError, NotFoundError
from app.core.services.service_killteam_game import (
    EQUIPMENT_LIMIT,
    LAST_TURNING_POINT,
    KTGameService,
    KTGameValidationError,
)

_nth = itertools.count(1)


def _service(session):
    return KTGameService(session)


@contextmanager
def _counting(session):
    """Count the statements a block issues, to pin a flat read."""
    counter = {"n": 0}

    def tick(*_a, **_k):
        counter["n"] += 1

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", tick)
    try:
        yield counter
    finally:
        event.remove(bind, "before_cursor_execute", tick)


@pytest.fixture
def battle(
    make_user,
    make_kill_team,
    make_kt_faction,
    make_kt_operative,
    make_kt_weapon,
    make_kt_ability,
    make_kill_team_rule,
    make_kt_ploy,
    make_kt_equipment,
    make_kt_roster,
    make_kt_roster_operative,
):
    """A player, a fully furnished kill team, and a roster of three operatives.

    Three and not one on purpose: a game's reads and its event log are collection work,
    and a fixture of one cannot fail a query-count or an ordering assertion.
    """

    def _make(operatives=3):
        # Named per call, because several of these tests need TWO battles and a faction
        # name is unique. A fixture that could only be used once would quietly force
        # every multi-game test to be a single-game one.
        n = next(_nth)
        team = make_kill_team(faction=make_kt_faction(name=f"Tyranids {n}"), name=f"Raveners {n}")
        make_kill_team_rule(kill_team=team, name="Burrow", position=0)
        make_kill_team_rule(kill_team=team, name="Tunnel", position=1)
        make_kt_ploy(kill_team=team, name="Predatory Instincts", position=0)
        universal = make_kt_ploy(kill_team=None, name=f"Command Re-roll {n}", position=0)
        kit = make_kt_equipment(kill_team=team, name="Grisly Trophy", position=0)
        universal_kit = make_kt_equipment(kill_team=None, name=f"Frag Grenade {n}", position=0)
        user = make_user()
        roster = make_kt_roster(owner=user, kill_team=team, name="Mine")
        cards = []
        # `index`, not `n` -- `n` is the per-call counter above, and reusing it here left
        # it holding the last loop value for anything added after this loop.
        for index in range(operatives):
            card = make_kt_operative(kill_team=team, name=f"Warrior {index}", wounds=12, position=index)
            make_kt_weapon(operative=card, name="Claws", position=0)
            make_kt_ability(operative=card, name="Pounce", position=0)
            # `position` is PASSED. Left to the column default every roster row sat at 0,
            # and `create_game` copies the roster row's position -- so every game
            # operative sat at 0 too and three properties could not be asserted at all:
            # `_next_position`'s increment, the position copy itself, and the
            # `(position, id)` tie-break on the game's operatives relationship.
            make_kt_roster_operative(roster=roster, operative=card, position=index)
            cards.append(card)
        return {
            "user": user,
            "team": team,
            "roster": roster,
            "cards": cards,
            "kit": kit,
            "universal_kit": universal_kit,
            "universal_ploy": universal,
        }

    return _make


# --- the snapshot: a game stops reading the catalog -------------------------


def test_a_game_copies_every_datacard_it_will_play_with(session, battle):
    """Decision #22: the whole card, not the stat line.

    The point of the copy is that nothing reads the catalog afterwards, so this asserts
    the PROFILES came across and not just the numbers -- a snapshot missing its weapons
    would still look right on a stat line and be useless at the table.
    """
    b = battle()
    game = _service(session).create_game(b["user"].id, b["roster"].id, opponent_name="Bob")

    assert len(game.operatives) == 3
    for row in game.operatives:
        assert row.weapons and row.weapons[0]["name"] == "Claws"
        assert row.abilities and row.abilities[0]["name"] == "Pounce"
        assert row.weapons[0]["weapon_rules"] == []
        assert row.current_wounds == row.wounds == 12
        assert row.source == "roster"
        assert row.added_in_turning_point is None


def test_a_new_game_arrives_with_its_player_side_and_no_opponent(session, battle):
    """Decision #97, and the half of #74 the rule was not written about.

    #74 says a side row is created "once that side's kill team is known, not at game
    creation". The player's team IS known at creation, from the roster, so its row comes
    with the game; the opponent's does not, so no opponent row exists yet even when the
    caller named one.
    """
    b = battle()
    game = _service(session).create_game(b["user"].id, b["roster"].id, opponent_name="Bob")

    sides = session.exec(select(KTGameSide).where(KTGameSide.game_id == game.id)).all()
    assert [row.side for row in sides] == ["player"]

    player = sides[0]
    assert player.owner_user_id == game.owner_user_id
    assert player.occupant_user_id == game.owner_user_id
    assert player.kill_team_id == game.kill_team_id
    assert player.roster_id == game.roster_id
    assert player.display_name is None


def test_the_player_sides_snapshots_agree_with_the_games_by_construction(session, battle):
    """Copied from the game rather than rebuilt, so the two cannot disagree at creation.

    Building each from the catalog twice would make them equal only for as long as both
    code paths stayed in step, which is the kind of agreement that quietly stops holding.
    """
    b = battle()
    game = _service(session).create_game(b["user"].id, b["roster"].id)
    player = session.exec(select(KTGameSide).where(KTGameSide.game_id == game.id)).one()

    assert player.rules == game.rules
    assert player.ploys == game.ploys
    assert player.command_points == game.command_points
    assert player.victory_points == game.victory_points
    assert player.ploys_used == game.ploys_used
    assert player.choices == game.choices


def test_a_new_games_operative_can_now_be_sided(session, battle):
    """The capability #97 exists for, and the one that was REFUSED without it.

    `fk_kt_game_operative_side` needs a matching side row, so before #97 a freshly created
    game had none and setting `side = 'player'` raised a foreign key error. #77's backfill
    gave every game that already existed one, so leaving creation alone would have made
    pre-K8 games work and post-K8 games fail.
    """
    b = battle()
    game = _service(session).create_game(b["user"].id, b["roster"].id)

    row = game.operatives[0]
    row.side = "player"
    session.add(row)
    session.commit()

    assert session.get(KTGameOperative, row.id).side == "player"


def test_a_new_games_operative_cannot_name_the_absent_opponent_side(session, battle):
    """The other half: the leg still refuses a side the game does not have.

    No opponent row exists yet, so `'opponent'` has nothing to point at. This is what
    makes #97 a narrowing of what is representable rather than a blanket permission.
    """
    b = battle()
    game = _service(session).create_game(b["user"].id, b["roster"].id)

    row = game.operatives[0]
    row.side = "opponent"
    session.add(row)
    with pytest.raises(IntegrityError, match="(?i)foreign key"):
        session.commit()


def test_every_child_row_a_game_creates_names_its_side(session, battle):
    """Decision #67: an operative and a piece of equipment both belong to a SIDE.

    Every row the service creates is the creator's. `add_operative` refuses
    `source='roster'` and refuses a datacard of another team, and #93's `'opponent'`
    source is not built, so `'player'` is a statement about what the code can produce
    rather than a default.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    assert {row.side for row in game.operatives} == {"player"}

    granted = svc.add_operative(game.id, b["cards"][0].id, source="rule")
    assert granted.side == "player"

    piece = svc.add_equipment(game.id, b["kit"].id)
    assert piece.side == "player"


def _scopes(session, game_id):
    log = session.exec(
        select(KTGameEvent).where(KTGameEvent.game_id == game_id).order_by(KTGameEvent.sequence)
    ).all()
    return [(row.type, row.side) for row in log]


def test_an_event_about_a_row_takes_that_rows_side(session, battle):
    """A write aimed at a row passes the row's own side rather than inferring one (#94)."""
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]

    svc.update_operative(game.id, row.id, None, current_wounds=5)
    svc.activate_operative(game.id, row.id)
    svc.add_equipment(game.id, b["kit"].id)

    assert _scopes(session, game.id) == [
        ("operative_updated", "player"),
        ("operative_activated", "player"),
        ("equipment_added", "player"),
    ]


def test_a_ploy_is_the_players_and_the_clock_is_the_games(session, battle):
    """Two derivations, and they must differ.

    `use_ploy` moves `command_points` and `ploys_used`, both the player's after #67, so
    the event is the player's. `advance` moves `turning_point`, `phase`, `status` and
    `initiative`, which stay on `kt_games` (#76), so it belongs to no side and NULL says
    so. Both match what the historical backfill wrote, so one log reads one way.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, None, command_points=5, status="in_progress")
    svc.use_ploy(game.id, game.ploys[0]["name"])
    svc.advance(game.id)

    assert _scopes(session, game.id)[-2:] == [("ploy_used", "player"), ("advanced", None)]


def test_a_write_naming_the_absent_opponent_scopes_to_the_game(session, battle):
    """The guard that keeps a SHIPPED operation working.

    `opponent_victory_points` is writable today on a game with no opponent side, since
    #97 creates only the player's. Scoping the event `'opponent'` would make
    `fk_kt_game_event_side` refuse it and break the write. NULL is also the honest answer:
    that column is still on `kt_games` until the last revision, so the write really is
    about the game. The backfill revision carries the same guard.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    svc.update_game(game.id, None, opponent_victory_points=3)
    svc.update_game(game.id, None, command_points=7)
    svc.update_game(game.id, None, status="in_progress")

    assert _scopes(session, game.id) == [
        ("game_updated", None),
        ("game_updated", "player"),
        ("game_updated", None),
    ]


def test_undoing_a_removal_puts_the_side_back(session, battle):
    """`_undo_deleted` rebuilds the row from the event, so the event must carry its side.

    Without it undo recreated an UNSIDED piece: harmless-looking while `side` is nullable,
    a void `uq_kt_game_equipment` in the meantime, and a NOT NULL failure once the last
    revision lands.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    piece = svc.add_equipment(game.id, b["kit"].id)
    piece_id = piece.id

    svc.remove_equipment(game.id, piece_id)
    assert session.get(KTGameEquipment, piece_id) is None

    svc.undo(game.id)
    restored = session.get(KTGameEquipment, piece_id)
    assert restored is not None
    assert restored.side == "player"


def test_an_event_names_who_wrote_it(session, battle):
    """Decision #70: with two writers the log could not say who did anything.

    The display name is STORED rather than joined, because no route resolves a user id to
    a name -- `GET /me` is self-only and `PATCH /users/{user_id}` is admin-gated. Storing
    it also means a later rename leaves old entries reading as they did when written.
    """
    b = battle()
    svc = KTGameService(session, actor=b["user"])
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, None, command_points=4)

    entry = session.exec(
        select(KTGameEvent).where(KTGameEvent.game_id == game.id).order_by(KTGameEvent.sequence.desc())
    ).first()
    assert entry.actor_user_id == b["user"].id
    #: The `username`, which #70 calls an interim: `users` holds no display name until DM2.
    assert entry.actor_display_name == b["user"].username


def test_a_service_with_no_actor_still_writes_the_log(session, battle):
    """Both actor columns are nullable, and this is the case that justifies the default.

    A seed or a console has no caller. #70 makes the columns nullable for entries written
    before they existed; the constructor's default is for a writer with no request behind
    it, NOT for a route that forgot to pass one --
    `test_every_event_a_route_writes_names_its_actor` is what holds that shut.
    """
    b = battle()
    svc = KTGameService(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, None, command_points=4)

    entry = session.exec(
        select(KTGameEvent).where(KTGameEvent.game_id == game.id).order_by(KTGameEvent.sequence.desc())
    ).first()
    assert entry.actor_user_id is None
    assert entry.actor_display_name is None


def test_the_sides_snapshot_is_what_a_ploy_is_checked_against(session, battle):
    """Decision #67: `ploys` belongs to the side, so the side is what `use_ploy` reads.

    The two rows hold the SAME list today, since #97 copies it at creation, so a test
    that merely read one would pass whichever it read. This makes them differ: a name
    only the side lists is spendable, and a name only the stale game column lists is not.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, None, command_points=5)
    player = svc.player_side(game)

    player.ploys = [{"name": "Side Only", "cp_cost": 1, "universal": False}]
    game.ploys = [{"name": "Game Only", "cp_cost": 1, "universal": False}]
    session.add_all([player, game])
    session.commit()

    svc.use_ploy(game.id, "Side Only")
    # `ploys_used` and `command_points` are still written to the GAME; they move onto the
    # side with the next change. What this asserts is which snapshot was CONSULTED.
    assert [entry["name"] for entry in _player(session, game).ploys_used] == ["Side Only"]
    assert _player(session, game).command_points == 4

    with pytest.raises(KTGameValidationError, match="not one of this game's ploys"):
        svc.use_ploy(game.id, "Game Only")


def test_ploys_used_is_validated_against_the_sides_snapshot(session, battle):
    """The same authority question for `_validate_ploys_used` (#23's counterpart).

    It takes the snapshot rather than the game now, because after #67 the list belongs to
    a side and the caller is what knows which one.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    player = svc.player_side(game)

    player.ploys = [{"name": "Side Only", "cp_cost": 0, "universal": False}]
    game.ploys = [{"name": "Game Only", "cp_cost": 0, "universal": False}]
    session.add_all([player, game])
    session.commit()

    svc.update_game(game.id, None, ploys_used=[{"name": "Side Only", "turning_point": 1}])

    with pytest.raises(KTGameValidationError, match="not one of this game's ploys"):
        svc.update_game(game.id, None, ploys_used=[{"name": "Game Only", "turning_point": 1}])


def test_a_game_with_no_player_side_is_a_broken_invariant_not_a_404(session, battle):
    """#97 makes this unreachable, so it raises `LookupError` rather than `NotFoundError`.

    A `NotFoundError` would answer 404 for a game that plainly exists, which sends a
    client looking for the wrong fault. `LookupError` is not a `CodedError`, so it reaches
    the handler as a 500 -- correct for an invariant that cannot be violated by input.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    game_id = game.id
    session.delete(svc.player_side(game))
    session.commit()

    with pytest.raises(LookupError, match="no player side"):
        svc.player_side(svc.get_game(game_id))


def test_one_patch_spanning_two_rows_lands_on_both_and_undoes_from_both(session, battle):
    """Decision #96, which is the whole reason the routing exists.

    `_UPDATABLE`'s fields no longer live on one row. One `PATCH` setting `status` and
    `command_points` is ONE event spanning the game and its player side, and the old
    shape -- resolve one target, `setattr` every field onto it -- would call `setattr` for
    a column the target does not have. That raises a bare `ValueError`, which is not a
    `CodedError`, so the player gets an unexplained 500.

    The event payload stays FLAT and keyed by field name, exactly as a single-row write
    produced, so the log reads the same before and after the move.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    svc.update_game(game.id, None, status="in_progress", command_points=6)

    assert game.status == "in_progress", "the game's own field stayed on the game"
    assert svc.player_side(game).command_points == 6, "the moved field went to the side"

    entry = session.exec(
        select(KTGameEvent).where(KTGameEvent.game_id == game.id).order_by(KTGameEvent.sequence.desc())
    ).first()
    assert set(entry.payload) == {"op", "status", "command_points"}

    svc.undo(game.id)
    assert game.status == "setup", "the game's field came back to the game"
    assert svc.player_side(game).command_points == 0, "the side's field came back to the side"


def test_the_moved_fields_not_null_rule_survives_losing_the_game_columns(session, battle):
    """`_NOT_NULLABLE` is derived from BOTH tables, and this asserts the half that lasts.

    `not_nullable_fields` SKIPS a name that is not a column on the model it is given. The
    four moved fields are still columns on `KTGame` today, so a derivation from the game
    alone covers them and the union looks redundant -- reverting it passes every other
    test in this suite. It stops being redundant at the last revision, when those columns
    go and the game half starts returning three names instead of seven.

    So this asserts the SIDE half directly, which is the half that survives the drop: a
    null in any of the four is refused by this service rather than reaching the database's
    NOT NULL as an unexplained 500.
    """
    moved = {"command_points", "victory_points", "ploys_used", "choices"}
    assert moved <= not_nullable_fields(KTGameSide, KTGameService._UPDATABLE)
    assert moved <= KTGameService._NOT_NULLABLE

    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    for field in sorted(moved):
        with pytest.raises(KTGameValidationError):
            svc.update_game(game.id, None, **{field: None})


def test_an_event_naming_a_side_resolves_that_side_rather_than_404ing(session, battle):
    """Decision #94: `_event_target` must resolve a side row when an event names one.

    No operation writes such an event yet -- the side-scoped ones arrive with #92's `PUT`
    and `PATCH` -- so this builds the payload directly. That is the point: the probe has
    to work before the first caller depends on it, and #94 names the failure it prevents.
    Without `KTGameSide` in the probe the other two miss and the code raises "points at a
    row that is gone", which is a 404 for a row that plainly exists.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    player = svc.player_side(game)

    event = KTGameEvent(
        game_id=game.id,
        sequence=999,
        type="side_updated",
        turning_point=game.turning_point,
        side="player",
        payload={"op": "fields", "target_id": str(player.id), "command_points": {"before": 0, "after": 3}},
    )

    assert svc._event_target(game, event) is player


def test_undoing_a_side_scoped_event_writes_back_onto_the_side(session, battle):
    """The probe's payoff: `_undo_fields` takes the row branch, not the routing branch.

    An event that NAMES a row is about that row, so `_owner_of` is not consulted for it --
    which matters because `command_points` would route to the player side anyway here, and
    an operative's `current_wounds` would not. The branch is what keeps those separate.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    player = svc.player_side(game)
    player.command_points = 3
    session.add(player)
    session.commit()

    event = KTGameEvent(
        game_id=game.id,
        sequence=999,
        type="side_updated",
        turning_point=game.turning_point,
        side="player",
        payload={"op": "fields", "target_id": str(player.id), "command_points": {"before": 0, "after": 3}},
    )
    session.add(event)
    session.commit()

    svc._undo_fields(game, event)
    session.commit()

    assert svc.player_side(game).command_points == 0


def test_a_side_can_be_established_by_hand_for_an_opponent_with_no_account(session, battle, make_kill_team):
    """Decision #92: the operation K8 was missing.

    #67's reason to exist is "an opponent with no account whose side is filled in by
    hand", and the slice moved `opponent_name` off the game while naming no operation that
    writes it. `occupant_user_id` and `roster_id` stay NULL -- no account holds a
    hand-described side (#74), and the opponent's roster is provenance only a join can
    supply (#69, #86).
    """
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)

    row = svc._side_row(game.id, "opponent")
    assert row.display_name == "Dave"
    assert row.kill_team_id == theirs.id
    assert row.occupant_user_id is None
    assert row.roster_id is None
    # Setting a team snapshots its reference, so #69's "the snapshot is the authority"
    # holds for a hand-described side and not only a joined one. The universal ploys come
    # with it, exactly as they do at creation.
    assert [p["universal"] for p in row.ploys] == [True], "the universal list came with it"
    assert all(p["name"].startswith("Command Re-roll") for p in row.ploys)
    assert row.rules == [], "this team has no rules of its own in the fixture"


def test_naming_your_own_side_needs_no_team(session, battle):
    """#77 leaves the player row's `display_name` NULL and says this `PUT` sets it.

    Which is why `kill_team_id` may be omitted: #76 fixes the creator's team at creation,
    so a rename has no value to echo back from the read.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    assert svc.player_side(game).display_name is None

    svc.establish_side(game.id, "player", display_name="My Ravagers")

    row = svc.player_side(game)
    assert row.display_name == "My Ravagers"
    assert row.kill_team_id == game.kill_team_id, "the team was not touched"


def test_a_sides_team_is_settable_once_and_never_changed(session, battle, make_kill_team):
    """#76's immutability as a stated refusal rather than a constraint's accident.

    An established side's team is what its operatives and equipment are keyed to through
    the four-column leg, so changing it would be refused by the database once that side
    holds a row. Refusing it here means a 409 naming the field instead of whatever the
    constraint happens to raise -- and it refuses even for a side with no rows yet, which
    is this game's opponent.
    """
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    someone_else = make_kill_team(faction=b["team"].faction, name="Blooded")
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)

    # The SAME team again is not a change, so it is accepted and renames the side.
    svc.establish_side(game.id, "opponent", display_name="Dave Again", kill_team_id=theirs.id)
    assert svc._side_row(game.id, "opponent").display_name == "Dave Again"

    # A DIFFERENT team is refused, and the refusal names the field.
    with pytest.raises(ConflictError, match="already fields kill team"):
        svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=someone_else.id)
    assert svc._side_row(game.id, "opponent").kill_team_id == theirs.id


def test_establishing_a_side_is_undone_as_fields_and_leaves_the_row(session, battle, make_kill_team):
    """#92: replace is an UPDATE, so undo must not delete the row either.

    #94 points events at this row. `_undo_created` deletes its target, so an establishment
    written as `op: 'created'` would make undo destroy a row the log references. It is
    written as `op: 'fields'`, so undo restores both columns to NULL -- which is #77's own
    backfilled shape -- and the row stays.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)
    row_id = svc._side_row(game.id, "opponent").id

    entry = session.exec(
        select(KTGameEvent).where(KTGameEvent.game_id == game.id).order_by(KTGameEvent.sequence.desc())
    ).first()
    assert entry.type == "side_described"
    assert entry.payload["op"] == "fields"
    assert entry.side == "opponent"
    assert entry.payload["target_id"] == str(row_id)

    svc.undo(game.id)

    survivor = session.get(KTGameSide, row_id)
    assert survivor is not None, "undo must not delete a row the log points at"
    assert survivor.display_name is None
    assert survivor.kill_team_id is None


def test_an_unknown_side_name_is_refused_by_the_vocabulary(session, battle):
    """One vocabulary for sides and `initiative` (#68), so this is the same set."""
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="must be one of"):
        svc.establish_side(game.id, "banana", display_name="x")


def test_a_withdrawn_kill_team_cannot_newly_field_a_side(session, battle, make_kill_team):
    """The FIFTH write path to need this (#55).

    A withdrawn team stays resolvable for the games and rosters that already name it --
    that is the point of flagging rather than deleting -- but it cannot be newly taken up.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    gone = make_kill_team(faction=b["team"].faction, name="Retired", withdrawn=True)

    with pytest.raises(NotFoundError, match="no longer in the catalog"):
        svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=gone.id)


def test_a_host_can_field_the_opponents_models_by_hand(session, battle, make_kill_team, make_kt_operative):
    """Decision #93, and the inversion of the test that held its window open.

    The capability #67 exists for: #92 lets a host describe an opponent with no account,
    and this lets them field that opponent's models. Both of `add_operative`'s guards
    refused it before -- `'roster'` outright, and a team check against the GAME's
    `kill_team_id`, which rejected every datacard belonging to the other team.
    """
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    card = make_kt_operative(kill_team=theirs, name="Theyn", wounds=9)
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)

    row = svc.add_operative(game.id, card.id, source="opponent", side="opponent")

    assert (row.side, row.source) == ("opponent", "opponent")
    assert row.kill_team_id == theirs.id, "the SIDE's team, not the game's"
    assert row.owner_user_id == game.owner_user_id, "#74: the creator on both halves"


def test_a_datacard_must_belong_to_the_side_it_is_added_to(
    session, battle, make_kill_team, make_kt_operative
):
    """The team check moved to the side, so it now cuts BOTH ways (#93).

    Your own team's datacard on the opponent's half is refused, which the old check could
    not express -- it compared against the game's team, so it accepted exactly the wrong
    one of these two and rejected the other.
    """
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    their_card = make_kt_operative(kill_team=theirs, name="Theyn", wounds=9)
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)

    with pytest.raises(NotFoundError, match="is not one of kill team"):
        svc.add_operative(game.id, b["cards"][0].id, source="opponent", side="opponent")
    with pytest.raises(NotFoundError, match="is not one of kill team"):
        svc.add_operative(game.id, their_card.id, source="rule", side="player")


def test_an_operative_needs_a_side_that_exists_and_fields_a_team(
    session, battle, make_kill_team, make_kt_operative
):
    """Both preconditions of the four-column leg, reported rather than left to the leg.

    Under MATCH SIMPLE a NULL anywhere in that leg skips the whole check, so a side with
    no team would take the row SILENTLY and leave it unprotected -- which is why the
    second refusal is here rather than trusted to the database.
    """
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    card = make_kt_operative(kill_team=theirs, name="Theyn", wounds=9)
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="no opponent side yet"):
        svc.add_operative(game.id, card.id, source="opponent", side="opponent")

    # #77's backfilled shape: a row with a name and no team.
    session.add(
        KTGameSide(game_id=game.id, owner_user_id=game.owner_user_id, side="opponent", display_name="Dave")
    )
    session.commit()

    with pytest.raises(KTGameValidationError, match="has no kill team yet"):
        svc.add_operative(game.id, card.id, source="opponent", side="opponent")


def test_an_unknown_side_is_refused_when_adding_an_operative(session, battle):
    """Same vocabulary as everywhere else (#68)."""
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="must be one of"):
        svc.add_operative(game.id, b["cards"][0].id, source="rule", side="banana")


def test_either_side_can_be_scored_and_neither_is_the_callers_own(session, battle, make_kill_team):
    """Decision #87: either participant may record anything on either side.

    Nothing compares the caller to the side, which is the point -- one person holding one
    phone is the ordinary case at a table, and the opponent asking you to tick their score
    is not an edge case. The actor is on the event, so the write is visible rather than
    silent (#70).
    """
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)

    svc.update_side(game.id, "player", None, command_points=4, victory_points={"crit_op": 3})
    svc.update_side(game.id, "opponent", None, command_points=2, victory_points={"crit_op": 5})

    mine = svc.player_side(game)
    theirs_row = svc._side_row(game.id, "opponent")
    assert (mine.command_points, mine.victory_points) == (4, {"crit_op": 3})
    assert (theirs_row.command_points, theirs_row.victory_points) == (2, {"crit_op": 5})


def test_a_sides_spent_ploys_are_checked_against_that_sides_snapshot(
    session, battle, make_kill_team, make_kt_ploy
):
    """Which is why establishment snapshots one (#92, as amended).

    #87 lets either participant record PLOYS on either side, and the opponent's spent
    ploys have to be the OPPONENT's ploys -- so a name from your own team's list is
    refused on their half. Without a snapshot at establishment their list would be empty
    and every entry refused, which is the gap that amendment closes.
    """
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    make_kt_ploy(kill_team=theirs, name="Bolstered Resolve", position=0)
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)

    svc.update_side(game.id, "opponent", None, ploys_used=[{"name": "Bolstered Resolve", "turning_point": 1}])
    assert svc._side_row(game.id, "opponent").ploys_used[0]["name"] == "Bolstered Resolve"

    mine = svc.player_side(game).ploys[0]["name"]
    with pytest.raises(KTGameValidationError, match="not one of this game's ploys"):
        svc.update_side(game.id, "opponent", None, ploys_used=[{"name": mine, "turning_point": 1}])


def test_a_side_patch_refuses_what_it_does_not_own(session, battle):
    """`_SIDE_UPDATABLE` is the four fields #67 moved, and nothing else.

    `status`, `initiative` and `markers` stay the game's (#76, #67), so this path cannot
    set them -- a side has no such columns to set.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="cannot update"):
        svc.update_side(game.id, "player", None, status="finished")
    with pytest.raises(KTGameValidationError, match="cannot update"):
        svc.update_side(game.id, "player", None, markers=["a"])


def test_a_side_patch_takes_the_games_version_lock(session, battle):
    """`version` is the GAME's (#9): one battle is one optimistic-locking unit.

    A per-side version would let one tab overwrite the other's view of the board while
    both believed they were current.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    stale = game.version

    svc.update_side(game.id, "player", None, command_points=3)

    with pytest.raises(ConflictError, match="has moved on"):
        svc.update_side(game.id, "player", stale, command_points=9)
    assert svc.player_side(game).command_points == 3


def test_a_side_patch_needs_the_side_to_exist(session, battle):
    """An unestablished opponent cannot be scored, and says so rather than 404ing."""
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="no opponent side yet"):
        svc.update_side(game.id, "opponent", None, command_points=1)


def test_a_sides_ploy_is_paid_for_out_of_that_sides_own_pool(session, battle, make_kill_team, make_kt_ploy):
    """Decisions #65 and #67: one operation against ONE CP pool, and there are now two.

    The defect this guards is a routing one rather than an arithmetic one. `_apply_routed`
    sends `PLAYER_FIELDS` to the PLAYER's side, which is right for a write that named no
    side and wrong for one that did -- it would spend the creator's CP to pay for the
    opponent's ploy and leave the opponent's pool untouched. So the two pools are given
    DIFFERENT starting values here: a test with both on the same number would pass either
    way.
    """
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    make_kt_ploy(kill_team=theirs, name="Bolstered Resolve", cp_cost=1, position=0)
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)
    svc.update_side(game.id, "player", None, command_points=7)
    svc.update_side(game.id, "opponent", None, command_points=4)

    svc.use_ploy(game.id, "Bolstered Resolve", "opponent")

    assert svc._side_row(game.id, "opponent").command_points == 3, "theirs paid"
    assert svc.player_side(game).command_points == 7, "the creator's pool is untouched"
    assert [e["name"] for e in svc._side_row(game.id, "opponent").ploys_used] == ["Bolstered Resolve"]
    assert svc.player_side(game).ploys_used == []


def test_undoing_a_sides_ploy_restores_that_sides_cp_and_entry(session, battle, make_kill_team, make_kt_ploy):
    """#65's "one event, one undo", still true once the event names a row.

    The event carries that side's `target_id`, so `_undo_fields` takes its ROW branch and
    `_owner_of` is never consulted -- which is what keeps the reversal on the side that
    paid rather than on the creator's.
    """
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    make_kt_ploy(kill_team=theirs, name="Bolstered Resolve", cp_cost=1, position=0)
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)
    svc.update_side(game.id, "opponent", None, command_points=4)
    svc.use_ploy(game.id, "Bolstered Resolve", "opponent")

    svc.undo(game.id)

    row = svc._side_row(game.id, "opponent")
    assert row.command_points == 4
    assert row.ploys_used == []


def test_a_ploy_must_be_in_the_snapshot_of_the_side_spending_it(
    session, battle, make_kill_team, make_kt_ploy
):
    """The snapshot is checked per side, so a name from the wrong list is refused."""
    b = battle()
    theirs = make_kill_team(faction=b["team"].faction, name="Hearthkyn Salvagers")
    make_kt_ploy(kill_team=theirs, name="Bolstered Resolve", cp_cost=0, position=0)
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.establish_side(game.id, "opponent", display_name="Dave", kill_team_id=theirs.id)
    mine = next(p["name"] for p in svc.player_side(game).ploys if not p["universal"])

    with pytest.raises(KTGameValidationError, match="not one of this game's ploys"):
        svc.use_ploy(game.id, mine, "opponent")
    with pytest.raises(KTGameValidationError, match="not one of this game's ploys"):
        svc.use_ploy(game.id, "Bolstered Resolve", "player")


def test_a_ploy_cannot_be_spent_for_a_side_that_does_not_exist(session, battle):
    """An unestablished opponent has no pool to spend from, and no snapshot to check."""
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="no opponent side yet"):
        svc.use_ploy(game.id, "anything", "opponent")
    with pytest.raises(KTGameValidationError, match="must be one of"):
        svc.use_ploy(game.id, "anything", "banana")


def test_a_game_copies_the_teams_reference_including_the_universal_ploys(session, battle):
    # Decision #24: with the datacards, this is what lets a battle need no catalog call.
    b = battle()
    game = _service(session).create_game(b["user"].id, b["roster"].id)

    assert [rule["name"] for rule in game.rules] == ["Burrow", "Tunnel"]
    assert len(game.ploys) == 2
    # Flagged rather than merged, so a screen can group "the team's" and "everyone's".
    assert [p["name"] for p in game.ploys if p["universal"]][0].startswith("Command Re-roll")


def test_a_games_snapshot_does_not_follow_the_catalog(session, battle, make_kt_operative):
    """The consequence worth having a test for: a re-scrape cannot reach a battle.

    The catalog row is edited after the game exists, the way `make seed-kt` would rewrite
    it, and the game's copy is unmoved.
    """
    b = battle()
    game = _service(session).create_game(b["user"].id, b["roster"].id)
    card = b["cards"][0]

    card.wounds = 99
    card.name = "Rebalanced"
    session.add(card)
    session.commit()

    row = next(r for r in _service(session).get_game(game.id).operatives if r.operative_id == card.id)
    assert (row.name, row.wounds) == ("Warrior 0", 12)


def test_an_in_battle_datacard_is_not_copied_at_creation(session, battle, make_kt_operative):
    # Decision #20: a roster cannot hold one, so a game does not start with one either.
    # The ones a rule or a piece of equipment grants arrive through `add_operative`.
    b = battle()
    make_kt_operative(kill_team=b["team"], name="Cursemite", availability="in_battle", position=9)

    game = _service(session).create_game(b["user"].id, b["roster"].id)

    assert "Cursemite" not in {row.name for row in game.operatives}


def test_creating_a_game_needs_a_user_and_a_roster_that_exist(session, battle):
    b = battle()
    svc = _service(session)

    with pytest.raises(NotFoundError, match="user"):
        svc.create_game(uuid.uuid4(), b["roster"].id)
    with pytest.raises(NotFoundError, match="roster"):
        svc.create_game(b["user"].id, uuid.uuid4())


def _player(session, game):
    """The game's player side, which is where the four mutable numbers live after #67.

    Tests read through this rather than off the game, because `kt_games` keeps those
    columns until the last revision and they go STALE from the moment the writes move. An
    assertion against the game would pass today, for the wrong reason, and start failing
    at the drop.
    """
    return KTGameService(session).player_side(game)


def _touch_whole_game(game):
    """Walk everything a game read carries, so a lazy load would issue its query here."""
    assert game.kill_team.name is not None
    for collection in (game.sides, game.operatives, game.equipment, game.events):
        [row.id for row in collection]


def _assert_eagerly_loaded(game):
    """The collections are already loaded when the read RETURNS.

    Asked of SQLAlchemy directly, the way the router's shallow-read test asks the
    inverse, because a query COUNT cannot tell eager from lazy here and never could:
    for a single parent, a lazy collection costs one query on access and `selectinload`
    costs one query up front -- the same number, just at a different moment. Deleting
    all three `selectinload`s left the count at five and every test green.
    """
    unloaded = inspect(game).unloaded
    for name in ("sides", "operatives", "equipment", "events", "kill_team"):
        assert name not in unloaded, f"{name} came back lazy, so a battle screen pays per access"


def test_a_game_read_is_flat_whatever_the_battles_size(session, battle):
    """One query count for three operatives and for six.

    The snapshots cost no joins at all, which is the shape paying off: `rules`, `ploys`,
    `weapons` and `abilities` are columns on these rows rather than relationships.
    """
    svc = _service(session)
    b3 = battle(operatives=3)
    b6 = battle(operatives=6)
    game3 = svc.create_game(b3["user"].id, b3["roster"].id)
    game6 = svc.create_game(b6["user"].id, b6["roster"].id)
    session.expunge_all()

    with _counting(session) as c3:
        small = svc.get_game(game3.id)
        _assert_eagerly_loaded(small)
        _touch_whole_game(small)
    with _counting(session) as c6:
        large = svc.get_game(game6.id)
        _assert_eagerly_loaded(large)
        _touch_whole_game(large)

    # An ABSOLUTE count, and every collection walked inside the measured block. Both are
    # load-bearing, and this test had neither: it asserted only `c3 == c6`, so deleting
    # all three `selectinload`s left it green. Lazy loading costs ZERO queries inside
    # `get_game` when nothing touches the collections, and `selectinload` costs one per
    # collection whatever the size -- so the two sides moved together under every
    # mutation. The catalog's equivalent already asserts `== 9` for exactly this reason.
    # Six, not five: `sides` joined the bundle when the moved columns became the side's
    # (#67), so a battle screen still pays one query per collection and none per row.
    assert c3["n"] == c6["n"] == 6, f"{c3['n']} queries for three, {c6['n']} for six"


def test_a_read_after_a_write_sees_the_write(session, battle):
    """Regression for `populate_existing`.

    Without it a read that follows a write in the same session gets the collection as it
    was when first loaded -- SQLAlchemy returns the identity-mapped object and leaves an
    already-populated collection alone. The router makes this the normal case: its
    ownership dependency loads the game, the route mutates it, then reads it back.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    assert svc.get_game(game.id).events == [], "nothing has happened yet"

    svc.update_game(game.id, command_points=2)

    assert len(svc.get_game(game.id).events) == 1


# --- the listing is lean ---------------------------------------------------


def test_games_are_listed_newest_first(session, battle):
    """Newest first, unlike rosters — and this really asserts the order.

    An earlier version of this test compared a SET of ids, so it was named for an
    ordering property it never checked: mutating the query to oldest-first passed it.
    A game list answers "what did I play recently", where a roster list is a library
    you scroll, which is why the two sort opposite ways.
    """
    b = battle()
    svc = _service(session)
    first = svc.create_game(b["user"].id, b["roster"].id, opponent_name="First")
    second = svc.create_game(b["user"].id, b["roster"].id, opponent_name="Second")
    first.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    second.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    session.add_all([first, second])
    session.commit()

    assert [g.opponent_name for g in svc.list_games(b["user"].id)] == ["Second", "First"]
    assert svc.count_games(b["user"].id) == 2


def test_the_game_listing_pages_rather_than_returning_everything(session, battle):
    """`limit` and `offset` reach the query.

    Four mutants survived on this listing: ignoring `offset`, ignoring `limit`, dropping
    the `id` tie-break, and reversing the sort. Every test called `list_games(user.id)`
    with the defaults, so a listing that returned everything in any order passed them
    all. The roster listing had the same three, which is audit finding 13.
    """
    b = battle()
    svc = _service(session)
    for n in range(5):
        game = svc.create_game(b["user"].id, b["roster"].id, opponent_name=f"G{n}")
        game.created_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=n)
        session.add(game)
    session.commit()

    # Newest first, so G4 leads.
    assert [g.opponent_name for g in svc.list_games(b["user"].id, limit=2)] == ["G4", "G3"]
    assert [g.opponent_name for g in svc.list_games(b["user"].id, limit=2, offset=2)] == ["G2", "G1"]
    assert [g.opponent_name for g in svc.list_games(b["user"].id, limit=2, offset=4)] == ["G0"]
    # Paging the rows must not page the TOTAL.
    assert svc.count_games(b["user"].id) == 5


def test_games_sharing_a_timestamp_are_ordered_by_id(session, battle):
    """`created_at` then `id`, so a page boundary cannot show a row twice or skip one.

    Games created in one request share a timestamp, and the sort column alone would
    leave them in whatever order the plan yielded -- which can differ between two
    requests, so a client paging through would see one game twice and miss another.

    Ids fixed and inserted so insertion order is the reverse of id order, which makes
    dropping the tie-break fail deterministically.
    """
    b = battle()
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    ids = [uuid.UUID(f"ffffffff-0000-0000-0000-00000000000{n}") for n in (3, 2, 1)]
    for n, game_id in enumerate(ids):
        session.add(
            KTGame(
                id=game_id,
                owner_user_id=b["user"].id,
                kill_team_id=b["roster"].kill_team_id,
                roster_id=b["roster"].id,
                opponent_name=f"G{n}",
                created_at=stamp,
            )
        )
    session.commit()

    assert [g.id for g in _service(session).list_games(b["user"].id)] == sorted(ids)


def test_a_listing_only_ever_shows_the_callers_games(session, battle):
    mine, theirs = battle(), battle()
    svc = _service(session)
    svc.create_game(mine["user"].id, mine["roster"].id)
    svc.create_game(theirs["user"].id, theirs["roster"].id)

    assert len(svc.list_games(mine["user"].id)) == 1
    assert svc.count_games(mine["user"].id) == 1


# --- version: a stale write is answered (#9) --------------------------------


def test_a_stale_version_is_a_conflict_rather_than_an_overwrite(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    stale = game.version

    svc.update_game(game.id, stale, command_points=1)

    with pytest.raises(ConflictError, match="version"):
        svc.update_game(game.id, stale, command_points=99)
    assert _player(session, game).command_points == 1, "the second write was answered, not applied"


def test_every_mutation_moves_the_version(session, battle):
    # One helper writes the event and bumps the version, so neither can be forgotten --
    # an unbumped version lets the other tab overwrite this write without a 409.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]
    start = game.version

    svc.update_game(game.id, command_points=1)
    svc.update_operative(game.id, row.id, current_wounds=11)
    svc.activate_operative(game.id, row.id)
    svc.advance(game.id)

    assert game.version == start + 4


# --- bookkeeping, and what is deliberately not refused ----------------------


def test_wounds_stay_within_the_cards_own_maximum(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]

    with pytest.raises(KTGameValidationError, match="current_wounds"):
        svc.update_operative(game.id, row.id, current_wounds=row.wounds + 1)
    with pytest.raises(KTGameValidationError, match="current_wounds"):
        svc.update_operative(game.id, row.id, current_wounds=-1)


def test_zero_wounds_takes_an_operative_out_of_the_battle(session, battle):
    # Bookkeeping, not a rule: a caller that had to remember to say so would eventually
    # not, and a tracker showing a model on 0 wounds still "on board" is just wrong.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]

    svc.update_operative(game.id, row.id, current_wounds=0)

    assert row.status == "incapacitated"


def test_an_operative_activates_once_in_a_turning_point_and_again_in_the_next(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]

    svc.activate_operative(game.id, row.id)
    assert row.activated_in_turning_point == 1
    with pytest.raises(KTGameValidationError, match="already activated"):
        svc.activate_operative(game.id, row.id)

    svc.advance(game.id)
    svc.advance(game.id)  # into turning point 2
    svc.activate_operative(game.id, row.id)

    assert row.activated_in_turning_point == 2


def test_an_action_must_be_one_the_operatives_own_card_lists(session, battle):
    # Decision #23. WHICH actions exist is the snapshot's business; how OFTEN each may be
    # used is a rule, so the same entry twice is accepted.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]

    with pytest.raises(KTGameValidationError, match="not on"):
        svc.update_operative(game.id, row.id, actions_used=[{"name": "Teleport", "turning_point": 1}])

    svc.update_operative(
        game.id,
        row.id,
        actions_used=[{"name": "Pounce", "turning_point": 1}, {"name": "Pounce", "turning_point": 1}],
    )
    assert len(row.actions_used) == 2, "how often is a rule, so a repeat is accepted"


def test_victory_points_must_be_counts_even_though_the_sources_are_not_ours(session, battle):
    # The document holds nothing, so these are the rules the schema would have had.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    svc.update_game(game.id, victory_points={"crit_op": 4, "tac_op_1": 2})
    assert _player(session, game).victory_points == {"crit_op": 4, "tac_op_1": 2}

    for bad in ({"crit_op": -1}, {"crit_op": "lots"}, {"crit_op": True}, []):
        with pytest.raises(KTGameValidationError, match="victory_points"):
            svc.update_game(game.id, victory_points=bad)


def test_command_points_never_go_negative(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="command_points"):
        svc.update_game(game.id, command_points=-1)


def test_a_patch_cannot_move_the_turning_point_behind_advances_back(session, battle):
    # `turning_point` and `phase` are not updatable: they move through `advance`, which
    # applies the resets that go with them, and a direct set would skip those.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="cannot update"):
        svc.update_game(game.id, turning_point=4)
    with pytest.raises(KTGameValidationError, match="cannot update"):
        svc.update_game(game.id, phase="firefight")


# --- operatives that join or change mid-battle (#18, #19) -------------------


def test_an_operative_a_rule_grants_is_added_with_the_turning_point_it_arrived_in(
    session, battle, make_kt_operative
):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    vermin = make_kt_operative(kill_team=b["team"], name="Cursemite", availability="in_battle", position=9)
    svc.advance(game.id)
    svc.advance(game.id)

    row = svc.add_operative(game.id, vermin.id, source="equipment")

    assert (row.source, row.added_in_turning_point) == ("equipment", 2)
    assert row.name == "Cursemite" and row.current_wounds == row.wounds


def test_a_rosters_operatives_arrive_with_the_game_not_during_it(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="arrive with the game"):
        svc.add_operative(game.id, b["cards"][0].id, source="roster")


def test_an_added_operative_must_be_one_of_this_games_kill_teams(
    session, battle, make_kill_team, make_kt_operative
):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    stranger = make_kt_operative(kill_team=make_kill_team(name="Someone Else"))

    with pytest.raises(NotFoundError, match="not one of kill team"):
        svc.add_operative(game.id, stranger.id, source="rule")


def test_a_transform_keeps_the_model_and_moves_only_the_card(
    session, battle, make_kt_operative, make_kt_ability
):
    """Decision #19, in place: it is the same miniature on the table.

    The row keeps its id, its tokens, the actions it has used and where it stands; the
    catalog pointer moves and the snapshot is rewritten.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]
    svc.update_operative(
        game.id, row.id, tokens=["Poison"], actions_used=[{"name": "Pounce", "turning_point": 1}]
    )
    row_id, position = row.id, row.position

    torment = make_kt_operative(kill_team=b["team"], name="Torment", wounds=18, position=8)
    make_kt_ability(operative=torment, name="Writhe", position=0)
    svc.transform_operative(game.id, row_id, torment.id)

    assert row.id == row_id and row.position == position
    assert (row.name, row.wounds) == ("Torment", 18)
    assert row.operative_id == torment.id
    assert row.tokens == ["Poison"], "its tokens are still on the table"
    assert row.actions_used == [{"name": "Pounce", "turning_point": 1}]
    assert row.abilities[0]["name"] == "Writhe", "the card is re-snapshotted"


def test_a_transform_onto_a_smaller_card_resolves_the_wounds_it_would_break(
    session, battle, make_kt_operative
):
    # A card with fewer wounds than the model currently has is a state the schema
    # refuses, so it is resolved here rather than reaching an IntegrityError.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]
    smaller = make_kt_operative(kill_team=b["team"], name="Devotee", wounds=7, position=8)

    svc.transform_operative(game.id, row.id, smaller.id)

    assert (row.wounds, row.current_wounds) == (7, 7)


# --- equipment (#17, #57) ---------------------------------------------------


def test_the_same_piece_cannot_be_taken_twice_but_a_fifth_piece_can(session, battle, make_kt_equipment):
    """The one equipment rule enforced, and the one deliberately not.

    `UNIQUE(game_id, equipment_id)` is integrity. The ALLOWANCE is reported and not
    refused (#57), which is what lets a custom game be built -- the same reasoning that
    made composition text rather than structure.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    svc.add_equipment(game.id, b["kit"].id)
    with pytest.raises(ConflictError, match="already taken"):
        svc.add_equipment(game.id, b["kit"].id)

    for n in range(EQUIPMENT_LIMIT + 1):
        svc.add_equipment(game.id, make_kt_equipment(kill_team=b["team"], name=f"Kit {n}").id)
    assert len(svc.get_game(game.id).equipment) == EQUIPMENT_LIMIT + 2


def test_the_universal_equipment_list_is_available_to_every_team(session, battle):
    # `kill_team_id IS NULL` belongs to no team (#48), so the schema cannot express the
    # pair and the service checks it instead.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    row = svc.add_equipment(game.id, b["universal_kit"].id)

    assert row.name.startswith("Frag Grenade")


def test_another_teams_equipment_is_not_available(session, battle, make_kill_team, make_kt_equipment):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    theirs = make_kt_equipment(kill_team=make_kill_team(name="Someone Else"), name="Theirs")

    with pytest.raises(NotFoundError, match="not available"):
        svc.add_equipment(game.id, theirs.id)


def test_equipment_is_revealed_during_the_battle(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = svc.add_equipment(game.id, b["kit"].id)
    assert row.revealed is False

    svc.reveal_equipment(game.id, row.id)

    assert row.revealed is True


# --- advance (#61, #62) ----------------------------------------------------


def test_advance_walks_the_phases_then_the_turning_points(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    seen = [(game.turning_point, game.phase, game.status)]

    for _ in range(8):
        svc.advance(game.id)
        seen.append((game.turning_point, game.phase, game.status))

    assert seen[0] == (1, "strategy", "setup")
    assert seen[1] == (1, "firefight", "in_progress")
    assert seen[2] == (2, "strategy", "in_progress")
    assert seen[-1] == (LAST_TURNING_POINT, "firefight", "finished")
    assert max(tp for tp, _, _ in seen) == LAST_TURNING_POINT


def test_advancing_into_a_new_turning_point_puts_initiative_back_to_nobody(session, battle):
    # Decision #62: it is rolled off per turning point, so the new one holds nobody.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, initiative="player")

    svc.advance(game.id)
    assert game.initiative == "player", "the same turning point keeps its roll-off"

    svc.advance(game.id)
    assert game.initiative is None


def test_advancing_clears_no_activations_because_none_need_clearing(session, battle):
    """Decision #61, stated as the thing it buys.

    The stamp stays where it was and simply stops matching, so `advance` writes one row
    rather than every operative -- and its event carries one field rather than as many
    as the team has models.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    for row in game.operatives:
        svc.activate_operative(game.id, row.id)
    svc.update_game(game.id, initiative="player")

    svc.advance(game.id)
    svc.advance(game.id)

    assert all(row.activated_in_turning_point == 1 for row in game.operatives)
    assert game.turning_point == 2
    advanced = [e for e in svc.get_game(game.id).events if e.type == "advanced"]
    touched = {k for k in advanced[-1].payload if k not in {"op", "target_id"}}
    assert touched == {"phase", "turning_point", "initiative"}


def test_a_finished_game_cannot_be_advanced_again(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    for _ in range(8):
        svc.advance(game.id)

    with pytest.raises(KTGameValidationError, match="already finished"):
        svc.advance(game.id)


# --- undo (#8, #58) --------------------------------------------------------


def test_undo_writes_the_before_values_back(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]
    svc.update_operative(game.id, row.id, current_wounds=7)
    svc.update_operative(game.id, row.id, current_wounds=3)

    svc.undo(game.id)
    assert row.current_wounds == 7
    svc.undo(game.id)
    assert row.current_wounds == 12


def test_undo_walks_back_rather_than_toggling(session, battle):
    """Pressing undo twice goes two steps back, not back to where it started.

    The compensating event is itself a standing event, so a naive "newest un-undone"
    search would pick it and revert the revert. There is no redo (#58).
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, command_points=1)
    svc.update_game(game.id, command_points=5)

    svc.undo(game.id)
    svc.undo(game.id)

    assert _player(session, game).command_points == 0
    log = session.exec(select(KTGameEvent).order_by(KTGameEvent.sequence)).all()
    assert [e.type for e in log] == ["game_updated", "game_updated", "undone", "undone"]
    assert [e.undone_by is not None for e in log] == [True, True, False, False]


def test_undo_appends_rather_than_deleting_so_the_log_keeps_its_history(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, command_points=2)
    before = len(session.exec(select(KTGameEvent)).all())

    svc.undo(game.id)

    assert len(session.exec(select(KTGameEvent)).all()) == before + 1


def test_undoing_an_added_operative_takes_the_row_away(session, battle, make_kt_operative):
    # An addition is not a field change, so its undo is a deletion rather than a restore.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    vermin = make_kt_operative(kill_team=b["team"], name="Cursemite", availability="in_battle")
    svc.add_operative(game.id, vermin.id, source="equipment")
    assert len(svc.get_game(game.id).operatives) == 4

    svc.undo(game.id)

    assert len(svc.get_game(game.id).operatives) == 3
    assert "Cursemite" not in {r.name for r in svc.get_game(game.id).operatives}


def test_undoing_a_removed_piece_of_equipment_brings_it_back(session, battle):
    # A deletion's undo is a recreation, so the event has to carry the whole row --
    # nothing else remembers it.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = svc.add_equipment(game.id, b["kit"].id)
    svc.reveal_equipment(game.id, row.id)
    row_id = row.id
    svc.remove_equipment(game.id, row_id)
    assert svc.get_game(game.id).equipment == []

    svc.undo(game.id)

    back = svc.get_game(game.id).equipment
    assert len(back) == 1
    assert (back[0].id, back[0].name, back[0].revealed) == (row_id, "Grisly Trophy", True)


def test_undo_reopens_a_game_that_was_finished_by_accident(session, battle):
    # The finish is an event like any other, which is the reason `advance` was allowed
    # to end the game rather than refusing (#60's sibling decision) -- and the reason
    # decision #66 lets a FINISHED game accept `undo` and needs no `reopen` route. This
    # test predates #66 and is what it was built on.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    for _ in range(8):
        svc.advance(game.id)
    assert game.status == "finished"

    svc.undo(game.id)

    assert game.status == "in_progress"


def test_a_game_with_nothing_left_to_undo_says_so(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="nothing left to undo"):
        svc.undo(game.id)

    svc.update_game(game.id, command_points=1)
    svc.undo(game.id)
    with pytest.raises(KTGameValidationError, match="nothing left to undo"):
        svc.undo(game.id)


def test_an_event_records_only_the_fields_that_actually_changed(session, battle):
    # So an event never claims to have touched something it left alone, and its undo
    # restores exactly what the write replaced.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, command_points=3)

    svc.update_game(game.id, command_points=3, opponent_victory_points=2)

    latest = svc.get_game(game.id).events[-1]
    assert set(latest.payload) - {"op", "target_id"} == {"opponent_victory_points"}


# --- scoping ---------------------------------------------------------------


def test_a_row_from_another_game_reads_as_missing(session, battle):
    b = battle()
    svc = _service(session)
    mine = svc.create_game(b["user"].id, b["roster"].id)
    theirs = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(NotFoundError, match="is not in game"):
        svc.update_operative(mine.id, theirs.operatives[0].id, current_wounds=1)


def test_an_equipment_row_from_another_game_reads_as_missing(session, battle):
    """`_require_equipment` scopes by `game_id`, not by row id alone.

    The operative twin above was tested and this was not, so dropping
    `or row.game_id != game_id` here let a `PATCH` or `DELETE` under one game reach
    another game's equipment row -- including a game the caller does not own, since the
    router's ownership dependency only vouches for the game in the PATH.
    """
    b = battle()
    svc = _service(session)
    mine = svc.create_game(b["user"].id, b["roster"].id)
    theirs = svc.create_game(b["user"].id, b["roster"].id)
    theirs_kit = svc.add_equipment(theirs.id, b["kit"].id)

    with pytest.raises(NotFoundError, match="is not in game"):
        svc.reveal_equipment(mine.id, theirs_kit.id)
    with pytest.raises(NotFoundError, match="is not in game"):
        svc.remove_equipment(mine.id, theirs_kit.id)


def test_deleting_a_game_takes_its_rows_and_leaves_the_roster(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.add_equipment(game.id, b["kit"].id)
    svc.update_game(game.id, command_points=1)

    svc.delete_game(game.id)
    session.commit()

    assert session.exec(select(KTGameOperative)).all() == []
    assert session.exec(select(KTGameEvent)).all() == []
    assert session.get(type(b["roster"]), b["roster"].id) is not None


# --- the incapacitation reads both ways ------------------------------------


def test_raising_wounds_above_zero_puts_an_operative_back_on_the_board(session, battle):
    """The correction path, and the reason it has to exist.

    Dropping to zero sets `incapacitated`; raising back above zero clears it. Setting
    the field on the way down and not on the way up is asymmetric automation, which is
    worse than none -- because something maintains the field, a player trusts it, and it
    would be right only half the time.

    It matters more now that `activate_operative` refuses an incapacitated model: a
    stale label would leave a live operative unable to act for the rest of the game,
    with nothing but an explicit `status` write to rescue it.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]

    svc.update_operative(game.id, row.id, current_wounds=0)
    assert row.status == "incapacitated"

    svc.update_operative(game.id, row.id, current_wounds=5)

    assert row.status == "on_board"
    # And it can act again, which is the point.
    assert svc.activate_operative(game.id, row.id).activated_in_turning_point == 1


def test_an_explicit_status_wins_over_the_automatic_one(session, battle):
    # `setdefault`, not an assignment: a caller that names a status means it. Wounding a
    # model down to zero while declaring it `reserve` is odd but it is the caller's call.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]

    svc.update_operative(game.id, row.id, current_wounds=0, status="reserve")

    assert row.status == "reserve"


def test_an_operative_in_reserve_that_gains_wounds_stays_off_the_board(session, battle):
    # Only `incapacitated` is cleared. A model in reserve has not arrived, and healing it
    # does not put it on the table.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]
    svc.update_operative(game.id, row.id, status="reserve", current_wounds=4)

    svc.update_operative(game.id, row.id, current_wounds=9)

    assert row.status == "reserve"


def test_an_incapacitated_operative_cannot_activate(session, battle):
    """Bookkeeping, not a rule: a model that is gone cannot act.

    Deliberately NOT extended to `reserve`. Whether an operative can arrive from reserve
    and act in the same turning point is a RULE, and #1 leaves rules to the players --
    refusing it would be the tracker guessing.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    dead, reserved = game.operatives[0], game.operatives[1]
    svc.update_operative(game.id, dead.id, current_wounds=0)
    svc.update_operative(game.id, reserved.id, status="reserve")

    with pytest.raises(KTGameValidationError, match="incapacitated"):
        svc.activate_operative(game.id, dead.id)

    # The reserve one is allowed, because that is the players' call.
    assert svc.activate_operative(game.id, reserved.id).activated_in_turning_point == 1


# --- spending a ploy is one operation (#24, #1) -----------------------------


def test_using_a_ploy_records_it_and_pays_for_it(session, battle):
    """The two halves were a caller's job to remember, and CP is the number that counts.

    The cost comes from the game's own snapshot, so the client neither supplies it nor
    can forget it.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, command_points=3)
    ploy = next(p for p in game.ploys if not p["universal"])

    svc.use_ploy(game.id, ploy["name"])

    side = _player(session, game)
    assert side.ploys_used == [{"name": ploy["name"], "turning_point": 1}]
    assert side.command_points == 3 - ploy["cp_cost"]


def test_undoing_a_ploy_restores_the_cp_and_the_entry_together(session, battle):
    """Why it is one operation rather than two writes.

    Two PATCHes would be two events, and undoing once would leave either a ploy logged
    that had been paid for or paid-for CP with no ploy to show it. One event means one
    undo puts both back.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, command_points=4)
    ploy = next(p for p in game.ploys if not p["universal"])
    svc.use_ploy(game.id, ploy["name"])

    svc.undo(game.id)

    side = _player(session, game)
    assert side.ploys_used == []
    assert side.command_points == 4


def test_a_ploy_this_game_does_not_have_is_refused(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)

    with pytest.raises(KTGameValidationError, match="not one of this game's ploys"):
        svc.use_ploy(game.id, "Teleportarium")


def test_a_ploy_cannot_be_spent_on_cp_the_side_does_not_have(session, battle):
    # Bookkeeping the CHECK would refuse anyway, caught here so the message names the
    # ploy and the shortfall rather than arriving as an IntegrityError. The message names
    # WHOSE pool fell short, because after #67 a game has two of them.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    ploy = next(p for p in svc.player_side(game).ploys if p["cp_cost"] > 0)

    with pytest.raises(KTGameValidationError, match="costs 1 CP and the player side has 0"):
        svc.use_ploy(game.id, ploy["name"])


def test_the_same_ploy_twice_is_accepted_because_that_is_a_rule(session, battle):
    # How often a ploy may be used, and how many a turning point allows, are the
    # players' (#1). Only that it EXISTS and that the CP covers it is bookkeeping.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, command_points=5)
    ploy = next(p for p in game.ploys if p["cp_cost"] > 0)

    svc.use_ploy(game.id, ploy["name"])
    svc.use_ploy(game.id, ploy["name"])

    side = _player(session, game)
    assert len(side.ploys_used) == 2
    assert side.command_points == 5 - 2 * ploy["cp_cost"]


def test_a_universal_ploy_can_be_spent_like_the_teams_own(session, battle):
    # The snapshot carries both (#24), so a game needs no catalog call to spend
    # Command Re-roll.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, command_points=2)
    universal = next(p for p in game.ploys if p["universal"])

    svc.use_ploy(game.id, universal["name"])

    assert _player(session, game).ploys_used[0]["name"] == universal["name"]


def test_setting_ploys_used_directly_is_checked_against_the_snapshot(session, battle):
    """The missing half of #23's rule.

    `actions_used` was checked against an operative's card and `ploys_used` was checked
    against nothing, so the field accepted a ploy the team does not have. The field stays
    writable -- a custom game may need to put anything in it -- but a typo is caught.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    real = game.ploys[0]["name"]

    with pytest.raises(KTGameValidationError, match="not one of this game's ploys"):
        svc.update_game(game.id, ploys_used=[{"name": "Teleportarium", "turning_point": 1}])

    svc.update_game(game.id, ploys_used=[{"name": real, "turning_point": 1}])
    assert _player(session, game).ploys_used == [{"name": real, "turning_point": 1}]


# --- withdrawn rows and a battle (#55, K7) ----------------------------------


def test_a_withdrawn_datacard_cannot_be_newly_added_to_a_game(session, battle, make_kt_operative):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    vermin = make_kt_operative(kill_team=b["team"], name="Cursemite", availability="in_battle")
    vermin.withdrawn = True
    session.add(vermin)
    session.commit()

    with pytest.raises(NotFoundError, match="no longer in the catalog"):
        svc.add_operative(game.id, vermin.id, source="equipment")


def test_withdrawn_equipment_cannot_be_newly_taken(session, battle):
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    b["kit"].withdrawn = True
    session.add(b["kit"])
    session.commit()

    with pytest.raises(NotFoundError, match="no longer in the catalog"):
        svc.add_equipment(game.id, b["kit"].id)


def test_a_transform_onto_a_withdrawn_card_is_allowed(session, battle, make_kt_operative):
    """Deliberately NOT refused, and this is the case the flag exists to survive.

    A model already on the table changing into something the catalog has since dropped is
    mid-battle state, not a new pick. Refusing it would strand a game over a change
    upstream -- the thing #55 chose a flag over a delete to avoid.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    row = game.operatives[0]
    torment = make_kt_operative(kill_team=b["team"], name="Torment", wounds=18, position=8)
    torment.withdrawn = True
    session.add(torment)
    session.commit()

    svc.transform_operative(game.id, row.id, torment.id)

    assert row.name == "Torment"


def test_a_game_already_under_way_is_untouched_by_a_withdrawal(session, battle):
    # A game snapshots everything (#22, #24), so a withdrawal cannot reach it at all.
    # Stated as a test because it is the strongest form of #55's promise.
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    before = [row.name for row in game.operatives]
    for card in b["cards"]:
        card.withdrawn = True
        session.add(card)
    session.commit()
    game_id = game.id
    session.expunge_all()

    assert [row.name for row in svc.get_game(game_id).operatives] == before


def test_a_games_operatives_keep_the_rosters_order(session, battle):
    """`create_game` copies each roster row's `position`, rather than numbering afresh.

    The roster order is the PLAYER's (decision #25's exception: every other `position` is
    the printed one, a roster's is theirs to set), so a battle screen that renumbered
    would reorder the models the player deliberately arranged.

    Unassertable until the `battle` fixture passed a position: left to the column default
    every roster row sat at 0, so `position=row.position` and a literal `position=0` were
    indistinguishable.
    """
    b = battle(operatives=4)
    game = _service(session).create_game(b["user"].id, b["roster"].id)

    roster_order = [(row.position, row.operative.name) for row in b["roster"].operatives]
    assert [(row.position, row.name) for row in game.operatives] == roster_order
    assert [row.position for row in game.operatives] == [0, 1, 2, 3], "positions were not copied"


def test_an_operative_added_mid_battle_goes_last(session, battle, make_kt_operative):
    """`_next_position` is the highest PLUS ONE, so a granted model lands after the rest.

    Revealing equipment or a team rule can field a datacard the roster never held (#18).
    It goes last, because it arrived last -- and with `+ 0` instead of `+ 1` it would tie
    with the model already at the end and the pair's order would fall to the id
    tie-break, i.e. to chance.
    """
    b = battle(operatives=3)
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    granted = make_kt_operative(kill_team=b["team"], name="Vermin", wounds=4, position=9)

    row = svc.add_operative(game.id, granted.id, source="equipment")

    assert row.position == 3, "the granted model did not go last"
    assert [r.name for r in svc.get_game(game.id).operatives][-1] == "Vermin"


def test_game_operatives_tied_on_position_are_ordered_by_id(session, battle):
    """`(position, id)`, so two models at the same position have ONE order.

    Nothing stops a tie -- there is no `UNIQUE(game_id, position)`, deliberately, because
    `_next_position` is a read-then-write and a unique constraint would turn a race into
    an error rather than a harmless tie. So the tie-break is what makes the read
    deterministic, and without it the two rows come back in whatever order the plan gave.

    The ids are fixed and inserted DESCENDING, like the roster and game sibling tests, so
    dropping the tie-break fails this every run rather than only when random uuid4s
    happen to disagree with insertion order.
    """
    b = battle(operatives=1)
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    original = game.operatives[0]

    ids = [uuid.UUID(f"ffffffff-0000-0000-0000-00000000000{n}") for n in (3, 2, 1)]
    for row_id in ids:  # descending ids, so insertion order is the reverse of id order
        clone = KTGameOperative(
            **{
                column: getattr(original, column)
                for column in KTGameOperative.__table__.columns.keys()
                if column not in {"id", "created_at", "updated_at"}
            }
        )
        clone.id = row_id
        clone.position = original.position  # tied with every other row on purpose
        session.add(clone)
    session.commit()

    tied = [row.id for row in svc.get_game(game.id).operatives if row.id in set(ids)]
    assert tied == sorted(ids), "rows tied on position came back in insertion order, not id order"


def test_a_write_onto_a_row_that_moved_is_refused_by_the_database(session, battle):
    """Decision #9 is ENFORCED, not checked. The write loses the row, not the race.

    `version` used to be compared in Python and incremented in Python, which under READ
    COMMITTED is a check-then-act with nothing behind it: two writers who both read
    version 5 both passed the check and both wrote, and the second silently overwrote
    the first. `use_ploy` was the costly case -- it reads `command_points`, computes
    `- cost` and writes the absolute value, so one CP paid for two ploys.

    Mapped as SQLAlchemy's `version_id_col`, every UPDATE of a game row carries
    `WHERE version = <the value this writer read>`, so the loser matches zero rows.

    The other writer is simulated with a Core `update()` rather than a second session,
    for the reason `test_a_direct_update_cannot_move_a_row_into_another_players_roster`
    does the same: the test tier is one shared in-memory connection, so two sessions
    would share a transaction and prove nothing. `synchronize_session=False` leaves this
    session's loaded game holding the version it read, which IS the stale state.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    read_version = game.version

    moved = session.execute(
        update(KTGame)
        .where(KTGame.id == game.id)
        .values(version=KTGame.version + 1)
        .execution_options(synchronize_session=False)
    )
    assert moved.rowcount == 1, "the other writer's update did not land"
    assert game.version == read_version, "this session no longer holds the stale version"

    with pytest.raises(StaleDataError):
        svc.update_game(game.id, None, command_points=4)
    session.rollback()


def test_a_ploy_cannot_be_paid_for_twice_from_one_pool(session, battle):
    """The lost update that cost real CP, now refused rather than silently applied.

    Two writers reading `command_points = 5` both computed 4 and both appended a
    one-element `ploys_used`, so the pool paid once and the log showed one ploy where
    two had been spent. The version lock makes the second write lose its row.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, None, command_points=5)
    ploy_name = game.ploys[0]["name"]
    read_version = game.version

    session.execute(  # the other tab spends the same CP first
        update(KTGame)
        .where(KTGame.id == game.id)
        .values(version=KTGame.version + 1, command_points=4)
        .execution_options(synchronize_session=False)
    )
    assert game.version == read_version

    with pytest.raises(StaleDataError):
        svc.use_ploy(game.id, ploy_name)
    session.rollback()


def test_two_undos_of_the_same_event_cannot_both_land(session, battle):
    """`undo` is a read-then-write too, and had no version parameter to guard it.

    Both writers found the same newest standing event, so the event was reverted twice
    and the change below it was stranded -- the log said two steps back, the game had
    taken one. The lock catches it because `undo` writes its compensating event through
    `_write`, which moves the version like every other mutation.
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, None, command_points=3)
    read_version = game.version

    session.execute(
        update(KTGame)
        .where(KTGame.id == game.id)
        .values(version=KTGame.version + 1)
        .execution_options(synchronize_session=False)
    )
    assert game.version == read_version

    with pytest.raises(StaleDataError):
        svc.undo(game.id)
    session.rollback()


def test_the_finished_refusal_defaults_on_so_a_new_write_is_covered(session, battle):
    """`_require_game` refuses by DEFAULT; a caller must opt out explicitly (#66).

    This is the property that matters more than any one route: the nine write paths are
    covered because the refusal lives in the loader they all share, not because nine
    places remembered it. A mutation added next month inherits it on the day it is
    written, where an opt-in list would quietly not cover it.

    The four opt-outs each have a reason stated at `_require_game`: a read, a delete,
    `undo` (the way back out), and `advance` (which refuses on its own with a better
    message).
    """
    b = battle()
    svc = _service(session)
    game = svc.create_game(b["user"].id, b["roster"].id)
    svc.update_game(game.id, None, status="finished")

    with pytest.raises(KTGameValidationError, match="finished") as refused:
        svc._require_game(game.id)
    assert refused.value.field == "status"

    # And the opt-out really does let a caller through, which is what the read, the
    # delete, `undo` and `advance` rely on.
    assert svc._require_game(game.id, allow_finished=True).id == game.id
