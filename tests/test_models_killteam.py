"""Kill Team catalog models, slice 1: KTFaction → KillTeam → KillTeamRule.

These pin the rules KILLTEAM.md sets for the top of the tree — what is unique and
where, what a delete takes with it, and what it refuses — at the database level, so
they hold however a row is written (service, seed script, or admin route).
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.db.models_killteam import KillTeam, KillTeamRule, KTFaction


def test_faction_kill_team_rule_chain_links_both_ways(
    session, make_kt_faction, make_kill_team, make_kill_team_rule
):
    tyranids = make_kt_faction(name="Tyranids")
    raveners = make_kill_team(faction=tyranids, name="Raveners", operative_count=5)
    burrow = make_kill_team_rule(kill_team=raveners, name="Burrow")

    assert raveners.faction.name == "Tyranids"
    assert [k.name for k in tyranids.kill_teams] == ["Raveners"]
    assert burrow.kill_team.name == "Raveners"
    assert [r.name for r in raveners.rules] == ["Burrow"]


def test_faction_name_is_unique(session, make_kt_faction):
    make_kt_faction(name="Tyranids")
    session.add(KTFaction(name="Tyranids"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_kill_team_name_is_unique(session, make_kill_team):
    make_kill_team(name="Raveners")
    with pytest.raises(IntegrityError):
        make_kill_team(name="Raveners")


def test_same_rule_name_is_allowed_on_different_kill_teams(session, make_kill_team, make_kill_team_rule):
    # Decision #13: rules belong to the kill team, so two teams may each have a rule
    # of the same name — possibly worded differently.
    make_kill_team_rule(kill_team=make_kill_team(), name="Predatory Instincts")
    make_kill_team_rule(kill_team=make_kill_team(), name="Predatory Instincts")

    rules = session.exec(select(KillTeamRule).where(KillTeamRule.name == "Predatory Instincts"))
    assert len(rules.all()) == 2


def test_same_rule_name_twice_on_one_kill_team_is_rejected(session, make_kill_team, make_kill_team_rule):
    raveners = make_kill_team()
    make_kill_team_rule(kill_team=raveners, name="Burrow")
    with pytest.raises(IntegrityError):
        make_kill_team_rule(kill_team=raveners, name="Burrow")


def test_deleting_a_kill_team_deletes_its_rules(session, make_kill_team, make_kill_team_rule):
    raveners = make_kill_team()
    make_kill_team_rule(kill_team=raveners, name="Burrow")
    make_kill_team_rule(kill_team=raveners, name="Tunnel")

    session.delete(raveners)
    session.commit()

    assert session.exec(select(KillTeamRule)).all() == []


def test_deleting_a_faction_with_kill_teams_is_refused(session, make_kt_faction, make_kill_team):
    # A faction is a grouping; deleting one must not take its kill teams along.
    tyranids = make_kt_faction()
    make_kill_team(faction=tyranids)

    session.delete(tyranids)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    assert session.exec(select(KillTeam)).one().faction_id == tyranids.id


def test_deleting_an_unused_faction_is_allowed(session, make_kt_faction):
    faction = make_kt_faction()
    session.delete(faction)
    session.commit()

    assert session.exec(select(KTFaction)).all() == []


@pytest.mark.parametrize("count", [0, -1])
def test_operative_count_must_be_at_least_one(session, make_kill_team, count):
    with pytest.raises(IntegrityError):
        make_kill_team(operative_count=count)
