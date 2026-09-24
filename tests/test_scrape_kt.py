"""The Kill Team nav parser (scripts.scrape_wahapedia_kt.parse_nav).

Runs against a synthetic fixture reproducing Wahapedia's nav DOM — no network, no
scraped content. The fetch layer is shared with the 40k scraper and is not tested
here.
"""

from pathlib import Path

import pytest

from scripts.scrape_wahapedia_kt import (
    CompositionNotParsed,
    KeywordCap,
    NavEntry,
    OperativeNotResolved,
    parse_composition,
    parse_equipment,
    parse_nav,
    parse_operatives,
    parse_ploys,
    parse_team_rules,
    resolve_operative,
    universal_equipment_url,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "kt_nav.html").read_text()
TEAM = (Path(__file__).parent / "fixtures" / "kt_team.html").read_text()
COMPOSITION = (Path(__file__).parent / "fixtures" / "kt_composition.html").read_text()
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


# ---- Matching a composition entry to a datacard ----
#
# A page names each operative twice: the composition says FELLTALON, the datacard says
# "Ravener Felltalon". `KTSelectionOption` needs the datacard, and the scraper is the
# only place holding both halves.

RAVENERS = ["Ravener Prime", "Ravener Felltalon", "Ravener Warrior", "Ravener Wrecker"]
SPECTRES = ["Spectre Gunner", "Spectre Heavy Gunner", "Spectre Stub-Gunner", "Spectre Guide"]


def test_an_entry_naming_the_datacard_outright_resolves():
    assert resolve_operative("RAVENER WARRIOR", RAVENERS) == "Ravener Warrior"


def test_an_entry_that_drops_the_team_prefix_resolves():
    # The commonest shape by far: 292 of the 444 real entries.
    assert resolve_operative("FELLTALON", RAVENERS) == "Ravener Felltalon"


def test_the_shortest_match_wins_when_several_cards_end_with_the_entry():
    # GUNNER matches three Spectre cards. The plain one is meant -- the others are
    # their own entries and match themselves exactly.
    assert resolve_operative("GUNNER", SPECTRES) == "Spectre Gunner"
    assert resolve_operative("HEAVY GUNNER", SPECTRES) == "Spectre Heavy Gunner"
    assert resolve_operative("STUB-GUNNER", SPECTRES) == "Spectre Stub-Gunner"


def test_an_entry_resolves_when_the_datacard_adds_a_suffix():
    assert resolve_operative("AUTOSAVANT", ["Autosavant Agent", "Interrogator Agent"]) == ("Autosavant Agent")


def test_an_entry_resolves_when_the_words_are_in_a_different_order():
    assert (
        resolve_operative("INQUISITORIAL AGENT INTERROGATOR", ["Interrogator Agent", "Tome-Skull"])
        == "Interrogator Agent"
    )


def test_an_entry_resolves_on_its_last_word_when_the_prefixes_differ():
    # The composition uses the kill team's name, the datacard the unit's: "EXACTION
    # SQUAD PROCTOR-EXACTANT" against "Arbites Proctor-Exactant". Ten real entries.
    assert (
        resolve_operative(
            "EXACTION SQUAD PROCTOR-EXACTANT", ["Arbites Proctor-Exactant", "Arbites Castigator"]
        )
        == "Arbites Proctor-Exactant"
    )


def test_punctuation_does_not_decide_a_match():
    assert resolve_operative("PROCTOR EXACTANT", ["Arbites Proctor-Exactant"]) == ("Arbites Proctor-Exactant")


def test_an_entry_matching_two_cards_equally_raises_rather_than_guessing():
    # Picking one would seed a roster list that looks legal and offers the wrong
    # operative.
    with pytest.raises(OperativeNotResolved, match="several datacards"):
        resolve_operative("GUNNER", ["Alpha Gunner", "Omega Gunner"])


