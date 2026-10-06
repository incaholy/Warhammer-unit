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

    assert loaded.team.name == "Raveners"
    assert loaded.team.faction.name  # the faction travels with it
    assert [card.operative.name for card in loaded.operatives] == [
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

    # Still flat, and still nine: the team, its faction, one per collection, and one each
    # for all the operatives' weapons and abilities grouped by `operative_id` in Python.
    # The shape changed from `selectinload` to per-collection queries (#55 needs the
    # catalog to filter where a roster read must not) and the COUNT did not.
    assert len(for_small) == len(for_large) == 9


def _touch_everything(detail):
    """Walk everything the detail carries, so any lazy load would issue its query here."""
    assert detail.team.faction.name is not None
    for collection in (detail.rules, detail.ploys, detail.equipment, detail.selection_rules):
        [row.id for row in collection]
    for card in detail.operatives:
        [weapon.id for weapon in card.weapons]
        [ability.id for ability in card.abilities]


def test_a_loaded_team_is_readable_once_the_session_is_done_with_it(session, make_kill_team, furnish):
    # The query count above cannot catch a missing eager load on a DIRECT collection: it
    # hangs off one team row, so lazy and eager both cost exactly one query -- the
    # difference is WHEN it runs. This is the difference that matters: a router serialises
    # the object after the request's session is finished with it, and a collection left to
    # lazy-load then raises DetachedInstanceError instead of returning rows.
    team_id = furnish(make_kill_team(name="Raveners"), operatives=2, rules=3).id
    session.expunge_all()

    detail = _service(session).get_kill_team(team_id)
    session.expunge_all()  # as a closed request-scoped session would leave it

    # Nothing here can lazy-load: the detail holds the rows in plain lists rather than
    # relationships, which is a stronger guarantee than the eager loading it replaced.
    # `team.faction` is the one relationship left, and it is still eager-loaded.
    assert detail.team.faction.name
    assert [r.position for r in detail.selection_rules] == [0, 1, 2]
    assert [r.name for r in detail.rules] and [p.name for p in detail.ploys]
    assert [e.name for e in detail.equipment]
    assert all(card.weapons and card.abilities for card in detail.operatives)


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


# --- the composition on its own --------------------------------------------


def test_the_composition_can_be_read_without_the_datacards(session, make_kill_team, make_kt_selection_rule):
    # The detail already carries these (#47); this is for a reader that wants the printed
    # rules alone. In position order, because that plus depth IS the printed section.
    team = make_kill_team()
    make_kt_selection_rule(kill_team=team, position=1, depth=1, text="SENTINEL")
    make_kt_selection_rule(kill_team=team, position=0, depth=0, text="4 HOLLOW operatives:")

    rules = _service(session).list_selection_rules(team.id)

    assert [(r.position, r.text) for r in rules] == [(0, "4 HOLLOW operatives:"), (1, "SENTINEL")]


def test_a_team_with_no_composition_reads_as_empty(session, make_kill_team):
    assert _service(session).list_selection_rules(make_kill_team().id) == []


def test_reading_the_composition_of_a_team_that_does_not_exist_is_a_not_found(session):
    # "No composition" and "no such team" are different answers, and only one is a 404.
    with pytest.raises(NotFoundError):
        _service(session).list_selection_rules(uuid.uuid4())


# --- operatives across teams -----------------------------------------------


def test_operatives_can_be_narrowed_to_one_team(session, make_kill_team, make_kt_operative):
    a, b = make_kill_team(name="A"), make_kill_team(name="B")
    make_kt_operative(kill_team=a, name="Alpha One", position=0)
    make_kt_operative(kill_team=b, name="Beta One", position=0)
    service = _service(session)

    assert [c.operative.name for c in service.list_operatives(kill_team_id=a.id)] == ["Alpha One"]
    assert service.count_operatives(kill_team_id=a.id) == 1
    assert service.count_operatives() == 2


def test_a_keyword_filter_matches_the_whole_keyword_and_not_a_prefix(
    session, make_kill_team, make_kt_operative
):
    # The reason this is a containment test and not a LIKE: the pages print `GUN` and
    # `SERVITOR` as two keywords on one datacard and `GUN SERVITOR` as one on another, so
    # they are genuinely different keywords and a substring match would conflate them.
    team = make_kill_team()
    make_kt_operative(kill_team=team, name="Battleclade Gunner", keywords=["GUN", "SERVITOR"])
    make_kt_operative(kill_team=team, name="Requisitioned Servitor", keywords=["GUN SERVITOR"])
    service = _service(session)

    assert [c.operative.name for c in service.list_operatives(keyword="GUN")] == ["Battleclade Gunner"]
    assert [c.operative.name for c in service.list_operatives(keyword="GUN SERVITOR")] == [
        "Requisitioned Servitor"
    ]
    assert service.count_operatives(keyword="GUN") == 1


def test_a_keyword_filter_is_upper_cased_the_way_a_datacard_prints_it(
    session, make_kill_team, make_kt_operative
):
    make_kt_operative(kill_team=make_kill_team(), name="Leader", keywords=["LEADER"])

    assert [c.operative.name for c in _service(session).list_operatives(keyword="leader")] == ["Leader"]


def test_a_keyword_matching_nothing_is_an_empty_result(session, make_kill_team, make_kt_operative):
    make_kt_operative(kill_team=make_kill_team(), keywords=["LEADER"])
    service = _service(session)

    assert service.list_operatives(keyword="NOBODY") == []
    assert service.count_operatives(keyword="NOBODY") == 0


def test_a_listed_operative_brings_its_datacard_with_it(
    session, make_kill_team, make_kt_operative, make_kt_weapon, make_kt_ability
):
    # An operative without its weapons and abilities is not much of an answer, and lazily
    # it would be two queries per row.
    #
    # TWO operatives, not one: with a single row, lazy loading costs the same three
    # queries as eager (one per collection), so the assertion could not fail. An N+1
    # hides whenever N is 1 -- the same trap as the detail read's query count.
    team = make_kill_team()
    for name in ("Warden", "Sentinel"):
        operative = make_kt_operative(kill_team=team, name=name)
        make_kt_weapon(operative=operative, name=f"{name}'s blade")
        make_kt_ability(operative=operative, name=f"{name}'s vigil")
    session.expunge_all()

    with counting_queries(session) as statements:
        cards = _service(session).list_operatives()
        _ = [(w.name, a.name) for c in cards for w in c.weapons for a in c.abilities]

    assert sorted(c.operative.name for c in cards) == ["Sentinel", "Warden"]
    # Operatives, then ONE query for all their weapons and one for all their abilities.
    # The count survived the move from `selectinload` to grouped queries -- which #55
    # forced, because a withdrawn PROFILE has to be filtered and the relationships are
    # shared with the roster read.
    assert len(statements) == 3


# --- withdrawn rows are hidden from the catalog (#55, K7) -------------------


def _withdraw(session, row):
    row.withdrawn = True
    session.add(row)
    session.commit()
    return row


def test_the_catalog_hides_a_withdrawn_team_and_its_count_agrees(session, make_kill_team, make_kt_faction):
    """A listing whose total counts rows the page hides is worse than either alone.

    So the filter sits where the listing and the count share it, not in both callers.
    """
    faction = make_kt_faction(name="Tyranids")
    make_kill_team(faction=faction, name="Raveners")
    gone = make_kill_team(faction=faction, name="Withdrawn Team")
    service = _service(session)
    assert len(service.list_kill_teams()) == 2 and service.count_kill_teams() == 2

    _withdraw(session, gone)

    assert [t.name for t in service.list_kill_teams()] == ["Raveners"]
    assert service.count_kill_teams() == 1


def test_the_catalog_hides_a_withdrawn_operative_and_its_count_agrees(
    session, make_kill_team, make_kt_operative
):
    team = make_kill_team(name="Raveners")
    make_kt_operative(kill_team=team, name="Warrior", position=0)
    gone = make_kt_operative(kill_team=team, name="Withdrawn Warrior", position=1)
    service = _service(session)
    assert service.count_operatives() == 2

    _withdraw(session, gone)

    assert [c.operative.name for c in service.list_operatives()] == ["Warrior"]
    assert service.count_operatives() == 1


def test_a_withdrawn_faction_leaves_the_faction_list(session, make_kt_faction):
    make_kt_faction(name="Tyranids")
    gone = make_kt_faction(name="Withdrawn Faction")
    service = _service(session)
    assert service.count_kt_factions() == 2

    _withdraw(session, gone)

    assert [f.name for f in service.list_kt_factions()] == ["Tyranids"]
    assert service.count_kt_factions() == 1


def test_a_withdrawn_universal_row_leaves_the_universal_lists(session, make_kt_ploy, make_kt_equipment):
    make_kt_ploy(kill_team=None, name="Command Re-roll", position=0)
    gone_ploy = make_kt_ploy(kill_team=None, name="Withdrawn Ploy", position=1)
    make_kt_equipment(kill_team=None, name="Frag Grenade", position=0)
    gone_kit = make_kt_equipment(kill_team=None, name="Withdrawn Kit", position=1)
    service = _service(session)

    _withdraw(session, gone_ploy)
    _withdraw(session, gone_kit)

    assert [p.name for p in service.list_universal_ploys()] == ["Command Re-roll"]
    assert [e.name for e in service.list_universal_equipment()] == ["Frag Grenade"]


def test_a_team_detail_hides_withdrawn_rows_at_every_level(
    session,
    make_kill_team,
    make_kt_operative,
    make_kt_weapon,
    make_kt_ability,
    make_kill_team_rule,
    make_kt_ploy,
    make_kt_equipment,
):
    """Every collection, including the ones NESTED under an operative.

    The nested two are the reason `get_kill_team` stopped using `selectinload`: a
    relationship-level filter would have reached the roster read too, so a weapon the
    source dropped is filtered by the query that fetches it instead.
    """
    team = make_kill_team(name="Raveners")
    make_kill_team_rule(kill_team=team, name="Burrow", position=0)
    gone_rule = make_kill_team_rule(kill_team=team, name="Withdrawn Rule", position=1)
    make_kt_ploy(kill_team=team, name="Tunnel", position=0)
    gone_ploy = make_kt_ploy(kill_team=team, name="Withdrawn Ploy", position=1)
    make_kt_equipment(kill_team=team, name="Spike", position=0)
    gone_kit = make_kt_equipment(kill_team=team, name="Withdrawn Kit", position=1)
    card = make_kt_operative(kill_team=team, name="Warrior", position=0)
    gone_card = make_kt_operative(kill_team=team, name="Withdrawn Warrior", position=1)
    make_kt_weapon(operative=card, name="Claws", position=0)
    gone_weapon = make_kt_weapon(operative=card, name="Withdrawn Claws", position=1)
    make_kt_ability(operative=card, name="Pounce", position=0)
    gone_ability = make_kt_ability(operative=card, name="Withdrawn Ability", position=1)
    for row in (gone_rule, gone_ploy, gone_kit, gone_card, gone_weapon, gone_ability):
        _withdraw(session, row)
    team_id = team.id  # before the expunge, or reading it is a DetachedInstanceError
    session.expunge_all()

    detail = _service(session).get_kill_team(team_id)

    assert [r.name for r in detail.rules] == ["Burrow"]
    assert [p.name for p in detail.ploys] == ["Tunnel"]
    assert [e.name for e in detail.equipment] == ["Spike"]
    assert [c.operative.name for c in detail.operatives] == ["Warrior"]
    assert [w.name for w in detail.operatives[0].weapons] == ["Claws"]
    assert [a.name for a in detail.operatives[0].abilities] == ["Pounce"]


def test_the_composition_is_never_filtered_because_it_has_no_flag(
    session, make_kill_team, make_kt_selection_rule
):
    # #44 replaces a composition as a whole, so it already loses whatever the source
    # dropped. A filter here would have nothing to read.
    team = make_kill_team(name="Raveners")
    for n in range(3):
        make_kt_selection_rule(kill_team=team, position=n)
    team_id = team.id
    session.expunge_all()

    detail = _service(session).get_kill_team(team_id)

    assert [r.position for r in detail.selection_rules] == [0, 1, 2]


def test_a_withdrawn_team_is_not_readable_by_id_either(session, make_kill_team, make_kt_faction):
    """An audit caught this: the LISTING hid a withdrawn team and this read served it.

    "The catalog hides a withdrawn row" was true of five reads out of six, which is the
    kind of claim that is worse than an obvious gap -- it reads as settled.
    """
    team = make_kill_team(faction=make_kt_faction(name="Tyranids"), name="Raveners")
    team_id = team.id
    service = _service(session)
    assert service.get_kill_team(team_id).team.name == "Raveners"

    _withdraw(session, team)
    session.expunge_all()

    with pytest.raises(NotFoundError):
        _service(session).get_kill_team(team_id)


def test_a_withdrawn_teams_composition_is_not_readable_either(
    session, make_kill_team, make_kt_selection_rule
):
    # The composition route is gated on a LIVE team, through the same `_live_team` the
    # detail uses, so the two cannot drift apart.
    team = make_kill_team(name="Raveners")
    make_kt_selection_rule(kill_team=team, position=0)
    team_id = team.id
    assert _service(session).list_selection_rules(team_id)

    _withdraw(session, team)
    session.expunge_all()

    with pytest.raises(NotFoundError):
        _service(session).list_selection_rules(team_id)


def test_the_operative_listing_hides_a_withdrawn_profile_too(
    session, make_kill_team, make_kt_operative, make_kt_weapon, make_kt_ability
):
    """The second half the audit found: the listing served withdrawn WEAPONS.

    The row was filtered and its profiles were not, because the listing eager-loaded them
    through the relationships -- which the roster read shares and must not filter. Both
    reads now go through `_live_profiles`, so a datacard cannot depend on which route
    asked for it.
    """
    team = make_kill_team(name="Raveners")
    card = make_kt_operative(kill_team=team, name="Warrior", position=0)
    make_kt_weapon(operative=card, name="Claws", position=0)
    gone_weapon = make_kt_weapon(operative=card, name="Withdrawn Claws", position=1)
    make_kt_ability(operative=card, name="Pounce", position=0)
    gone_ability = make_kt_ability(operative=card, name="Withdrawn Ability", position=1)
    _withdraw(session, gone_weapon)
    _withdraw(session, gone_ability)
    session.expunge_all()

    listed = _service(session).list_operatives()

    assert [c.operative.name for c in listed] == ["Warrior"]
    assert [w.name for w in listed[0].weapons] == ["Claws"]
    assert [a.name for a in listed[0].abilities] == ["Pounce"]


def test_both_catalog_reads_agree_on_one_datacard(session, make_kill_team, make_kt_operative, make_kt_weapon):
    # The property the shared `_live_profiles` exists for: a client must not get a
    # different datacard depending on whether it asked for the team or the listing.
    team = make_kill_team(name="Raveners")
    card = make_kt_operative(kill_team=team, name="Warrior", position=0)
    make_kt_weapon(operative=card, name="Claws", position=0)
    gone = make_kt_weapon(operative=card, name="Withdrawn Claws", position=1)
    _withdraw(session, gone)
    team_id = team.id
    session.expunge_all()
    service = _service(session)

    from_detail = service.get_kill_team(team_id).operatives[0]
    from_listing = service.list_operatives(kill_team_id=team_id)[0]

    assert [w.name for w in from_detail.weapons] == [w.name for w in from_listing.weapons]


# --- the orderings and pagings nothing pinned -------------------------------
#
# Every assertion below inserts rows so that printed order, alphabetical order and
# insertion order all DISAGREE. The audit found six `ORDER BY`s and two pagings in this
# service that no test could fail, and several of the orderings that WERE asserted were
# satisfied by alphabetical too -- so a relationship switched to `.name` stayed green.


def _named(rows):
    return [row.name for row in rows]


def test_the_universal_lists_come_back_in_printed_order(session, make_kt_ploy, make_kt_equipment):
    """`(position, name)`, not alphabetical and not insertion order.

    Every other test has exactly ONE universal row of each kind, which cannot fail an
    ordering assertion. These are inserted so all three orders differ.
    """
    service = _service(session)
    for name, position in (("Ash", 1), ("Mire", 2), ("Zeal", 0)):  # inserted out of order
        make_kt_ploy(kill_team=None, name=name, position=position)
        make_kt_equipment(kill_team=None, name=name, position=position)

    assert _named(service.list_universal_ploys()) == ["Zeal", "Ash", "Mire"]
    assert _named(service.list_universal_equipment()) == ["Zeal", "Ash", "Mire"]
    assert _named(service.list_universal_ploys()) != sorted(_named(service.list_universal_ploys()))


def test_operatives_are_ordered_by_team_then_printed_position(
    session, make_kt_faction, make_kill_team, make_kt_operative
):
    """The order the docstring and the router both promise, and nothing asserted.

    Two teams, each with cards whose printed order is the reverse of alphabetical, so a
    missing `ORDER BY` or one switched to `name` both fail here.
    """
    service = _service(session)
    faction = make_kt_faction(name="Tyranids")
    # Fixed ids, so "ordered by team" is a definite sequence rather than whichever team
    # a random uuid4 happened to sort first.
    low = make_kill_team(faction=faction, name="Aaa Team", id=uuid.UUID(int=1))
    high = make_kill_team(faction=faction, name="Bbb Team", id=uuid.UUID(int=2))
    # Inserted high-team-first, and within each team position-1 before position-0, so
    # INSERTION order disagrees with the asserted order on both keys. Without that,
    # dropping the `ORDER BY` is invisible on SQLite, which returns rows in rowid order.
    for team, names in ((high, ("Mire", "Ash")), (low, ("Zealot", "Acolyte"))):
        for name, position in ((names[1], 1), (names[0], 0)):
            make_kt_operative(kill_team=team, name=name, position=position)

    cards = [card.operative for card in service.list_operatives()]

    assert _named(cards) == ["Zealot", "Acolyte", "Mire", "Ash"]
    assert [card.kill_team_id for card in cards] == [low.id, low.id, high.id, high.id]
    assert _named(cards) != sorted(_named(cards))


def test_the_operative_and_team_listings_page_independently_of_their_counts(
    session, make_kt_faction, make_kill_team, make_kt_operative
):
    """`limit` and `offset` reach the query on both listings.

    `test_factions_page_and_count_independently` exists for factions; the teams and the
    cross-team operative listing had no equivalent, so dropping either `.offset()` or
    either `.limit()` was invisible.
    """
    service = _service(session)
    faction = make_kt_faction(name="Tyranids")
    teams = [make_kill_team(faction=faction, name=f"Team {letter}") for letter in "ABCD"]
    for index, team in enumerate(teams):
        make_kt_operative(kill_team=team, name=f"Warrior {index}", position=index)

    assert len(service.list_kill_teams(limit=2)) == 2
    assert len(service.list_kill_teams(limit=2, offset=2)) == 2
    assert service.list_kill_teams(limit=2)[0].id != service.list_kill_teams(limit=2, offset=2)[0].id
    assert service.count_kill_teams() == 4

    assert len(service.list_operatives(limit=2)) == 2
    assert len(service.list_operatives(limit=2, offset=2)) == 2
    first_page = [c.operative.id for c in service.list_operatives(limit=2)]
    second_page = [c.operative.id for c in service.list_operatives(limit=2, offset=2)]
    assert set(first_page).isdisjoint(second_page), "offset did not move the window"
    assert service.count_operatives() == 4


def test_a_listed_card_carries_its_profiles_in_printed_order(
    session, make_kill_team, make_kt_operative, make_kt_weapon, make_kt_ability
):
    """`_live_profiles` orders by `position`, on the LISTING as well as the detail.

    Asserted against names whose printed order is the reverse of alphabetical, because
    everywhere else the two coincide -- which is how the ability relationship survived
    being switched to `order_by: name`.
    """
    service = _service(session)
    card = make_kt_operative(kill_team=make_kill_team(name="Raveners"), name="Warrior", position=0)
    # Inserted LAST-first, so insertion order is the reverse of printed order.
    for name, position in (("Ember Claw", 1), ("Zealous Strike", 0)):
        make_kt_weapon(operative=card, name=name, position=position)
        make_kt_ability(operative=card, name=name, position=position)

    listed = service.list_operatives()[0]
    assert _named(listed.weapons) == ["Zealous Strike", "Ember Claw"]
    assert _named(listed.abilities) == ["Zealous Strike", "Ember Claw"]
    assert _named(listed.abilities) != sorted(_named(listed.abilities))


def test_a_detail_read_orders_every_collection_it_carries(
    session,
    make_kt_faction,
    make_kill_team,
    make_kill_team_rule,
    make_kt_ploy,
    make_kt_equipment,
    make_kt_operative,
    make_kt_selection_rule,
):
    """`get_kill_team`'s own `ORDER BY`s, including the composition's.

    `list_selection_rules` is covered; the detail's separate copy of that query was not,
    and neither were the detail's rules, ploys, equipment or operative orderings.
    """
    service = _service(session)
    team = make_kill_team(faction=make_kt_faction(name="Tyranids"), name="Raveners")
    # Inserted middle, last, first -- so insertion order matches neither the printed
    # order nor alphabetical, and dropping an `ORDER BY` fails rather than coinciding.
    for name, position in (("Ash", 1), ("Mire", 2), ("Zeal", 0)):
        make_kill_team_rule(kill_team=team, name=name, position=position)
        make_kt_ploy(kill_team=team, name=name, position=position)
        make_kt_equipment(kill_team=team, name=name, position=position)
        make_kt_operative(kill_team=team, name=name, position=position)
    for text, position in (("A leader", 1), ("Mixed", 2), ("Zero to six", 0)):
        make_kt_selection_rule(kill_team=team, text=text, position=position)

    detail = service.get_kill_team(team.id)

    for collection in (detail.rules, detail.ploys, detail.equipment):
        assert _named(collection) == ["Zeal", "Ash", "Mire"]
        assert _named(collection) != sorted(_named(collection))
    assert _named([card.operative for card in detail.operatives]) == ["Zeal", "Ash", "Mire"]
    assert [rule.text for rule in detail.selection_rules] == ["Zero to six", "A leader", "Mixed"]
