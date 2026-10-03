"""Service tests for kill team rosters (`KTRosterService`).

The ones that carry weight are the three where this deliberately differs from
`ArmyService`: an add APPENDS rather than 409ing, a row is addressed by its own id
rather than by the operative it names, and nothing refuses an operative on grounds
of legality -- only of reference.
"""

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app.core.services.errors import ConflictError, NotFoundError
from app.core.services.service_killteam_roster import KTRosterService, KTRosterValidationError


def _service(session):
    return KTRosterService(session)


# --- creating and reading ---------------------------------------------------


def test_a_roster_is_created_empty_for_one_kill_team(session, make_user, make_kill_team):
    user, team = make_user(), make_kill_team(name="Raveners")

    roster = _service(session).create_roster(user.id, team.id, "My Raveners", "for Tuesday")

    assert (roster.name, roster.description) == ("My Raveners", "for Tuesday")
    assert roster.kill_team_id == team.id
    assert roster.operatives == []


def test_creating_a_roster_for_a_team_that_does_not_exist_is_a_not_found(session, make_user):
    with pytest.raises(NotFoundError):
        _service(session).create_roster(make_user().id, uuid.uuid4(), "Nowhere")


def test_creating_a_roster_for_a_user_that_does_not_exist_is_a_not_found(session, make_kill_team):
    with pytest.raises(NotFoundError):
        _service(session).create_roster(uuid.uuid4(), make_kill_team().id, "Nobody's")


def test_one_player_cannot_have_two_rosters_of_the_same_name(session, make_user, make_kill_team):
    user, team = make_user(), make_kill_team()
    service = _service(session)
    service.create_roster(user.id, team.id, "Raveners")

    with pytest.raises(ConflictError):
        service.create_roster(user.id, team.id, "Raveners")


def test_two_players_may_each_have_a_roster_of_the_same_name(session, make_user, make_kill_team):
    # The name rule is about one player's list being readable, not about integrity, which
    # is why it is a service check and not a UNIQUE constraint.
    team = make_kill_team()
    service = _service(session)
    service.create_roster(make_user().id, team.id, "Raveners")

    service.create_roster(make_user().id, team.id, "Raveners")  # no conflict


def test_reading_a_roster_brings_its_operatives_and_their_datacards(
    session, make_kt_roster, make_kt_operative, make_kt_weapon, make_kt_ability
):
    # Decision #52: a roster read embeds the datacards, because it is what a player has
    # in front of them while building and then playing.
    roster = make_kt_roster()
    operative = make_kt_operative(kill_team=roster.kill_team, name="Prime")
    make_kt_weapon(operative=operative, name="Tail blade")
    make_kt_ability(operative=operative, name="Crest")
    service = _service(session)
    service.add_operative(roster.id, operative.id)
    session.expunge_all()

    loaded = _service(session).get_roster(roster.id)

    assert [row.operative.name for row in loaded.operatives] == ["Prime"]
    assert [w.name for w in loaded.operatives[0].operative.weapons] == ["Tail blade"]
    assert [a.name for a in loaded.operatives[0].operative.abilities] == ["Crest"]
    assert loaded.kill_team.name


def test_a_detail_read_carries_the_teams_reference_too(
    session, make_kt_roster, make_kill_team_rule, make_kt_ploy, make_kt_equipment
):
    # Decision #52: the team's rules, ploys and equipment travel with the roster, because
    # they are what applies while playing it. The two UNIVERSAL lists belong to no team
    # and are read separately -- the router joins them on.
    roster = make_kt_roster()
    make_kill_team_rule(kill_team=roster.kill_team, name="Burrow")
    make_kt_ploy(kill_team=roster.kill_team, name="TUNNEL")
    make_kt_equipment(kill_team=roster.kill_team, name="SPORE")
    roster_id = roster.id
    session.expunge_all()

    loaded = _service(session).get_roster(roster_id)

    assert [r.name for r in loaded.kill_team.rules] == ["Burrow"]
    assert [p.name for p in loaded.kill_team.ploys] == ["TUNNEL"]
    assert [e.name for e in loaded.kill_team.equipment] == ["SPORE"]
    assert loaded.kill_team.faction.name


