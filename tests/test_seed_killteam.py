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
    KTSelectionRule,
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
            "selection_rules": [
                {"position": 0, "depth": 0, "kind": "line", "text": "1 HOLLOW WARDEN operative"},
                {
                    "position": 1,
                    "depth": 0,
                    "kind": "line",
                    "text": "4 HOLLOW operatives selected from the following list:",
                },
                {"position": 2, "depth": 1, "kind": "line", "text": "SENTINEL with ash lash"},
                {
                    "position": 3,
                    "depth": 0,
                    "kind": "restriction",
                    "text": "Other than SENTINEL operatives, once each.",
                },
                {"position": 4, "depth": 0, "kind": "note", "text": "A footnote the page prints."},
            ],
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
            lambda team: team["selection_rules"].append(
                dict(copy.deepcopy(team["selection_rules"][0]), text="A second rule here")
            ),
            "selection rule position 0 appears twice",
            id="rule position",
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


def test_a_composition_is_stored_as_the_page_printed_it(session):
    # Text and an indent depth, nothing derived (decision #28): no budget, no cost, no cap
    # and no option resolved against a datacard. Read in position order, indenting by
    # depth, and the page's composition section is back.
    seed(session, SAMPLE)

    rules = session.exec(select(KTSelectionRule).order_by(KTSelectionRule.position)).all()

    assert [(r.position, r.depth, r.kind) for r in rules] == [
        (0, 0, "line"),
        (1, 0, "line"),
        (2, 1, "line"),
        (3, 0, "restriction"),
        (4, 0, "note"),
    ]
    assert rules[2].text == "SENTINEL with ash lash"


def test_a_changed_composition_is_replaced_whole_rather_than_rewritten(session):
    # A rule is identified by its print POSITION, because nothing else about a line is
    # stable. A page that gains or moves a line shifts every row after it, so rewriting
    # in place would pair each surviving row with the next rule's text and report a tidy
    # incremental update. The whole run is replaced instead.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    rules = payload["kill_teams"][0]["selection_rules"]
    rules.insert(0, {"position": 0, "depth": 0, "kind": "line", "text": "1 NEW LEADER operative"})
    for index, rule in enumerate(rules):
        rule["position"] = index

    counts = seed(session, payload)

    assert counts["compositions_replaced"] == 1
    assert counts["selection_rules"] == 0  # replaced, not created alongside
    stored = session.exec(select(KTSelectionRule).order_by(KTSelectionRule.position)).all()
    assert [r.text for r in stored][:2] == ["1 NEW LEADER operative", "1 HOLLOW WARDEN operative"]
    assert [r.position for r in stored] == [0, 1, 2, 3, 4, 5]


def test_an_unchanged_composition_is_not_touched(session):
    # The common case on a re-run, and the reason the comparison exists at all.
    seed(session, SAMPLE)

    counts = seed(session, copy.deepcopy(SAMPLE))

    assert counts["compositions_replaced"] == 0
    assert counts["selection_rules"] == 0


def test_a_payload_with_no_composition_key_leaves_the_rules_alone(session):
    # Absent is NOT empty. The scraper raises rather than emitting a team without the
    # section, so a missing key means a partial or hand-made payload -- and reading it as
    # "this team has no composition" would delete every rule, reported as a tidy replace.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    del payload["kill_teams"][0]["selection_rules"]

    seed(session, payload)

    assert len(session.exec(select(KTSelectionRule)).all()) == 5


def test_an_explicitly_empty_composition_does_replace_it_with_nothing(session):
    # The other half of absent-is-not-empty: `[]` is a statement, and it is honoured.
    seed(session, SAMPLE)
    payload = copy.deepcopy(SAMPLE)
    payload["kill_teams"][0]["selection_rules"] = []

    seed(session, payload)

    assert session.exec(select(KTSelectionRule)).all() == []
