"""The Kill Team nav parser (scripts.scrape_wahapedia_kt.parse_nav).

Runs against a synthetic fixture reproducing Wahapedia's nav DOM — no network, no
scraped content. The fetch layer is shared with the 40k scraper and is not tested
here.
"""

from pathlib import Path

import pytest

from scripts.scrape_wahapedia_kt import (
    CompositionNotParsed,
    NavEntry,
    OperativeNotResolved,
    _weapon_rules,
    core_rules_url,
    main,
    parse_equipment,
    parse_in_battle_operatives,
    parse_nav,
    parse_operatives,
    parse_ploys,
    parse_selection_rules,
    parse_team_rules,
    resolve_operative,
    scrape,
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


def test_a_weapon_rule_styled_mid_word_is_not_split():
    # The pages style game terms inline wherever they appear, including part of a word:
    # `Conceal</span></span>ed Position`, because Conceal is an order. Joining the cell's
    # nodes with a space split it into "Conceal ed Position" on three teams, and did the
    # same after a hyphen and an opening bracket. Taking the source's own spacing instead
    # fixes all three -- and the comma-separated list still reads correctly, which is what
    # the separator had been there for.
    html = """
    <table class="dsWeapon"><tr class="pHeaderRow">
      <td class="dsWeaponName">Sniper rifle</td>
    </tr><tr>
      <td>Sniper rifle</td><td>4</td><td>3</td><td>3/3</td>
      <td>Silent, <span class="tt"><span class="kwbu">Conceal</span></span>ed Position<span class="ast">*</span>, Anti-<b>PSYKER</b>, Heavy (<b>Dash</b> Only)</td>
    </tr></table>
    """
    from bs4 import BeautifulSoup

    cell = BeautifulSoup(html, "html.parser").find_all("td")[-1]

    assert _weapon_rules(cell) == [
        "Silent",
        "Concealed Position*",
        "Anti-PSYKER",
        "Heavy (Dash Only)",
    ]


def test_a_weapon_with_no_rules_reads_as_an_empty_list():
    glaive = {w.name: w for w in _by_name()["Hollow Sentinel Warden"].weapons}["Rusted glaive"]
    assert glaive.rules == []


def test_abilities_and_unique_actions_come_back_together():
    # One list for both: they read the same way on a datacard, and the tracker shows
    # the text rather than acting on it.
    warden = _by_name()["Hollow Sentinel Warden"]

    assert [a.name for a in warden.abilities] == ["Warden's Vigil", "EMBER STRIKE", "EMBER BEACON"]


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

    assert [r.name for r in rules if r.group is None] == ["Ember Tide", "Hollow Resolve"]


def test_a_chosen_option_is_a_rule_carrying_its_section_as_its_group():
    # Blades of Khaine's whole mechanic lives in three "… Aspect Techniques" sections and
    # Exodite Dragon Masters' in three "… Upgrade" sections -- 30 named blocks the reader
    # used to walk past entirely, because it read only "Faction Rules" and only a bare h3.
    # They are rules, not ploys: not one of the 30 prints a CP cost. The SECTION is the
    # fact that matters, since a technique's name means nothing without knowing which
    # operatives may take it.
    grouped = [r for r in parse_team_rules(TEAM) if r.group is not None]

    assert [(r.group, r.name) for r in grouped] == [
        ("Warden Ember Techniques", "THE RISING EMBER"),
        ("Warden Ember Techniques", "ASH ON THE WIND"),
    ]
    assert "first attack is critical" in grouped[0].description
    assert "Flavour" not in grouped[0].description  # ShowFluff is dropped here too
    assert not grouped[0].description.startswith("THE RISING EMBER")


def test_an_always_on_faction_rule_has_no_group():
    ember = {r.name: r for r in parse_team_rules(TEAM)}["Ember Tide"]

    assert ember.group is None


def test_a_rule_keeps_an_action_printed_inside_it():
    # Raveners print the Burrow ACTION within the Burrow rule. It is not an
    # operative's action, and `KillTeamRule` is name-and-text, so it stays in the
    # rule -- only the element carrying the rule's own NAME is removed.
    ember = {r.name: r for r in parse_team_rules(TEAM)}["Ember Tide"]

    assert "STOKE" in ember.description
    assert "1AP" in ember.description
    assert ember.description.startswith("At the end of the Set Up Operatives step")


def test_flavour_text_is_left_out_of_every_rule():
    # `ShowFluff` is prose about the faction; the tracker shows rules.
    for rule in parse_team_rules(TEAM):
        assert "flavour" not in rule.description.lower()


def test_a_rule_does_not_repeat_its_own_name():
    resolve = {r.name: r for r in parse_team_rules(TEAM)}["Hollow Resolve"]
    assert not resolve.description.startswith("Hollow Resolve")


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
    # Still the commonest shape on the text ladder, though the ladder is now the fallback:
    # the page's own link resolves 444 of the 470 composition entries (#35), and of the 26
    # that reach these rules, 22 land on this one.
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
    # SQUAD PROCTOR-EXACTANT" against "Arbites Proctor-Exactant". No page needs this rung
    # today -- the anchor answers those entries (#35) -- so it is kept as a net, like the
    # prefix rule.
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


# ---------------------------------------------------------------------------
# The run itself (`scrape`): which failures are skipped, and which must not be.
# ---------------------------------------------------------------------------


def _serving(pages: dict[str, str], *, seen: list | None = None):
    """A stand-in for `fetch` that answers from `pages`, keyed by a substring of the URL."""

    def fake_fetch(url: str, *, refresh: bool = False, **_: object) -> str:
        if seen is not None:
            seen.append((url, refresh))
        # longest fragment first, so a per-team override beats the catch-all
        for fragment in sorted(pages, key=len, reverse=True):
            if fragment in url:
                return pages[fragment]
        raise AssertionError(f"unexpected fetch: {url}")

    return fake_fetch


def _pages(**overrides: str) -> dict[str, str]:
    pages = {
        "nav.html": FIXTURE,
        "universal-equipment": UNIVERSAL_EQUIPMENT,
        "core-rules": TEAM,  # its ploys become the universal ones
        "kill-teams/": COMPOSITION,  # a whole team page: datacards + composition
    }
    return {**pages, **overrides}


def test_a_failure_that_is_not_an_ambiguity_fails_the_whole_run(monkeypatch):
    # The other half of the trade. A missing section, an unreadable stat or an HTTP error
    # means the site or the parsers changed; reporting that as "skipped 47 teams" would
    # hand the seed a quietly thinner catalog.
    def exploding_fetch(url: str, *, refresh: bool = False, **_: object) -> str:
        if "rust-wardens" in url:
            raise RuntimeError("503 from the site")
        return _serving(_pages())(url, refresh=refresh)

    monkeypatch.setattr("scripts.scrape_wahapedia_kt.fetch", exploding_fetch)

    with pytest.raises(RuntimeError, match="503"):
        scrape()


def test_refresh_reaches_every_fetch(monkeypatch):
    # Without this the cache has no expiry, so a re-run can never see a CHANGED page and
    # the seed's whole update path is unreachable.
    seen: list = []
    monkeypatch.setattr("scripts.scrape_wahapedia_kt.fetch", _serving(_pages(), seen=seen))

    scrape(refresh=True)

    assert seen and all(refresh for _, refresh in seen)


def test_the_core_rules_page_is_discovered_from_the_nav():
    assert core_rules_url(FIXTURE) == "https://wahapedia.ru/kill-team3/the-rules/core-rules/"


def test_a_nav_without_a_core_rules_link_is_a_failure():
    with pytest.raises(ValueError, match="no core rules link"):
        core_rules_url("<html><body><a href='/kill-team3/kill-teams/x'>X</a></body></html>")


def test_a_look_alike_rules_page_is_not_mistaken_for_the_core_rules():
    # The patterns are anchored, and `find` takes the first match in document order, so an
    # unanchored one would silently prefer whichever look-alike the nav listed first.
    nav = """<html><body>
      <a href="/kill-team3/the-rules/core-rules-commentary">Commentary</a>
      <a href="/kill-team3/the-rules/core-rules">Core Rules</a>
    </body></html>"""

    assert core_rules_url(nav) == "https://wahapedia.ru/kill-team3/the-rules/core-rules/"


def test_a_run_that_parses_nothing_leaves_the_payload_file_untouched(monkeypatch, tmp_path):
    # killteam.json is gitignored, so replacing a good one with `{"kill_teams": []}` loses
    # it for good -- and the seed would then refuse it, having already lost the catalog.
    payload_file = tmp_path / "killteam.json"
    payload_file.write_text('{"kill_teams": ["the good payload"]}', encoding="utf-8")
    monkeypatch.setattr("scripts.scrape_wahapedia_kt.DATA_PATH", payload_file)
    monkeypatch.setattr("sys.argv", ["scrape_wahapedia_kt"])
    monkeypatch.setattr(
        "scripts.scrape_wahapedia_kt.scrape",
        lambda **_: {"kill_teams": [], "universal_ploys": [], "universal_equipment": [], "skipped": []},
    )

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 1
    assert payload_file.read_text(encoding="utf-8") == '{"kill_teams": ["the good payload"]}'


def test_the_most_specific_card_wins_when_the_entry_carries_the_extra_words():
    # The other side of the tie-break above. Here the ENTRY is the longer side, so the
    # card using more of its words is the more specific reading -- taking the shortest
    # offered a Player on the line that requires the Lead Player, and left the Lead
    # Player unfieldable.
    troupe = ["Lead Player", "Death Jester", "Player", "Shadowseer"]

    assert resolve_operative("VOID-DANCER TROUPE LEAD PLAYER", troupe) == "Lead Player"
    # and the plain entry still resolves to the plain card
    assert resolve_operative("PLAYER", troupe) == "Player"
    # the pre-existing entry-side case is unaffected: the card has no extra words to lose
    assert resolve_operative("INQUISITORIAL AGENT INTERROGATOR", ["Interrogator Agent"]) == (
        "Interrogator Agent"
    )


# ---------------------------------------------------------------------------
# `composition_warnings`: a mis-read composition seeds cleanly, so it has to be said
# out loud. Synthetic teams, in the payload's shape.
# ---------------------------------------------------------------------------


def test_a_unique_action_keeps_the_conditions_printed_beside_its_effect():
    # A datacard prints an action's effect and then the conditions on performing it, in
    # two sibling divs. Reading only `actionEffect` dropped the conditions from 597 blocks
    # across the site -- and an action shown without them reads as unconditional, which on
    # a reference sheet is worse than showing nothing.
    strike = {a.name: a for a in _by_name()["Hollow Sentinel Warden"].abilities}["EMBER STRIKE"]

    assert "inflict D3 damage" in strike.description
    assert "cannot perform this action while within Engagement Range" in strike.description


def test_a_unique_action_keeps_its_final_full_stop():
    # The description was assembled with `f"{cost}. {body}".strip(". ")`, which strips
    # from BOTH ends -- so 191 of the 193 action descriptions lost their closing full stop
    # and read as though they had been cut off mid-sentence.
    strike = {a.name: a for a in _by_name()["Hollow Sentinel Warden"].abilities}["EMBER STRIKE"]

    assert strike.description.startswith("1AP.")
    assert strike.description.endswith(".")


def test_an_action_whose_body_carries_no_class_still_keeps_its_text():
    # Pathfinders wrap the body in a plain <div>, and keying on `actionEffect` left ten
    # MARKERLIGHT rows whose whole description was "1AP".
    beacon = {a.name: a for a in _by_name()["Hollow Sentinel Warden"].abilities}["EMBER BEACON"]

    assert beacon.description == '1AP. Place one EMBER marker within 3" of this operative.'


def test_the_sites_keyword_markers_are_stripped_from_text():
    # 14 real rows carry the site's templating around a keyword ("your <KY>OBELISK</KY>
    # markers"). The keyword is content; the markers are not, and they would either
    # vanish into a client's HTML or be rendered literally.
    beacon = {a.name: a for a in _by_name()["Hollow Sentinel Warden"].abilities}["EMBER BEACON"]

    assert "EMBER marker" in beacon.description
    assert "<KY>" not in beacon.description and "</KY>" not in beacon.description


_TWO_CARDS = """
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Alpha Gunner</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table>
    <table class="dsKeywords"><tr><td><span class="tt kwbu">GUNNER</span></td></tr></table></div>
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Alpha Diktat</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table>
    <table class="dsKeywords"><tr><td><span class="tt kwbu">DIKTAT</span></td></tr></table></div>
"""


def test_a_card_that_begins_with_the_entry_beats_a_shorter_rearranged_one():
    # The prefix rule: a composition entry often names the start of a card's name, dropping
    # the trailing role ("SICARIAN RUSTSTALKER" for "Sicarian Ruststalker Warrior"). Without
    # it, resolution falls through to card-contains-entry, whose fewest-extra-words
    # tie-break would prefer a shorter card that merely shares the words in another order.
    # Synthetic on purpose: no current page needs the rule, which is why it went unpinned.
    cards = ["Ash Prophet Elite", "Prophet Ash"]

    assert resolve_operative("ASH PROPHET", cards) == "Ash Prophet Elite"


_TWO_WARRIORS = """
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Alpha Infiltrator Warrior</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table>
    <table class="dsKeywords"><tr><td><span class="tt kwbu">CLADE</span>, <span class="tt kwbu">WARRIOR</span></td></tr></table></div>
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Alpha Ruststalker Warrior</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table>
    <table class="dsKeywords"><tr><td><span class="tt kwbu">CLADE</span>, <span class="tt kwbu">WARRIOR</span></td></tr></table></div>
"""


def _anchored_html(entry_html: str, cards: list[str]) -> str:
    """A one-list composition whose datacards carry the anchors the site links to."""
    frames = "".join(
        f"""
        <div class="dsOuterFrame"><table><tr class="pHeaderRow">
          <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3">
            <div id="{card.replace(" ", "-")}">{card}</div></h3></div></td>
          <td class="pCell">APL<div class="dsStat">2</div></td>
        </tr></table></div>"""
        for card in cards
    )
    return f"""
    {frames}
    <h2>Operatives</h2>
    <ul class="redTriangle"><li>2 ALPHA operatives selected from the following list:
      <ul class="redCircle2">{entry_html}</ul>
    </li></ul>
    """


# ---------------------------------------------------------------------------
# The composition, as printed text (`parse_selection_rules`, decision #28).


def _rules(html=None):
    return parse_selection_rules(html if html is not None else COMPOSITION)


def test_a_composition_line_and_its_entries_keep_the_pages_indent():
    # The page prints composition as an indented list and the indent carries meaning: an
    # entry belongs to the line above it. Depth 0 is a line, 1 one of its entries.
    rules = _rules()
    first = next(r for r in rules if r.kind == "line")

    assert first.depth == 0
    assert first.text == "1 HOLLOW WARDEN operative"
    assert [r.depth for r in rules[:3]] == [0, 0, 1]


def test_a_loadout_under_an_entry_is_nested_rather_than_a_sibling():
    # The failure this shape exists to avoid: flattened, "Ash lash; rusted glaive" would
    # sit beside SENTINEL as though it were another operative to choose. It is a loadout
    # FOR the WARDEN entry above it, which depth 2 says and a flat list cannot.
    rules = _rules()
    options = [r for r in rules if r.depth == 2]

    assert options, "the fixture prints a nested loadout list"
    assert all(r.kind == "line" for r in options)
    owner = max(
        (r for r in rules if r.depth == 1 and r.position < options[0].position), key=lambda r: r.position
    )
    assert "one of the following options" in owner.text


def test_every_printed_bullet_is_kept_including_the_loadout_lists():
    # `redEmptyCircle2` items are weapon loadouts, and the old model dropped them because
    # resolving them as operatives was a hazard. Nothing is resolved now, so dropping them
    # would only lose 157 lines the page prints.
    texts = [r.text for r in _rules() if r.kind == "line"]

    assert "Ash lash; rusted glaive" in texts
    assert "Ember charm or ash token" in texts


def test_the_restriction_sentence_is_its_own_rule_kept_verbatim():
    # Nothing is read out of it any more -- no repeat cap, no keyword cap. The sentence IS
    # the rule, which is exact where a column had to approximate (decision #28).
    restriction = next(r for r in _rules() if r.kind == "restriction")

    assert restriction.depth == 0
    assert restriction.text.startswith("Other than SENTINEL operatives")
    assert "up to one EMBER operative" in restriction.text


def test_the_notes_printed_around_a_composition_are_their_own_rules():
    # A marker starts a note and a callout box is a note of its own (decision #30).
    notes = [r.text for r in _rules() if r.kind == "note"]

    assert notes == [
        "You cannot select more than two of these operatives combined.",
        "Designer's Note: the page prints this in a box beside the composition.",
        # a requisition group prints footnotes too, and they are the team's (decision #30)
        "These operatives count as half a selection each.",
    ]


def test_a_requisition_group_is_a_heading_followed_by_its_lines():
    # Its lines are text like any other, so whether the ally has a page of its own stops
    # being a question the catalog answers (it was decisions #33 and #34).
    rules = _rules()
    headings = [r for r in rules if r.kind == "heading"]

    assert [r.text for r in headings] == ["Ember Wardens", "Ashen Choir"]
    after = next(r for r in rules if r.position == headings[0].position + 1)
    assert after.kind == "line" and after.depth == 0


def test_position_runs_across_the_whole_composition():
    # One ordering for lines, sentences and notes together, so reading in position order
    # and indenting by depth gives the page's section back.
    rules = _rules()

    assert [r.position for r in rules] == list(range(len(rules)))


def test_a_second_composition_block_is_read_with_its_condition_between_them():
    # Gellerpox Infected print two blocks: the roster, then "If you selected the MUTOID
    # VERMIN faction equipment:" and the three vermin that equipment adds. Reading only
    # the first block lost four printed lines. It was unmodellable while composition was
    # a structure -- "Specified number of" has no budget, the number lives in the
    # equipment text -- and as text there is nothing to model. The two blocks share a
    # wrapper and the condition is printed between them, which is where it is stored.
    html = """
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Alpha Warden</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table></div>
    <h2>Operatives</h2>
    <div class="BreakInsideAvoid">
      <ul class="redTriangle"><li>Every ALPHA operative in the following list:
        <ul class="redCircle2"><li>1 WARDEN</li></ul>
      </li></ul>
      If you selected the MUTOID VERMIN faction equipment:
      <ul class="redTriangle"><li>Specified number of ALPHA operatives:
        <ul class="redCircle2"><li>CURSEMITE</li></ul>
      </li></ul>
    </div>
    """

    rules = parse_selection_rules(html)

    assert [(r.kind, r.depth, r.text) for r in rules] == [
        ("line", 0, "Every ALPHA operative in the following list:"),
        ("line", 1, "1 WARDEN"),
        ("note", 0, "If you selected the MUTOID VERMIN faction equipment:"),
        ("line", 0, "Specified number of ALPHA operatives:"),
        ("line", 1, "CURSEMITE"),
    ]


def test_a_list_from_a_later_section_is_not_claimed_by_the_composition():
    # `find_all_next` walks the whole document, so the blocks have to be cut off at the
    # next heading -- otherwise "Operatives" would swallow the Faction Rules lists.
    html = """
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Alpha Warden</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table></div>
    <h2>Operatives</h2>
    <!-- wrapped as the real pages wrap it: without the div, the block's "wrapper" is the
         document and its loose text picks up the datacard's own stats. -->
    <div class="BreakInsideAvoid">
      <ul class="redTriangle"><li>1 ALPHA WARDEN operative</li></ul>
    </div>
    <h2>Faction Rules</h2>
    <div class="BreakInsideAvoid">
      <ul class="redTriangle"><li>Not part of the composition at all</li></ul>
    </div>
    """

    lines = [r.text for r in parse_selection_rules(html) if r.kind == "line"]

    assert lines == ["1 ALPHA WARDEN operative"]


def test_a_page_with_no_operatives_section_fails_loudly():
    with pytest.raises(CompositionNotParsed, match="no 'Operatives' section"):
        parse_selection_rules("<html><body><h2>Something else</h2></body></html>")


def test_a_page_whose_operatives_section_has_no_list_fails_loudly():
    with pytest.raises(CompositionNotParsed, match="no composition list"):
        parse_selection_rules("<html><body><h2>Operatives</h2><p>nothing</p></body></html>")


def test_operatives_a_condition_grants_are_marked_in_battle():
    # Gellerpox print a second block under "If you selected the MUTOID VERMIN faction
    # equipment:". Those datacards are not rosterable at all -- they arrive mid-battle
    # (decisions #18, #20) -- so they are marked rather than offered. This is the one
    # place that still resolves an entry against a datacard.
    html = """
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Alpha Warden</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">2</div></td>
    </tr></table></div>
    <div class="dsOuterFrame"><table><tr class="pHeaderRow">
      <td class="pDisplayHeaderCell"><div class="dsUnitHeader"><h3 class="pTable_h3"><div>Alpha Cursemite</div></h3></div></td>
      <td class="pCell">APL<div class="dsStat">1</div></td>
    </tr></table></div>
    <h2>Operatives</h2>
    <ul class="redTriangle"><li>1 ALPHA WARDEN operative</li></ul>
    If you selected the MUTOID VERMIN faction equipment:
    <ul class="redTriangle"><li>Specified number of ALPHA CURSEMITE operatives</li></ul>
    """

    assert parse_in_battle_operatives(html) == ["Alpha Cursemite"]
