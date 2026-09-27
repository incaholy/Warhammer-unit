"""The Kill Team seed (scripts.seed_killteam.seed) — loading, and re-loading.

Uses an inline sample rather than the scraped killteam.json, which is gitignored and
holds someone else's content: this tests the loader, not any particular catalog.
"""

import copy
import re

import pytest
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
from scripts.seed_killteam import SeedError, seed

SAMPLE = {
    "kill_teams": [
        {
            "name": "Hollow Vigil",
            "faction": "Sentinels",
            "rules": [{"name": "Ember Tide", "description": "Place one Ember marker.", "group": None}],
            "ploys": [
                {"name": "ASHEN ADVANCE", "kind": "strategy", "description": "Move further."},
                # a printed cost, unlike the team pages
                {
                    "name": "EMBER GUARD",
                    "kind": "firefight",
                    "description": "Improve a Save.",
                    "cp_cost": 2,
                },
            ],
            "equipment": [{"name": "Cinder Charm", "description": "Re-roll one die."}],
            "operatives": [
                {
                    "name": "Hollow Warden",
                    "apl": 3,
                    "move": 7,
                    "save": 5,
                    "wounds": 19,
                    "keywords": ["HOLLOW", "WARDEN"],
                    "availability": "roster",
                    "weapons": [
                        {
                            "name": "Brazier",
                            "category": "range",
                            "range": 4,
                            "attacks": 4,
                            "hit": 2,
                            "normal_damage": 4,
                            "crit_damage": 4,
                            "rules": ['Range 4"', "Saturate"],
                        },
                        # the same NAME in the other category: one weapon, two profiles
                        {
                            "name": "Brazier",
                            "category": "melee",
                            "range": None,
                            "attacks": 4,
                            "hit": 4,
                            "normal_damage": 4,
                            "crit_damage": 4,
                            "rules": ["Shock"],
                        },
                    ],
                    # two, so the stored positions distinguish the payload's order from a
                    # constant — with one ability, `index` and `0` are the same write
                    "abilities": [
                        {"name": "Warden's Vigil", "description": "Does something."},
                        {"name": "EMBER STRIKE", "description": "1AP. Does something else."},
                    ],
                },
                {
                    "name": "Hollow Sentinel",
                    "apl": 2,
                    "move": 6,
                    "save": 4,
                    "wounds": 12,
                    "keywords": ["HOLLOW", "SENTINEL", "EMBER"],
                    "availability": "roster",
                    "weapons": [],
                    "abilities": [],
                },
            ],
            "selection_lists": [
                {
                    "label": "1 HOLLOW WARDEN operative",
                    "budget": 1,
                    "shape": "budgeted",
                    "requisition_source": None,
                    "same_options_as": None,
                    "position": 0,
                    "restriction_text": None,
                    "options": [
                        {
                            "operative": "Hollow Warden",
                            "cost": 1,
                            "models": 1,
                            "max_selections": None,
                            "loadout_options": [],
                        }
                    ],
                },
                {
                    "label": "4 HOLLOW operatives selected from the following list:",
                    "budget": 4,
                    "shape": "budgeted",
                    "requisition_source": None,
                    "same_options_as": None,
                    "position": 1,
                    "restriction_text": "Other than SENTINEL operatives, once each.",
                    "options": [
                        {
                            "operative": "Hollow Sentinel",
                            "cost": 2,
                            "models": 2,
                            "max_selections": 1,
                            "loadout_options": ["with ash lash"],
                        }
                    ],
                },
            ],
            "keyword_caps": [{"keyword": "EMBER", "max_operatives": 1}],
            "composition_notes": ["Other than SENTINEL operatives, once each."],
        }
    ],
    "universal_ploys": [
        {"name": "COMMAND RE-ROLL", "kind": "firefight", "description": "Re-roll one die.", "cp_cost": 1},
        {"name": "GUARD", "kind": "strategy", "description": "A second universal ploy."},
    ],
    "universal_equipment": [
        {"name": "1X AMMO CACHE", "description": "Set up one marker."},
        {"name": "2X LADDERS", "description": "Set up two ladders."},
    ],
}


def test_seeding_an_empty_payload_fails_rather_than_looking_successful(session):
    with pytest.raises(SeedError, match="no kill teams"):
        seed(session, {"kill_teams": []})


def test_every_part_of_a_kill_team_is_loaded(session):
    counts = seed(session, SAMPLE)

    assert counts == {
        "factions": 1,
        "kill_teams": 1,
        "rules": 1,
        "ploys": 2,
        "equipment": 1,
        "operatives": 2,
        "weapons": 2,
        "abilities": 2,
        "selection_lists": 2,
        "selection_options": 2,
        "keyword_caps": 1,
        "universal_ploys": 2,
        "universal_equipment": 2,
        "updated": 0,
        "compositions_replaced": 0,
    }
    team = session.exec(select(KillTeam)).one()
    assert team.faction.name == "Sentinels"
    assert {o.name for o in team.operatives} == {"Hollow Warden", "Hollow Sentinel"}
    assert [r.name for r in team.rules] == ["Ember Tide"]


