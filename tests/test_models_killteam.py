"""Kill Team catalog models, slice 1: KTFaction → KillTeam → KillTeamRule.

These pin the rules KILLTEAM.md sets for the top of the tree — what is unique and
where, what a delete takes with it, and what it refuses — at the database level, so
they hold however a row is written (service, seed script, or admin route).
"""

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.db.models_killteam import (
    KillTeam,
    KillTeamRule,
    KTAbility,
    KTEquipment,
    KTFaction,
    KTGame,
    KTGameEquipment,
    KTGameEvent,
    KTGameOperative,
    KTOperative,
    KTPloy,
    KTRoster,
    KTRosterOperative,
    KTSelectionRule,
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


def test_a_teams_ploys_come_back_in_the_pages_order(session, make_kill_team, make_kt_ploy):
    # The pages print Strategy Ploys before Firefight Ploys, and ordering by `kind` reversed
    # that for all 48 teams -- "firefight" sorts before "strategy". The page's order is the
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
    # A page prints the leader first, and in 46 of the 48 teams that is not the
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


def test_a_selection_rule_with_no_text_is_refused(session, make_kill_team):
    # A rule IS its text: an empty one renders as a blank line in a printed section and
    # means nothing. The parser came within one list comprehension of storing one, so this
    # is enforced rather than observed.
    session.add(KTSelectionRule(kill_team_id=make_kill_team().id, kind="note", text="   "))

    with pytest.raises(IntegrityError, match="ck_kt_selection_rule_text"):
        session.commit()


@pytest.mark.parametrize("kind", ["heading", "restriction", "note"])
def test_only_a_line_may_carry_an_indent(session, make_kill_team, kind):
    # Indent belongs to a bullet. A heading, a sentence and a note are each printed at the
    # top level, so a depth on one of them would be meaningless rather than merely unused.
    session.add(KTSelectionRule(kill_team_id=make_kill_team().id, kind=kind, text="Something", depth=1))

    with pytest.raises(IntegrityError, match="ck_kt_selection_rule_depth_kind"):
        session.commit()


def test_a_line_may_carry_any_indent(session, make_kill_team):
    # The other side of the same constraint: a bullet's depth is how far the page indents
    # it, and the real pages go to 2.
    team = make_kill_team()
    for depth in (0, 1, 2):
        session.add(
            KTSelectionRule(
                kill_team_id=team.id, kind="line", text=f"depth {depth}", position=depth, depth=depth
            )
        )

    session.commit()  # no constraint stands in the way

    assert len(session.exec(select(KTSelectionRule)).all()) == 3


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
            lambda team, op: KTSelectionRule(
                kill_team_id=team.id, kind="line", text="1 X operative", position=-1
            ),
            "ck_kt_selection_rule_position",
            id="selection rule",
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


def test_an_availability_outside_the_vocabulary_is_refused(session, make_kill_team):
    team = make_kill_team()
    session.add(
        KTOperative(
            kill_team_id=team.id,
            name="Schrödinger",
            apl=2,
            move=6,
            save=4,
            wounds=8,
            availability="maybe",
        )
    )
    with pytest.raises(IntegrityError, match="ck_kt_operative_availability"):
        session.commit()


# ---------------------------------------------------------------------------
# Rosters (K4). A row is ONE operative, and it cannot belong to another team.


def test_a_roster_may_field_the_same_operative_twice(session, make_kt_roster, make_kt_operative):
    # Decision #50: a row is an individual, not a count, so two Warriors are two rows.
    # There is deliberately no UNIQUE(roster_id, operative_id) to stop it.
    roster = make_kt_roster()
    operative = make_kt_operative(kill_team=roster.kill_team, name="Warrior")
    for position in (0, 1):
        session.add(
            KTRosterOperative(
                roster_id=roster.id,
                operative_id=operative.id,
                kill_team_id=roster.kill_team_id,
                owner_user_id=roster.owner_user_id,
                position=position,
            )
        )

    session.commit()

    assert [row.position for row in session.exec(select(KTRosterOperative)).all()] == [0, 1]


def test_a_roster_cannot_field_another_teams_operative(
    session, make_kt_roster, make_kill_team, make_kt_operative
):
    # The pair of composite foreign keys both go through `kill_team_id`, so this is
    # unrepresentable rather than merely checked -- the guarantee decision #31 wanted and
    # a service could forget.
    roster = make_kt_roster()
    stranger = make_kt_operative(kill_team=make_kill_team(name="Someone Else"))
    session.add(
        KTRosterOperative(
            roster_id=roster.id,
            operative_id=stranger.id,
            kill_team_id=roster.kill_team_id,  # claims the roster's team
            owner_user_id=roster.owner_user_id,
            position=0,
        )
    )

    # Matched, not bare: every column is populated, so this can only be the operative
    # leg refusing. A bare `IntegrityError` would also accept a NOT NULL violation from
    # a column the test forgot -- which is exactly what adding `owner_user_id` caused
    # before this line was tightened.
    with pytest.raises(IntegrityError, match="(?i)foreign key"):
        session.commit()


def test_a_roster_row_cannot_claim_a_team_that_is_not_its_rosters(
    session, make_kt_roster, make_kill_team, make_kt_operative
):
    # The other leg: naming the operative's team on the row does not help, because the
    # roster side of the pair then fails instead.
    roster = make_kt_roster()
    other = make_kill_team(name="Someone Else")
    stranger = make_kt_operative(kill_team=other)
    session.add(
        KTRosterOperative(
            roster_id=roster.id,
            operative_id=stranger.id,
            kill_team_id=other.id,  # claims the operative's team
            owner_user_id=roster.owner_user_id,
            position=0,
        )
    )

    with pytest.raises(IntegrityError, match="(?i)foreign key"):
        session.commit()


def test_a_rosters_operatives_come_back_in_the_players_order(
    session, make_kt_roster, make_kt_operative, make_kt_roster_operative
):
    # Unlike every `position` in the catalog, this one is the PLAYER's (decision #25 is
    # about the page's order; a roster's is its owner's to set).
    roster = make_kt_roster()
    for name, position in (("Third", 2), ("First", 0), ("Second", 1)):
        operative = make_kt_operative(kill_team=roster.kill_team, name=name)
        make_kt_roster_operative(roster=roster, operative=operative, position=position)
    session.expire_all()

    names = [row.operative.name for row in session.get(KTRoster, roster.id).operatives]

    assert names == ["First", "Second", "Third"]


def test_a_negative_roster_position_is_refused(session, make_kt_roster, make_kt_operative):
    roster = make_kt_roster()
    operative = make_kt_operative(kill_team=roster.kill_team)
    session.add(
        KTRosterOperative(
            roster_id=roster.id,
            operative_id=operative.id,
            kill_team_id=roster.kill_team_id,
            owner_user_id=roster.owner_user_id,
            position=-1,
        )
    )

    with pytest.raises(IntegrityError, match="ck_kt_roster_operative_position"):
        session.commit()


def test_deleting_a_roster_takes_its_operatives_and_leaves_the_catalog(
    session, make_kt_roster, make_kt_roster_operative
):
    roster = make_kt_roster()
    make_kt_roster_operative(roster=roster)

    session.delete(roster)
    session.commit()

    assert session.exec(select(KTRosterOperative)).all() == []
    assert session.exec(select(KTOperative)).all(), "the catalog operative survives"


def test_deleting_a_user_takes_their_rosters_and_the_rows_on_them(
    session, make_user, make_kt_roster, make_kt_roster_operative
):
    """Two cascade legs, both of them the DATABASE's, which is why the rows matter.

    There is no `User -> KTRoster` relationship, so the ORM knows nothing about these
    rosters: `session.delete(user)` emits one DELETE on `users` and the rest is
    `ON DELETE CASCADE`. That takes the rosters through `kt_rosters.owner_user_id`, and
    then their rows through `fk_kt_roster_operative_roster` -- the composite leg.

    The second leg is the one worth testing, because it is the one the ORM cannot stand
    in for: `cascade_delete=True` on `KTRoster.operatives` only fires when a roster is
    deleted through a session, which is not what happens here. This test built a roster
    with NO operatives, so the leg never fired and the assertion could not fail. The
    same shape as the N+1 tests that pass because N is 1; here N was 0.
    """
    user = make_user()
    roster = make_kt_roster(owner=user)
    make_kt_roster_operative(roster=roster)
    make_kt_roster_operative(roster=roster)
    assert session.exec(select(KTRosterOperative)).all(), "the rows exist before the delete"

    session.delete(user)
    session.commit()

    assert session.exec(select(KTRoster)).all() == []
    assert session.exec(select(KTRosterOperative)).all() == []
    assert session.exec(select(KTOperative)).all(), "the catalog operative survives"


def test_a_roster_row_cannot_sit_in_a_roster_its_owner_does_not_own(
    session, make_user, make_kt_roster, make_kt_operative
):
    """The owner leg of the composite foreign key, attacked at the table.

    Every other column is valid and consistent: the operative really belongs to the
    roster's kill team, the position is fine. Only `owner_user_id` names someone other
    than the roster's owner -- and the triple `(owner, kill_team, roster)` has no match
    on `kt_rosters`, so the row cannot exist. This is the guarantee a service could
    forget and the database cannot.
    """
    roster = make_kt_roster()
    operative = make_kt_operative(kill_team=roster.kill_team)
    stranger = make_user(username="interloper", email="interloper@test.invalid")
    session.add(
        KTRosterOperative(
            roster_id=roster.id,
            operative_id=operative.id,
            kill_team_id=roster.kill_team_id,
            owner_user_id=stranger.id,  # not the roster's owner
            position=0,
        )
    )

    with pytest.raises(IntegrityError, match="(?i)foreign key"):
        session.commit()


def test_a_direct_update_cannot_move_a_row_into_another_players_roster(
    session, make_user, make_kill_team, make_kt_roster, make_kt_operative, make_kt_roster_operative
):
    """Finding 15: the one attack the two-column leg allowed.

    Both rosters are for the SAME kill team, so the old `(kill_team_id, roster_id)` leg
    was satisfied by either of them -- a direct `UPDATE ... SET roster_id` moved a row
    from one player's roster into another's and the database had nothing to say. Nothing
    in the API does this; the point is that the schema permitted it.

    With `owner_user_id` in the leg the row must name a roster belonging to its own
    owner, so the UPDATE has no matching triple. That is as far as a foreign key can go:
    an attacker who rewrites `owner_user_id` in the same statement produces an
    internally consistent row, and no constraint can tell that from a legitimate one.

    A Core `update()` rather than `text()`: SQLite stores a UUID as 32 hex characters
    with no dashes, so `text("... WHERE id = :row")` bound to `str(uuid)` matches
    NOTHING and the statement cannot violate anything. The first UPDATE here is
    harmless and asserts `rowcount == 1`, so the attack below is known to address a real
    row on both test tiers rather than silently addressing none.
    """
    team = make_kill_team(name="Raveners")
    victim = make_kt_roster(owner=make_user(username="victim", email="victim@test.invalid"), kill_team=team)
    thief = make_kt_roster(owner=make_user(username="thief", email="thief@test.invalid"), kill_team=team)
    operative = make_kt_operative(kill_team=team)
    row_id = make_kt_roster_operative(roster=victim, operative=operative).id
    victim_id, thief_id = victim.id, thief.id
    session.commit()

    addresses_the_row = session.execute(
        update(KTRosterOperative).where(KTRosterOperative.id == row_id).values(position=5)
    )
    assert addresses_the_row.rowcount == 1, "the WHERE clause must match, or the attack proves nothing"

    with pytest.raises(IntegrityError, match="(?i)foreign key"):
        session.execute(
            update(KTRosterOperative).where(KTRosterOperative.id == row_id).values(roster_id=thief_id)
        )
        session.commit()
    session.rollback()

    assert session.get(KTRosterOperative, row_id).roster_id == victim_id


# --- the game tables (K5) --------------------------------------------------


def test_a_game_is_created_from_a_roster_with_its_owner_and_team(session, make_kt_game):
    game = make_kt_game()

    assert game.status == "setup"
    assert game.turning_point == 1
    assert game.phase == "strategy"
    assert game.command_points == 0
    assert game.version == 1
    # The documents default to empty rather than NULL, so a read never has to ask which.
    assert (game.markers, game.ploys_used, game.rules, game.ploys) == ([], [], [], [])
    assert (game.victory_points, game.choices) == ({}, {})


def test_a_game_operative_cannot_sit_in_another_players_game(
    session, make_user, make_kt_game, make_kt_operative
):
    """The owner leg of `fk_kt_game_operative_game` — #54's pattern, applied to a game.

    Every other column is valid: the datacard really belongs to the game's kill team.
    Only `owner_user_id` names someone else, so the triple has no match on `kt_games`.
    """
    game = make_kt_game()
    operative = make_kt_operative(kill_team=game.kill_team)
    stranger = make_user(username="gatecrasher", email="gatecrasher@test.invalid")
    session.add(
        KTGameOperative(
            game_id=game.id,
            operative_id=operative.id,
            kill_team_id=game.kill_team_id,
            owner_user_id=stranger.id,
            name=operative.name,
            apl=operative.apl,
            move=operative.move,
            save=operative.save,
            wounds=operative.wounds,
            current_wounds=operative.wounds,
        )
    )

    with pytest.raises(IntegrityError, match="(?i)foreign key"):
        session.commit()


def test_a_game_operative_cannot_name_another_teams_datacard(
    session, make_kill_team, make_kt_game, make_kt_operative
):
    # The shared `kill_team_id` again: it has to satisfy the game leg AND the operative
    # leg, so a card from another team has nowhere to sit.
    game = make_kt_game()
    stranger = make_kt_operative(kill_team=make_kill_team(name="Someone Else"))
    session.add(
        KTGameOperative(
            game_id=game.id,
            operative_id=stranger.id,
            kill_team_id=game.kill_team_id,  # claims the game's team
            owner_user_id=game.owner_user_id,
            name=stranger.name,
            apl=stranger.apl,
            move=stranger.move,
            save=stranger.save,
            wounds=stranger.wounds,
            current_wounds=stranger.wounds,
        )
    )

    with pytest.raises(IntegrityError, match="(?i)foreign key"):
        session.commit()


def test_an_operative_cannot_hold_more_wounds_than_its_snapshot_allows(session, make_kt_game_operative):
    # The bookkeeping rule held in the schema, so no path can store it: a transform that
    # lowers the card's maximum has to resolve the current value, not leave it over.
    row = make_kt_game_operative()
    row.current_wounds = row.wounds + 1
    session.add(row)

    with pytest.raises(IntegrityError, match="ck_kt_game_operative_current_wounds"):
        session.commit()


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("order", "sprinting", "ck_kt_game_operative_order"),
        ("status", "airborne", "ck_kt_game_operative_status"),
        ("source", "wishful_thinking", "ck_kt_game_operative_source"),
        ("activated_in_turning_point", 5, "ck_kt_game_operative_activated_tp"),
        ("added_in_turning_point", 0, "ck_kt_game_operative_added_tp"),
    ],
)
def test_a_game_operatives_vocabulary_is_closed(session, make_kt_game_operative, field, value, constraint):
    row = make_kt_game_operative()
    setattr(row, field, value)
    session.add(row)

    with pytest.raises(IntegrityError, match=constraint):
        session.commit()


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("status", "abandoned", "ck_kt_game_status"),
        ("phase", "shooting", "ck_kt_game_phase"),
        ("initiative", "nobody", "ck_kt_game_initiative"),
        ("turning_point", 5, "ck_kt_game_turning_point"),
        ("turning_point", 0, "ck_kt_game_turning_point"),
        ("command_points", -1, "ck_kt_game_command_points"),
        ("version", 0, "ck_kt_game_version"),
        ("opponent_name", "   ", "ck_kt_game_opponent_name"),
    ],
)
def test_a_games_vocabulary_is_closed(session, make_kt_game, field, value, constraint):
    game = make_kt_game()
    setattr(game, field, value)
    session.add(game)

    with pytest.raises(IntegrityError, match=constraint):
        session.commit()


