"""Scrape Wahapedia datasheets into scripts/data/datasheets.json (the seed input).

See SPEC.md "Scraping the catalog (Wahapedia)". Two-stage and decoupled: this
writes the JSON, then `make seed` loads it. The fetch layer is **cached + polite**;
the parse layer is a **pure function** tested against a saved HTML fixture.

    python -m scripts.scrape_wahapedia      # scrape the configured factions
    make scrape

v1 scope: **units + stat line + chapter → subfaction**. Two known limits:
  - **Points are a placeholder (0)** — Wahapedia injects unit points via JavaScript,
    so they aren't in the page HTML; backfill later (points source or admin API).
  - **Weapons / abilities / keywords are not parsed yet** (Stage 2) — units seed
    with empty lists.
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

DATA_PATH = Path(__file__).parent / "data" / "datasheets.json"
CACHE_DIR = Path(__file__).parent / "data" / "cache"
BASE = "https://wahapedia.ru/wh40k10ed/factions"
USER_AGENT = "warhammer-unit learning project (personal/dev use)"

# faction url-slug -> our canonical FactionName value
FACTIONS = {"space-marines": "Space Marines"}

# Wahapedia datasheet theme color code -> our subfaction name (must exist in
# models.FACTION_SUBFACTIONS). "SM" = faction-wide -> no subfaction.
CHAPTER_CODES = {
    "CHBA": "Blood Angels", "CHDA": "Dark Angels", "CHDW": "Deathwatch",
    "CHIF": "Imperial Fists", "CHIH": "Iron Hands", "CHRG": "Raven Guard",
    "CHSA": "Salamanders", "CHSW": "Space Wolves", "CHWS": "White Scars",
    "CHUL": "Ultramarines", "CHBR": "Blood Ravens", "CHBT": "Black Templars",
}

# Wahapedia stat label -> our unit field
STAT_FIELDS = {
    "M": "movement", "T": "toughness", "Sv": "armor_save", "W": "wounds",
    "Ld": "leadership", "OC": "objective_control",
}


def fetch(url: str, *, cache_dir: Path = CACHE_DIR, delay: float = 3.0) -> str:
    """GET `url` politely, caching the HTML to disk so re-runs don't re-hit the site."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / (re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_") + ".html")
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    time.sleep(delay)  # be gentle — one request every few seconds
    resp = httpx.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True
    )
    resp.raise_for_status()
    cached.write_text(resp.text, encoding="utf-8")
    return resp.text


def _stat_int(text: str) -> Optional[int]:
    """First integer in a stat string: `6"` -> 6, `2+` -> 2, `-` -> None."""
    m = re.search(r"-?\d+", text or "")
    return int(m.group()) if m else None


def _theme_code(block) -> Optional[str]:
    """The datasheet's chapter/faction color code (e.g. `CHSA`, or `SM` for generic)."""
    for tag in block.find_all(class_=True):
        for cls in tag.get("class"):
            m = re.fullmatch(r"dsColorBg(SM|CH[A-Z0-9]{2})", cls)
            if m:
                return m.group(1)
    return None


def _weapon_name_and_keywords(cell) -> tuple[str, list[str]]:
    """Split a weapon name cell into (name, keywords). Keywords render as inline
    `.kwb2` spans (e.g. 'Bolt pistol [pistol]'); the name is the rest of the text."""
    keywords = [
        re.sub(r"\s+", " ", s.get_text(" ", strip=True)) for s in cell.select(".kwb2")
    ]
    tmp = BeautifulSoup(str(cell), "lxml")  # a copy, so we can strip the keyword spans
    for s in tmp.select(".kwb2"):
        s.decompose()
    name = re.sub(r"\s+", " ", tmp.get_text(" ", strip=True)).strip(" –-")
    return name, keywords


