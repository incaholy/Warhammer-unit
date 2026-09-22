"""The Kill Team nav parser (scripts.scrape_wahapedia_kt.parse_nav).

Runs against a synthetic fixture reproducing Wahapedia's nav DOM — no network, no
scraped content. The fetch layer is shared with the 40k scraper and is not tested
here.
"""

from pathlib import Path

import pytest

from scripts.scrape_wahapedia_kt import (
    NavEntry,
    parse_equipment,
    parse_nav,
    parse_operatives,
    parse_ploys,
    parse_team_rules,
    universal_equipment_url,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "kt_nav.html").read_text()
TEAM = (Path(__file__).parent / "fixtures" / "kt_team.html").read_text()
UNIVERSAL_EQUIPMENT = (Path(__file__).parent / "fixtures" / "kt_universal_equipment.html").read_text()


def test_every_kill_team_comes_back_with_its_faction_and_slug():
    entries = parse_nav(FIXTURE)

    assert entries == [
        NavEntry(name="Hollow Sentinels", faction="Vigil Host", slug="hollow-sentinels"),
        NavEntry(name="Ashen Choir", faction="Vigil Host", slug="ashen-choir"),
        NavEntry(name="Rust Wardens", faction="Iron Covenant", slug="rust-wardens"),
        NavEntry(name="Tidewalkers", faction="Kaeth'ar Compact", slug="tidewalkers"),
    ]


def test_the_faction_carries_over_to_the_links_beneath_it():
    # The nav is a flat run of siblings, not nesting: a factionGroup_KT applies to
    # every link after it until the next one. Two teams under one faction is the case
    # that catches a parser reading only the nearest preceding label.
    by_faction: dict[str, list[str]] = {}
    for entry in parse_nav(FIXTURE):
        by_faction.setdefault(entry.faction, []).append(entry.name)

    assert by_faction["Vigil Host"] == ["Hollow Sentinels", "Ashen Choir"]
    assert by_faction["Iron Covenant"] == ["Rust Wardens"]


def test_the_grand_alliance_is_dropped():
    # KILLTEAM.md decision #12: the faction list is flat. The nav's FactionHeader
    # level (Imperium / Chaos / Xenos / Aeldari) is read past, never stored -- and it
    # is not symmetric with 40k's, which is why those tables were not reused.
    factions = {entry.faction for entry in parse_nav(FIXTURE)}

    assert "Dominion" not in factions  # the fixture's alliance labels
    assert "Outlanders" not in factions
    assert factions == {"Vigil Host", "Iron Covenant", "Kaeth'ar Compact"}


def test_a_faction_label_is_normalised_to_the_name_we_store():
    # The nav writes a typographic apostrophe; the 40k side stores a straight one, and
    # two spellings of one faction would seed two rows.
    factions = {entry.faction for entry in parse_nav(FIXTURE)}

    assert "Kaeth'ar Compact" in factions
    assert "Kaeth’ar Compact" not in factions


def test_only_the_kill_teams_dropdown_is_read():
    # The fixture links a kill team from the Rules dropdown as well. Searching the
    # whole document would collect it -- and it is not a kill team page we want.
    slugs = {entry.slug for entry in parse_nav(FIXTURE)}

    assert "not-in-the-dropdown" not in slugs


def test_each_entry_knows_its_page_url():
    # With the trailing slash: the site 301s the slash-less form, and `fetch()` keys
    # its cache on the URL requested, so 48 teams would each pay a redirect first.
    entry = parse_nav(FIXTURE)[0]
    assert entry.url == "https://wahapedia.ru/kill-team3/kill-teams/hollow-sentinels/"


def test_a_missing_dropdown_fails_loudly():
    # A silent empty list would seed an empty catalog and look like a working run.
    with pytest.raises(ValueError, match="no 'Kill Teams' dropdown"):
        parse_nav("<html><body><div class='NavBtn NavBtn_Rules'>The Rules</div></body></html>")


def test_a_dropdown_with_no_teams_fails_loudly():
    html = """
    <div class="NavBtn NavBtn_Factions">Kill Teams</div>
    <div class="NavDropdown-content"><div class="FactionHeader">Imperium</div></div>
    """
    with pytest.raises(ValueError, match="no kill team links"):
        parse_nav(html)


def test_a_team_before_any_faction_group_fails_loudly():
    # Rather than inventing a faction or dropping the team: both would be a partial
    # catalog that looks complete.
    html = """
    <div class="NavBtn NavBtn_Factions">Kill Teams</div>
    <div class="NavDropdown-content">
      <a href="/kill-team3/kill-teams/orphan">Orphan</a>
    </div>
    """
    with pytest.raises(ValueError, match="before any faction group"):
        parse_nav(html)


# ---- The datacards on a kill team's page ----


def _by_name(html: str = TEAM):
    return {op.name: op for op in parse_operatives(html)}