def test_a_second_run_of_the_same_payload_changes_nothing(session):
    seed(session, SAMPLE)
    again = seed(session, SAMPLE)

    # Nothing created AND nothing updated: "fix one team, scrape again, seed again" is
    # cheap, and an unchanged row is not touched (so `updated_at` stays honest).
    assert set(again.values()) == {0}
    assert len(session.exec(select(KTOperative)).all()) == 2
    assert len(session.exec(select(KTWeapon)).all()) == 2
    assert len(session.exec(select(KTPloy)).all()) == 4  # two team ploys + two universal


def test_one_weapon_name_lands_in_both_categories(session):
    # Sanctifiers' brazier is fired and swung; the natural key is
    # (operative, name, category), so both profiles are separate rows.
    seed(session, SAMPLE)

    braziers = {w.category: w for w in session.exec(select(KTWeapon).where(KTWeapon.name == "Brazier")).all()}
    assert sorted(braziers) == ["melee", "range"]

    # And the range comes from the right place in each case: the ranged profile printed
    # "Range 4"" so 4 is stored, while the melee profile printed none and the column's
    # per-category default fills it in -- which is why the parser reports None rather
    # than inventing the same number in a second place (decision #15).
    assert braziers["range"].range == 4
    assert braziers["melee"].range == 1


def test_a_ploy_takes_the_printed_cost_or_the_column_default(session):
    seed(session, SAMPLE)
    ploys = {p.name: p for p in session.exec(select(KTPloy)).all()}

    assert ploys["EMBER GUARD"].cp_cost == 2  # printed
    assert ploys["ASHEN ADVANCE"].cp_cost == 1  # the column's default


def test_the_universal_rows_belong_to_no_kill_team(session):
    seed(session, SAMPLE)

    reroll = session.exec(select(KTPloy).where(KTPloy.name == "COMMAND RE-ROLL")).one()
    cache = session.exec(select(KTEquipment).where(KTEquipment.name == "1X AMMO CACHE")).one()

    assert reroll.kill_team_id is None
    assert cache.kill_team_id is None
    # …so they are not in any team's own lists
    team = session.exec(select(KillTeam)).one()
    assert "COMMAND RE-ROLL" not in {p.name for p in team.ploys}
    assert "1X AMMO CACHE" not in {q.name for q in team.equipment}


def test_selection_options_carry_cost_models_and_caps(session):
    seed(session, SAMPLE)

    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert [(row.budget, row.position) for row in lists] == [(1, 0), (4, 1)]
    option = lists[1].options[0]
    assert (option.cost, option.models, option.max_selections) == (2, 2, 1)
    assert option.loadout_options == ["with ash lash"]
    assert lists[1].restriction_text is not None


def test_an_option_is_tied_to_the_operative_and_the_team(session):
    # The composite foreign keys mean an option's list and operative must share a team;
    # the seed passes the team explicitly, so a payload mismatch is refused.
    seed(session, SAMPLE)

    option = session.exec(
        select(KTSelectionOption).join(KTOperative).where(KTOperative.name == "Hollow Warden")
    ).one()
    team = session.exec(select(KillTeam)).one()
    assert option.kill_team_id == team.id
    assert option.operative.name == "Hollow Warden"


def test_a_keyword_cap_lands_on_the_team(session):
    seed(session, SAMPLE)

    cap = session.exec(select(KTSelectionRestriction)).one()
    team = session.exec(select(KillTeam)).one()
    assert (cap.kill_team_id, cap.keyword, cap.max_operatives) == (team.id, "EMBER", 1)


def test_an_option_naming_an_unknown_operative_stops_the_seed(session):
    # The scraper resolved that name against the page's datacards, so a miss means the
    # payload is inconsistent -- and the option would offer an operative nobody can field.
    payload = {
        "kill_teams": [
            {
                "name": "Broken",
                "faction": "Sentinels",
                "composition_notes": [],
                "operatives": [],
                "selection_lists": [
                    {
                        "label": "1 X",
                        "budget": 1,
                        "shape": "single",
                        "requisition_source": None,
                        "same_options_as": None,
                        "position": 0,
                        "options": [{"operative": "Nobody", "cost": 1, "models": 1}],
                    }
                ],
            }
        ]
    }
    with pytest.raises(SeedError, match="not one of the team's operatives"):
        seed(session, payload)