def test_the_same_equipment_cannot_be_taken_twice_in_one_game(
    session, make_kt_game, make_kt_equipment, make_kt_game_equipment
):
    # Decision #17's one schema-enforced equipment rule. The ALLOWANCE is not enforced
    # at all (#57) -- a game may take a fifth piece.
    game = make_kt_game()
    item = make_kt_equipment()
    make_kt_game_equipment(game=game, equipment=item)

    make_kt_game_equipment(game=game, equipment=make_kt_equipment(name="Something Else"))
    session.add(KTGameEquipment(game_id=game.id, equipment_id=item.id, name=item.name, text="x"))

    # `(?i)unique`, not the constraint name: SQLite names the COLUMNS in a unique
    # violation where Postgres names the constraint, so matching the name would
    # pass on one tier and fail on the other. Still specific enough to tell a
    # unique violation from a CHECK, a NOT NULL or a foreign key.
    with pytest.raises(IntegrityError, match="(?i)unique"):
        session.commit()


def test_a_games_event_sequence_is_unique_so_the_last_event_is_a_fact(
    session, make_kt_game, make_kt_game_event
):
    """`sequence` is what `undo` reads to find the last event (#58).

    `created_at` could not: two events written in one transaction share a timestamp, and
    an undo that picked the wrong one of a tied pair would revert the wrong field.
    """
    game = make_kt_game()
    make_kt_game_event(game=game)
    make_kt_game_event(game=game)
    session.add(KTGameEvent(game_id=game.id, sequence=1, type="cp_spent", turning_point=1))

    # `(?i)unique`, not the constraint name: SQLite names the COLUMNS in a unique
    # violation where Postgres names the constraint, so matching the name would
    # pass on one tier and fail on the other. Still specific enough to tell a
    # unique violation from a CHECK, a NOT NULL or a foreign key.
    with pytest.raises(IntegrityError, match="(?i)unique"):
        session.commit()