def test_an_entry_matching_nothing_raises_with_the_candidates():
    with pytest.raises(OperativeNotResolved, match="no datacard"):
        resolve_operative("SOMETHING ELSE", RAVENERS)


def test_a_loadout_line_resolves_to_nothing_which_is_how_it_is_recognised():
    # This is the discriminator, and it replaces reading a style class. The site marks
    # a keyword only on its FIRST appearance on a page, so "GUNNER with webber and gun
    # butt" carries no styled span although it names an operative, while
    # "Autogun; gun butt" looks identical and does not. No datacard is called Autogun.
    assert resolve_operative("Autogun; gun butt", SPECTRES, strict=False) is None
    assert resolve_operative("GUNNER", SPECTRES, strict=False) == "Spectre Gunner"


def test_an_empty_entry_is_not_an_operative():
    assert resolve_operative("", RAVENERS, strict=False) is None
    with pytest.raises(OperativeNotResolved):
        resolve_operative("   ", RAVENERS)


# ---- Composition: the budgeted selection lists (decision #16) ----


def _composition():
    return parse_composition(COMPOSITION)


def _lists():
    return _composition().lists


def _options(index: int):
    return {option.operative: option for option in _lists()[index].options}


def test_each_printed_line_becomes_a_budgeted_list_in_order():
    lists = _lists()

    assert [(lst.position, lst.budget) for lst in lists] == [(0, 1), (1, 4), (2, 1), (3, 3)]
    assert lists[0].label == "1 HOLLOW WARDEN operative"


def test_a_line_naming_its_own_operative_is_a_budget_of_one():
    # "1 HOLLOW WARDEN operative" has no entries beneath it: the line IS the option, and
    # its number is the budget, not models. A budget-1 list over one option is what
    # "required" means (decision #16), so no flag is needed.
    warden = _lists()[0]

    assert warden.budget == 1
    assert [option.operative for option in warden.options] == ["Hollow Warden"]
    assert warden.options[0].models == 1


def test_a_numbered_line_takes_its_budget_from_the_leading_number():
    assert _lists()[1].budget == 4


def test_digits_inside_a_name_are_not_read_as_a_budget():
    # The third line names "XV9 EMBER SUIT" and carries no leading count, so its budget
    # is 1. Reading any number in the text instead would make it 9 -- the real bug my
    # own first scan hit, where "XV26 Stealth Battlesuits" became 26 operatives.
    suits = _lists()[2]

    assert suits.budget == 1
    assert [option.operative for option in suits.options] == ["XV9 Ember Suit"]


def test_one_operative_printed_twice_collapses_into_one_option():
    # Wyrmblade prints three "GUNNER with ..." lines and 12 of the 48 teams repeat an
    # operative that way. It is one operative with a weapon choice, and
    # UNIQUE(selection_list_id, operative_id) would reject two rows for it.
    sentinel = _options(1)["Hollow Sentinel"]

    assert [option.operative for option in _lists()[1].options].count("Hollow Sentinel") == 1
    assert sentinel.loadout_options == [
        "with ash lash and rusted glaive",
        "with cinder bolt and rusted glaive",
    ]


def test_an_entry_can_put_two_models_on_the_table_for_one_selection():
    # "2 EMBER WISP operatives (still counts as one selection)".
    wisp = _options(1)["Hollow Ember Wisp"]

    assert (wisp.models, wisp.cost) == (2, 1)


def test_an_entry_can_cost_more_than_one_selection():
    # "ASH PROPHET (counts as two selections)" -- the parenthetical is a note about the
    # entry, so it is stripped before the name is resolved and read for the cost.
    assert _options(1)["Hollow Ash Prophet"].cost == 2