def test_two_teams_can_share_a_faction(session):
    second = dict(SAMPLE["kill_teams"][0], name="Ashen Choir")
    counts = seed(session, {"kill_teams": [SAMPLE["kill_teams"][0], second]})

    assert (counts["factions"], counts["kill_teams"]) == (1, 2)
    assert len(session.exec(select(KTFaction)).all()) == 1
    # each team gets its OWN copies of the operatives, rules and abilities
    assert len(session.exec(select(KTOperative)).all()) == 4
    assert len(session.exec(select(KillTeamRule)).all()) == 2
    assert len(session.exec(select(KTAbility)).all()) == 4  # two teams, two abilities each


def test_a_changed_stat_is_rewritten_rather_than_left_stale(session):
    # The catalog is derived data: when the source rebalances an operative, the payload
    # is right and the stored row is stale. Create-once kept the old number silently.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["operatives"][0]["wounds"] = 21
    payload["universal_ploys"][0]["description"] = "Re-roll one attack die."

    counts = seed(session, payload)

    assert counts["updated"] == 2  # the operative and the universal ploy
    assert counts["operatives"] == 0  # updated, not duplicated
    warden = session.exec(select(KTOperative).where(KTOperative.name == "Hollow Warden")).one()
    assert warden.wounds == 21
    reroll = session.exec(
        select(KTPloy).where(KTPloy.kill_team_id.is_(None), KTPloy.name == "COMMAND RE-ROLL")
    ).one()
    assert reroll.description == "Re-roll one attack die."


def test_a_default_range_survives_a_re_seed_and_a_withdrawn_one_reverts(session):
    # Both halves of decision #15's split. The melee profile prints no range, so the
    # shared rule supplies 1 -- and keeps supplying it on every run. And a range the
    # source STOPS printing goes back to that default instead of sticking: the seed writes
    # the value rather than omitting the column, because a column default fires only on
    # INSERT.
    seed(session, SAMPLE)
    seed(session, copy.deepcopy(SAMPLE))
    assert {w.category: w.range for w in session.exec(select(KTWeapon)).all()} == {"range": 4, "melee": 1}

    withdrawn = copy.deepcopy(SAMPLE)
    withdrawn["kill_teams"][0]["operatives"][0]["weapons"][0]["range"] = None  # the ranged one
    withdrawn["kill_teams"][0]["ploys"][1]["cp_cost"] = None  # EMBER GUARD printed 2
    counts = seed(session, withdrawn)

    assert counts["updated"] == 2
    assert {w.category: w.range for w in session.exec(select(KTWeapon)).all()} == {"range": 2, "melee": 1}
    ploys = {p.name: p.cp_cost for p in session.exec(select(KTPloy)).all()}
    assert ploys["EMBER GUARD"] == 1


@pytest.mark.parametrize(
    ("edit", "check"),
    [
        pytest.param(
            lambda team: team["rules"][0].update(description="Place two Ember markers."),
            lambda session: session.exec(select(KillTeamRule)).one().description
            == "Place two Ember markers.",
            id="rule description",
        ),
        pytest.param(
            lambda team: team["ploys"][0].update(description="Move even further."),
            lambda session: session.exec(select(KTPloy).where(KTPloy.name == "ASHEN ADVANCE"))
            .one()
            .description
            == "Move even further.",
            id="ploy description",
        ),
        pytest.param(
            lambda team: team["ploys"][1].update(cp_cost=3),
            lambda session: session.exec(select(KTPloy).where(KTPloy.name == "EMBER GUARD")).one().cp_cost
            == 3,
            id="ploy cost",
        ),
        pytest.param(
            lambda team: team["equipment"][0].update(description="Re-roll two dice."),
            lambda session: session.exec(select(KTEquipment).where(KTEquipment.kill_team_id.is_not(None)))
            .one()
            .description
            == "Re-roll two dice.",
            id="equipment description",
        ),
        pytest.param(
            lambda team: team["operatives"][0].update(apl=2, move=8, save=3, keywords=["HOLLOW", "REVENANT"]),
            lambda session: (
                lambda row: (row.apl, row.move, row.save, row.keywords) == (2, 8, 3, ["HOLLOW", "REVENANT"])
            )(session.exec(select(KTOperative).where(KTOperative.name == "Hollow Warden")).one()),
            id="operative stats and keywords",
        ),
        pytest.param(
            lambda team: team["operatives"][0]["weapons"][0].update(
                attacks=5, hit=3, normal_damage=5, crit_damage=6, rules=['Range 4"', "Blast 2"]
            ),
            lambda session: (
                lambda row: (row.attacks, row.hit, row.normal_damage, row.crit_damage, row.weapon_rules)
                == (5, 3, 5, 6, ['Range 4"', "Blast 2"])
            )(
                session.exec(
                    select(KTWeapon).where(KTWeapon.name == "Brazier", KTWeapon.category == "range")
                ).one()
            ),
            id="weapon profile",
        ),
        pytest.param(
            lambda team: team["operatives"][0]["abilities"][0].update(description="Does something else."),
            lambda session: session.exec(select(KTAbility).where(KTAbility.name == "Warden's Vigil"))
            .one()
            .description
            == "Does something else.",
            id="ability description",
        ),
        pytest.param(
            lambda team: team["keyword_caps"][0].update(max_operatives=2),
            lambda session: session.exec(select(KTSelectionRestriction)).one().max_operatives == 2,
            id="keyword cap",
        ),
        pytest.param(
            lambda team: team.update(faction="Ashen Sentinels"),
            lambda session: session.exec(select(KillTeam)).one().faction.name == "Ashen Sentinels",
            id="faction",
        ),
    ],
)
def test_every_table_takes_a_source_side_change(session, edit, check):
    """One changed column per table, asserted to reach the database.

    Without these, reverting any of these tables to create-only leaves the suite green --
    so a source rewording an ability or halving a cap would stay stale forever, and
    nothing would say so.
    """
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    edit(payload["kill_teams"][0])

    counts = seed(session, payload)

    assert check(session)
    # the row was rewritten, not duplicated
    assert not any(count for name, count in counts.items() if name not in {"updated", "factions"})


