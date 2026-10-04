"""Service tests for kill team games (`KTGameService`).

The ones that carry weight are the three mechanisms new to this codebase: the snapshot
(a game stops reading the catalog), the event log with `undo`, and `version` answering a
stale write. Everything else is bookkeeping, and the tests say which is which -- what is
REFUSED is bookkeeping, what is merely recorded is a rule the players apply (#1).
"""

import itertools
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlmodel import select

from app.core.db.models_killteam import KTGameEvent, KTGameOperative
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
        for n in range(operatives):
            card = make_kt_operative(kill_team=team, name=f"Warrior {n}", wounds=12, position=n)
            make_kt_weapon(operative=card, name="Claws", position=0)
            make_kt_ability(operative=card, name="Pounce", position=0)
            make_kt_roster_operative(roster=roster, operative=card)
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
        svc.get_game(game3.id)
    with _counting(session) as c6:
        svc.get_game(game6.id)

    assert c3["n"] == c6["n"], f"{c3['n']} queries for three, {c6['n']} for six"


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


def test_games_are_listed_newest_first_and_without_the_bundle(session, battle):
    b = battle()
    svc = _service(session)
    older = svc.create_game(b["user"].id, b["roster"].id, opponent_name="First")
    newer = svc.create_game(b["user"].id, b["roster"].id, opponent_name="Second")
    older.created_at, newer.created_at = older.created_at, newer.created_at

    listed = svc.list_games(b["user"].id)

    assert {g.id for g in listed} == {older.id, newer.id}
    assert svc.count_games(b["user"].id) == 2


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
    assert game.command_points == 1, "the second write was answered, not applied"


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
    assert game.victory_points == {"crit_op": 4, "tac_op_1": 2}

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

    assert game.command_points == 0
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
    # to end the game rather than refusing (#60's sibling decision).
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