def parse_points(block) -> int:
    """Minimum-size points from the datasheet's `.PriceTag` table (0 if none).

    Wahapedia lists points per unit size ('5 models' -> 80, '10 models' -> 160);
    we store the smallest size's cost. (The `dsPointy` box is JS-filled and empty in
    the HTML — these `.PriceTag` values are the static source.)
    """
    best: Optional[tuple[int, int]] = None  # (model_count, points)
    for tag in block.select(".PriceTag"):
        row = tag.find_parent("tr")
        if row is None:
            continue
        # only unit-size prices ('N models'); skip enhancement/stratagem PriceTags
        m = re.search(r"(\d+)\s*model", row.get_text(" ", strip=True), re.I)
        pts = _stat_int(tag.get_text(strip=True))
        if m is None or pts is None:
            continue
        size = int(m.group(1))
        if best is None or size < best[0]:
            best = (size, pts)
    return best[1] if best else 0


def parse_keywords(block) -> list[str]:
    """The unit KEYWORDS (not FACTION KEYWORDS), title-cased.

    Note: Wahapedia's column classes use a Cyrillic 'С' (`dsLeftСolKW`), so match on
    the Latin substring `olKW` and pick the unit list by its 'KEYWORDS:' label."""
    for kd in block.select('[class*="olKW"]'):
        text = re.sub(r"\s+", " ", kd.get_text(" ", strip=True))
        up = text.upper()
        if up.startswith("KEYWORDS:") and "FACTION KEYWORDS:" not in up:
            body = text.split(":", 1)[1]
            return [k.strip().title() for k in body.split(",") if k.strip()]
    return []


def _parse_one_ability(el) -> Optional[dict]:
    """A `.dsAbility` block -> {name, description}, or None if it's not a real
    ability (unit composition, a points table, or a bare faction/core reference)."""
    if el.select_one(".dsUl") or el.select_one(".PriceTag") or el.find("table"):
        return None  # composition / points, not an ability
    bold = el.find("b")
    if bold is None:
        return None
    name = re.sub(r"\s+", " ", bold.get_text(" ", strip=True)).rstrip(":").strip()
    tmp = BeautifulSoup(str(el), "lxml")  # copy so we can drop the name to get the body
    if tmp.find("b"):
        tmp.find("b").decompose()
    desc = re.sub(r"\s+", " ", tmp.get_text(" ", strip=True))
    desc = re.sub(r"^(FACTION|CORE|WARGEAR)\s*:\s*", "", desc, flags=re.I).strip(" :")
    if not name or len(desc) < 4:
        return None  # a bare reference (e.g. 'FACTION: Oath of Moment') — skip
    return {"name": name, "description": desc}


def parse_abilities(block) -> tuple[list[dict], list[str]]:
    """A datasheet's abilities -> (ability dicts, names). Scoped to the ABILITIES
    section (from its header to the next section header), so unit composition,
    the points table, etc. are excluded."""
    abilities, names, collecting = [], [], False
    for el in block.select(".dsHeader, .dsAbility"):
        if "dsHeader" in (el.get("class") or []):
            head = el.get_text(strip=True).upper()
            if head == "ABILITIES":
                collecting = True
            elif collecting and head:  # a new section header ends the abilities
                break
            continue
        if collecting:
            ability = _parse_one_ability(el)
            if ability:
                abilities.append(ability)
                names.append(ability["name"])
    return abilities, names


def parse_weapons(block) -> tuple[list[dict], list[str]]:
    """A datasheet's weapons -> (weapon dicts, the unit's weapon names). Walks the
    weapon table rows tracking category from the RANGED/MELEE section headers."""
    weapons, names, category = [], [], None
    for tr in block.select("tr"):
        if tr.select_one(".dsHeader"):
            head = tr.get_text(" ", strip=True).upper()
            if "RANGED WEAPONS" in head:
                category = "range"
            elif "MELEE WEAPONS" in head:
                category = "melee"
            continue
        namecell = tr.select_one(".wTable2_short")
        if namecell is None or category is None:
            continue
        cells = tr.find_all("td")
        stat = cells[cells.index(namecell) + 1 : cells.index(namecell) + 7]
        if len(stat) < 6:
            continue
        name, kw = _weapon_name_and_keywords(namecell)
        if not name:
            continue
        text = [c.get_text(strip=True) for c in stat]  # [range, A, WS/BS, S, AP, D]
        weapons.append({
            "name": name,
            "category": category,
            "attacks": text[1] or "1",
            "weapon_skill": _stat_int(text[2]) or 0,
            "strength": _stat_int(text[3]) or 0,
            "armor_piercing": abs(_stat_int(text[4]) or 0),  # model stores AP magnitude
            "damage": text[5] or "1",
            "range_inches": None if category == "melee" else _stat_int(text[0]),
            "keywords": kw,
        })
        names.append(name)
    return weapons, names


