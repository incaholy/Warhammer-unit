"""The Wahapedia parser (scripts.scrape_wahapedia.parse_datasheets).

Runs against a synthetic HTML fixture that reproduces Wahapedia's datasheet DOM —
no network, no scraped content. The fetch layer is not tested here (it hits the
live site and is exercised manually).
"""

from pathlib import Path

from scripts.scrape_wahapedia import _stat_int, parse_datasheets

FIXTURE = (Path(__file__).parent / "fixtures" / "wahapedia_datasheets.html").read_text()


def test_stat_int_strips_symbols():
    assert _stat_int('6"') == 6
    assert _stat_int("2+") == 2
    assert _stat_int("4") == 4
    assert _stat_int("-") is None
    assert _stat_int("") is None


def test_parse_extracts_units_stats_and_subfaction():
    data = parse_datasheets(FIXTURE, "Space Marines")

    assert data["factions"] == [{"name": "Space Marines", "subfactions": ["Salamanders"]}]
    assert len(data["units"]) == 2

    by_name = {u["unit_name"]: u for u in data["units"]}

    generic = by_name["Test Marine Squad"]
    assert generic["faction"] == "Space Marines"
    assert "subfaction" not in generic  # SM theme -> faction-wide
    assert (generic["movement"], generic["toughness"], generic["armor_save"]) == (6, 4, 3)
    assert (generic["wounds"], generic["leadership"], generic["objective_control"]) == (2, 6, 2)
    assert generic["points"] == 80  # minimum-size cost from the PriceTag table

    chapter = by_name["Test Salamander Captain"]
    assert chapter["subfaction"] == "Salamanders"  # CHSA theme -> Salamanders
    assert (chapter["toughness"], chapter["wounds"]) == (4, 5)


def test_parse_extracts_weapons_and_links_them():
    data = parse_datasheets(FIXTURE, "Space Marines")
    weapons = {w["name"]: w for w in data["weapons"]}

    # ranged: keywords split from the name, AP stored as magnitude, range parsed
    bolt = weapons["Bolt rifle"]
    assert bolt["category"] == "range"
    assert bolt["range_inches"] == 24
    assert (bolt["attacks"], bolt["weapon_skill"], bolt["strength"]) == ("2", 3, 4)
    assert bolt["armor_piercing"] == 1  # from "-1"
    assert bolt["keywords"] == ["assault", "heavy"]

    # melee: no range
    ccw = weapons["Close combat weapon"]
    assert ccw["category"] == "melee"
    assert ccw["range_inches"] is None

    # the unit links both by name
    unit = next(u for u in data["units"] if u["unit_name"] == "Test Marine Squad")
    assert unit["weapons"] == ["Bolt rifle", "Close combat weapon"]


def test_parse_extracts_abilities_scoped_to_the_section():
    data = parse_datasheets(FIXTURE, "Space Marines")
    abilities = {a["name"]: a for a in data["abilities"]}

    # real datasheet ability kept (name + description)
    assert "Test Ability" in abilities
    assert abilities["Test Ability"]["description"].startswith("Does a test thing")
    # a bare faction reference (no description) is skipped
    assert "Oath of Moment" not in abilities
    # unit composition ("1 Test Marine", under the next section header) is not an ability
    assert "1 Test Marine" not in abilities

    unit = next(u for u in data["units"] if u["unit_name"] == "Test Marine Squad")
    assert unit["abilities"] == ["Test Ability"]


def test_parse_extracts_points_and_keywords():
    unit = next(
        u for u in parse_datasheets(FIXTURE, "Space Marines")["units"]
        if u["unit_name"] == "Test Marine Squad"
    )
    # minimum size (5 models = 80), NOT the 160 max nor the 15 enhancement
    assert unit["points"] == 80
    # unit KEYWORDS only (not FACTION KEYWORDS), title-cased
    assert unit["keywords"] == ["Infantry", "Battleline", "Imperium", "Test Marine Squad"]


def test_parse_chapter_codes_map_to_real_subfactions():
    # every code the scraper knows must be a real Space Marines subfaction
    from app.core.db.models import FACTION_SUBFACTIONS, FactionName
    from scripts.scrape_wahapedia import CHAPTER_CODES

    allowed = set(FACTION_SUBFACTIONS[FactionName.SPACE_MARINES])
    assert set(CHAPTER_CODES.values()) <= allowed
