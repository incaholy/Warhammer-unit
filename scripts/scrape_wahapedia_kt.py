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

import re
from copy import copy
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from scripts.scrape_wahapedia import _stat_int, fetch

NAV_URL = "https://wahapedia.ru/kill-team3/nav.html"
# Trailing slash on purpose: the site 301s the slash-less form, and `fetch()` caches
# by the URL it was asked for, so the canonical form is what keeps a run from paying
# 48 redirects before the cache can help.
TEAM_URL = "https://wahapedia.ru/kill-team3/kill-teams/{slug}/"

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


def _soup(html: str) -> BeautifulSoup:
    """A page ready to read: hidden tooltip templates removed.

    The site keeps a copy of some content inside `div.tooltip_templates` to fill
    hover popups. It is not visible content, and it is a COPY -- Novitiates' page
    repeats two firefight ploys there, so reading it produced the same ploy twice
    and would have violated `UNIQUE(kill_team_id, name)` at seed time. Dropping the
    templates once, here, is narrower than de-duplicating results in every parser,
    and it protects the ones where a duplicate would be harder to notice.
    """
    soup = BeautifulSoup(html, "html.parser")
    for template in soup.find_all("div", class_="tooltip_templates"):
        template.extract()
    return soup


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


# ---------------------------------------------------------------------------
# A team page's datacards. One `div.dsOuterFrame` per operative, holding its name
# (`h3.pTable_h3`), its stat line (a `td.pCell` per stat, labelled), its weapons
# (`table.wTable`) and its keywords (`table.dsKeywords`).
# ---------------------------------------------------------------------------

# Ranged and melee are not distinguishable from a weapon row's text -- both print
# NAME/ATK/HIT/DMG/WR, and a short-ranged weapon reads like a melee one. The site
# marks them with a class on the name row's first cell, which is the only signal.
_CATEGORY_BY_CLASS = {"wsDataRanged": "range", "wsDataMelee": "melee"}

