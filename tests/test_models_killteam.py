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
    KTSelectionList,
    KTSelectionOption,
    KTSelectionRestriction,
    KTWeapon,
)


def test_faction_kill_team_rule_chain_links_both_ways(
    session, make_kt_faction, make_kill_team, make_kill_team_rule
):
    tyranids = make_kt_faction(name="Tyranids")
    raveners = make_kill_team(faction=tyranids, name="Raveners")
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
    with pytest.raises(IntegrityError, match="ck_kt_weapon_range"):
        make_kt_weapon(range=0)


def test_weapon_category_is_one_of_two_values(session, make_kt_weapon):
    with pytest.raises(IntegrityError, match="ck_kt_weapon_category"):
        make_kt_weapon(category="thrown")


def test_one_weapon_name_can_appear_once_per_category(session, make_kt_operative, make_kt_weapon):
    # Sanctifiers' Missionary carries "Brazier of holy fire" as a ranged profile
    # (Saturate, Torrent) and a melee one (Shock). One weapon, two profiles -- and the
    # only case across the 48 teams, which is how a unique constraint per operative
    # would have turned into a failed seed for exactly one team.
    missionary = make_kt_operative(name="Sanctifier Missionary")
    make_kt_weapon(operative=missionary, name="Brazier of holy fire", category="range")
    make_kt_weapon(operative=missionary, name="Brazier of holy fire", category="melee")

    session.refresh(missionary)
    assert sorted(w.category for w in missionary.weapons) == ["melee", "range"]


def test_the_same_name_twice_in_one_category_is_still_rejected(session, make_kt_weapon, make_kt_operative):
    operative = make_kt_operative()
    make_kt_weapon(operative=operative, name="Bolt pistol", category="range")
    with pytest.raises(IntegrityError):
        make_kt_weapon(operative=operative, name="Bolt pistol", category="range")


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
    with pytest.raises(IntegrityError, match="ck_kt_ploy_kind"):
        make_kt_ploy(kind="tactical")


def test_cp_cost_defaults_to_one_and_cannot_be_negative(session, make_kill_team, make_kt_ploy):
    default = KTPloy(kill_team_id=make_kill_team().id, name="Free-ish", kind="strategy", description="x")
    session.add(default)
    session.commit()
    session.refresh(default)
    assert default.cp_cost == 1

    with pytest.raises(IntegrityError, match="ck_kt_ploy_cp_cost"):
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


# ---- Selection lists: how a roster is built (decision #16) ----


def test_a_list_offers_operatives_within_a_budget(
    session, make_kill_team, make_kt_operative, make_kt_selection_list, make_kt_selection_option
):
    # Raveners: "1 RAVENER PRIME operative", then "4 RAVENER operatives selected from
    # the following list". Two lists, each with its own budget.
    raveners = make_kill_team(name="Raveners")
    prime = make_kt_operative(kill_team=raveners, name="Ravener Prime")
    warrior = make_kt_operative(kill_team=raveners, name="Ravener Warrior")

    leaders = make_kt_selection_list(
        kill_team=raveners, label="1 RAVENER PRIME operative", budget=1, position=0
    )
    body = make_kt_selection_list(kill_team=raveners, label="4 RAVENER operatives", budget=4, position=1)
    make_kt_selection_option(selection_list=leaders, operative=prime)
    make_kt_selection_option(selection_list=body, operative=warrior)
    session.refresh(raveners)

    assert [lst.budget for lst in raveners.selection_lists] == [1, 4]
    assert leaders.options[0].operative.name == "Ravener Prime"
    # A budget of 1 over a single option IS "required" -- no flag needed for it.
    assert (leaders.budget, len(leaders.options)) == (1, 1)


def test_an_option_defaults_to_one_selection_for_one_model(session, make_kt_selection_option):
    option = make_kt_selection_option()

    assert (option.cost, option.models) == (1, 1)
    assert option.max_selections is None  # no limit beyond the list's budget