def test_a_detail_read_is_usable_once_the_session_is_done_with_it(
    session, make_kt_roster, make_kt_operative, make_kt_weapon, make_kill_team_rule
):
    # A query count cannot catch a missing eager load on a direct collection -- lazy and
    # eager both cost one query there. What matters is that a router can serialise the
    # object after the request's session has let go of it.
    roster = make_kt_roster()
    make_kill_team_rule(kill_team=roster.kill_team, name="Burrow")
    for name in ("One", "Two"):
        operative = make_kt_operative(kill_team=roster.kill_team, name=name)
        make_kt_weapon(operative=operative, name=f"{name}'s blade")
        _service(session).add_operative(roster.id, operative.id)
    roster_id = roster.id
    session.expunge_all()

    loaded = _service(session).get_roster(roster_id)
    session.expunge(loaded)  # as a closed request-scoped session would leave it

    assert [row.operative.name for row in loaded.operatives] == ["One", "Two"]
    assert all(row.operative.weapons for row in loaded.operatives)
    assert all(row.operative.abilities == [] for row in loaded.operatives)
    assert [r.name for r in loaded.kill_team.rules] == ["Burrow"]
    assert loaded.kill_team.faction.name


def test_reading_a_roster_that_does_not_exist_is_a_not_found(session):
    with pytest.raises(NotFoundError):
        _service(session).get_roster(uuid.uuid4())


def test_a_players_rosters_are_listed_oldest_first_and_counted(session, make_user, make_kill_team):
    user, team = make_user(), make_kill_team()
    service = _service(session)
    for name in ("First", "Second", "Third"):
        service.create_roster(user.id, team.id, name)

    assert [r.name for r in service.list_rosters(user.id)] == ["First", "Second", "Third"]
    assert service.count_rosters(user.id) == 3


@contextmanager
def counting_queries(session):
    """Count the SQL statements a block issues, off the session's engine."""
    statements: list[str] = []
    engine = session.get_bind()

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def test_a_listing_carries_the_team_and_its_faction_and_nothing_heavier(
    session, make_user, make_kt_faction, make_kill_team, make_kt_roster_operative
):
    # A listing answers "which rosters do I have?", so it carries a name, a kill team and
    # that team's faction -- and NOT the operatives, whose datacards make a detail read
    # 20-36 KB (decision #52). THREE rosters, each with its own faction: with one, a lazy
    # load would cost a single cached query and this count could not fail.
    user_id = make_user().id
    for name in ("A", "B", "C"):
        team = make_kill_team(faction=make_kt_faction(name=f"Faction {name}"), name=f"Team {name}")
        roster = _service(session).create_roster(user_id, team.id, f"Roster {name}")
        make_kt_roster_operative(roster=roster)
    session.commit()
    session.expunge_all()  # the factories left these loaded; measure a cold read

    with counting_queries(session) as statements:
        rosters = _service(session).list_rosters(user_id)
        named = [(r.name, r.kill_team.name, r.kill_team.faction.name) for r in rosters]

    assert named == [
        ("Roster A", "Team A", "Faction A"),
        ("Roster B", "Team B", "Faction B"),
        ("Roster C", "Team C", "Faction C"),
    ]
    # rosters, their kill teams, those teams' factions -- three, not three per row
    assert len(statements) == 3


def test_a_listing_does_not_load_the_operatives(session, make_user, make_kill_team, make_kt_roster_operative):
    # The other half: reading an operative off a LISTED roster costs a lazy query, which
    # is the proof it was not fetched. A detail read is what carries them.
    user_id, team = make_user().id, make_kill_team()
    roster = _service(session).create_roster(user_id, team.id, "Mine")
    make_kt_roster_operative(roster=roster)
    session.commit()
    session.expunge_all()

    listed = _service(session).list_rosters(user_id)[0]
    with counting_queries(session) as statements:
        assert len(listed.operatives) == 1

    assert len(statements) == 1, "the operatives were eager-loaded after all"


def test_a_listing_shows_only_the_askers_rosters(session, make_user, make_kill_team):
    team = make_kill_team()
    mine, theirs = make_user(), make_user()
    service = _service(session)
    service.create_roster(mine.id, team.id, "Mine")
    service.create_roster(theirs.id, team.id, "Theirs")

    assert [r.name for r in service.list_rosters(mine.id)] == ["Mine"]
    assert service.count_rosters(mine.id) == 1


# --- updating and deleting --------------------------------------------------


def test_a_roster_can_be_renamed_and_described(session, make_kt_roster):
    roster = make_kt_roster(name="Old")

    updated = _service(session).update_roster(roster.id, name="New", description="now with a plan")

    assert (updated.name, updated.description) == ("New", "now with a plan")


def test_renaming_onto_another_roster_of_the_same_player_conflicts(session, make_user, make_kill_team):
    user, team = make_user(), make_kill_team()
    service = _service(session)
    service.create_roster(user.id, team.id, "Taken")
    mine = service.create_roster(user.id, team.id, "Mine")

    with pytest.raises(ConflictError):
        service.update_roster(mine.id, name="Taken")