def test_a_team_that_fails_leaves_nothing_behind(session):
    # One commit for the whole payload. A team that raises halfway must not leave the
    # teams before it half-loaded, because the next run would then see a catalog that no
    # single scrape ever produced.
    payload = copy.deepcopy(SAMPLE)
    broken = copy.deepcopy(SAMPLE["kill_teams"][0])
    broken["name"] = "Broken Choir"
    broken["selection_lists"][0]["options"][0]["operative"] = "Nobody"
    payload["kill_teams"].append(broken)

    with pytest.raises(SeedError, match="not one of the team's operatives"):
        seed(session, payload)

    assert session.exec(select(KillTeam)).all() == []
    assert session.exec(select(KTOperative)).all() == []
    assert session.exec(select(KTFaction)).all() == []


def test_a_team_ploy_named_like_the_universal_one_stays_separate(session):
    # The universal rows are matched with `kill_team_id IS NULL`. Drop that from the key
    # and the universal upsert would find the TEAM's ploy of the same name and overwrite
    # it -- which the partial unique index cannot prevent, since the two rows are legal.
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["ploys"].append(
        {"name": "COMMAND RE-ROLL", "kind": "strategy", "description": "The team's own version."}
    )

    seed(session, payload)

    rows = {
        p.kill_team_id is None: p
        for p in session.exec(select(KTPloy).where(KTPloy.name == "COMMAND RE-ROLL")).all()
    }
    assert len(rows) == 2
    assert rows[True].description == "Re-roll one die."  # the universal one
    assert rows[False].description == "The team's own version."
    assert rows[False].kind == "strategy"


def test_a_list_inserted_at_the_top_does_not_corrupt_the_lists_below_it(session):
    # The regression that made a composition a replace rather than an upsert. A list is
    # identified by its print position, so a new line at the top shifts every list down:
    # upserting rewrote each surviving row with the NEXT list's label and budget while it
    # kept its own options, leaving a budget-1 list offering two operatives, one of them
    # costing more than the whole budget -- a composition never printed anywhere.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["operatives"].append(
        {
            "name": "Hollow Herald",
            "apl": 2,
            "move": 6,
            "save": 4,
            "wounds": 10,
            "keywords": ["HOLLOW", "HERALD"],
            "availability": "roster",
            "weapons": [],
            "abilities": [],
        }
    )
    for listing in payload["kill_teams"][0]["selection_lists"]:
        listing["position"] += 1
    payload["kill_teams"][0]["selection_lists"].insert(
        0,
        {
            "label": "1 HOLLOW HERALD operative",
            "budget": 1,
            "shape": "budgeted",
            "requisition_source": None,
            "same_options_as": None,
            "position": 0,
            "restriction_text": None,
            "options": [
                {
                    "operative": "Hollow Herald",
                    "cost": 1,
                    "models": 1,
                    "max_selections": None,
                    "loadout_options": [],
                }
            ],
        },
    )

    counts = seed(session, payload)

    assert counts["compositions_replaced"] == 1
    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert [(row.position, row.budget, row.label) for row in lists] == [
        (0, 1, "1 HOLLOW HERALD operative"),
        (1, 1, "1 HOLLOW WARDEN operative"),
        (2, 4, "4 HOLLOW operatives selected from the following list:"),
    ]
    # each list offers exactly what the payload says, with nothing left over
    assert [[option.operative.name for option in row.options] for row in lists] == [
        ["Hollow Herald"],
        ["Hollow Warden"],
        ["Hollow Sentinel"],
    ]
    assert len(session.exec(select(KTSelectionOption)).all()) == 3