def test_every_operative_on_the_page_is_read():
    # Two frames, so a parser cannot pass by reading only the first datacard.
    assert list(_by_name()) == ["Hollow Sentinel Warden", "Hollow Sentinel"]


def test_the_stat_line_is_read_by_label_not_by_column():
    # Labels, so a layout change moves a value rather than silently swapping two --
    # APL 3 and SAVE 5 would be indistinguishable by position if the order changed.
    warden = _by_name()["Hollow Sentinel Warden"]

    assert (warden.apl, warden.move, warden.save, warden.wounds) == (3, 7, 5, 19)

    # The second operative's cells are in a DIFFERENT order in the fixture, with four
    # distinct values, so a parser reading by column returns them swapped.
    sentinel = _by_name()["Hollow Sentinel"]
    assert (sentinel.apl, sentinel.move, sentinel.save, sentinel.wounds) == (2, 6, 4, 12)


def test_a_multi_word_keyword_stays_one_keyword():
    # "HOLLOW VIGIL" is two spans on the page. Joining elements with a comma split it
    # into two keywords; the strip already prints its own commas.
    assert _by_name()["Hollow Sentinel Warden"].keywords == [
        "SENTINEL",
        "HOLLOW VIGIL",
        "WARDEN",
    ]


def test_the_base_size_is_stripped_without_losing_the_last_keyword():
    # The base size trails the last keyword in the same chunk ("WARDEN ⌀40mm").
    # Dropping the chunk lost each operative's most specific keyword.
    keywords = _by_name()["Hollow Sentinel Warden"].keywords

    assert "WARDEN" in keywords
    assert not any("⌀" in k or "40mm" in k for k in keywords)


def test_a_weapon_is_ranged_or_melee_by_its_row_class():
    # Nothing in a row's TEXT says which: a short-ranged weapon prints like a melee
    # one. The site marks it with wsDataRanged / wsDataMelee on the name row.
    weapons = {w.name: w for w in _by_name()["Hollow Sentinel Warden"].weapons}

    assert weapons["Ash lash"].category == "range"
    assert weapons["Rusted glaive"].category == "melee"


def test_damage_is_split_into_normal_and_critical():
    # The page prints "3/4"; the model stores two integers.
    ash = {w.name: w for w in _by_name()["Hollow Sentinel Warden"].weapons}["Ash lash"]

    assert (ash.normal_damage, ash.crit_damage) == (3, 4)
    assert (ash.attacks, ash.hit) == (4, 3)


def test_a_printed_range_rule_becomes_the_range_and_stays_in_the_rules():
    weapons = {w.name: w for w in _by_name()["Hollow Sentinel Warden"].weapons}

    assert weapons["Ash lash"].range == 3
    assert 'Range 3"' in weapons["Ash lash"].rules


def test_a_weapon_without_a_printed_range_leaves_it_to_the_column_default():
    # KILLTEAM.md #15: the per-category default (melee 1, range 2) belongs to the
    # column, so the parser reports None rather than inventing the same number in a
    # second place.
    weapons = {w.name: w for w in _by_name()["Hollow Sentinel Warden"].weapons}

    assert weapons["Cinder bolt"].range is None
    assert weapons["Cinder bolt"].rules == ["Piercing 1"]
    assert weapons["Rusted glaive"].range is None


def test_a_weapon_with_no_rules_reads_as_an_empty_list():
    glaive = {w.name: w for w in _by_name()["Hollow Sentinel Warden"].weapons}["Rusted glaive"]
    assert glaive.rules == []


def test_abilities_and_unique_actions_come_back_together():
    # One list for both: they read the same way on a datacard, and the tracker shows
    # the text rather than acting on it.
    warden = _by_name()["Hollow Sentinel Warden"]

    assert [a.name for a in warden.abilities] == ["Warden's Vigil", "EMBER STRIKE"]


def test_an_ability_keeps_its_rule_text_without_repeating_its_name():
    # The block prints "NAME: text"; only the text is the description.
    vigil = _by_name()["Hollow Sentinel Warden"].abilities[0]

    assert vigil.name == "Warden's Vigil"
    assert vigil.description == "Whenever this operative is activated, it may do the thing."
    assert not vigil.description.startswith("Warden's Vigil")


def test_a_unique_action_keeps_its_ap_cost_in_the_text():
    # No AP column: the cost stays where a player reads it, at the front.
    strike = _by_name()["Hollow Sentinel Warden"].abilities[1]

    assert strike.name == "EMBER STRIKE"  # not "EMBER STRIKE1AP"
    assert strike.description.startswith("1AP.")
    assert "inflict D3 damage" in strike.description


def test_a_unique_action_is_read_once_despite_nesting():
    # `BreakInsideAvoid` nests around an action, so selecting on it alone returned
    # every action twice. Actions are matched by their `stratWrapper` instead.
    names = [a.name for a in _by_name()["Hollow Sentinel Warden"].abilities]

    assert names.count("EMBER STRIKE") == 1


