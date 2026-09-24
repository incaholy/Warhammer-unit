"""Scrape Wahapedia's Kill Team (2024) pages into the seed input.

Same two-stage shape as the 40k scraper (`scrape_wahapedia.py`): this writes JSON,
then `make seed-kt` loads it. The fetch layer is **cached and polite** -- reused from
that module, so there is one place that talks to the site -- and every parse layer is
a **pure function** tested against a synthetic fixture.

    python -m scripts.scrape_wahapedia_kt              # writes scripts/data/killteam.json
    python -m scripts.scrape_wahapedia_kt --refresh   # ...ignoring the page cache
    make scrape-kt / make scrape-kt-fresh

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

import argparse
import json
import re
import sys
from collections.abc import Iterable
from copy import copy
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from scripts.scrape_wahapedia import _stat_int, fetch

NAV_URL = "https://wahapedia.ru/kill-team3/nav.html"
# Gitignored: it is a copy of someone else's content, and the 40k equivalent
# (scripts/data/datasheets.json) ended up tracked. `make seed-kt` reads it.
DATA_PATH = Path(__file__).parent / "data" / "killteam.json"
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
class Equipment:
    """A piece of equipment, as `KTEquipment` stores it.

    The same type for a team's own equipment and for the universal list; which it is
    depends on the page it came from, and the seed decides the `kill_team_id` (NULL
    for universal).
    """

    name: str
    description: str


# "COMMAND RE-ROLL 1CP" -- the core rules page prints a ploy's cost inside its name,
# where the kill team pages print none at all.
_PLOY_COST = re.compile(r"\s*(\d+)\s*CP$", re.IGNORECASE)


@dataclass(frozen=True)
class Ploy:
    """A ploy, as `KTPloy` stores it.

    `cp_cost` is None unless the page states one, and the seed fills in the default of 1
    -- the same split as a weapon's `range`. Most pages print no cost at all; the core
    rules print Command Re-roll's inside its name, and Blades of Khaine prints its four
    firefight ploys' the same way.
    """

    name: str
    kind: str
    description: str
    cp_cost: int | None = None


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


def _own_element(el: Tag) -> Tag:
    """A copy of `el` with its nested lists removed.

    Composition lists nest three deep -- a selection list, its operatives, and each
    operative's loadouts -- so "what does THIS line say?" has to exclude the children.
    Read whole, Wrecka Krew's `<li>KRUSHA GUNNER<ul><li>'Eavy rokkit launcha; fists`
    yields the name with its weapons glued on.
    """
    clone = copy(el)
    for nested in clone.find_all("ul"):
        nested.extract()
    return clone


def _own_text(el: Tag) -> str:
    """`el`'s own text, its nested lists excluded."""
    return re.sub(r"\s+", " ", _clean(_own_element(el).get_text(" ", strip=True))).strip()


def _words(name: str) -> list[str]:
    """A name as upper-case words, punctuation treated as a separator.

    "Proctor-Exactant" and "PROCTOR-EXACTANT" both become ["PROCTOR", "EXACTANT"], so
    the two halves of a page can be compared without caring how either is punctuated.

    Trailing footnote markers are dropped: the composition prints "SHARPSHOOTER ¹" and
    "WARRIOR SICARIAN *" where the marker points at a note below the list, and keeping
    it as a word stopped those entries matching their datacards. Only a TRAILING
    all-digit or asterisk word goes, so "XV26 Stealth Battlesuit" keeps its model
    number.
    """
    words = re.sub(r"[^A-Z0-9 ]", " ", name.upper()).split()
    while words and (words[-1].isdigit() or set(words[-1]) <= {"*"}):
        words.pop()
    return words


class OperativeNotResolved(ValueError):
    """A composition entry could not be tied to exactly one datacard."""


def resolve_operative(entry: str, datacards: list[str], *, strict: bool = True) -> str | None:
    """The datacard name a composition entry refers to.

    A page names its operatives twice, differently: the composition says `FELLTALON`
    or `EXACTION SQUAD PROCTOR-EXACTANT`, while the datacards say `Ravener Felltalon`
    and `Arbites Proctor-Exactant`. `KTSelectionOption.operative_id` needs the second,
    so the two have to be matched -- and the scraper is the only place holding both.

    Rules run strongest first, and each needs a UNIQUE winner or falls through, so a
    loose rule can never steal a match from a strict one. Counted over all 48 teams
    (444 entries) the ordering resolves every one, with no ambiguity: exact, suffix,
    suffix+shortest, prefix, same words, card-contains-entry, entry-contains-card, and
    finally the last word.

    The tie-break is "fewest extra words", and WHICH SIDE the extras are on decides the
    direction. Where the card is the longer side, the shortest card is meant: `GUNNER`
    matches `Spectre Gunner`, `Spectre Heavy Gunner` and `Spectre Stub-Gunner`, and the
    plain one is it -- the others are their own entries and match themselves exactly.
    Where the ENTRY is the longer side the opposite holds, because a card that uses more
    of the entry's words is the more specific reading: `VOID-DANCER TROUPE LEAD PLAYER`
    matches both `Player` and `Lead Player`, and taking the shortest offered a Player on
    the line that requires the Lead Player. Over all 48 teams 12 entries reach a
    tie-break; 11 are on the card side, and this is the one on the entry side.

    `strict=False` returns None for a line that matches NOTHING, which is how a caller
    asks "is this line an operative at all?". An ambiguous line still raises: it is
    certainly naming an operative, and silently dropping it would lose a real entry.

    That "is it an operative?" question cannot be answered from the markup:
    the site styles a keyword only on its FIRST appearance on a page, so
    `GUNNER with webber and gun butt` carries no styled span even though it names an
    operative, while `Autogun; gun butt` looks the same and does not. Resolving the
    text is the reliable test -- no datacard is called "Autogun".
    """
    entry_words = _words(entry)
    if not entry_words:
        if strict:
            raise OperativeNotResolved("an empty composition entry cannot name an operative")
        return None
    joined = " ".join(entry_words)
    as_set = set(entry_words)

    # (matcher, how to break a tie): `min` where the CARD carries the extra words, `max`
    # where the ENTRY does -- see the docstring.
    rules = (
        (lambda card: _words(card) == entry_words, min),
        (lambda card: " ".join(_words(card)).endswith(" " + joined), min),
        (lambda card: " ".join(_words(card)).startswith(joined + " "), min),
        (lambda card: set(_words(card)) == as_set, min),
        (lambda card: set(_words(card)) >= as_set, min),
        # The other direction: the ENTRY carries a prefix the card does not.
        # "INQUISITORIAL AGENT INTERROGATOR" against the card "Interrogator Agent".
        (lambda card: bool(_words(card)) and as_set >= set(_words(card)), max),
        (lambda card: bool(_words(card)) and _words(card)[-1] == entry_words[-1], min),
    )
    for matches, prefer in rules:
        hits = [card for card in datacards if matches(card)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            winner = prefer(hits, key=lambda card: len(_words(card)))
            if sum(1 for card in hits if len(_words(card)) == len(_words(winner))) == 1:
                return winner
            # Raised even when `strict=False`. A line matching several datacards IS
            # naming an operative -- we just cannot say which -- so returning None
            # would make the caller drop it as though it were a weapon loadout. Hunter
            # Clade's "WARRIOR SICARIAN *" vanished that way, and its footnote is what
            # tells a human which Warrior is meant.
            raise OperativeNotResolved(
                f"composition entry {entry!r} matches several datacards equally: {hits}"
            )
    if strict:
        raise OperativeNotResolved(
            f"composition entry {entry!r} matches no datacard (candidates: {datacards})"
        )
    return None


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
        printed = _clean(name_el.get_text(" ", strip=True))
        cost = _PLOY_COST.search(printed)
        ploys.append(
            Ploy(
                name=_PLOY_COST.sub("", printed).strip(),
                kind=kind,
                description=_rule_text(block, name_el),
                cp_cost=int(cost.group(1)) if cost else None,
            )
        )
    return ploys


# Anchored, not substring matches: an unanchored `core-rules` would also match a future
# `core-rules-commentary`, and `find` returns whichever comes first in the document.
def universal_equipment_url(nav_html: str) -> str:
    """The universal equipment page, discovered from the nav rather than hardcoded.

    It is listed there like the kill teams are, so the one URL this module spells out
    stays `NAV_URL`.
    """
    soup = BeautifulSoup(nav_html, "html.parser")
    link = soup.find("a", href=re.compile(r"/kill-team3/the-rules/universal-equipment/?$"))
    if link is None:
        raise ValueError(f"no universal equipment link in the nav — has {NAV_URL} changed?")
    href = link["href"]
    if not href.endswith("/"):
        href += "/"  # the site 301s the slash-less form; `fetch()` caches by URL
    return f"https://wahapedia.ru{href}"


def core_rules_url(nav_html: str) -> str:
    """The core rules page, discovered from the nav like everything else.

    It carries the ploys every kill team may use -- today just Command Re-roll, which the
    model stores with no kill team. Whatever is marked as a ploy there becomes universal,
    so a section added to that page widens the universal list rather than being ignored.
    """
    soup = BeautifulSoup(nav_html, "html.parser")
    link = soup.find("a", href=re.compile(r"/kill-team3/the-rules/core-rules/?$"))
    if link is None:
        raise ValueError(f"no core rules link in the nav — has {NAV_URL} changed?")
    href = link["href"]
    if not href.endswith("/"):
        href += "/"
    return f"https://wahapedia.ru{href}"


def parse_equipment(html: str) -> list[Equipment]:
    """Every piece of equipment on a page, in printed order.

    Serves both sources: a kill team's "Faction Equipment" section and the universal
    equipment page, which print the same `stratEquipment` blocks. Whether a row ends
    up team-owned or universal is the seed's call, from which page it read.

    Names are stored VERBATIM, including the universal page's quantity prefixes
    ("1X AMMO CACHE", "2X LADDERS"). Stripping them would read more consistently
    beside faction equipment, but the quantity is part of what the page calls the
    entry, and inventing a tidier name is the kind of quiet divergence from the
    source this pipeline exists to avoid.
    """
    soup = _soup(html)
    equipment: list[Equipment] = []
    for name_el in soup.find_all("div", class_="stratEquipment"):
        block = name_el.find_parent("div", class_="stratWrapper")
        if block is None:
            continue
        equipment.append(
            Equipment(
                name=_clean(name_el.get_text(" ", strip=True)),
                description=_rule_text(block, name_el),
            )
        )
    return equipment


# ---------------------------------------------------------------------------
# Composition: which operatives a roster may contain (KILLTEAM.md decision #16).
#
# The section is one `ul.redTriangle`, three levels deep:
#
#   li  "4 RAVENER operatives selected from the following list:"   <- a list
#     ul.redCircle2
#       li  "GUNNER with flamer and gun butt"                      <- an option
#         ul
#           li  "Autogun; gun butt"                                <- a loadout
#
# Counts are read from the LEADING number of a line, never from its text: "XV26
# Stealth Battlesuit" would otherwise be read as 26 operatives.
# ---------------------------------------------------------------------------

_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
# "your kill team can only include each operative on this list once"
_ONCE_EACH = re.compile(r"only include each operative on this list once", re.IGNORECASE)
# "Other than CREMATOR and WARRIOR operatives, ..." -- the exceptions to that
_OTHER_THAN = re.compile(r"other than (.+?) operatives,", re.IGNORECASE | re.DOTALL)
# "Your kill team can only include up to two GUNNER operatives."
_KEYWORD_CAP = re.compile(r"include up to (\w+) ([A-Z][A-Z0-9’'\- ]*?) operatives?", re.DOTALL)
_COUNTS_AS = re.compile(r"counts as (\w+) selections?", re.IGNORECASE)
_LEADING_COUNT = re.compile(r"^\s*(\d+)\s")
_IS_NESTED_LIST = re.compile(r"selected from the following list", re.IGNORECASE)


class CompositionNotParsed(ValueError):
    """A composition line does not match a shape this parser knows."""


@dataclass(frozen=True)
class KeywordCap:
    """A cap on operatives carrying a keyword, as `KTSelectionRestriction` stores it.

    `keyword` is the phrase as printed, and matching it is by WORDS rather than by
    string, because the pages are not consistent about which is which: Battleclade's
    datacards print "COMBAT, SERVITOR" -- two keywords, comma-separated -- while
    Pathfinders prints "WEAPONS EXPERT" as one. Both are capped by a sentence naming the
    phrase, so an operative satisfies a cap when every word of the phrase appears among
    the words of its keywords.
    """

    keyword: str
    max_operatives: int

    def matches(self, keywords: Iterable[str]) -> bool:
        """Whether an operative carrying `keywords` counts towards this cap."""
        carried = {word for keyword in keywords for word in _words(keyword)}
        return bool(carried) and set(_words(self.keyword)) <= carried


@dataclass(frozen=True)
class SelectionOption:
    """One operative a list offers, as `KTSelectionOption` stores it."""

    operative: str  # the canonical datacard name, via resolve_operative
    cost: int = 1
    models: int = 1
    max_selections: int | None = None
    loadout_options: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SelectionList:
    """One budgeted list, as `KTSelectionList` stores it."""

    label: str
    budget: int
    position: int
    options: list[SelectionOption] = field(default_factory=list)
    restriction_text: str | None = None


def _restriction_sentence(top: Tag) -> str | None:
    """The sentence printed after the composition list, if any.

    It is a loose text node in the wrapper that holds the heading and the list --
    Raveners' "Other than WARRIOR operatives, your kill team can only include each
    operative on this list once." Kept verbatim; the caps and keyword limits are read
    from it separately.
    """
    wrapper = top.parent
    if wrapper is None:
        return None
    clone = copy(wrapper)
    for el in clone.find_all(["ul", "h1", "h2", "h3"]):
        el.extract()
    text = re.sub(r"\s+", " ", _clean(clone.get_text(" ", strip=True))).strip()
    return text or None


def _loadouts(entry: Tag) -> list[str]:
    """An entry's printed weapon variants: the items of its child lists. Display only.

    Every nested list, not just the first: a line reading "with one of the following
    options: ... Or one option from each of the following:" prints two groups, and for
    display purposes they are all variants of the same entry.
    """
    variants = []
    for nested in entry.find_all("ul"):
        variants.extend(_own_text(li) for li in nested.find_all("li", recursive=False))
    return [v for v in variants if v]


def _option_from(entry: Tag, datacards: list[str]) -> SelectionOption | None:
    """One option, or None when the line does not name an operative.

    The line's own text is "<count?> <NAME> <with loadout?>", and the name is what
    resolves against the datacards -- which is also how a loadout line is recognised,
    since no datacard is called "Autogun".
    """
    text = _own_text(entry)
    if not text or _IS_NESTED_LIST.search(text):
        return None

    count = _LEADING_COUNT.match(text)
    models = int(count.group(1)) if count else 1
    name = _LEADING_COUNT.sub("", text, count=1)
    # "equipped with" as well as "with": Nemesis Claw prints "VISIONARY equipped with
    # one of the following options", and splitting on "with" alone left "VISIONARY
    # equipped", which resolves to nothing.
    name = re.split(r"\bequipped\b|\bwith\b|\bOr the following\b", name)[0]
    name = re.sub(r"\boperatives?\b.*$", "", name, flags=re.IGNORECASE)
    # A parenthetical is a note about the entry, not part of its name: "ASH PROPHET
    # (counts as two selections)" resolves only once it is removed. The note itself is
    # read from the full text below.
    name = re.sub(r"\(.*?\)", " ", name).strip(" :,")

    operative = resolve_operative(name, datacards, strict=False) if name else None
    if operative is None:
        return None

    # "MAGUS (counts as two selections)" -- what taking it spends from the budget.
    spend = _COUNTS_AS.search(text)
    cost = _COUNT_WORDS.get(spend.group(1).lower(), 1) if spend else 1
    # "2 PSYCHIC FAMILIAR operatives (still counts as one selection)": the leading
    # count is models, and the sentence says it is still one selection.
    if spend and models > 1:
        cost = _COUNT_WORDS.get(spend.group(1).lower(), 1)

    # Printed variants: the child list items, or the "with ..." tail when the line
    # spells one loadout out inline ("3 VOIDSMAN with lasgun and gun butt").
    variants = _loadouts(entry)
    if not variants:
        tail = re.search(r"\bwith\b(?! one of the following| one option)(.+)$", text, re.IGNORECASE)
        if tail:
            variants = [f"with {tail.group(1).strip(' :,')}"]

    return SelectionOption(operative=operative, cost=cost, models=models, loadout_options=variants)


@dataclass(frozen=True)
class Composition:
    """What a roster may contain: the budgeted lists, plus the team's keyword caps.

    The caps sit here rather than on a list because the sentence scopes them that way --
    "your kill team can only include up to one GRAVIS operative", beside a repeat clause
    that says "each operative on this list once". Brood Brother caps BROODCOVEN, whose
    operatives are offered by a different list than the one the sentence follows.
    """

    lists: list[SelectionList]
    keyword_caps: list[KeywordCap] = field(default_factory=list)


def _keyword_caps(sentence: str) -> list[KeywordCap]:
    """Caps on a SET of operatives, from "can only include up to two GUNNER operatives".

    13 of the 48 teams state one, and several state more than one (Inquisitorial Agent
    caps GUN SERVITOR, SUBDUCTOR and GUNNER). A number word this parser does not know
    raises rather than defaulting: a silently wrong cap approves illegal rosters.
    """
    caps: dict[str, int] = {}
    for word, keyword in _KEYWORD_CAP.findall(sentence):
        limit = _COUNT_WORDS.get(word.lower())
        if limit is None:
            raise CompositionNotParsed(f"unknown quantity {word!r} in a keyword cap: {sentence!r}")
        caps[_clean(keyword).strip()] = limit
    return [KeywordCap(keyword=keyword, max_operatives=limit) for keyword, limit in caps.items()]


def _repeat_exceptions(sentence: str) -> set[str]:
    """The keywords exempt from "each operative ... once", from "Other than CREMATOR and
    WARRIOR operatives, ...". 38 of the 42 restriction sentences carry such a clause."""
    match = _OTHER_THAN.search(sentence)
    if match is None:
        return set()
    listed = re.split(r",| and ", match.group(1))
    return {_clean(word).strip().upper() for word in listed if _clean(word).strip()}


def parse_composition(html: str) -> Composition:
    """The kill team's selection lists, in printed order.

    Three shapes appear across the 48 teams:

      A  "4 RAVENER operatives selected from the following list:"  -> budget 4
      B  "Every ELUCIDIAN STARSTRIDER operative in the following list: 1 X, 1 Y"
                                                                   -> a fixed roster,
                                                                      budget = the sum
      C  "BOSS NOB operative with one of the following options:"    -> an implicit 1

    Anything else raises rather than guessing a budget: a wrong number would seed a
    roster rule that looks right.

    Repeated operatives collapse. Wyrmblade prints three "GUNNER with ..." lines, and
    that is one operative with a weapon choice (KILLTEAM.md: loadouts are display
    only), so the variants merge into one option.
    """
    soup = _soup(html)
    head = next((h for h in soup.find_all(["h2", "h3"]) if h.get_text(strip=True) == "Operatives"), None)
    if head is None:
        raise CompositionNotParsed("no 'Operatives' section on the page")
    top = next(
        (el for el in head.find_all_next() if el.name == "ul" and "redTriangle" in (el.get("class") or [])),
        None,
    )
    if top is None:
        raise CompositionNotParsed("the 'Operatives' section has no composition list")

    # After the structural checks, so a page with no composition reports that rather
    # than failing on its datacards.
    operatives = parse_operatives(html)
    datacards = [operative.name for operative in operatives]
    keywords_of = {operative.name: set(operative.keywords) for operative in operatives}

    # Which items are LISTS rather than entries, at any depth. Two teams print a second
    # list inside the first rather than beside it: Blades of Khaine nests "7 BLADES OF
    # KHAINE operatives ..." under its leader line, and Hunter Clade wraps "9 HUNTER
    # CLADE operatives ..." in a div inside the same `ul`, so it is neither a direct
    # child nor a descendant of the first line. Both were silently folded into the
    # budget-1 line above, offering a one-operative team.
    items: list[Tag] = top.find_all("li")

    def enclosing_items(node: Tag) -> list[Tag]:
        return [item for ancestor in node.parents for item in items if ancestor is item]

    line_nodes: list[Tag] = [
        item
        for item in items
        # A list is either an item no other item contains (however it is wrapped), or
        # one that says so in its own text.
        if not enclosing_items(item) or _IS_NESTED_LIST.search(_own_text(item))
    ]

    def owner_of(entry: Tag) -> Tag | None:
        """The line an entry belongs to: its NEAREST enclosing line. A nested list's
        entries belong to that list, not to the line that contains it."""
        for ancestor in entry.parents:
            if any(ancestor is line for line in line_nodes):
                return ancestor
        return None

    lists: list[SelectionList] = []
    for position, line in enumerate(line_nodes):
        label = _own_text(line)
        own_entries = [
            entry
            for entry in items
            if not any(entry is other for other in line_nodes) and owner_of(entry) is line
        ]
        options: dict[str, SelectionOption] = {}
        for entry in own_entries:
            option = _option_from(entry, datacards)
            if option is None:
                continue
            existing = options.get(option.operative)
            if existing is None:
                options[option.operative] = option
            else:  # the same operative again, with another printed loadout
                options[option.operative] = SelectionOption(
                    operative=existing.operative,
                    cost=existing.cost,
                    models=existing.models,
                    loadout_options=[*existing.loadout_options, *option.loadout_options],
                )

        # A line with no entries beneath it names its own operative ("1 RAVENER PRIME
        # operative", "BOSS NOB operative with one of the following options:"). Its
        # leading number is the BUDGET, not models -- "2 BOMB SQUIG operatives" is two
        # selections of one operative, where a nested "2 PSYCHIC FAMILIAR operatives"
        # is two models for one selection.
        if not options:
            inline = _option_from(line, datacards)
            if inline is not None:
                options = {
                    inline.operative: SelectionOption(
                        operative=inline.operative,
                        cost=inline.cost,
                        models=1,
                        loadout_options=inline.loadout_options,
                    )
                }

        count = _LEADING_COUNT.match(label)
        if count:
            budget = int(count.group(1))  # shape A
        elif label.lower().startswith("every"):
            budget = sum(option.models for option in options.values())  # shape B
        else:
            budget = 1  # shape C: an unnumbered line naming one operative

        if not options:
            raise CompositionNotParsed(f"no operatives found for composition line {label!r}")
        lists.append(
            SelectionList(
                label=label,
                budget=budget,
                position=position,
                options=list(options.values()),
            )
        )

    if not lists:
        raise CompositionNotParsed("the composition list is empty")

    sentence = _restriction_sentence(top)
    caps: list[KeywordCap] = []
    if sentence:
        # Printed after the whole composition and referring to "this list", so it
        # belongs to the last one. Two clauses are read out of it: how often an
        # operative may repeat, and caps on whole sets of operatives.
        last = lists[-1]
        exempt = _repeat_exceptions(sentence)
        repeats_capped = bool(_ONCE_EACH.search(sentence))
        options = [
            SelectionOption(
                operative=option.operative,
                cost=option.cost,
                models=option.models,
                # "each operative on this list once" -- except the keywords the
                # sentence exempts, which stay uncapped beyond the budget. The
                # exemption is a KEYWORD, matched against what the datacard carries:
                # Raveners exempt WARRIOR, and "Ravener Warrior" holds that keyword.
                max_selections=1
                if repeats_capped and not (exempt & keywords_of.get(option.operative, set()))
                else option.max_selections,
                loadout_options=option.loadout_options,
            )
            for option in last.options
        ]
        caps = _keyword_caps(sentence)  # team-wide; see `Composition`
        # A cap nobody can trigger means the phrase was misread, and a cap that
        # silently never applies is worse than none: `validate` would approve rosters
        # it should refuse. Checked against EVERY operative on the page, not just this
        # list's -- the sentence says "your kill team" (Brood Brother caps BROODCOVEN,
        # which its Magus and Patriarch carry from a different list).
        for cap in caps:
            if not any(cap.matches(carried) for carried in keywords_of.values()):
                raise CompositionNotParsed(
                    f"the cap on {cap.keyword!r} matches no operative's keywords on this page"
                )
        lists[-1] = SelectionList(
            label=last.label,
            budget=last.budget,
            position=last.position,
            options=options,
            restriction_text=sentence,
        )
    return Composition(lists=lists, keyword_caps=caps)


def composition_warnings(team: dict) -> list[str]:
    """Things a human should look at in a scraped team. None of them is fatal.

    Both checks exist because a mis-read composition is SILENT: it seeds cleanly, and
    the result is a roster rule that looks right. They found three real problems in one
    pass -- Gellerpox's three vermin (a second composition block the parser does not
    read), Void-dancer's Lead Player (a tie-break that picked the wrong card), and Chaos
    Cult's two rule-granted operatives, which turned out to be correct.

    1. A datacard no list offers. That operative cannot be fielded, so either the page
       grants it some other way or the composition was read wrongly. Operatives named by
       a team RULE or an ABILITY are excluded: Chaos Cult's Mutant and Torment are
       gained mid-game by "Accursed Gifts" and "Mutation", not selected. Equipment text
       is deliberately NOT searched -- Gellerpox's vermin come from MUTOID VERMIN
       equipment, and "equipment can add an operative" is a shape the model cannot hold
       yet, so it should stay visible.

    2. A list whose label names a datacard the list does not offer. This catches the
       mis-resolutions the first check cannot: one that lands on a card offered
       elsewhere leaves no unused datacard behind, and nothing else would notice.
    """
    warnings: list[str] = []
    cards = [operative["name"] for operative in team["operatives"]]
    offered = {option["operative"] for listing in team["selection_lists"] for option in listing["options"]}

    prose = " ".join(
        [rule["description"] for rule in team["rules"]]
        + [ability["description"] for operative in team["operatives"] for ability in operative["abilities"]]
    )
    prose_words = set(_words(prose.upper()))
    for card in cards:
        card_words = set(_words(card.upper()))
        if card in offered or not card_words or card_words <= prose_words:
            continue
        warnings.append(f"no selection list offers {card!r}, and no rule or ability names it")

    for listing in team["selection_lists"]:
        label_words = set(_words(listing["label"].upper()))
        named = [card for card in cards if _words(card.upper()) and set(_words(card.upper())) <= label_words]
        listed = {option["operative"] for option in listing["options"]}
        # The MOST SPECIFIC card the label names, not just any of them: a label reading
        # "... LEAD PLAYER operative" names both `Lead Player` and `Player`, and a list
        # offering only the second is exactly the mis-resolution being looked for.
        best = max(named, key=lambda card: len(_words(card)), default=None)
        if best is not None and best not in listed:
            warnings.append(
                f"list {listing['position']} is labelled {listing['label'][:48]!r}, "
                f"which names {best!r}, but it offers {sorted(listed)}"
            )
    return warnings


def scrape_team(entry: NavEntry, *, refresh: bool = False) -> dict:
    """Everything one kill team's page holds, in the seed's shape.

    Operative names are the DATACARD's, here and in the selection options, so the seed
    resolves nothing: `resolve_operative` already did that while both halves of the page
    were in hand.
    """
    html = fetch(entry.url, refresh=refresh)
    composition = parse_composition(html)
    return {
        "name": entry.name,
        "faction": entry.faction,
        "rules": [asdict(rule) for rule in parse_team_rules(html)],
        "ploys": [asdict(ploy) for ploy in parse_ploys(html)],
        "equipment": [asdict(item) for item in parse_equipment(html)],
        "operatives": [asdict(operative) for operative in parse_operatives(html)],
        "selection_lists": [asdict(lst) for lst in composition.lists],
        "keyword_caps": [asdict(cap) for cap in composition.keyword_caps],
    }


def scrape(*, refresh: bool = False) -> dict:
    """The whole catalog, with the teams that could not be read named in `skipped`.

    A team whose page is AMBIGUOUS is skipped rather than failing the run: 46 of the 48
    parse, and holding those 46 hostage to Hunter Clade's and Inquisitorial Agent's
    ambiguous composition entries would be the wrong trade (both are K6 work).

    Only the two ambiguity errors are caught, deliberately. They mean "this page needs a
    human decision"; anything else -- a missing section, an unreadable stat, an HTTP
    failure -- means the parsers or the site changed, and that must surface as a failed
    run rather than as 46 quietly thinner teams.

    `skipped` goes INTO the payload so the seed can report it too. A caller that only
    printed it would leave `make seed-kt` announcing success over a catalog it knows is
    incomplete.
    """
    nav_html = fetch(NAV_URL, refresh=refresh)
    payload: dict = {
        "kill_teams": [],
        "universal_ploys": [],
        "universal_equipment": [],
        "skipped": [],
    }

    for entry in parse_nav(nav_html):
        try:
            payload["kill_teams"].append(scrape_team(entry, refresh=refresh))
        except (CompositionNotParsed, OperativeNotResolved) as exc:
            payload["skipped"].append({"team": entry.name, "reason": str(exc)})

    # Available to every kill team, so stored with no kill team: Command Re-roll from
    # the core rules, and the universal equipment list.
    payload["universal_ploys"] = [
        asdict(p) for p in parse_ploys(fetch(core_rules_url(nav_html), refresh=refresh))
    ]
    payload["universal_equipment"] = [
        asdict(item) for item in parse_equipment(fetch(universal_equipment_url(nav_html), refresh=refresh))
    ]
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-request every page instead of reading the cache, so a changed page is seen",
    )
    args = parser.parse_args()

    payload = scrape(refresh=args.refresh)
    teams = payload["kill_teams"]
    if not teams:
        # The file is gitignored, so overwriting a good one with `{"kill_teams": []}`
        # loses it for good. Nothing parsed means the site or the parsers changed.
        print(
            f"no kill teams parsed -- {DATA_PATH} left untouched. The site or the parsers changed.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"{len(teams)} kill teams written to {DATA_PATH}")
    # Every section is counted, including the three that come back empty rather than
    # raising (rules, ploys, equipment) -- a zero here is the only sign that a renamed
    # class stopped matching.
    print(
        f"  {sum(len(t['operatives']) for t in teams)} operatives, "
        f"{sum(len(o['weapons']) for t in teams for o in t['operatives'])} weapons, "
        f"{sum(len(o['abilities']) for t in teams for o in t['operatives'])} abilities"
    )
    print(
        f"  {sum(len(t['rules']) for t in teams)} team rules, "
        f"{sum(len(t['ploys']) for t in teams)} ploys, "
        f"{sum(len(t['equipment']) for t in teams)} equipment"
    )
    print(
        f"  {sum(len(t['selection_lists']) for t in teams)} selection lists, "
        f"{sum(len(sel['options']) for t in teams for sel in t['selection_lists'])} options, "
        f"{sum(len(t['keyword_caps']) for t in teams)} keyword caps"
    )
    print(
        f"  {len(payload['universal_ploys'])} universal ploys, "
        f"{len(payload['universal_equipment'])} universal equipment"
    )
    if payload["skipped"]:
        print(f"\nskipped {len(payload['skipped'])} team(s) -- ambiguous pages, left for K6:")
        for entry in payload["skipped"]:
            print(f"  {entry['team']}: {entry['reason']}")

    # Not failures: the catalog is usable, but a silently mis-read composition seeds just
    # as cleanly as a correct one, so anything suspicious is said out loud.
    flagged = [(team["name"], warning) for team in teams for warning in composition_warnings(team)]
    if flagged:
        print(f"\n{len(flagged)} composition(s) to check by hand:")
        for name, warning in flagged:
            print(f"  {name}: {warning}")
    print("\nNow run: make seed-kt")


if __name__ == "__main__":
    main()