def test_loadout_variants_are_collected_from_the_nested_list():
    # "WARDEN equipped with one of the following options:" -- and "equipped with" as
    # well as "with", which is how Nemesis Claw phrases it.
    # Every nested group, not just the first: a line reading "equipped with one of the
    # following options: ... Or one option from each of the following:" prints two.
    assert _options(1)["Hollow Warden"].loadout_options == [
        "Ash lash; rusted glaive",
        "Cinder bolt; rusted glaive",
        "Ember charm or ash token",
    ]


def test_a_loadout_line_is_not_read_as_an_operative():
    # "Ash lash; rusted glaive" resolves to no datacard, which is how it is recognised.
    assert "Ash lash; rusted glaive" not in _options(1)


def test_the_restriction_sentence_is_kept_verbatim():
    # A loose text node after the list, and the source of the caps and keyword limits
    # read from it next. Kept whole so a human can check that reading.
    restriction = _lists()[-1].restriction_text

    # Attached to the LAST list in print order, which is what "this list" refers to
    # when the sentence is printed after the whole composition.
    assert _lists()[-1].position == 3
    assert restriction is not None
    assert "each operative on this list once" in restriction
    assert "up to one EMBER operative" in restriction


def test_a_nested_list_becomes_its_own_list():
    # Two teams print a second list INSIDE the first rather than beside it: Blades of
    # Khaine nests it under the leader line, and Hunter Clade wraps it in a div inside
    # the same `ul`, so it is neither a direct child nor a descendant of another line.
    # Both used to fold their operatives into the budget-1 line above -- silently
    # offering a one-operative team, which is the worst kind of wrong.
    nested = _lists()[3]

    assert nested.budget == 3
    assert {option.operative for option in nested.options} == {
        "Hollow Ash Prophet",
        "Hollow Ember Wisp",
        "Hollow Sentinel",
    }


def test_a_nested_lists_operatives_do_not_leak_into_the_line_above():
    # An entry belongs to its NEAREST enclosing list.
    assert len(_lists()[2].options) == 1  # the line that contains the nested list


def test_an_ambiguous_entry_stops_the_team_instead_of_vanishing():
    # Hunter Clade prints "WARRIOR SICARIAN *", which matches both the Infiltrator and
    # the Ruststalker Warrior; the footnote tells a human which. The parser asks
    # "is this an operative?" leniently, and a lenient None would have dropped the
    # entry as though it were a weapon loadout -- so ambiguity raises even then.
    html = """
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Alpha Warrior</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table></div>
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Omega Warrior</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table></div>
    <h2>Operatives</h2>
    <ul class="redTriangle"><li>2 THINGS selected from the following list:
      <ul class="redCircle2"><li><span class="kwb kwbo">WARRIOR</span> *</li></ul>
    </li></ul>
    """
    with pytest.raises(OperativeNotResolved, match="several datacards"):
        parse_composition(html)


def test_the_restriction_sentence_caps_repeats_at_one():
    # "your kill team can only include each operative on this list once" -- 39 of the 42
    # restriction sentences say this, and it is what KTSelectionOption.max_selections
    # holds.
    options = {option.operative: option for option in _lists()[-1].options}

    assert options["Hollow Ash Prophet"].max_selections == 1
    assert options["Hollow Ember Wisp"].max_selections == 1


def test_an_exempt_keyword_stays_uncapped():
    # "Other than SENTINEL operatives, ..." -- and the exemption is a KEYWORD matched
    # against what the datacard carries, not an operative name: Raveners exempt WARRIOR
    # and "Ravener Warrior" holds that keyword. 38 sentences carry such a clause.
    options = {option.operative: option for option in _lists()[-1].options}

    assert options["Hollow Sentinel"].max_selections is None


def test_a_keyword_cap_is_read_as_its_own_rule():
    # "can only include up to one EMBER operative" caps a SET of operatives, which no
    # per-option limit can express: two different entries both carry EMBER.
    # On the COMPOSITION, not a list: the sentence says "your kill team", and Brood
    # Brother caps a keyword carried by operatives from a different list than the one
    # the sentence follows, so a list-scoped cap could never have applied.
    assert _composition().keyword_caps == [KeywordCap(keyword="EMBER", max_operatives=1)]