# "Range 3"" among a weapon's rules is the printed distance (KILLTEAM.md #15).
_RANGE_RULE = re.compile(r"^Range\s+(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Weapon:
    """One weapon profile, in the shape `KTWeapon` stores.

    `range` is None unless the page printed a `Range x` rule: the per-category
    default (melee 1, range 2) is the column's, not the parser's, so there is one
    place it lives (KILLTEAM.md → "The datacard").
    """

    name: str
    category: str
    attacks: int
    hit: int
    normal_damage: int
    crit_damage: int
    rules: list[str] = field(default_factory=list)
    range: int | None = None


@dataclass(frozen=True)
class Ability:
    """An ability or a unique action, in the shape `KTAbility` stores.

    One type for both, as the table is: they read the same way on a datacard, and
    nothing in the tracker needs to tell them apart -- the players do. A unique
    action's AP cost stays at the front of its text ("1AP. Select one enemy ...")
    rather than becoming a column; see KILLTEAM.md if that ever needs structuring.
    """

    name: str
    description: str


@dataclass(frozen=True)
class Operative:
    """One operative's datacard."""

    name: str
    apl: int
    move: int
    save: int
    wounds: int
    keywords: list[str] = field(default_factory=list)
    weapons: list[Weapon] = field(default_factory=list)
    abilities: list[Ability] = field(default_factory=list)


def _stat_line(frame: Tag) -> dict[str, int | None]:
    """The four stats, read by their printed LABEL rather than by column order, so a
    layout change moves a value rather than silently swapping two."""
    stats: dict[str, int | None] = {}
    for cell in frame.find_all("td", class_="pCell"):
        value = cell.find(class_="dsStat")
        if value is None:
            continue
        label = cell.get_text(" ", strip=True).split()[0].upper()
        stats[label] = _stat_int(_clean(value.get_text(" ", strip=True)).replace(" ", ""))
    return stats


def _weapon_rules(cell: Tag) -> list[str]:
    """The WR column: rule names with their parameters, as printed."""
    text = _clean(cell.get_text(" ", strip=True))
    return [re.sub(r"\s+", " ", rule).strip() for rule in text.split(",") if rule.strip()]


def _parse_weapon(name_row: Tag) -> Weapon | None:
    """One weapon, from its name row plus the data row beneath it."""
    first_cell = name_row.find("td")
    classes = (first_cell.get("class") or []) if first_cell else []
    category = next((_CATEGORY_BY_CLASS[c] for c in classes if c in _CATEGORY_BY_CLASS), None)
    if category is None:
        return None

    data_row = name_row.find_next_sibling("tr")
    if data_row is None:
        return None
    cells = [_clean(td.get_text(" ", strip=True)) for td in data_row.find_all("td")]
    values = [c for c in cells if c]
    # name, ATK, HIT, DMG, then WR (which may be absent)
    if len(values) < 4:
        return None
    _, attacks, hit, damage, *rest = values
    normal, _, crit = damage.partition("/")

    rules_cell = data_row.find_all("td")[-1]
    rules = _weapon_rules(rules_cell) if rest else []
    printed_range = next((int(m.group(1)) for rule in rules if (m := _RANGE_RULE.match(rule))), None)

    return Weapon(
        name=_clean(name_row.get_text(" ", strip=True)),
        category=category,
        attacks=_stat_int(attacks) or 0,
        hit=_stat_int(hit) or 0,
        normal_damage=_stat_int(normal) or 0,
        crit_damage=_stat_int(crit) or 0,
        rules=rules,
        range=printed_range,
    )


def _keywords(frame: Tag) -> list[str]:
    """The keyword strip. Its last entry is the model's base size, which we do not
    store -- `KTOperative` dropped `base_size` as nothing reads it."""
    table = frame.find("table", class_="dsKeywords")
    if table is None:
        return []
    # Joined with a SPACE, not a comma: the strip already prints commas between
    # keywords, while a multi-word keyword ("GREAT DEVOURER") is several spans --
    # comma-joining every element split that into two keywords.
    text = _clean(table.get_text(" ", strip=True))
    keywords = []
    for chunk in text.split(","):
        # The base size trails the LAST keyword in the same chunk ("PRIME ⌀40mm"), so
        # it is stripped rather than used to drop the chunk -- which silently lost
        # each operative's most specific keyword.
        word = re.sub(r"\s+", " ", re.sub(r"⌀.*$", "", chunk)).strip()
        if word:
            keywords.append(word.upper())
    return keywords


def _abilities(frame: Tag) -> list[Ability]:
    """An operative's abilities and unique actions, in the order printed.

    Two shapes share one block on the page, and each needs its own reading:

      ability       <div class="BreakInsideAvoid"><span class="redfont">NAME</span>: text
      unique action <div class="stratWrapper"><h3 class="h_actions_ds">NAME<span>1AP</span></h3>
                      <div class="actionEffect">text</div>

    Actions are matched by their wrapper rather than by `BreakInsideAvoid`, which
    nests -- selecting on it returned every action twice.
    """
    abilities: list[Ability] = []

    for block in frame.find_all("div", class_="BreakInsideAvoid"):
        # An ability is the only thing here named by a `redfont` span: an action
        # block carries none (checked across two teams' pages, 44 action blocks), so
        # this single test separates the two shapes.
        label = block.find("span", class_="redfont")
        if label is None:
            continue
        name = _clean(label.get_text(" ", strip=True)).lstrip("*").strip()
        text = _clean(block.get_text(" ", strip=True))
        # The block repeats the name, then a colon, then the rule.
        _, _, description = text.partition(":")
        abilities.append(Ability(name=name, description=re.sub(r"\s+", " ", description).strip()))

    for block in frame.find_all("div", class_="stratWrapper"):
        heading = block.find("h3", class_="h_actions_ds")
        if heading is None:
            continue
        cost = heading.find("span")
        cost_text = _clean(cost.get_text(strip=True)) if cost else ""
        if cost:
            cost.extract()  # so the name is not "TOXIC LUNGE1AP"
        name = _clean(heading.get_text(" ", strip=True))
        effect = block.find("div", class_="actionEffect")
        body = _clean(effect.get_text(" ", strip=True)) if effect else ""
        description = f"{cost_text}. {body}".strip(". ") if cost_text else body
        abilities.append(Ability(name=name, description=re.sub(r"\s+", " ", description).strip()))

    return abilities


def parse_operatives(html: str) -> list[Operative]:
    """Every operative on a kill team's page, with its stats, keywords and weapons.

    Raises `ValueError` when a page yields no operatives: a team whose datacards
    failed to parse must not seed as an empty roster list.
    """
    soup = _soup(html)
    operatives: list[Operative] = []
    for frame in soup.find_all("div", class_="dsOuterFrame"):
        heading = frame.find("h3", class_="pTable_h3")
        if heading is None:
            continue
        stats = _stat_line(frame)
        weapons = [
            weapon
            for row in frame.find_all("tr", class_="wTable2_long")
            if (weapon := _parse_weapon(row)) is not None
        ]
        operatives.append(
            Operative(
                name=_clean(heading.get_text(" ", strip=True)),
                apl=stats.get("APL") or 0,
                move=stats.get("MOVE") or 0,
                save=stats.get("SAVE") or 0,
                wounds=stats.get("WOUNDS") or 0,
                keywords=_keywords(frame),
                weapons=weapons,
                abilities=_abilities(frame),
            )
        )
    if not operatives:
        raise ValueError("no operatives on the page — has the datacard markup changed?")
    return operatives


# ---------------------------------------------------------------------------
# The team-level sections: Faction Rules, Strategy/Firefight Ploys, Equipment.
# Each is introduced by an `h2.outline_header2`, and the page prints FLAVOUR text
# (`p.ShowFluff`) beside the rules -- excluded everywhere, since it is prose about
# the faction rather than anything the tracker shows.
# ---------------------------------------------------------------------------

# The site's class says "Tactical"; the section it sits under is printed "Firefight
# Ploys", which is what the rules call them and what `KTPloy.kind` stores.
_PLOY_KIND_BY_CLASS = {"stratStrategicPloy": "strategy", "stratTacticalPloy": "firefight"}


@dataclass(frozen=True)
class TeamRule:
    """A team-wide rule (Raveners: Burrow, Tunnel, Predatory Instincts), as
    `KillTeamRule` stores it."""

    name: str
    description: str


@dataclass(frozen=True)
class Ploy:
    """A ploy, as `KTPloy` stores it, minus the CP cost.

    The pages print no cost, so `cp_cost` is left to the column's default of 1 --
    the same split as a weapon's `range`: the parser reports what the page said, and
    a default that applies to everything lives in one place.
    """

    name: str
    kind: str
    description: str


def _section(soup: BeautifulSoup, title: str) -> list[Tag]:
    """Every element between an `h2.outline_header2` and the next one."""
    heading = next((h for h in soup.find_all("h2", class_="outline_header2") if title in h.get_text()), None)
    if heading is None:
        return []
    nodes = []
    for el in heading.find_all_next():
        if el.name == "h2" and "outline_header2" in (el.get("class") or []):
            break
        nodes.append(el)
    return nodes


def _rule_text(block: Tag, name_el: Tag) -> str:
    """A block's rule text: its heading and any flavour paragraph removed.

    Only the element that carries the NAME is dropped, matched by tag and text -- a
    heading INSIDE the rule (Raveners print the Burrow action within the Burrow rule)
    is part of the rule and stays. `ShowFluff` is the page's prose about the faction,
    which the tracker never shows.

    Works on a COPY: extracting from the live tree would corrupt the document for
    every parser reading the same soup afterwards.
    """
    clone = copy(block)
    for fluff in clone.find_all("p", class_="ShowFluff"):
        fluff.extract()
    wanted = _clean(name_el.get_text(" ", strip=True))
    for candidate in clone.find_all(name_el.name):
        if _clean(candidate.get_text(" ", strip=True)) == wanted:
            candidate.extract()
            break
    return re.sub(r"\s+", " ", _clean(clone.get_text(" ", strip=True))).strip()


def parse_team_rules(html: str) -> list[TeamRule]:
    """The kill team's own rules, from the "Faction Rules" section.

    Each is a `BreakInsideAvoid` block headed by a bare `h3`. An action printed
    inside a rule (Raveners' BURROW) stays part of that rule's text: it is not an
    operative's action, and `KillTeamRule` is name-and-text.
    """
    soup = _soup(html)
    rules: list[TeamRule] = []
    # Iterating the HEADINGS rather than the blocks is what keeps this free of the
    # nesting problem that duplicated unique actions: a rule is named once, however
    # many wrappers the page puts around it.
    for node in _section(soup, "Faction Rules"):
        if node.name != "h3" or node.get("class"):
            continue  # an action's heading carries a class; a rule's does not
        block = node.find_parent("div", class_="BreakInsideAvoid")
        if block is None:
            continue
        rules.append(
            TeamRule(
                name=_clean(node.get_text(" ", strip=True)),
                description=_rule_text(block, node),
            )
        )
    return rules


def parse_ploys(html: str) -> list[Ploy]:
    """Every ploy on the page, strategy and firefight, in printed order."""
    soup = _soup(html)
    ploys: list[Ploy] = []
    for name_el in soup.find_all("div", class_="stratName"):
        kind = next(
            (_PLOY_KIND_BY_CLASS[c] for c in (name_el.get("class") or []) if c in _PLOY_KIND_BY_CLASS),
            None,
        )
        if kind is None:
            continue  # equipment shares this shape and is read separately
        block = name_el.find_parent("div", class_="stratWrapper")
        if block is None:
            continue
        ploys.append(
            Ploy(
                name=_clean(name_el.get_text(" ", strip=True)),
                kind=kind,
                description=_rule_text(block, name_el),
            )
        )
    return ploys
