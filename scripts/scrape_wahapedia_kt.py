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
import contextlib
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


# The site's templating leaks these around keywords in 14 rows of text ("your <KY>OBELISK
# </KY> <KY>NODE</KY> markers"). The keyword itself is wanted; the markers are not.
_PSEUDO_TAGS = re.compile(r"</?KY>")


# A space the SOURCE leaves before punctuation: "Other than GUNNER , SUBDUCTOR and ..."
# where each keyword is a span and the markup has whitespace before the comma. Five
# stored strings; it used to be 941, because joining inline elements with a space put one
# there ourselves -- `_text` no longer does (see its docstring).
_SPACED_PUNCTUATION = re.compile(r"\s+([:;,.!?])")


def _clean(text: str) -> str:
    """Text as we store it: no &nbsp; padding, no curly quotes, no `<KY>` markers, and no
    space before punctuation where the source left one."""
    text = _PSEUDO_TAGS.sub("", text.replace("\xa0", " ")).translate(_TYPOGRAPHIC)
    return _SPACED_PUNCTUATION.sub(r"\1", text).strip()


def _text(node) -> str:
    """A node's text as we store it: the source's own spacing, collapsed and cleaned.

    No separator, deliberately. `get_text(" ")` joins every text node with a space, which
    the WR column needs for its comma-separated rule names -- and which is wrong wherever
    the source has no space at a boundary. Wahapedia style game terms inline wherever they
    appear, including mid-word, so joining with a space split words:

        Conceal</span></span>ed Position  ->  "Conceal ed Position"
        Anti-<b>PSYKER                    ->  "Anti- PSYKER"
        Heavy (<b>Dash</b> Only)          ->  "Heavy ( Dash Only)"

    The separator was there because `strip=True` removes each node's own whitespace,
    including the space after a comma, so without a separator the list fused. Dropping
    BOTH keeps the source's spacing exactly: `Silent, Concealed Position*`. It changed 671
    stored strings and not one of them in any way but whitespace.
    """
    return re.sub(r"\s+", " ", _clean(node.get_text())).strip()


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
            faction = _text(el)
        elif el.name == "a" and "kill-teams/" in (el.get("href") or ""):
            if faction is None:
                raise ValueError(f"kill team {_text(el)!r} appears before any faction group")
            slug = el["href"].rstrip("/").rsplit("/", 1)[-1]
            entries.append(NavEntry(name=_text(el), faction=faction, slug=slug))
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
        # The ONE place a separator is still needed: the cell is `APL<div>3</div>` with no
        # whitespace between label and value, so without one they fuse into "APL3". This
        # is reading a label, not storing text, so a stray space costs nothing.
        label = cell.get_text(" ", strip=True).split()[0].upper()
        stats[label] = _stat_int(_text(value).replace(" ", ""))
    return stats