def test_a_cap_is_matched_by_words_because_the_pages_disagree_about_keywords():
    # Battleclade's datacards print "COMBAT, SERVITOR" -- two comma-separated keywords --
    # while Pathfinders prints "WEAPONS EXPERT" as one. Both are capped by a sentence
    # naming the phrase, so matching compares WORDS, as name resolution does.
    two_keywords = KeywordCap(keyword="COMBAT SERVITOR", max_operatives=3)
    one_keyword = KeywordCap(keyword="WEAPONS EXPERT", max_operatives=2)

    assert two_keywords.matches(["BATTLECLADE", "COMBAT", "SERVITOR"])
    assert one_keyword.matches(["PATHFINDER", "WEAPONS EXPERT"])
    # and the other way round, since the pages are inconsistent in both directions
    assert two_keywords.matches(["BATTLECLADE", "COMBAT SERVITOR"])


def test_a_cap_does_not_match_an_operative_without_every_word():
    cap = KeywordCap(keyword="COMBAT SERVITOR", max_operatives=3)

    assert not cap.matches(["BATTLECLADE", "GUN", "SERVITOR"])
    assert not cap.matches([])


def test_a_cap_matching_nobody_on_the_page_raises():
    # A cap that can never trigger means the phrase was misread, and it would leave
    # `validate` approving rosters it should refuse -- silently, which is worse than
    # failing the team.
    html = """
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>A Card</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table>
    <table class="dsKeywords"><tr><td><span class="tt kwbu">HOLLOW</span></td></tr></table>
    </div>
    <div class="BreakInsideAvoid"><h2>Operatives</h2>
    <ul class="redTriangle"><li>2 THINGS selected from the following list:
      <ul class="redCircle2"><li><span class="kwb kwbo">A CARD</span></li></ul>
    </li></ul>
    Your kill team can only include up to two NOBODY operatives.
    </div>
    """
    with pytest.raises(CompositionNotParsed, match="matches no operative"):
        parse_composition(html)


def test_only_the_list_the_sentence_belongs_to_has_its_repeats_capped():
    # The repeat clause says "each operative on this list", so it applies to the list the
    # sentence follows -- unlike the keyword cap, which is team-wide.
    assert all(option.max_selections is None for option in _lists()[0].options)


def test_an_unknown_quantity_in_a_cap_raises():
    # A silently wrong cap approves illegal rosters, so an unrecognised number word
    # stops the team instead.
    html = """
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>A Card</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table></div>
    <div class="BreakInsideAvoid"><h2>Operatives</h2>
    <ul class="redTriangle"><li>2 THINGS selected from the following list:
      <ul class="redCircle2"><li><span class="kwb kwbo">A CARD</span></li></ul>
    </li></ul>
    Your kill team can only include up to seventeen CARD operatives.
    </div>
    """
    with pytest.raises(CompositionNotParsed, match="unknown quantity"):
        parse_composition(html)


def test_a_page_with_no_composition_fails_loudly():
    # A team that seeds with no selection lists would offer a roster nothing, and look
    # like a successful scrape.
    with pytest.raises(CompositionNotParsed, match="no 'Operatives' section"):
        parse_composition("<html><body><h2>Something else</h2></body></html>")


def test_a_line_whose_operatives_cannot_be_found_fails_loudly():
    # Inquisitorial Agent prints "5 ... operatives selected from the list above, or
    # REQUISITIONED operatives from one group" -- a shape this parser does not know, and
    # guessing its budget would seed a roster rule that looks right.
    html = """
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>A Card</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table></div>
    <h2>Operatives</h2>
    <ul class="redTriangle"><li>5 THINGS selected from the list above</li></ul>
    """
    with pytest.raises(CompositionNotParsed, match="no operatives found"):
        parse_composition(html)