def test_two_lists_that_swap_positions_end_up_with_their_own_options(session):
    # The strictest ordering case: without deleting before inserting, reusing
    # (kill_team_id, position) inside one run hits the unique constraint.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    first, second = payload["kill_teams"][0]["selection_lists"]
    first["position"], second["position"] = 1, 0

    counts = seed(session, payload)

    assert counts["compositions_replaced"] == 1
    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert [(row.position, row.budget) for row in lists] == [(0, 4), (1, 1)]
    assert [[option.operative.name for option in row.options] for row in lists] == [
        ["Hollow Sentinel"],
        ["Hollow Warden"],
    ]
    assert len(session.exec(select(KTSelectionOption)).all()) == 2


def test_a_withdrawn_list_and_option_are_removed_from_the_composition(session):
    # Composition is replaced as a whole, so a line the source dropped goes with it --
    # unlike the rest of the catalog, where a removed row is left behind for K4.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["selection_lists"].pop()

    counts = seed(session, payload)

    assert counts["compositions_replaced"] == 1
    assert [row.position for row in session.exec(select(KTSelectionList)).all()] == [0]
    assert len(session.exec(select(KTSelectionOption)).all()) == 1


def test_an_unchanged_composition_is_left_alone(session):
    # The comparison is the whole subtree, so the common case writes nothing: no delete,
    # no insert, and the rows keep their ids.
    seed(session, SAMPLE)
    before = {row.position: row.id for row in session.exec(select(KTSelectionList)).all()}

    counts = seed(session, copy.deepcopy(SAMPLE))

    assert counts["compositions_replaced"] == 0
    assert {row.position: row.id for row in session.exec(select(KTSelectionList)).all()} == before


def test_a_changed_budget_replaces_the_composition(session):
    # A budget change used to hit UNIQUE(kill_team_id, position) when the budget was part
    # of the lookup key; now the whole composition is rewritten in place.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["selection_lists"][1]["budget"] = 5

    counts = seed(session, payload)

    assert counts["compositions_replaced"] == 1
    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert [(row.position, row.budget) for row in lists] == [(0, 1), (1, 5)]


def test_a_changed_option_alone_replaces_the_composition(session):
    # The comparison reaches into the options, not just the lists: a list whose label,
    # budget and position are all unchanged still has to be rewritten when what it OFFERS
    # changed -- a cost, a repeat cap or a printed loadout.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    option = payload["kill_teams"][0]["selection_lists"][1]["options"][0]
    option["cost"] = 1
    option["max_selections"] = None
    option["loadout_options"] = ["with ash lash", "with ember brand"]

    counts = seed(session, payload)

    assert counts["compositions_replaced"] == 1
    stored = session.exec(
        select(KTSelectionOption).join(KTOperative).where(KTOperative.name == "Hollow Sentinel")
    ).one()
    assert (stored.cost, stored.models, stored.max_selections) == (1, 2, None)
    assert stored.loadout_options == ["with ash lash", "with ember brand"]


def test_a_payload_missing_the_composition_section_leaves_it_alone(session):
    # Absent is not empty. A team whose page could not be read never reaches the seed at
    # all (it lands in the payload's `skipped`), so a missing key means a partial payload
    # -- and deleting every list and option over it, reported as a replace, would be the
    # worst possible reading.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    del payload["kill_teams"][0]["selection_lists"]

    counts = seed(session, payload)

    assert counts["compositions_replaced"] == 0
    assert len(session.exec(select(KTSelectionList)).all()) == 2
    assert len(session.exec(select(KTSelectionOption)).all()) == 2


