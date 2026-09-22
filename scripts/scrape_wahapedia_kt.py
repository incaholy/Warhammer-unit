"""Scrape Wahapedia's Kill Team (2024) pages into the seed input.

Same two-stage shape as the 40k scraper (`scrape_wahapedia.py`): this writes JSON,
then `make seed-kt` loads it. The fetch layer is **cached and polite** -- reused from
that module, so there is one place that talks to the site -- and every parse layer is
a **pure function** tested against a synthetic fixture.

    python -m scripts.scrape_wahapedia_kt      # (parsers land per KILLTEAM.md → K2)
    make scrape-kt

Which kill teams? All of them, discovered rather than configured. The site's
navigation lives in one small standalone file (`nav.html`, loaded by JS, which is why
it is absent from a page's own HTML), and its "Kill Teams" dropdown carries every
team with its faction and its URL slug -- so there is no hand-maintained list of
teams to drift from the site. Contrast the 40k scraper, whose `FACTIONS` map has to
name each faction page.

Its two grouping levels are NOT symmetric with ours, on purpose:

    FactionHeader     Imperium, Chaos, Xenos, Aeldari   <- grand alliance, DROPPED
    factionGroup_KT   Adepta Sororitas, Tyranids, ...   <- our KTFaction
    <a>               Novitiates, Raveners, ...         <- our KillTeam

KILLTEAM.md decision #12 keeps the faction list flat, so the alliance is read and
discarded. It is also why the 40k `Faction`/`Subfaction` tables could not be reused:
Kill Team lists Aeldari as an alliance above Craftworlds, Corsairs and Harlequins,
where 40k has Aeldari as a subfaction *under* Xenos.
"""

from dataclasses import dataclass

from bs4 import BeautifulSoup

from scripts.scrape_wahapedia import fetch

NAV_URL = "https://wahapedia.ru/kill-team3/nav.html"
TEAM_URL = "https://wahapedia.ru/kill-team3/kill-teams/{slug}"

# Faction and team names come from the site's own nav grouping rather than a list
# typed here -- 22 factions and 48 teams, and a hand-copied list is what drifts from
# the source. The one thing normalised on the way through is typography: the nav
# writes curly quotes ("T’au Empire"), while the 40k side stores a straight
# apostrophe ("T'au Empire"), and two spellings of one faction would seed two rows.
# A rule, not a per-faction exception list, so a new team named the same way needs no
# edit here.
_TYPOGRAPHIC = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})


@dataclass(frozen=True)
class NavEntry:
    """One kill team as the navigation describes it."""

    name: str
    faction: str
    slug: str

    @property
    def url(self) -> str:
        return TEAM_URL.format(slug=self.slug)


def _clean(text: str) -> str:
    """One nav label as we store it: no &nbsp; padding, no curly quotes."""
    return text.replace("\xa0", " ").translate(_TYPOGRAPHIC).strip()


def parse_nav(html: str) -> list[NavEntry]:
    """Every kill team in the "Kill Teams" dropdown, with its faction and slug.

    Reads the dropdown in document order: a `factionGroup_KT` sets the faction that
    the links after it belong to, and a `FactionHeader` (the grand alliance) is
    skipped. Only this dropdown is searched, so a kill team linked from elsewhere in
    the nav is not collected.

    Raises `ValueError` if the dropdown is missing, or if a link appears before any
    faction group -- both mean the page's structure changed, and a scraper that
    quietly returned fewer teams would seed a partial catalog.
    """
    soup = BeautifulSoup(html, "html.parser")
    button = soup.find("div", class_="NavBtn_Factions")
    if button is None:
        raise ValueError(f"no 'Kill Teams' dropdown in the nav — has {NAV_URL} changed?")
    content = button.find_next_sibling("div", class_="NavDropdown-content")
    if content is None:
        raise ValueError("the 'Kill Teams' button has no dropdown content beside it")

    entries: list[NavEntry] = []
    faction: str | None = None
    for el in content.find_all(["div", "a"]):
        classes = el.get("class") or []
        if el.name == "div" and "factionGroup_KT" in classes:
            faction = _clean(el.get_text())
        elif el.name == "a" and "kill-teams/" in (el.get("href") or ""):
            if faction is None:
                raise ValueError(f"kill team {_clean(el.get_text())!r} appears before any faction group")
            slug = el["href"].rstrip("/").rsplit("/", 1)[-1]
            entries.append(NavEntry(name=_clean(el.get_text()), faction=faction, slug=slug))
    if not entries:
        raise ValueError("the 'Kill Teams' dropdown contained no kill team links")
    return entries


def discover() -> list[NavEntry]:
    """Every kill team the site currently lists, via the cached fetch layer."""
    return parse_nav(fetch(NAV_URL))


def main() -> None:
    """Report what the site lists. The per-team page parsers land next (KILLTEAM.md
    → K2); until they do, this is the discovery half, and it writes no JSON."""
    entries = discover()
    factions: dict[str, list[str]] = {}
    for entry in entries:
        factions.setdefault(entry.faction, []).append(entry.name)

    for faction in sorted(factions):
        print(f"{faction}:")
        for name in factions[faction]:
            print(f"  {name}")
    print(f"\n{len(entries)} kill teams across {len(factions)} factions")
    print("Page parsing is not implemented yet, so no JSON was written.")


if __name__ == "__main__":
    main()