def _weapon_rules(cell: Tag) -> list[str]:
    """The WR column: rule names with their parameters, as printed.

    A comma-separated list, which is why the extractor's separator mattered here most:
    the cell styles each rule name and sometimes only PART of a word, so joining with a
    space produced "Conceal ed Position" on three teams and "Anti- PSYKER" on a fourth
    (see `_text`). 103 distinct rules over 2,328 strings.
    """
    return [rule.strip() for rule in _text(cell).split(",") if rule.strip()]


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
    cells = [_text(td) for td in data_row.find_all("td")]
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
        name=_text(name_row),
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
    text = _text(table)
    keywords = []
    for chunk in text.split(","):
        # The base size trails the LAST keyword in the same chunk ("PRIME ⌀40mm"), so
        # it is stripped rather than used to drop the chunk -- which silently lost
        # each operative's most specific keyword. Stripped and DISCARDED on purpose: the
        # tracker records an operative's state, never its place on the table, so a base
        # size has no reader (KILLTEAM.md, "Out of scope for v1").
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
        name = _text(label).lstrip("*").strip()
        text = _text(block)
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
        name = _text(heading)
        # Effect THEN conditions, the order the page prints them. Reading only the effect
        # dropped every "you cannot perform this action while ..." clause, which turns a
        # conditional action into an unconditional one on a reference sheet.
        parts = [block.find("div", class_=cls) for cls in ("actionEffect", "actionConditions")]
        body = " ".join(_text(part) for part in parts if part is not None)
        if not body:
            # Pathfinders wraps an action's body in a plain <div> with no class, so keying
            # on `actionEffect` left ten MARKERLIGHT rows reading just "1AP".
            body = _text(block).removeprefix(name).lstrip(" :.").strip()
        # Joined rather than stripped: `.strip(". ")` also took the body's final full stop,
        # so 191 of the 193 action descriptions read as though they had been cut off.
        description = f"{cost_text}. {body}" if cost_text and body else (body or cost_text)
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
                name=_text(heading),
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
    # The page section a CHOSEN rule belongs to (decision #27): Blades of Khaine print
    # "Dire Avenger Aspect Techniques", Exodite Dragon Masters "Stonesinger Upgrade".
    # None for an ordinary always-on faction rule.
    group: str | None = None


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
    rules print Command Re-roll's inside its name, and Blades of Khaine and Inquisitorial
    Agent each print their four firefight ploys' the same way -- 8 of the 384 team ploys.
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
    """`el`'s own text, its nested lists and footnote markers excluded.

    A marker is a REFERENCE to a note printed below, not part of the line: Death Korps
    print "4 TROOPER operatives *" and Brood Brother a `<sup>3</sup>` before the colon,
    which rendered into the stored label as "… the following list 3 :". The notes
    themselves are kept, on the composition (decision #30).
    """
    own = _own_element(el)
    for marker in own.find_all(["sup"]) + own.find_all("span", class_="ast"):
        marker.extract()  # `_own_element` already works on a copy
    return _text(own)


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
    """A composition entry could not be tied to exactly one datacard.

    `candidates` holds the datacards it matched equally, so a caller can narrow them with
    what it knows -- see the elimination pass in `_lists_from` (decision #37).
    """

    def __init__(self, message: str, candidates: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.candidates: list[str] = list(candidates)


def resolve_operative(entry: str, datacards: list[str], *, strict: bool = True) -> str | None:
    """The datacard name a composition entry refers to.

    A page names its operatives twice, differently: the composition says `FELLTALON`
    or `EXACTION SQUAD PROCTOR-EXACTANT`, while the datacards say `Ravener Felltalon`
    and `Arbites Proctor-Exactant`. `KTSelectionOption.operative_id` needs the second,
    so the two have to be matched -- and the scraper is the only place holding both.

    Rules run strongest first, and each needs a UNIQUE winner or falls through, so a
    loose rule can never steal a match from a strict one. Six rungs, strongest first:
    exact, suffix, prefix, card-contains-entry, entry-contains-card, and finally the last
    word. They are the fallback, not the main path -- the page's own link answers 444 of
    the 470 composition entries that resolve (decision #35), so only 26 reach this ladder,
    and just three rungs fire on today's pages: suffix 22, exact 3, card-contains-entry 1.
    Two entries resolve on no rung at all and are handled above this function: Hunter
    Clade's `WARRIOR SICARIAN` by elimination (#37) and Inquisitorial Agent's own line by
    the cross-reference guard (#38).

    There was a "same words" rule between prefix and card-contains-entry. It is gone: any
    card whose word SET equals the entry's is also matched by card-contains-entry, whose
    fewest-extra-words tie-break prefers that same card, so it could only ever differ if
    two cards shared the entry's word set with different word counts (a repeated word).
    Removing it changed none of the 452 stored options.

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
                f"composition entry {entry!r} matches several datacards equally: {hits}",
                candidates=hits,
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
    wanted = _text(name_el)
    for candidate in clone.find_all(name_el.name):
        if _text(candidate) == wanted:
            candidate.extract()
            break
    return _text(clone)


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
                name=_text(node),
                description=_rule_text(block, node),
            )
        )

    # Two teams print what they CHOOSE in sections of their own, which this reader used
    # to walk straight past: Blades of Khaine's three Aspect Technique sections (its whole
    # mechanic) and Exodite Dragon Masters' three Upgrade sections -- 30 named blocks in
    # all. They are rules, not ploys: not one of the 30 carries a CP cost. What makes them
    # different is the SECTION, because "THE SLICING HURRICANE" only means anything as a
    # Dire Avenger technique, so the heading becomes the rule's `group` (decision #27).
    #
    # Found by their own name class rather than by listing the sections, so a team that
    # prints a section we have never seen is read too.
    for name_el in soup.select("div.stratName.stratStrategicAsset"):
        wrapper = name_el.find_parent("div", class_="stratWrapper")
        heading = name_el.find_previous("h2")
        if wrapper is None or heading is None:
            continue
        rules.append(
            TeamRule(
                name=_text(name_el),
                description=_rule_text(wrapper, name_el),
                group=_text(heading),
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
        printed = _text(name_el)
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
                name=_text(name_el),
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

# The headings that end a team's own content: anything after them is the page's furniture.
_AFTER_THE_TEAM = {"Datacards", "Books", "FAQ"}
# What makes a segment a restriction sentence rather than a footnote: every one of the 48
# real ones says a kill team "can only include" something, or names an exception with
# "other than".
_IS_RESTRICTION = re.compile(r"can only include|other than", re.IGNORECASE)


class CompositionNotParsed(ValueError):
    """A composition line does not match a shape this parser knows."""


def _without_structure(node: Tag) -> Tag:
    """A copy of `node` holding only its loose text: the lists and headings removed."""
    clone = copy(node)
    for el in clone.find_all(["ul", "h1", "h2", "h3"]):
        el.extract()
    return clone


def _text_wrapper(top: Tag) -> Tag | None:
    """The element whose loose text belongs to the composition list `top`.

    Normally `top.parent`: 47 of the 48 pages put the composition list inside a
    `div.BreakInsideAvoid`, which on 43 of them also prints the restriction sentence after
    it. Hunter Clade wraps the list in a `div.Columns2` of its own first, so the parent
    holds no text and the sentence sits one level up -- where nothing looked, and the team was read as
    printing no restriction at all. It cost that team its repeat clause, three keyword
    caps (DIKTAT, SURVEYOR, SICARIAN), four capped options and a footnote, and no warning
    could report it because every check runs on the sentence this function returns.

    The climb is allowed only when `top` is the wrapper's SOLE element child, which is
    what distinguishes a container from a column of content. Climbing merely because the
    parent held no text of its own would read a NEIGHBOUR's prose as this team's
    restriction: Exodite Dragon Masters' grandparent prints 284 characters of another
    column's actions, and Elucidian Starstrider's is the whole 21,000-character page body.
    Blades of Khaine climbs too, harmlessly: its grandparent is empty as well, so it still
    reads no sentence. Identity, not equality -- two empty `div`s compare equal in
    BeautifulSoup.
    """
    wrapper = top.parent
    if wrapper is None:
        return None
    if _without_structure(wrapper).get_text():
        return wrapper
    children = [child for child in wrapper.children if getattr(child, "name", None)]
    if len(children) == 1 and children[0] is top and wrapper.parent is not None:
        return wrapper.parent
    return wrapper


def _composition_text(top: Tag) -> tuple[str | None, list[str]]:
    """The loose text printed around the composition, as (restriction sentence, notes).

    It used to be one blob. The wrapper's remaining text is a flat run of text nodes and
    inline spans, so taking all of it gave one field holding four different things -- and
    attached to whichever list happened to precede it. Kasrkin's was 342 characters and
    six sentences: the repeat clause, a footnote body, and a glossary note defining
    "hot-shot weapon". 13 teams stored a footnote body on a list, 3 of them on the wrong
    one, and 7 where no marker survived to say which entries it was about.

    The page marks the boundaries itself, which is what makes the split exact rather than
    a guess: a `sup` or `span.ast` marker STARTS a note (it is a reference to the note
    that follows), and a `div.Corner25` callout -- the page's "Designer's Note" box -- is
    a note of its own. Everything before the first marker is the restriction sentence,
    which is the half the repeat clause and the exemptions are read from.

    Notes belong to the composition rather than to a list (decision #30): that is where
    the page prints them, and it is the only attachment that is never wrong.
    """
    wrapper = _text_wrapper(top)
    if wrapper is None:
        return None, []
    clone = _without_structure(wrapper)

    segments: list[list[str]] = [[]]
    for child in clone.children:
        classes = child.get("class") or [] if hasattr(child, "get") else []
        name = getattr(child, "name", None)
        if name == "sup" or (name == "span" and "ast" in classes):
            segments.append([])  # a marker: everything after it is its note
            continue
        text = child.get_text() if hasattr(child, "get_text") else str(child)
        if not text.strip():
            continue
        if name == "div" and "Corner25" in classes:
            segments.append([text])  # a callout box stands alone
            segments.append([])
            continue
        segments[-1].append(text)

    # Filtered AFTER cleaning, not before: a segment of whitespace-only children is
    # truthy but cleans away to nothing, and storing it put an empty `note` on a team.
    cleaned = [text for parts in segments if (text := re.sub(r"\s+", " ", _clean(" ".join(parts))).strip())]
    if not cleaned:
        return None, []
    # The first segment is the restriction sentence only if it READS like one. Gellerpox
    # print a conditional footnote and no restriction at all ("If you selected the MUTOID
    # VERMIN faction equipment:"), which otherwise landed in the restriction slot -- where a
    # reader sees a footnote presented as a constraint on that list, and where the cap and
    # repeat patterns then scan it. 48 of the 49 leading segments carry one of these phrases.
    if not _IS_RESTRICTION.search(cleaned[0]):
        return None, cleaned
    return cleaned[0], cleaned[1:]


def _anchored_operative(entry: Tag, datacards: list[str]) -> str | None:
    """The datacard an entry LINKS to, if the page linked one.

    A composition entry usually carries `<a class="kwbOne" href="…#Sicarian-Infiltrator-
    Warrior">`, which is the site's own link to that datacard further down the page. The
    fragment is the card's name with hyphens for spaces, so matching it by words identifies
    exactly one card -- measured over the 48 composition trees: 444 anchors, 444 unique
    matches, no misses and no ambiguity. The requisition trees add 45 more, and 38 of those
    match nothing here, because a known team's group links the ALLY's page (decision #34):
    returning None is right, and the text ladder never resolves them either.

    This is better evidence than any amount of text matching, and it is what would have
    prevented Void-dancer Troupe's line offering a `Player` where the page requires the
    `Lead Player`. Only the entry's OWN element is searched, so a nested loadout list cannot
    contribute a link.

    Absent for 147 of the 591 composition items this parser reads (296 of the 740 `li` if
    the `redEmptyCircle2` loadouts it skips are counted), and not because the site hides
    anything: it links a keyword on its FIRST appearance only, so Blooded's three repeated "GUNNER with …"
    variants are styled and unlinked, and a phrase naming no single card -- Hunter Clade's
    "WARRIOR SICARIAN", which should read "WARRIOR RUSTSTALKER" -- is left unlinked because
    the site's own linker could not tell either.
    """
    link = _own_element(entry).select_one("a.kwbOne")
    if link is None:
        return None
    fragment = (link.get("href") or "").split("#")[-1]
    wanted = sorted(_words(fragment.replace("-", " ").upper()))
    if not wanted:
        return None
    hits = [card for card in datacards if sorted(_words(card.upper())) == wanted]
    return hits[0] if len(hits) == 1 else None


def parse_in_battle_operatives(html: str) -> list[str]:
    """Datacards a page offers through a CONDITION rather than through its composition.

    Gellerpox Infected print a second `ul.redTriangle` block under "If you selected the
    MUTOID VERMIN faction equipment:", listing Cursemite, Eyestinger Swarm and Sludge-Grub --
    and its line reads "SPECIFIED NUMBER of … operatives", because the number is in the
    equipment text ("add four … for the battle"), not on the line.

    So those three are not rosterable at all: they arrive mid-battle when the equipment is
    revealed, which is decision #18's `add` operation. Marking them `in_battle` (decision
    #20) is what says so -- reading the block as a selection list would claim the opposite.
    It is the only such block across the 48 pages.
    """
    soup = _soup(html)
    head = next((h for h in soup.find_all(["h2", "h3"]) if h.get_text(strip=True) == "Operatives"), None)
    if head is None:
        return []
    blocks = []
    for el in head.find_all_next():
        if el.name == "h2" and el is not head:
            break
        if el.name == "ul" and "redTriangle" in (el.get("class") or []) and el.find_parent("ul") is None:
            blocks.append(el)
    if len(blocks) < 2:
        return []

    datacards = [operative.name for operative in parse_operatives(html)]
    names: list[str] = []
    for block in blocks[1:]:
        for entry in block.find_all("li"):
            with contextlib.suppress(OperativeNotResolved):
                operative = _anchored_operative(entry, datacards) or resolve_operative(
                    _own_text(entry), datacards, strict=False
                )
                if operative is not None and operative not in names:
                    names.append(operative)
    return names


@dataclass(frozen=True)
class SelectionRule:
    """One printed thing from a composition: a bullet, a sentence, a note or a heading."""

    position: int
    kind: str
    text: str
    depth: int = 0


def _printed_bullets(top: Tag) -> list[tuple[int, str]]:
    """Every bullet in a composition tree as (indent depth, text), in printed order.

    Depth is how many list items enclose this one, which is exactly the page's indent: a
    composition line is 0, one of its entries 1, and an entry's own weapon options 2.
    Measured over the 49 composition trees the 48 pages print: 744 bullets, 115 / 493 /
    136 by depth. Forty-NINE trees for 48 teams because Gellerpox Infected print two.

    Keeping the depth is the whole point. The alternative shapes both lie about the page.
    Flattening a line's descendants into one list made "Servo-claw; meltagun" a sibling of
    "AUTO-PROXY SERVITOR" rather than a loadout for the COMBAT SERVITOR above it -- wrong
    on 45 of the 85 lines that have entries. Dropping the `redEmptyCircle2` loadout
    bullets instead lost 157 printed lines and left two lines promising "one of the
    following options:" and listing none.

    Text only, and nothing is resolved against a datacard. That is what makes this
    faithful: an entry names an operative the way the page writes it, and the page is not
    always consistent with its own card names -- Hunter Clade print "WARRIOR SICARIAN"
    for a card called Sicarian Ruststalker Warrior.
    """
    items = list(top.find_all("li"))
    bullets = []
    for item in items:
        depth = sum(1 for ancestor in item.parents for other in items if ancestor is other)
        if text := _own_text(item):
            bullets.append((depth, text))
    return bullets


def _requisition_trees(soup: Tag) -> list[tuple[str, Tag]]:
    """Each "... Requisition" group as (ally name, its composition tree).

    Only Inquisitorial Agent prints a requisition section, and under this model its groups
    are not special: the heading and the lines are text like any other. `known_teams` is no
    longer needed -- there is nothing to resolve and no reference to link, so whether the
    ally has a page of its own stops being a question the catalog answers.
    """
    head = next((h for h in soup.find_all("h2") if h.get_text(strip=True).endswith("Requisition")), None)
    if head is None:
        return []
    trees = []
    for heading in head.find_all_next("h2"):
        title = _clean(heading.get_text(strip=True))
        if title in _AFTER_THE_TEAM:
            break
        top = next(
            (
                el
                for el in heading.find_all_next()
                if el.name == "ul" and "redTriangle" in (el.get("class") or [])
            ),
            None,
        )
        # `find_all_next` walks the whole document, so a group's tree must come before the
        # NEXT heading -- otherwise the last group would borrow a later section's.
        if top is None or (nxt := heading.find_next("h2")) is not None and nxt in top.parents:
            continue
        if top.find_previous("h2") is not heading:
            continue
        trees.append((title, top))
    return trees


def _before_the_next_heading(element: Tag, head: Tag) -> bool:
    """Whether `element` belongs to `head`'s section rather than a later one.

    `find_all_next` walks the whole document, so a section's blocks have to be cut off
    at the next heading -- otherwise "Operatives" would claim the Faction Rules lists.
    """
    previous = element.find_previous(["h2", "h3"])
    return previous is head or previous is None


def parse_selection_rules(html: str) -> list[SelectionRule]:
    """A kill team's composition as the page prints it, in one ordered run.

    Replaces `parse_composition`'s structured output. The catalog describes composition
    and never enforces it (decision #28), so there is no budget to compute, no option to
    resolve, no cap to read out of a sentence and no reference to link -- and therefore
    nothing a mis-read can get subtly wrong while looking right. A line the parser cannot
    interpret is not a problem any more, because it is not interpreted.

    `position` runs across the whole composition, lines and sentences and notes
    interleaved as printed, so the order a reader sees is the order stored.
    """
    soup = _soup(html)
    head = next((h for h in soup.find_all(["h2", "h3"]) if h.get_text(strip=True) == "Operatives"), None)
    if head is None:
        raise CompositionNotParsed("no 'Operatives' section on the page")
    # EVERY top-level block under the heading, not just the first. Gellerpox Infected
    # print a second one -- "If you selected the MUTOID VERMIN faction equipment:" and
    # then the three vermin -- and reading only the first lost those four printed lines.
    # It was unmodellable while composition was a structure: "Specified number of" has no
    # budget, because the number lives in the equipment's own text. As text there is
    # nothing to model. Those datacards are still marked `in_battle` (decision #20), which
    # is the one thing about this block that is read structurally.
    blocks = [
        el
        for el in head.find_all_next()
        if el.name == "ul"
        and "redTriangle" in (el.get("class") or [])
        and el.find_parent("ul") is None
        and _before_the_next_heading(el, head)
    ]
    if not blocks:
        raise CompositionNotParsed("the 'Operatives' section has no composition list")

    rules: list[SelectionRule] = []

    def emit(kind: str, text: str, depth: int = 0) -> None:
        rules.append(SelectionRule(position=len(rules), kind=kind, text=text, depth=depth))

    def emit_loose(tree: Tag) -> None:
        sentence, notes = _composition_text(tree)
        if sentence:
            emit("restriction", sentence)
        for note in notes:
            emit("note", note)

    for index, block in enumerate(blocks):
        for depth, text in _printed_bullets(block):
            emit("line", text, depth)
        # The loose text once, after the first block. Gellerpox's two blocks share one
        # wrapper and the condition is printed BETWEEN them, so this is where it belongs;
        # on the 47 single-block pages it is the text after the only block, as before.
        if index == 0:
            emit_loose(block)

    for title, group in _requisition_trees(soup):
        emit("heading", title)
        for depth, text in _printed_bullets(group):
            emit("line", text, depth)
        emit_loose(group)

    if not rules:
        raise CompositionNotParsed("the composition list is empty")
    return rules


def scrape_team(entry: NavEntry, *, known_teams: Iterable[str] = (), refresh: bool = False) -> dict:
    """Everything one kill team's page holds, in the seed's shape.

    Operative names are the DATACARD's. A composition entry's text is NOT -- it is what
    the page printed, which is sometimes a name no datacard carries (decision #28).
    """
    html = fetch(entry.url, refresh=refresh)
    in_battle = set(parse_in_battle_operatives(html))
    return {
        "name": entry.name,
        "faction": entry.faction,
        "rules": [asdict(rule) for rule in parse_team_rules(html)],
        "ploys": [asdict(ploy) for ploy in parse_ploys(html)],
        "equipment": [asdict(item) for item in parse_equipment(html)],
        "operatives": [
            asdict(operative) | {"availability": "in_battle" if operative.name in in_battle else "roster"}
            for operative in parse_operatives(html)
        ],
        # The composition as printed: lines with their entries, the restriction sentences
        # and the notes, in one ordered run (decision #28). Nothing derived, so there is
        # no budget, cap or resolved option to be subtly wrong about.
        "selection_rules": [asdict(rule) for rule in parse_selection_rules(html)],
    }


def scrape(*, refresh: bool = False) -> dict:
    """The whole catalog, with the teams that could not be read named in `skipped`.

    A team whose page is AMBIGUOUS is skipped rather than failing the run, because holding
    47 teams hostage to one unreadable page would be the wrong trade. All 48 parse today and
    `skipped` is empty: the mechanism is a guard against a page CHANGING, not a standing
    exclusion.

    Only the two ambiguity errors are caught, deliberately. They mean "this page needs a
    human decision"; anything else -- a missing section, an unreadable stat, an HTTP
    failure -- means the parsers or the site changed, and that must surface as a failed
    run rather than as a catalog of quietly thinner teams.

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
        "warnings": [],
    }

    entries = parse_nav(nav_html)
    known_teams = [entry.name for entry in entries]
    for entry in entries:
        try:
            team = scrape_team(entry, known_teams=known_teams, refresh=refresh)
        except (CompositionNotParsed, OperativeNotResolved) as exc:
            payload["skipped"].append({"team": entry.name, "reason": str(exc)})
            continue
        payload["kill_teams"].append(team)
        # In the payload rather than only on the terminal, for the same reason as `skipped`:
        # a seed printing only its own counts would look like a clean catalog.

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
        f"  {sum(len(t['selection_rules']) for t in teams)} selection rules "
        f"({sum(1 for t in teams for r in t['selection_rules'] if r['kind'] == 'line')} lines, "
        f"{sum(1 for t in teams for r in t['selection_rules'] if r['kind'] == 'restriction')} restrictions, "
        f"{sum(1 for t in teams for r in t['selection_rules'] if r['kind'] == 'note')} notes)"
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
    if payload["warnings"]:
        print(f"\n{len(payload['warnings'])} composition(s) to check by hand:")
        for entry in payload["warnings"]:
            print(f"  {entry['team']}: {entry['warning']}")
    print("\nNow run: make seed-kt")


if __name__ == "__main__":
    main()