def test_an_explicitly_empty_composition_is_a_statement_and_clears_it(session):
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["selection_lists"] = []

    counts = seed(session, payload)

    assert counts["compositions_replaced"] == 1
    assert session.exec(select(KTSelectionList)).all() == []
    assert session.exec(select(KTSelectionOption)).all() == []


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        pytest.param(
            lambda team: team["ploys"].append(dict(team["ploys"][0], kind="firefight")),
            "ploy 'ASHEN ADVANCE' appears twice",
            id="ploy name",
        ),
        pytest.param(
            lambda team: team["operatives"].append(copy.deepcopy(team["operatives"][0])),
            "operative 'Hollow Warden' appears twice",
            id="operative name",
        ),
        pytest.param(
            lambda team: team["selection_lists"].append(
                dict(copy.deepcopy(team["selection_lists"][0]), label="A second list here")
            ),
            "selection list position 0 appears twice",
            id="list position",
        ),
        pytest.param(
            lambda team: team["selection_lists"][1]["options"].append(
                copy.deepcopy(team["selection_lists"][1]["options"][0])
            ),
            "option 'Hollow Sentinel' appears twice",
            id="option operative",
        ),
        pytest.param(
            lambda team: team["operatives"][0]["weapons"].append(
                copy.deepcopy(team["operatives"][0]["weapons"][0])
            ),
            "weapon ('Brazier', 'range') appears twice",
            id="weapon name and category",
        ),
    ],
)
def test_a_payload_naming_the_same_thing_twice_is_refused(session, edit, message):
    # Each of these keys is a unique constraint, so the second entry would not become a
    # second row: the upsert would find the first and rewrite it, and the last one printed
    # would silently win. A request for that row would be refused as a conflict, so this is
    # refused too -- before anything is written, since the payload is one transaction.
    payload = copy.deepcopy(SAMPLE)
    edit(payload["kill_teams"][0])

    with pytest.raises(SeedError, match=re.escape(message)):
        seed(session, payload)

    assert session.exec(select(KillTeam)).all() == []


def test_the_payloads_order_becomes_the_stored_print_order(session):
    # The payload lists a datacard's profiles in the order the page prints them, so the
    # index IS the print order (decision #25). Stored, because rows have none of their own.
    seed(session, SAMPLE)

    warden = session.exec(select(KTOperative).where(KTOperative.name == "Hollow Warden")).one()
    assert [(w.category, w.position) for w in warden.weapons] == [("range", 0), ("melee", 1)]
    assert [a.position for a in warden.abilities] == [0, 1]
    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert [o.position for o in lists[0].options] == [0]


def test_a_reordered_datacard_moves_the_positions(session):
    # The page swaps a profile's place. Nothing about either weapon's data changed, so
    # without `position` the seed would report "nothing changed" and keep serving the old
    # order for good — which is exactly what it did before the column existed.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["operatives"][0]["weapons"].reverse()

    counts = seed(session, payload)

    assert counts["updated"] == 2  # both profiles moved
    assert counts["weapons"] == 0  # and neither was duplicated
    warden = session.exec(select(KTOperative).where(KTOperative.name == "Hollow Warden")).one()
    assert [(w.category, w.position) for w in warden.weapons] == [("melee", 0), ("range", 1)]


def test_a_reordered_option_list_is_replaced(session):
    # Options come with their list, so a reorder goes through the composition replace.
    payload = copy.deepcopy(SAMPLE)
    listing = payload["kill_teams"][0]["selection_lists"][1]
    listing["options"].append(
        {
            "operative": "Hollow Warden",
            "cost": 1,
            "models": 1,
            "max_selections": 1,
            "loadout_options": [],
        }
    )
    seed(session, payload)
    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert [o.operative.name for o in lists[1].options] == ["Hollow Sentinel", "Hollow Warden"]

    reordered = copy.deepcopy(payload)
    reordered["kill_teams"][0]["selection_lists"][1]["options"].reverse()
    counts = seed(session, reordered)

    assert counts["compositions_replaced"] == 1
    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert [o.operative.name for o in lists[1].options] == ["Hollow Warden", "Hollow Sentinel"]
    assert [o.position for o in lists[1].options] == [0, 1]


def test_a_chosen_rules_group_is_stored(session):
    # A technique's name is not usable without the section it was printed under, because
    # that is what says which operatives may take it (decision #27).
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["rules"].append(
        {
            "name": "THE RISING EMBER",
            "description": "Its first attack is critical.",
            "group": "Warden Ember Techniques",
        }
    )

    seed(session, payload)

    rules = {r.name: r for r in session.exec(select(KillTeamRule)).all()}
    assert rules["Ember Tide"].group is None
    assert rules["THE RISING EMBER"].group == "Warden Ember Techniques"


def test_a_lists_shape_is_stored_and_a_changed_shape_replaces_it(session):
    # `budget` means selections on a `budgeted` or `single` line and MODELS on a `fixed`
    # one, so the shape has to travel with the number (decision #29).
    seed(session, SAMPLE)
    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert [row.shape for row in lists] == ["budgeted", "budgeted"]

    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["selection_lists"][0]["shape"] = "fixed"
    counts = seed(session, payload)

    assert counts["compositions_replaced"] == 1
    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert [row.shape for row in lists] == ["fixed", "budgeted"]