def test_two_games_number_their_events_independently(session, make_kt_game, make_kt_game_event):
    # `UNIQUE(game_id, sequence)` and not `UNIQUE(sequence)`: one game's log is no
    # constraint on another's.
    first, second = make_kt_game(), make_kt_game()
    make_kt_game_event(game=first, sequence=1)
    make_kt_game_event(game=second, sequence=1)

    assert len(session.exec(select(KTGameEvent)).all()) == 2


def test_an_event_cannot_undo_itself(session, make_kt_game_event):
    event = make_kt_game_event()
    event.undone_by = event.id
    session.add(event)

    with pytest.raises(IntegrityError, match="ck_kt_game_event_undone_by"):
        session.commit()


def test_deleting_a_roster_is_refused_while_a_game_was_played_from_it(session, make_kt_roster, make_kt_game):
    """`fk_kt_game_roster` has no `ondelete`, so NO ACTION refuses it.

    A game is self-contained once it starts (#22, #24), but `roster_id` stays NOT NULL
    so a battle can always say which roster it was played from. The service turns this
    into a 409 naming the games, the shape `UnitService.delete_unit` already uses.
    """
    roster = make_kt_roster()
    make_kt_game(roster=roster)

    session.delete(roster)

    with pytest.raises(IntegrityError, match="(?i)foreign key"):
        session.commit()


