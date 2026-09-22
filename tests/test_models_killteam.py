"""Kill Team catalog models, slice 1: KTFaction → KillTeam → KillTeamRule.

These pin the rules KILLTEAM.md sets for the top of the tree — what is unique and
where, what a delete takes with it, and what it refuses — at the database level, so
they hold however a row is written (service, seed script, or admin route).
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.db.models_killteam import (
    KillTeam,
    KillTeamRule,
    KTAbility,
    KTEquipment,
    KTFaction,
    KTOperative,
    KTPloy,
    KTWeapon,
)


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


# ---- The datacard: operatives, their weapons and abilities (slice 2) ----


def test_an_operative_owns_its_weapons_and_abilities(
    session, make_kt_operative, make_kt_weapon, make_kt_ability
):
    prime = make_kt_operative(name="Ravener Prime", apl=3, move=7, save=4, wounds=10)
    make_kt_weapon(operative=prime, name="Toxic lunge", category="melee")
    make_kt_ability(operative=prime, name="Neuropredatory Crest")
    session.refresh(prime)

    assert [w.name for w in prime.weapons] == ["Toxic lunge"]
    assert [a.name for a in prime.abilities] == ["Neuropredatory Crest"]
    assert prime.weapons[0].operative.name == "Ravener Prime"


def test_operative_name_is_unique_per_kill_team_only(session, make_kill_team, make_kt_operative):
    raveners = make_kill_team()
    make_kt_operative(kill_team=raveners, name="Ravener Warrior")
    # The same name on another kill team is fine; twice on one is not.
    make_kt_operative(kill_team=make_kill_team(), name="Ravener Warrior")
    with pytest.raises(IntegrityError):
        make_kt_operative(kill_team=raveners, name="Ravener Warrior")


def test_max_per_roster_is_null_for_an_unlimited_operative(session, make_kt_operative):
    # Raveners: every specialist is capped at 1, Warriors are not capped at all --
    # still bounded by the kill team's operative_count.
    warrior = make_kt_operative(name="Ravener Warrior", max_per_roster=None)
    felltalon = make_kt_operative(name="Ravener Felltalon", max_per_roster=1)

    assert warrior.max_per_roster is None
    assert felltalon.max_per_roster == 1


def test_max_per_roster_of_zero_is_rejected(session, make_kt_operative):
    # "Cannot be taken" is said by leaving the operative off the list, not by a 0.
    with pytest.raises(IntegrityError):
        make_kt_operative(max_per_roster=0)


def test_required_marks_the_operative_a_roster_cannot_omit(session, make_kt_operative):
    assert make_kt_operative(name="Ravener Prime", required=True).required is True
    assert make_kt_operative(name="Ravener Warrior").required is False


def test_weapon_range_defaults_by_category(session, make_kt_weapon):
    # Decision #15. A 2024 profile prints no range, so the column has a defined
    # meaning instead of a blank: melee reaches 1, ranged 2.
    assert make_kt_weapon(category="melee").range == 1
    assert make_kt_weapon(category="range").range == 2


def test_a_printed_range_overrides_the_default(session, make_kt_weapon):
    # What the scraper does with a "Range 3" weapon rule (K2).
    spitter = make_kt_weapon(category="range", range=3, weapon_rules=["Range 3", "Poison"])
    assert spitter.range == 3
    assert "Range 3" in spitter.weapon_rules


def test_a_range_below_one_is_rejected_even_though_a_default_exists(session, make_kt_weapon):
    # A default only fires when the column is OMITTED, so the check is what holds an
    # explicit 0 from a future parser out.
    with pytest.raises(IntegrityError):
        make_kt_weapon(range=0)


def test_weapon_category_is_one_of_two_values(session, make_kt_weapon):
    with pytest.raises(IntegrityError):
        make_kt_weapon(category="thrown")


def test_damage_is_stored_as_normal_and_crit(session, make_kt_weapon):
    # The page prints "4/5"; splitting it once here beats parsing a string on every
    # read in the roster and the game tracker.
    claws = make_kt_weapon(normal_damage=4, crit_damage=5)
    assert (claws.normal_damage, claws.crit_damage) == (4, 5)


def test_deleting_an_operative_deletes_its_weapons_and_abilities(
    session, make_kt_operative, make_kt_weapon, make_kt_ability
):
    warrior = make_kt_operative()
    make_kt_weapon(operative=warrior)
    make_kt_ability(operative=warrior)

    session.delete(warrior)
    session.commit()

    assert session.exec(select(KTWeapon)).all() == []
    assert session.exec(select(KTAbility)).all() == []


def test_deleting_a_kill_team_cascades_through_operatives(
    session, make_kill_team, make_kt_operative, make_kt_weapon, make_kt_ability
):
    # Two levels down: the kill team names neither weapons nor abilities, and the
    # database walks the chain.
    raveners = make_kill_team()
    warrior = make_kt_operative(kill_team=raveners)
    make_kt_weapon(operative=warrior)
    make_kt_ability(operative=warrior)

    session.delete(raveners)
    session.commit()

    assert session.exec(select(KTOperative)).all() == []
    assert session.exec(select(KTWeapon)).all() == []
    assert session.exec(select(KTAbility)).all() == []


# ---- Ploys (slice 3) ----


def test_a_ploy_belongs_to_its_kill_team(session, make_kill_team, make_kt_ploy):
    raveners = make_kill_team(name="Raveners")
    ploy = make_kt_ploy(kill_team=raveners, name="Subterranean Assault", kind="strategy")

    assert ploy.kill_team.name == "Raveners"
    assert [p.name for p in raveners.ploys] == ["Subterranean Assault"]


def test_command_reroll_belongs_to_no_kill_team(session, make_kill_team, make_kt_ploy):
    # The one ploy every kill team can use is stored once, with no owner, rather
    # than copied onto each team.
    reroll = make_kt_ploy(kill_team=None, name="Command Re-roll", cp_cost=1)
    raveners = make_kill_team()

    assert reroll.kill_team_id is None
    assert reroll.kill_team is None
    # It is nobody's ploy, so it is not in a team's own list…
    assert raveners.ploys == []
    # …and a team's ploy list is "its own plus the universal ones".
    universal = session.exec(select(KTPloy).where(KTPloy.kill_team_id.is_(None))).all()
    assert [p.name for p in universal] == ["Command Re-roll"]


def test_a_universal_ploy_cannot_be_added_twice(session, make_kt_ploy):
    # A plain UNIQUE(kill_team_id, name) does NOT catch this: two NULLs are distinct
    # to the database, so a second "Command Re-roll" would be accepted. The partial
    # unique index is what refuses it.
    make_kt_ploy(kill_team=None, name="Command Re-roll")
    with pytest.raises(IntegrityError):
        make_kt_ploy(kill_team=None, name="Command Re-roll")


def test_a_team_may_name_a_ploy_after_a_universal_one(session, make_kt_ploy, make_kill_team):
    # The partial index only covers the universal rows, so a kill team is free to
    # carry a ploy of the same name; scoping still holds per team.
    make_kt_ploy(kill_team=None, name="Command Re-roll")
    make_kt_ploy(kill_team=make_kill_team(), name="Command Re-roll")

    assert len(session.exec(select(KTPloy)).all()) == 2


def test_ploy_name_is_unique_per_kill_team_only(session, make_kill_team, make_kt_ploy):
    raveners = make_kill_team()
    make_kt_ploy(kill_team=raveners, name="Predatory Bound")
    make_kt_ploy(kill_team=make_kill_team(), name="Predatory Bound")
    with pytest.raises(IntegrityError):
        make_kt_ploy(kill_team=raveners, name="Predatory Bound")


def test_ploy_kind_is_strategy_or_firefight(session, make_kt_ploy):
    assert make_kt_ploy(kind="strategy").kind == "strategy"
    assert make_kt_ploy(kind="firefight").kind == "firefight"
    with pytest.raises(IntegrityError):
        make_kt_ploy(kind="tactical")


def test_cp_cost_defaults_to_one_and_cannot_be_negative(session, make_kill_team, make_kt_ploy):
    default = KTPloy(kill_team_id=make_kill_team().id, name="Free-ish", kind="strategy", description="x")
    session.add(default)
    session.commit()
    session.refresh(default)
    assert default.cp_cost == 1

    with pytest.raises(IntegrityError):
        make_kt_ploy(cp_cost=-1)


def test_deleting_a_kill_team_takes_its_ploys_but_not_the_universal_one(
    session, make_kill_team, make_kt_ploy
):
    raveners = make_kill_team()
    make_kt_ploy(kill_team=raveners, name="Subterranean Assault")
    make_kt_ploy(kill_team=None, name="Command Re-roll")

    session.delete(raveners)
    session.commit()

    remaining = session.exec(select(KTPloy)).all()
    assert [p.name for p in remaining] == ["Command Re-roll"]


# ---- Equipment (slice 3) ----


def test_equipment_belongs_to_its_kill_team(session, make_kill_team, make_kt_equipment):
    raveners = make_kill_team(name="Raveners")
    item = make_kt_equipment(kill_team=raveners, name="Chromatospore Camouflage")

    assert item.kill_team.name == "Raveners"
    assert [e.name for e in raveners.equipment] == ["Chromatospore Camouflage"]


def test_universal_equipment_belongs_to_no_kill_team(session, make_kill_team, make_kt_equipment):
    # The universal list is available to every team, so it is stored once rather
    # than copied onto each -- the same shape as Command Re-roll.
    shared = make_kt_equipment(kill_team=None, name="Frag grenade")
    raveners = make_kill_team()

    assert shared.kill_team_id is None
    assert raveners.equipment == []
    universal = session.exec(select(KTEquipment).where(KTEquipment.kill_team_id.is_(None))).all()
    assert [e.name for e in universal] == ["Frag grenade"]


def test_universal_equipment_cannot_be_added_twice(session, make_kt_equipment):
    # UNIQUE(kill_team_id, name) does not catch this: two NULLs are distinct. The
    # partial unique index is what refuses it.
    make_kt_equipment(kill_team=None, name="Frag grenade")
    with pytest.raises(IntegrityError):
        make_kt_equipment(kill_team=None, name="Frag grenade")


def test_a_team_may_name_equipment_after_a_universal_entry(session, make_kill_team, make_kt_equipment):
    # The partial index covers only the universal rows, so this must be allowed.
    make_kt_equipment(kill_team=None, name="Frag grenade")
    make_kt_equipment(kill_team=make_kill_team(), name="Frag grenade")

    assert len(session.exec(select(KTEquipment)).all()) == 2


def test_equipment_name_is_unique_per_kill_team_only(session, make_kill_team, make_kt_equipment):
    raveners = make_kill_team()
    make_kt_equipment(kill_team=raveners, name="Acid Blood")
    make_kt_equipment(kill_team=make_kill_team(), name="Acid Blood")
    with pytest.raises(IntegrityError):
        make_kt_equipment(kill_team=raveners, name="Acid Blood")


def test_deleting_a_kill_team_keeps_the_universal_equipment(session, make_kill_team, make_kt_equipment):
    raveners = make_kill_team()
    make_kt_equipment(kill_team=raveners, name="Acid Blood")
    make_kt_equipment(kill_team=None, name="Frag grenade")

    session.delete(raveners)
    session.commit()

    assert [e.name for e in session.exec(select(KTEquipment)).all()] == ["Frag grenade"]