def test_every_collection_keeps_the_payloads_order(session):
    # One convention, no per-table judgement (decision #25): the payload lists everything in
    # the order the page prints it, and every child of a kill team stores that index.
    payload = copy.deepcopy(SAMPLE)
    team = payload["kill_teams"][0]
    team["rules"].append({"name": "Ashen Vigil", "description": "Second rule.", "group": None})
    team["equipment"].append({"name": "Ash Token", "description": "Second piece."})

    seed(session, payload)

    stored = session.exec(select(KillTeam)).one()
    assert [(r.position, r.name) for r in stored.rules] == [(0, "Ember Tide"), (1, "Ashen Vigil")]
    # the page prints ASHEN ADVANCE (strategy) first, which ordering by `kind` reversed
    assert [(p.position, p.name) for p in stored.ploys] == [(0, "ASHEN ADVANCE"), (1, "EMBER GUARD")]
    assert [(q.position, q.name) for q in stored.equipment] == [(0, "Cinder Charm"), (1, "Ash Token")]
    assert [(o.position, o.name) for o in stored.operatives] == [
        (0, "Hollow Warden"),
        (1, "Hollow Sentinel"),
    ]
    # and the universal rows carry their own page order
    universal = session.exec(select(KTEquipment).where(KTEquipment.kill_team_id.is_(None))).all()
    assert [(q.position, q.name) for q in universal] == [(0, "1X AMMO CACHE"), (1, "2X LADDERS")]
    universal_ploys = sorted(
        session.exec(select(KTPloy).where(KTPloy.kill_team_id.is_(None))).all(),
        key=lambda row: row.position,
    )
    assert [(p.position, p.name) for p in universal_ploys] == [(0, "COMMAND RE-ROLL"), (1, "GUARD")]


def test_a_reordered_page_moves_every_position(session):
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["operatives"].reverse()
    payload["kill_teams"][0]["ploys"].reverse()

    counts = seed(session, payload)

    assert counts["operatives"] == 0 and counts["ploys"] == 0  # updated, not duplicated
    stored = session.exec(select(KillTeam)).one()
    assert [o.name for o in stored.operatives] == ["Hollow Sentinel", "Hollow Warden"]
    assert [p.name for p in stored.ploys] == ["EMBER GUARD", "ASHEN ADVANCE"]


def test_a_rule_is_scoped_to_its_team_even_when_two_teams_print_the_same_name(session):
    # "Astartes" is printed by seven kill teams and "Rifles" by two. Each is that team's own
    # row -- never one shared rule -- so a reader always knows whose it is.
    payload = copy.deepcopy(SAMPLE)
    second = copy.deepcopy(payload["kill_teams"][0])
    second["name"] = "Ashen Choir"
    payload["kill_teams"].append(second)

    seed(session, payload)

    rules = session.exec(select(KillTeamRule).where(KillTeamRule.name == "Ember Tide")).all()
    assert len(rules) == 2
    assert {r.kill_team.name for r in rules} == {"Hollow Vigil", "Ashen Choir"}
    assert all(r.kill_team_id is not None for r in rules)


def test_composition_notes_are_stored_on_the_team(session):
    # The page prints them under the whole composition, so that is where they live -- not
    # pinned to whichever list preceded them (decision #30).
    seed(session, SAMPLE)

    team = session.exec(select(KillTeam)).one()
    assert team.composition_notes == ["Other than SENTINEL operatives, once each."]


def test_a_changed_note_is_rewritten(session):
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["composition_notes"] = ["A reworded note.", "And a second one."]

    counts = seed(session, payload)

    assert counts["updated"] == 1
    team = session.exec(select(KillTeam)).one()
    assert team.composition_notes == ["A reworded note.", "And a second one."]


def test_a_list_with_no_options_is_stored_as_the_page_printed_it(session):
    # Inquisitorial Agent's cross-referencing line: the page states a budget and a source we
    # cannot structure, because an option can only offer its OWN team's operative
    # (decision #31). The label and the budget are still page facts, so they are stored.
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["selection_lists"].append(
        {
            "label": "5 HOLLOW operatives selected from the list above",
            "budget": 5,
            "position": 2,
            "shape": "budgeted",
            "requisition_source": None,
            "same_options_as": None,
            "restriction_text": None,
            "options": [],
        }
    )

    counts = seed(session, payload)

    assert counts["selection_lists"] == 3
    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert lists[2].options == []
    assert (lists[2].budget, lists[2].label) == (5, "5 HOLLOW operatives selected from the list above")

    # and re-seeding it is still a no-op, so an empty list is not mistaken for a change
    assert set(seed(session, copy.deepcopy(payload)).values()) == {0}