def test_an_option_may_cost_more_than_one_selection(session, make_kt_selection_option):
    # Brood Brother: "MAGUS (counts as two selections)". A headcount could not say
    # this, which is why operative_count is gone.
    assert make_kt_selection_option(cost=2).cost == 2


def test_an_option_may_put_two_models_on_the_table_for_one_selection(session, make_kt_selection_option):
    # "2 PSYCHIC FAMILIAR operatives (still counts as one selection)" -- a different
    # axis from cost, so its own column.
    option = make_kt_selection_option(cost=1, models=2)

    assert (option.cost, option.models) == (1, 2)


def test_a_cap_belongs_to_the_option_not_the_operative(
    session, make_kill_team, make_kt_operative, make_kt_selection_list, make_kt_selection_option
):
    # The cap is stated by the list ("other than WARRIOR, each operative once"), so
    # the same operative can be offered on different terms by two lists.
    team = make_kill_team()
    warrior = make_kt_operative(kill_team=team, name="Warrior")
    strict = make_kt_selection_list(kill_team=team, budget=2, position=0)
    loose = make_kt_selection_list(kill_team=team, budget=4, position=1)

    once = make_kt_selection_option(selection_list=strict, operative=warrior, max_selections=1)
    freely = make_kt_selection_option(selection_list=loose, operative=warrior)

    assert once.max_selections == 1
    assert freely.max_selections is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("cost", 0), ("models", 0), ("max_selections", 0)],
)
def test_an_option_cannot_be_taken_zero_ways(session, make_kt_selection_option, field, value):
    # The constraint follows the field, so the assertion names it rather than accepting any
    # IntegrityError -- which is how a whole missing CHECK stayed invisible elsewhere.
    with pytest.raises(IntegrityError, match=f"ck_kt_selection_option_{field}"):
        make_kt_selection_option(**{field: value})


def test_a_budget_of_zero_is_rejected(session, make_kt_selection_list):
    # A list nobody can spend on is not a list.
    with pytest.raises(IntegrityError, match="ck_kt_selection_list_budget"):
        make_kt_selection_list(budget=0)


def test_two_lists_cannot_share_a_position(session, make_kill_team, make_kt_selection_list):
    team = make_kill_team()
    make_kt_selection_list(kill_team=team, position=0)
    with pytest.raises(IntegrityError):
        make_kt_selection_list(kill_team=team, position=0)


def test_one_list_offers_an_operative_once(
    session, make_kill_team, make_kt_operative, make_kt_selection_list, make_kt_selection_option
):
    team = make_kill_team()
    operative = make_kt_operative(kill_team=team)
    listing = make_kt_selection_list(kill_team=team)
    make_kt_selection_option(selection_list=listing, operative=operative)

    with pytest.raises(IntegrityError):
        make_kt_selection_option(selection_list=listing, operative=operative)


def test_deleting_a_kill_team_takes_its_lists_and_options(
    session, make_kill_team, make_kt_selection_list, make_kt_selection_option
):
    team = make_kill_team()
    listing = make_kt_selection_list(kill_team=team)
    make_kt_selection_option(selection_list=listing)

    session.delete(team)
    session.commit()

    assert session.exec(select(KTSelectionList)).all() == []
    assert session.exec(select(KTSelectionOption)).all() == []


def test_deleting_an_operative_withdraws_it_from_every_list(
    session, make_kill_team, make_kt_operative, make_kt_selection_list, make_kt_selection_option
):
    # An option pointing at an operative that no longer exists would offer a roster
    # something it cannot field.
    team = make_kill_team()
    operative = make_kt_operative(kill_team=team)
    make_kt_selection_option(
        selection_list=make_kt_selection_list(kill_team=team, position=0), operative=operative
    )
    make_kt_selection_option(
        selection_list=make_kt_selection_list(kill_team=team, position=1), operative=operative
    )

    session.delete(operative)
    session.commit()

    assert session.exec(select(KTSelectionOption)).all() == []
    assert len(session.exec(select(KTSelectionList)).all()) == 2  # the lists remain


