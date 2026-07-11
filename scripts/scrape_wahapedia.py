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

        unit = {
            "unit_name": name,
            "faction": faction,
            "movement": stats["movement"],
            "toughness": stats["toughness"],
            "armor_save": stats["armor_save"],
            "wounds": stats["wounds"],
            "leadership": stats["leadership"],
            "objective_control": stats["objective_control"],
            "points": 0,  # PLACEHOLDER: Wahapedia injects points via JS; backfill later
            "invulnerable_save": None,
            "keywords": [],
            "weapons": list(dict.fromkeys(weapon_names)),  # dedupe, keep order
            "abilities": [],
        }
        if subfaction:
            unit["subfaction"] = subfaction
        units.append(unit)

    return {
        "factions": [{"name": faction, "subfactions": sorted(subfactions)}],
        "weapons": list(weapons.values()),
        "abilities": [],
        "units": units,
    }


def scrape_faction(slug: str) -> dict:
    return parse_datasheets(fetch(f"{BASE}/{slug}/datasheets.html"), FACTIONS[slug])


def main() -> None:
    factions, units = [], []
    weapons: dict[str, dict] = {}  # dedupe shared weapons across factions
    for slug in FACTIONS:
        data = scrape_faction(slug)
        factions += data["factions"]
        units += data["units"]
        for w in data["weapons"]:
            weapons.setdefault(w["name"], w)
    out = {
        "factions": factions,
        "weapons": list(weapons.values()),
        "abilities": [],
        "units": units,
    }
    DATA_PATH.write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"scraped {len(units)} units, {len(weapons)} weapons "
        f"across {len(factions)} faction(s) -> {DATA_PATH}"
    )


if __name__ == "__main__":
    main()
