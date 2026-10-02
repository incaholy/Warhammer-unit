"""Service tests for the Kill Team catalog (`KillTeamService`), which is read-only.

The load-bearing one is `test_reading_a_team_costs_the_same_number_of_queries_however_big_it_is`:
the detail read is the only place in this service where getting the eager loading wrong
is invisible in the result and expensive in production.
"""

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app.core.services.errors import NotFoundError
from app.core.services.service_killteam import KillTeamService


@contextmanager
def counting_queries(session):
    """Count the SQL statements a block issues.

    Attached to the session's engine rather than wrapping the service, so it counts
    what the database is actually asked -- including the lazy loads a missing
    `selectinload` would add, which is the whole point.
    """
    statements: list[str] = []
    engine = session.get_bind()

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def _service(session):
    return KillTeamService(session)


# --- factions -------------------------------------------------------------


def test_factions_come_back_alphabetically(session, make_kt_faction):
    # Alphabetical is the only order there is: a faction is a way to FIND a team
    # (decision #12) and carries no `position`.
    make_kt_faction(name="Tyranids")
    make_kt_faction(name="Aeldari")
    make_kt_faction(name="Necrons")

    names = [faction.name for faction in _service(session).list_kt_factions()]

    assert names == ["Aeldari", "Necrons", "Tyranids"]


def test_factions_page_and_count_independently(session, make_kt_faction):
    for name in ("Aeldari", "Necrons", "Tyranids"):
        make_kt_faction(name=name)
    service = _service(session)

    page = service.list_kt_factions(limit=2, offset=1)

    assert [faction.name for faction in page] == ["Necrons", "Tyranids"]
    # `total` ignores paging, which is what the Page envelope promises.
    assert service.count_kt_factions() == 3


# --- kill teams -----------------------------------------------------------


def test_kill_teams_can_be_narrowed_to_one_faction(session, make_kt_faction, make_kill_team):
    tyranids = make_kt_faction(name="Tyranids")
    necrons = make_kt_faction(name="Necrons")
    make_kill_team(faction=tyranids, name="Raveners")
    make_kill_team(faction=tyranids, name="Gellerpox Infected")
    make_kill_team(faction=necrons, name="Hierotek Circle")
    service = _service(session)

    assert [team.name for team in service.list_kill_teams(faction_id=tyranids.id)] == [
        "Gellerpox Infected",
        "Raveners",
    ]
    assert service.count_kill_teams(faction_id=tyranids.id) == 2
    assert service.count_kill_teams() == 3


def test_an_unknown_faction_is_an_empty_page_not_an_error(session, make_kill_team):
    # A filter naming nothing is a legitimate empty result. Only a missing ROW that was
    # asked for by id is a 404, which is `get_kill_team`'s job.
    make_kill_team()
    service = _service(session)

    assert service.list_kill_teams(faction_id=uuid.uuid4()) == []
    assert service.count_kill_teams(faction_id=uuid.uuid4()) == 0


def test_a_listed_team_carries_its_faction_without_a_query_per_row(session, make_kt_faction, make_kill_team):
    # A listing names the faction ("Raveners — Tyranids"), so it is eager-loaded: lazily
    # it would be one query per row, which is the N+1 nobody notices on 48 rows in dev.
    # A faction EACH, deliberately: three teams sharing one would be lazy-loaded with a
    # single query and cached, so the N+1 would hide behind N=1 and this test would pass
    # with no eager load at all.
    for name, faction in (
        ("Raveners", "Tyranids"),
        ("Gellerpox Infected", "Nurgle"),
        ("Hierotek Circle", "Necrons"),
    ):
        make_kill_team(faction=make_kt_faction(name=faction), name=name)
    session.expunge_all()  # the factories left these loaded; measure a cold read

    with counting_queries(session) as statements:
        teams = _service(session).list_kill_teams()
        names = [(team.name, team.faction.name) for team in teams]

    assert names == [
        ("Gellerpox Infected", "Nurgle"),
        ("Hierotek Circle", "Necrons"),
        ("Raveners", "Tyranids"),
    ]
    # One for the teams, one for the factions behind them -- not one per team.
    assert len(statements) == 2


# --- one team, whole ------------------------------------------------------


def _furnish(team, *, operatives, rules, factories):
    """Give `team` a full page's worth of rows, so a detail read has work to do."""
    make_operative, make_weapon, make_ability, make_rule, make_ploy, make_equipment, make_srule = factories
    make_rule(kill_team=team, name="Burrow", position=0)
    make_ploy(kill_team=team, name="TUNNEL", position=0)
    make_equipment(kill_team=team, name="Spike", position=0)
    for index in range(rules):
        make_srule(kill_team=team, position=index, depth=index and 1, text=f"line {index}")
    for index in range(operatives):
        operative = make_operative(kill_team=team, name=f"Operative {index}", position=index)
        make_weapon(operative=operative, name=f"Claw {index}", position=0)
        make_ability(operative=operative, name=f"Ability {index}", position=0)
    return team