def test_an_option_cannot_offer_another_kill_teams_operative(
    session, make_kill_team, make_kt_operative, make_kt_selection_list
):
    # Two plain foreign keys only promise "some list" and "some operative", so nothing
    # stopped a list offering an operative from a different kill team -- a roster would
    # then be offered something it cannot field. The option carries `kill_team_id` and
    # reaches both parents through it, so the database refuses the mismatch.
    ravener_list = make_kt_selection_list(kill_team=make_kill_team(name="Raveners"))
    stranger = make_kt_operative(kill_team=make_kill_team(name="Novitiates"))

    session.add(
        KTSelectionOption(
            kill_team_id=ravener_list.kill_team_id,
            selection_list_id=ravener_list.id,
            operative_id=stranger.id,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_an_options_team_cannot_disagree_with_its_list(
    session, make_kill_team, make_kt_operative, make_kt_selection_list
):
    # The denormalised column is kept honest by the same constraints: it cannot name a
    # team that is not the list's.
    listing = make_kt_selection_list(kill_team=make_kill_team())
    other = make_kill_team()
    operative = make_kt_operative(kill_team=other)

    session.add(
        KTSelectionOption(
            kill_team_id=other.id,  # matches the operative, not the list
            selection_list_id=listing.id,
            operative_id=operative.id,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_the_printed_sentences_are_kept_beside_the_structure(
    session, make_kt_selection_list, make_kt_selection_option
):
    # Structured columns drive validation; the printed text is how a human checks the
    # parse read them correctly.
    listing = make_kt_selection_list(
        restriction_text="Other than WARRIOR operatives, your kill team can only include each operative on this list once."
    )
    option = make_kt_selection_option(
        selection_list=listing,
        loadout_options=["with flamer and gun butt", "with webber and gun butt"],
    )

    assert "WARRIOR" in listing.restriction_text
    # Display only: one operative with a weapon choice is ONE option (Wyrmblade prints
    # three "GUNNER with ..." lines), and nothing validates these strings.
    assert option.loadout_options == ["with flamer and gun butt", "with webber and gun butt"]


def test_a_keyword_cap_is_a_rule_about_a_SET_of_operatives(
    session,
    make_kill_team,
    make_kt_operative,
    make_kt_selection_list,
    make_kt_selection_option,
    make_kt_selection_restriction,
):
    # Deathwatch: "can only include each operative on this list once, and can only
    # include up to one GRAVIS operative." Per-option caps cannot say the second part:
    # several entries carry GRAVIS, so capping each at one still allows two.
    watch = make_kill_team(name="Deathwatch")
    bombard = make_kt_operative(kill_team=watch, name="Bombard", keywords=["DEATHWATCH", "GRAVIS"])
    demolisher = make_kt_operative(kill_team=watch, name="Demolisher", keywords=["DEATHWATCH", "GRAVIS"])
    listing = make_kt_selection_list(kill_team=watch, budget=5)
    make_kt_selection_option(selection_list=listing, operative=bombard, max_selections=1)
    make_kt_selection_option(selection_list=listing, operative=demolisher, max_selections=1)
    make_kt_selection_restriction(kill_team=watch, keyword="GRAVIS", max_operatives=1)
    session.refresh(watch)

    # Scoped to the TEAM: the sentence says "your kill team", and Brood Brother caps a
    # keyword its operatives carry from a different list than the one it follows.
    assert [(r.keyword, r.max_operatives) for r in watch.keyword_caps] == [("GRAVIS", 1)]
    # The keywords the rule is evaluated against are already on the operatives.
    assert all("GRAVIS" in o.operative.keywords for o in listing.options)


def test_a_team_may_carry_several_keyword_caps(session, make_kt_selection_restriction, make_kill_team):
    # Inquisitorial Agent states caps for GUN SERVITOR, SUBDUCTOR and GUNNER.
    team = make_kill_team()
    # SUBDUCTOR first, so insertion order differs from the keyword order being asserted.
    make_kt_selection_restriction(kill_team=team, keyword="SUBDUCTOR", max_operatives=2)
    make_kt_selection_restriction(kill_team=team, keyword="GUNNER", max_operatives=2)

    # A LIST, not a set: the relationship orders caps by keyword (the one collection with no
    # page order of its own, since caps are read out of a sentence), and a set assertion
    # left that ordering unpinned.
    assert [r.keyword for r in team.keyword_caps] == ["GUNNER", "SUBDUCTOR"]


def test_one_keyword_is_capped_once_per_team(session, make_kill_team, make_kt_selection_restriction):
    # Two caps for one keyword would be a parse that read the same sentence twice.
    team = make_kill_team()
    make_kt_selection_restriction(kill_team=team, keyword="GRAVIS", max_operatives=1)
    with pytest.raises(IntegrityError):
        make_kt_selection_restriction(kill_team=team, keyword="GRAVIS", max_operatives=2)


def test_a_cap_of_zero_is_rejected(session, make_kt_selection_restriction):
    # "None of these" is said by not offering them.
    with pytest.raises(IntegrityError, match="ck_kt_selection_restriction_max"):
        make_kt_selection_restriction(max_operatives=0)


def test_deleting_a_kill_team_takes_its_keyword_caps(session, make_kill_team, make_kt_selection_restriction):
    team = make_kill_team()
    make_kt_selection_restriction(kill_team=team)

    session.delete(team)
    session.commit()

    assert session.exec(select(KTSelectionRestriction)).all() == []


# ---------------------------------------------------------------------------
# Print order (decision #25). Rows have no order of their own, so every one of these
# collections says how it is sorted — and for a datacard that has to be the card's own
# order, not alphabetical.
# ---------------------------------------------------------------------------


def test_an_operatives_weapons_come_back_in_the_cards_order(session, make_kt_operative, make_kt_weapon):
    # Written out of order on purpose: insertion order is what the storage happens to
    # return, and it held by luck until a `VACUUM FULL` or a rewritten row moved one.
    warden = make_kt_operative(name="Warden")
    make_kt_weapon(warden, name="Fists", category="melee", position=3)
    make_kt_weapon(warden, name="Bolt rifle", category="range", position=0)
    make_kt_weapon(warden, name="Chainsword", category="melee", position=2)
    make_kt_weapon(warden, name="Plasma pistol", category="range", position=1)
    session.expire_all()

    assert [w.name for w in warden.weapons] == ["Bolt rifle", "Plasma pistol", "Chainsword", "Fists"]
    # …which alphabetical would not give, and a card lists ranged before melee
    assert [w.name for w in warden.weapons] != sorted(w.name for w in warden.weapons)


def test_an_operatives_abilities_come_back_in_the_cards_order(session, make_kt_operative, make_kt_ability):
    warden = make_kt_operative(name="Warden")
    make_kt_ability(warden, name="Zealous Charge", position=1)
    make_kt_ability(warden, name="Ember Strike", position=0)
    session.expire_all()

    assert [a.name for a in warden.abilities] == ["Ember Strike", "Zealous Charge"]


def test_a_lists_options_come_back_in_print_order(
    session, make_kill_team, make_kt_selection_list, make_kt_operative, make_kt_selection_option
):
    team = make_kill_team(name="Hollow Vigil")
    listing = make_kt_selection_list(team, position=0)
    second = make_kt_operative(kill_team=team, name="Sentinel")
    first = make_kt_operative(kill_team=team, name="Warden")
    make_kt_selection_option(listing, second, position=1)
    make_kt_selection_option(listing, first, position=0)
    session.expire_all()

    assert [o.operative.name for o in listing.options] == ["Warden", "Sentinel"]


def test_a_teams_selection_lists_come_back_in_print_order(session, make_kill_team, make_kt_selection_list):
    team = make_kill_team(name="Hollow Vigil")
    make_kt_selection_list(team, position=2, label="third")
    make_kt_selection_list(team, position=0, label="first")
    make_kt_selection_list(team, position=1, label="second")
    session.expire_all()

    assert [row.label for row in team.selection_lists] == ["first", "second", "third"]


def test_a_teams_ploys_come_back_in_the_pages_order(session, make_kill_team, make_kt_ploy):
    # The pages print Strategy Ploys before Firefight Ploys, and ordering by `kind` reversed
    # that for all 46 teams -- "firefight" sorts before "strategy". The page's order is the
    # only one that gets both the grouping and the order within a group right.
    team = make_kill_team(name="Hollow Vigil")
    make_kt_ploy(team, name="ZEAL", kind="firefight", position=3)
    make_kt_ploy(team, name="ASH", kind="strategy", position=0)
    make_kt_ploy(team, name="EMBER", kind="firefight", position=2)
    make_kt_ploy(team, name="CINDER", kind="strategy", position=1)
    session.expire_all()

    assert [(p.kind, p.name) for p in team.ploys] == [
        ("strategy", "ASH"),
        ("strategy", "CINDER"),
        ("firefight", "EMBER"),
        ("firefight", "ZEAL"),
    ]


def test_a_teams_operatives_keep_the_leader_first(session, make_kill_team, make_kt_operative):
    # A page prints the leader first, and in 44 of the 46 teams that is not the
    # alphabetically first operative -- Raveners print Prime before Felltalon.
    team = make_kill_team(name="Hollow Vigil")
    make_kt_operative(kill_team=team, name="Warden", position=2)
    make_kt_operative(kill_team=team, name="Prime", position=0)
    make_kt_operative(kill_team=team, name="Ash Prophet", position=1)
    session.expire_all()

    assert [o.name for o in team.operatives] == ["Prime", "Ash Prophet", "Warden"]


def test_a_teams_rules_keep_their_printed_order_and_their_groups_together(
    session, make_kill_team, make_kill_team_rule
):
    # Two reasons the name is not enough. Raveners print Burrow, Tunnel, Predatory
    # Instincts, and alphabetical order separates Burrow from the Tunnel rule that refers
    # to it. And now that a team's chosen options are rules with a `group` (decision #27),
    # alphabetical order interleaves the three Aspects, so a reader cannot see one set.
    # Written out of order, or insertion order alone would produce the expected list and
    # the test could not fail -- which is exactly what it did before.
    team = make_kill_team(name="Hollow Vigil")
    make_kill_team_rule(team, name="THE RISING EMBER", group="Ember Techniques", position=4)
    make_kill_team_rule(team, name="Tunnel", position=1)
    make_kill_team_rule(team, name="ASH ON THE WIND", group="Ember Techniques", position=3)
    make_kill_team_rule(team, name="Burrow", position=0)
    make_kill_team_rule(team, name="Predatory Instincts", position=2)
    session.expire_all()

    assert [r.name for r in team.rules] == [
        "Burrow",
        "Tunnel",
        "Predatory Instincts",
        "ASH ON THE WIND",
        "THE RISING EMBER",
    ]


def test_a_teams_equipment_keeps_the_pages_order(session, make_kill_team, make_kt_equipment):
    team = make_kill_team(name="Hollow Vigil")
    make_kt_equipment(team, name="ZEAL CHARM", position=1)
    make_kt_equipment(team, name="ASH TOKEN", position=0)
    session.expire_all()

    assert [q.name for q in team.equipment] == ["ASH TOKEN", "ZEAL CHARM"]


def test_a_rule_always_belongs_to_a_kill_team(session, make_kill_team, make_kt_ploy):
    # "Astartes" is printed by seven different kill teams and "Rifles" by two, so a reader
    # seeing a rule needs to know whose it is. A rule is never universal: `kill_team_id` is
    # NOT NULL, which is the difference from a ploy, where NULL means every team may use it.
    session.add(KillTeamRule(name="Astartes", description="Does something."))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # …while a ploy with no kill team is the universal case, and legal
    universal = make_kt_ploy(kill_team=None, name="COMMAND RE-ROLL")
    assert universal.kill_team_id is None


def test_two_profiles_may_share_a_position(session, make_kt_operative, make_kt_weapon):
    # Deliberately NOT unique per operative: the seed rewrites positions in place when a
    # page reorders its profiles, and a unique constraint would collide with whichever row
    # has not moved yet — the same trap that made a selection list's budget part of its key.
    warden = make_kt_operative(name="Warden")
    make_kt_weapon(warden, name="One", position=0)
    make_kt_weapon(warden, name="Two", position=0)  # no error

    assert len(warden.weapons) == 2


def test_a_selection_list_shape_outside_the_vocabulary_is_refused(session, make_kill_team):
    # Same style as a weapon's category and a ploy's kind: three shapes a page prints, and
    # a reader can rely on the vocabulary (decision #29).
    team = make_kill_team()
    session.add(KTSelectionList(kill_team_id=team.id, label="1 X", budget=1, position=0, shape="whatever"))
    with pytest.raises(IntegrityError, match="ck_kt_selection_list_shape"):
        session.commit()


@pytest.mark.parametrize(
    ("build", "constraint"),
    [
        pytest.param(
            lambda team, op: KTOperative(
                kill_team_id=team.id, name="Backwards", apl=2, move=6, save=4, wounds=8, position=-1
            ),
            "ck_kt_operative_position",
            id="operative",
        ),
        pytest.param(
            lambda team, op: KillTeamRule(
                kill_team_id=team.id, name="Backwards", description="x", position=-1
            ),
            "ck_kt_kill_team_rule_position",
            id="rule",
        ),
        pytest.param(
            lambda team, op: KTPloy(
                kill_team_id=team.id, name="BACKWARDS", kind="strategy", description="x", position=-1
            ),
            "ck_kt_ploy_position",
            id="ploy",
        ),
        pytest.param(
            lambda team, op: KTEquipment(
                kill_team_id=team.id, name="Backwards", description="x", position=-1
            ),
            "ck_kt_equipment_position",
            id="equipment",
        ),
        pytest.param(
            lambda team, op: KTAbility(operative_id=op.id, name="Backwards", description="x", position=-1),
            "ck_kt_ability_position",
            id="ability",
        ),
        pytest.param(
            lambda team, op: KTWeapon(
                operative_id=op.id,
                name="Backwards",
                category="melee",
                attacks=4,
                hit=3,
                normal_damage=4,
                crit_damage=5,
                position=-1,
            ),
            "ck_kt_weapon_position",
            id="weapon",
        ),
        pytest.param(
            lambda team, op: KTSelectionList(
                kill_team_id=team.id, label="1 X", budget=1, position=-1, shape="single"
            ),
            "ck_kt_selection_list_position",
            id="selection list",
        ),
    ],
)
def test_a_negative_position_is_refused_on_every_table_that_carries_one(
    session, make_kill_team, make_kt_operative, build, constraint
):
    # One test per table, because the convention is only as good as its weakest column:
    # `kt_operatives` was the one carrying `position` with no CHECK at all, and the single
    # test that existed built a KTWeapon, so the other six could all have been dropped with
    # the suite green.
    team = make_kill_team()
    operative = make_kt_operative(kill_team=team)
    session.add(build(team, operative))

    with pytest.raises(IntegrityError, match=constraint):
        session.commit()