def test_deleting_a_user_takes_their_games_and_everything_on_them(
    session,
    make_user,
    make_kt_roster,
    make_kt_game,
    make_kt_game_operative,
    make_kt_game_equipment,
    make_kt_game_event,
):
    """Two cascade paths from one DELETE, and both have to fire.

    `kt_games.owner_user_id` cascades from `users`, and so does `kt_rosters.owner_user_id`
    -- while `fk_kt_game_roster` points at the roster with NO ACTION. The order those
    fire in is not guaranteed, so this is the test that says the pair does not deadlock:
    NO ACTION is checked at the end of the statement, by which time both rows are gone.
    Verified on both tiers, since SQLite and Postgres disagree about plenty else.
    """
    user = make_user()
    game = make_kt_game(roster=make_kt_roster(owner=user))
    make_kt_game_operative(game=game)
    make_kt_game_equipment(game=game)
    make_kt_game_event(game=game)
    assert session.exec(select(KTGameOperative)).all(), "the rows exist before the delete"

    session.delete(user)
    session.commit()

    assert session.exec(select(KTGame)).all() == []
    assert session.exec(select(KTGameOperative)).all() == []
    assert session.exec(select(KTGameEquipment)).all() == []
    assert session.exec(select(KTGameEvent)).all() == []
    assert session.exec(select(KTRoster)).all() == []
    assert session.exec(select(KTOperative)).all(), "the catalog operative survives"