def test_renaming_a_roster_to_its_own_name_is_not_a_conflict(session, make_kt_roster):
    # The duplicate check excludes the row being updated, or a no-op PATCH would 409.
    roster = make_kt_roster(name="Raveners")

    _service(session).update_roster(roster.id, name="Raveners", description="unchanged name")


def test_a_rosters_kill_team_cannot_be_changed(session, make_kt_roster, make_kill_team):
    # Every operative on the roster is held to the team by a composite foreign key, so a
    # change would orphan them and fail halfway. Build a new roster instead.
    roster = make_kt_roster()

    with pytest.raises(KTRosterValidationError, match="kill_team_id"):
        _service(session).update_roster(roster.id, kill_team_id=make_kill_team().id)


def test_a_patch_sending_null_for_the_name_is_refused_as_bad_input(session, make_kt_roster):
    # `exclude_unset` lets an explicit null through, and it would reach the database as an
    # IntegrityError rather than a 400.
    roster = make_kt_roster()

    with pytest.raises(KTRosterValidationError, match="name"):
        _service(session).update_roster(roster.id, name=None)


def test_the_description_may_be_cleared(session, make_kt_roster):
    # The other side of the same rule: `description` is nullable, so a null is a value.
    roster = make_kt_roster(description="something")

    assert _service(session).update_roster(roster.id, description=None).description is None


def test_deleting_a_roster_takes_its_rows_and_leaves_the_catalog(session, make_kt_roster, make_kt_operative):
    roster = make_kt_roster()
    operative = make_kt_operative(kill_team=roster.kill_team)
    service = _service(session)
    service.add_operative(roster.id, operative.id)

    service.delete_roster(roster.id)

    with pytest.raises(NotFoundError):
        service.get_roster(roster.id)
    assert session.get(type(operative), operative.id) is not None


# --- operatives on a roster -------------------------------------------------


def test_adding_the_same_operative_twice_appends_rather_than_conflicting(
    session, make_kt_roster, make_kt_operative
):
    # The clearest difference from `ArmyService.add_unit`, which is create-only and 409s.
    # A row is an individual (decision #50), so a repeat is a second operative, not a
    # double-applied request -- there is no quantity to increment.
    roster = make_kt_roster()
    operative = make_kt_operative(kill_team=roster.kill_team, name="Warrior")
    service = _service(session)

    first = service.add_operative(roster.id, operative.id)
    second = service.add_operative(roster.id, operative.id)

    assert first.id != second.id
    assert (first.position, second.position) == (0, 1)
    assert len(service.list_roster_operatives(roster.id)) == 2


def test_an_added_operative_goes_last(session, make_kt_roster, make_kt_operative):
    roster = make_kt_roster()
    service = _service(session)
    for name in ("A", "B", "C"):
        service.add_operative(roster.id, make_kt_operative(kill_team=roster.kill_team, name=name).id)

    assert [row.position for row in service.list_roster_operatives(roster.id)] == [0, 1, 2]


def test_an_operative_of_another_team_cannot_be_added(
    session, make_kt_roster, make_kill_team, make_kt_operative
):
    # Referential, not legal: an operative belongs to one kill team, and the composite
    # foreign keys would refuse the row anyway. Checked here so the caller gets a 404
    # naming the problem rather than an IntegrityError.
    roster = make_kt_roster()
    stranger = make_kt_operative(kill_team=make_kill_team(name="Someone Else"))

    with pytest.raises(NotFoundError, match="not one of kill team"):
        _service(session).add_operative(roster.id, stranger.id)


def test_an_in_battle_operative_may_be_rostered(session, make_kt_roster, make_kt_operative):
    # Decision #51: `availability` informs a picker, it does not refuse a row. A roster
    # may take any operative of its kill team, which is what lets a custom game be built.
    roster = make_kt_roster()
    vermin = make_kt_operative(kill_team=roster.kill_team, name="Cursemite", availability="in_battle")

    row = _service(session).add_operative(roster.id, vermin.id)

    assert row.operative_id == vermin.id


def test_adding_an_operative_that_does_not_exist_is_a_not_found(session, make_kt_roster):
    with pytest.raises(NotFoundError):
        _service(session).add_operative(make_kt_roster().id, uuid.uuid4())


