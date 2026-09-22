"""The Kill Team nav parser (scripts.scrape_wahapedia_kt.parse_nav).

Runs against a synthetic fixture reproducing Wahapedia's nav DOM — no network, no
scraped content. The fetch layer is shared with the 40k scraper and is not tested
here.
"""

from pathlib import Path

import pytest

from scripts.scrape_wahapedia_kt import NavEntry, parse_nav

FIXTURE = (Path(__file__).parent / "fixtures" / "kt_nav.html").read_text()


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
    entry = parse_nav(FIXTURE)[0]
    assert entry.url == "https://wahapedia.ru/kill-team3/kill-teams/hollow-sentinels"


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