def test_an_operative_is_never_shared_between_kill_teams(session):
    # The separation that matters: two teams in one faction share nothing. The same operative
    # NAME on two teams is two rows, and an option can only offer its own team's operative --
    # which is enforced by the composite foreign key, not by the seed remembering to check.
    payload = copy.deepcopy(SAMPLE)
    second = copy.deepcopy(payload["kill_teams"][0])
    second["name"] = "Ashen Choir"
    payload["kill_teams"].append(second)

    seed(session, payload)

    wardens = session.exec(select(KTOperative).where(KTOperative.name == "Hollow Warden")).all()
    assert len(wardens) == 2
    assert {w.kill_team.name for w in wardens} == {"Hollow Vigil", "Ashen Choir"}
    # every option points at an operative of its OWN team
    for option in session.exec(select(KTSelectionOption)).all():
        assert option.operative.kill_team_id == option.selection_list.kill_team_id
        assert option.kill_team_id == option.operative.kill_team_id


def test_a_requisition_list_references_the_team_it_names(session):
    # A reference, never shared rows: Death Korps' operatives stay Death Korps' (#31), and the
    # list says "you may field that team's operatives here" (#34). Resolved in a second pass,
    # because the payload's order is the site's — a team can be read before the one it names.
    payload = copy.deepcopy(SAMPLE)
    ally = copy.deepcopy(payload["kill_teams"][0])
    ally["name"] = "Ashen Choir"
    payload["kill_teams"][0]["selection_lists"].append(
        {
            "label": "5 ASHEN CHOIR operatives selected from the following list:",
            "budget": 5,
            "position": 2,
            "shape": "budgeted",
            "requisition_source": "Ashen Choir",
            "same_options_as": None,
            "restriction_text": None,
            "options": [],
        }
    )
    # the ally comes SECOND in the payload, so a single pass could not have linked it
    payload["kill_teams"].append(ally)

    seed(session, payload)

    requisition = session.exec(
        select(KTSelectionList).where(KTSelectionList.requisition_source.is_not(None))
    ).one()
    ally_row = session.exec(select(KillTeam).where(KillTeam.name == "Ashen Choir")).one()
    assert requisition.from_kill_team_id == ally_row.id
    assert requisition.kill_team.name == "Hollow Vigil"  # owned here, references there
    assert requisition.options == []  # nothing is copied


def test_a_requisition_group_with_no_team_of_its_own_keeps_a_null_reference(session):
    # Sister of Silence and Tempestus Scion are not kill teams, which is why the page carries
    # their datacards as its own rows — so the group offers them directly and references
    # nothing.
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["selection_lists"].append(
        {
            "label": "5 EMBER WARDEN operatives selected from the following list:",
            "budget": 5,
            "position": 2,
            "shape": "budgeted",
            "requisition_source": "Ember Wardens",
            "same_options_as": None,
            "restriction_text": None,
            "options": [
                {
                    "operative": "Hollow Sentinel",
                    "cost": 1,
                    "models": 1,
                    "max_selections": None,
                    "loadout_options": [],
                }
            ],
        }
    )

    seed(session, payload)

    requisition = session.exec(
        select(KTSelectionList).where(KTSelectionList.requisition_source == "Ember Wardens")
    ).one()
    assert requisition.from_kill_team_id is None
    assert [o.operative.name for o in requisition.options] == ["Hollow Sentinel"]


def test_a_list_referencing_another_is_linked_within_the_team(session):
    # Positions are unique within a team, so the reference resolves in the same pass — unlike
    # a requisition pointing at another TEAM, which needs every team to exist first (#34).
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["selection_lists"].append(
        {
            "label": "4 HOLLOW operatives selected from the list above",
            "budget": 4,
            "position": 2,
            "shape": "budgeted",
            "requisition_source": None,
            "same_options_as": 1,
            "restriction_text": None,
            "options": [],
        }
    )

    seed(session, payload)

    lists = sorted(session.exec(select(KTSelectionList)).all(), key=lambda row: row.position)
    assert lists[2].same_options_as_id == lists[1].id
    assert lists[2].options == []  # stated once, on the list it points at
    assert lists[2].budget == 4  # but the page states its own budget

    # and re-seeding is still a no-op: the payload names a position, the row holds an id
    assert set(seed(session, copy.deepcopy(payload)).values()) == {0}


def test_an_in_battle_operative_is_stored_as_such(session):
    # Gellerpox's three Mutoid Vermin: no list offers them because no roster may take them
    # (decision #20). The flag is what says so, and what K5's add-an-operative screen needs.
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["operatives"].append(
        {
            "name": "Hollow Cursemite",
            "apl": 1,
            "move": 4,
            "save": 6,
            "wounds": 4,
            "keywords": ["HOLLOW", "VERMIN"],
            "availability": "in_battle",
            "weapons": [],
            "abilities": [],
        }
    )

    seed(session, payload)

    by_name = {o.name: o for o in session.exec(select(KTOperative)).all()}
    assert by_name["Hollow Cursemite"].availability == "in_battle"
    assert by_name["Hollow Warden"].availability == "roster"