def test_a_row_is_moved_by_its_own_id_not_the_operatives(session, make_kt_roster, make_kt_operative):
    # Two rows may name the same operative, so the operative's id cannot address one.
    roster = make_kt_roster()
    operative = make_kt_operative(kill_team=roster.kill_team, name="Warrior")
    service = _service(session)
    first = service.add_operative(roster.id, operative.id)
    second = service.add_operative(roster.id, operative.id)

    service.move_operative(roster.id, second.id, 0)
    service.move_operative(roster.id, first.id, 1)

    order = [row.id for row in service.list_roster_operatives(roster.id)]
    assert order == [second.id, first.id]


def test_a_move_is_absolute_so_repeating_it_changes_nothing(
    session, make_kt_roster, make_kt_roster_operative
):
    # An absolute position is retry-safe where a delta is not (the same reasoning as
    # decision #7 for a game's values).
    roster = make_kt_roster()
    row = make_kt_roster_operative(roster=roster, position=0)
    service = _service(session)

    service.move_operative(roster.id, row.id, 3)
    service.move_operative(roster.id, row.id, 3)

    assert service.list_roster_operatives(roster.id)[0].position == 3


def test_a_negative_position_is_refused_as_bad_input(session, make_kt_roster, make_kt_roster_operative):
    roster = make_kt_roster()
    row = make_kt_roster_operative(roster=roster)

    with pytest.raises(KTRosterValidationError, match="position"):
        _service(session).move_operative(roster.id, row.id, -1)


def test_a_row_can_be_removed(session, make_kt_roster, make_kt_roster_operative):
    roster = make_kt_roster()
    row = make_kt_roster_operative(roster=roster)
    service = _service(session)

    service.remove_operative(roster.id, row.id)

    assert service.list_roster_operatives(roster.id) == []


def test_a_row_on_someone_elses_roster_reads_as_missing(session, make_kt_roster, make_kt_roster_operative):
    # Scoped to the roster, so a row id from elsewhere is not something a caller may
    # touch -- and not something whose existence is disclosed either.
    mine = make_kt_roster(name="Mine")
    theirs = make_kt_roster(name="Theirs")
    row = make_kt_roster_operative(roster=theirs)
    service = _service(session)

    for operation in (
        lambda: service.move_operative(mine.id, row.id, 0),
        lambda: service.remove_operative(mine.id, row.id),
    ):
        with pytest.raises(NotFoundError, match="is not on roster"):
            operation()


def test_listing_the_rows_of_a_roster_that_does_not_exist_is_a_not_found(session):
    with pytest.raises(NotFoundError):
        _service(session).list_roster_operatives(uuid.uuid4())


# --- a tie on `position` ----------------------------------------------------


def test_rows_tied_on_position_are_ordered_by_id_by_both_readers(
    session, make_kt_roster, make_kt_operative, make_kt_roster_operative
):
    """`position` is a sort hint, not an index (decision #53), so ties are reachable.

    `move_operative` sets a position absolutely and shifts nothing, which is what makes
    it one query and retry-safe -- and also what puts two rows at 0 the first time a
    player moves the third row to the top. The rows must still come back in ONE order,
    and the same order from both readers: `KTRoster.operatives` and
    `list_roster_operatives` are two paths to the same rows, and a client that reads a
    roster detail and then a row listing must not see them disagree.

    The ids are fixed and inserted in DESCENDING order, so insertion order is the
    reverse of id order. Without the tie-breaker a reader yields insertion order and
    this fails deterministically, rather than only when random ids happen to disagree.
    """
    roster = make_kt_roster()
    operative = make_kt_operative(kill_team=roster.kill_team)
    ids = [uuid.UUID(f"ffffffff-0000-0000-0000-00000000000{n}") for n in (3, 2, 1)]
    for row_id in ids:
        make_kt_roster_operative(roster=roster, operative=operative, id=row_id, position=0)

    ascending = sorted(ids)
    session.expire_all()

    # The relationship, as a roster detail read uses it.
    assert [row.id for row in _service(session).get_roster(roster.id).operatives] == ascending
    # The service's own reader, which has always ordered by both.
    assert [row.id for row in _service(session).list_roster_operatives(roster.id)] == ascending


def test_a_move_leaves_a_tie_and_a_gap_rather_than_renumbering(
    session, make_kt_roster, make_kt_operative, make_kt_roster_operative
):
    # The behaviour decision #53 chose, pinned so a later renumbering change is a
    # deliberate one: moving the last row to 0 gives two rows at 0 and vacates 2.
    roster = make_kt_roster()
    operative = make_kt_operative(kill_team=roster.kill_team)
    rows = [make_kt_roster_operative(roster=roster, operative=operative, position=n) for n in range(3)]

    _service(session).move_operative(roster.id, rows[2].id, 0)

    positions = [row.position for row in _service(session).list_roster_operatives(roster.id)]
    assert positions == [0, 0, 1]