@pytest.fixture
def furnish(
    make_kt_operative,
    make_kt_weapon,
    make_kt_ability,
    make_kill_team_rule,
    make_kt_ploy,
    make_kt_equipment,
    make_kt_selection_rule,
):
    factories = (
        make_kt_operative,
        make_kt_weapon,
        make_kt_ability,
        make_kill_team_rule,
        make_kt_ploy,
        make_kt_equipment,
        make_kt_selection_rule,
    )

    def _furnish_team(team, *, operatives, rules=3):
        return _furnish(team, operatives=operatives, rules=rules, factories=factories)

    return _furnish_team


def test_reading_a_team_brings_back_its_whole_page(session, make_kill_team, furnish):
    team_id = furnish(make_kill_team(name="Raveners"), operatives=3, rules=3).id
    session.expunge_all()  # the factories left these loaded; measure a cold read

    loaded = _service(session).get_kill_team(team_id)

    assert loaded.name == "Raveners"
    assert loaded.faction.name  # the faction travels with it
    assert [operative.name for operative in loaded.operatives] == [
        "Operative 0",
        "Operative 1",
        "Operative 2",
    ]
    assert [weapon.name for weapon in loaded.operatives[0].weapons] == ["Claw 0"]
    assert [ability.name for ability in loaded.operatives[0].abilities] == ["Ability 0"]
    assert [rule.name for rule in loaded.rules] == ["Burrow"]
    assert [ploy.name for ploy in loaded.ploys] == ["TUNNEL"]
    assert [item.name for item in loaded.equipment] == ["Spike"]
    # the composition as printed: text with an indent depth, nothing derived (#28)
    assert [(r.position, r.depth) for r in loaded.selection_rules] == [(0, 0), (1, 1), (2, 1)]


def test_reading_a_team_costs_the_same_number_of_queries_however_big_it_is(session, make_kill_team, furnish):
    # The reason this service exists rather than the router walking relationships: nine
    # queries flat, whatever the team's size. Lazy loading would be a query per operative
    # per collection -- a cost that never shows up in the result, only in production.
    small_id = furnish(make_kill_team(name="Small"), operatives=2, rules=3).id
    large_id = furnish(make_kill_team(name="Large"), operatives=12, rules=20).id
    service = _service(session)

    session.expunge_all()
    with counting_queries(session) as for_small:
        _touch_everything(service.get_kill_team(small_id))

    session.expunge_all()
    with counting_queries(session) as for_large:
        _touch_everything(service.get_kill_team(large_id))

    assert len(for_small) == len(for_large) == 9


def _touch_everything(team):
    """Walk every collection, so a missing eager load would issue its lazy query here."""
    assert team.faction.name is not None
    for collection in (team.rules, team.ploys, team.equipment, team.selection_rules):
        [row.id for row in collection]
    for operative in team.operatives:
        [weapon.id for weapon in operative.weapons]
        [ability.id for ability in operative.abilities]


def test_a_loaded_team_is_readable_once_the_session_is_done_with_it(session, make_kill_team, furnish):
    # The query count above cannot catch a missing eager load on a DIRECT collection: it
    # hangs off one team row, so lazy and eager both cost exactly one query -- the
    # difference is WHEN it runs. This is the difference that matters: a router serialises
    # the object after the request's session is finished with it, and a collection left to
    # lazy-load then raises DetachedInstanceError instead of returning rows.
    team_id = furnish(make_kill_team(name="Raveners"), operatives=2, rules=3).id
    session.expunge_all()

    team = _service(session).get_kill_team(team_id)
    session.expunge(team)  # as a closed request-scoped session would leave it

    assert team.faction.name
    assert [r.position for r in team.selection_rules] == [0, 1, 2]
    assert [r.name for r in team.rules] and [p.name for p in team.ploys]
    assert [e.name for e in team.equipment]
    assert all(o.weapons and o.abilities for o in team.operatives)


def test_reading_a_team_that_does_not_exist_is_a_not_found(session):
    # The only error this service raises. It inherits LookupError, so a service-level
    # test can catch either, and `app/main.py`'s one CodedError handler maps it to 404.
    with pytest.raises(NotFoundError):
        _service(session).get_kill_team(uuid.uuid4())

    with pytest.raises(LookupError):
        _service(session).get_kill_team(uuid.uuid4())


# --- the rows that belong to no team --------------------------------------


def test_universal_ploys_are_the_ones_no_team_owns(session, make_kt_ploy, make_kill_team):
    make_kt_ploy(kill_team=make_kill_team(), name="TEAM PLOY")
    make_kt_ploy(kill_team=None, name="COMMAND RE-ROLL")

    names = [ploy.name for ploy in _service(session).list_universal_ploys()]

    assert names == ["COMMAND RE-ROLL"]


def test_universal_equipment_is_the_list_no_team_owns(session, make_kt_equipment, make_kill_team):
    make_kt_equipment(kill_team=make_kill_team(), name="TEAM KIT")
    make_kt_equipment(kill_team=None, name="AMMO")

    names = [item.name for item in _service(session).list_universal_equipment()]

    assert names == ["AMMO"]