def test_an_operative_with_no_abilities_gets_an_empty_list():
    assert _by_name()["Hollow Sentinel"].abilities == []


def test_a_page_with_no_datacards_fails_loudly():
    # Seeding a kill team with an empty roster list would look like a working run.
    with pytest.raises(ValueError, match="no operatives"):
        parse_operatives("<html><body><div>Not a datacard</div></body></html>")


# ---- The team-level sections ----


def test_team_rules_come_from_the_faction_rules_section():
    rules = parse_team_rules(TEAM)

    assert [r.name for r in rules] == ["Ember Tide", "Hollow Resolve"]


def test_a_rule_keeps_an_action_printed_inside_it():
    # Raveners print the Burrow ACTION within the Burrow rule. It is not an
    # operative's action, and `KillTeamRule` is name-and-text, so it stays in the
    # rule -- only the element carrying the rule's own NAME is removed.
    ember = parse_team_rules(TEAM)[0]

    assert "STOKE" in ember.description
    assert "1AP" in ember.description
    assert ember.description.startswith("At the end of the Set Up Operatives step")


def test_flavour_text_is_left_out_of_every_rule():
    # `ShowFluff` is prose about the faction; the tracker shows rules.
    for rule in parse_team_rules(TEAM):
        assert "flavour" not in rule.description.lower()


def test_a_rule_does_not_repeat_its_own_name():
    assert not parse_team_rules(TEAM)[1].description.startswith("Hollow Resolve")


def test_both_kinds_of_ploy_are_read_with_the_kind_the_rules_use():
    # The site's class says "Tactical"; the section is printed "Firefight Ploys",
    # which is what `KTPloy.kind` stores.
    ploys = parse_ploys(TEAM)

    assert [(p.name, p.kind) for p in ploys] == [
        ("ASHEN ADVANCE", "strategy"),
        ("EMBER GUARD", "firefight"),
    ]


def test_a_ploy_keeps_its_rule_text_and_drops_its_flavour():
    ploy = parse_ploys(TEAM)[0]

    assert ploy.description == 'Friendly operatives may move an extra 1" this turning point.'


def test_equipment_is_not_read_as_a_ploy():
    # Equipment shares the stratWrapper shape, told apart by the stratName variant.
    assert "Cinder Charm" not in {p.name for p in parse_ploys(TEAM)}


def test_hidden_tooltip_copies_are_not_read_as_extra_content():
    # The site repeats some blocks inside `div.tooltip_templates` to fill hover
    # popups. Novitiates' page copies two firefight ploys that way, so reading them
    # produced each ploy twice -- which UNIQUE(kill_team_id, name) would reject at
    # seed time, after the scrape had already "succeeded".
    ploys = parse_ploys(TEAM)

    assert [p.name for p in ploys].count("EMBER GUARD") == 1
    assert len({p.name for p in ploys}) == len(ploys)


def test_a_page_with_no_faction_rules_section_returns_nothing():
    # Unlike a page with no datacards, this is legitimate: not every team has its
    # own rules section, so an empty list is an answer rather than a failure.
    assert parse_team_rules("<html><body><h2>Something else</h2></body></html>") == []


# ---- Equipment ----


def test_equipment_is_read_from_the_same_blocks_as_ploys():
    # Ploys and equipment share the stratWrapper shape; the stratName variant is
    # what tells them apart, so this must not pick up a ploy.
    items = parse_equipment(TEAM)

    assert [i.name for i in items] == ["Cinder Charm"]
    assert items[0].description == "The bearer may re-roll one defence die."


def test_an_equipment_name_is_stored_as_printed():
    # The universal page prefixes quantities ("1X AMMO CACHE", "2X LADDERS") where
    # faction equipment has none. Stored verbatim: the quantity is part of what the
    # page calls the entry, and a tidier invented name diverges from the source.
    items = {i.name: i for i in parse_equipment(UNIVERSAL_EQUIPMENT)}

    assert "1X AMMO CACHE" in items
    assert "AMMO CACHE" not in items


def test_a_ploy_is_not_read_as_equipment():
    assert "ASHEN ADVANCE" not in {i.name for i in parse_equipment(TEAM)}


def test_the_universal_equipment_page_is_found_from_the_nav():
    # One URL is spelled out in this module (the nav); everything else is discovered,
    # including this page. The trailing slash avoids the site's 301.
    assert (
        universal_equipment_url(FIXTURE) == "https://wahapedia.ru/kill-team3/the-rules/universal-equipment/"
    )


def test_a_nav_without_the_universal_equipment_link_fails_loudly():
    with pytest.raises(ValueError, match="universal equipment"):
        universal_equipment_url("<html><body><a href='/kill-team3/kill-teams/x'>X</a></body></html>")