def parse_datasheets(html: str, faction: str) -> dict:
    """Pure parse: a faction's datasheets HTML -> the seed JSON structure.

    v1: units + the six-stat line + chapter→subfaction; points are a placeholder,
    weapons/abilities/keywords are empty.
    """
    soup = BeautifulSoup(html, "lxml")
    units: list[dict] = []
    subfactions: set[str] = set()
    weapons: dict[str, dict] = {}  # by name — weapons are shared across units
    abilities: dict[str, dict] = {}  # by name — abilities are shared too

    for block in soup.select("div.datasheet"):
        name_el = block.select_one(".dsH2Header > div")
        if name_el is None:
            continue
        name = re.sub(r"\s+", " ", name_el.get_text(" ", strip=True)).strip()
        if not name:
            continue

        # stat line from the first profile only (multi-profile units are Stage 2)
        profiles = block.select(".dsProfileWrap")
        wraps = (profiles[0] if profiles else block).select(".dsCharWrap")
        stats: dict[str, Optional[int]] = {}
        for cw in wraps:
            label_el = cw.select_one(".dsCharName")
            value_el = cw.select_one(".dsCharValue")
            if label_el and value_el:
                label = label_el.get_text(strip=True)
                if label in STAT_FIELDS:
                    stats[STAT_FIELDS[label]] = _stat_int(value_el.get_text(strip=True))
        if any(stats.get(f) is None for f in STAT_FIELDS.values()):
            continue  # not a standard datasheet (no full stat line) — skip in v1

        code = _theme_code(block)
        subfaction = CHAPTER_CODES.get(code) if code and code != "SM" else None
        if subfaction:
            subfactions.add(subfaction)

        block_weapons, weapon_names = parse_weapons(block)
        for w in block_weapons:
            weapons.setdefault(w["name"], w)  # first definition wins (shared weapons)

        block_abilities, ability_names = parse_abilities(block)
        for a in block_abilities:
            abilities.setdefault(a["name"], a)

        unit = {
            "unit_name": name,
            "faction": faction,
            "movement": stats["movement"],
            "toughness": stats["toughness"],
            "armor_save": stats["armor_save"],
            "wounds": stats["wounds"],
            "leadership": stats["leadership"],
            "objective_control": stats["objective_control"],
            "points": parse_points(block),  # minimum-size cost (0 if not listed)
            "invulnerable_save": None,
            "keywords": parse_keywords(block),
            "weapons": list(dict.fromkeys(weapon_names)),  # dedupe, keep order
            "abilities": list(dict.fromkeys(ability_names)),
        }
        if subfaction:
            unit["subfaction"] = subfaction
        units.append(unit)

    return {
        "factions": [{"name": faction, "subfactions": sorted(subfactions)}],
        "weapons": list(weapons.values()),
        "abilities": list(abilities.values()),
        "units": units,
    }


def scrape_faction(slug: str) -> dict:
    return parse_datasheets(fetch(f"{BASE}/{slug}/datasheets.html"), FACTIONS[slug])


def main() -> None:
    factions, units = [], []
    weapons: dict[str, dict] = {}  # dedupe shared weapons across factions
    abilities: dict[str, dict] = {}
    for slug in FACTIONS:
        data = scrape_faction(slug)
        factions += data["factions"]
        units += data["units"]
        for w in data["weapons"]:
            weapons.setdefault(w["name"], w)
        for a in data["abilities"]:
            abilities.setdefault(a["name"], a)
    out = {
        "factions": factions,
        "weapons": list(weapons.values()),
        "abilities": list(abilities.values()),
        "units": units,
    }
    DATA_PATH.write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"scraped {len(units)} units, {len(weapons)} weapons, {len(abilities)} "
        f"abilities across {len(factions)} faction(s) -> {DATA_PATH}"
    )


if __name__ == "__main__":
    main()
