"""The two documentation rules a test can hold — see CLAUDE.md, "Documentation style".

The other rules in that section are habits. This file exists because every stale claim
the October 2026 docs audit turned up was a documented fact with nothing checking it: a
decision count that still said 66 after seven were added, a `SPEC.md` backlog item
asking for an `IntegrityError` handler that `app/main.py` already registers and that
`SPEC.md` itself describes 740 lines earlier, and a branching section instructing a
reader to merge a pull request that had merged seven weeks before. Decision #54's
standard applies to prose too: a documented fact that nothing checks is a convention.

So the gate is deliberately narrow. Neither check enforces a rule as *written* — both
enforce the condition under which breaking it does damage, which is the same move
`not_nullable_fields` makes in the game service. A rule stated more broadly than it can
be checked is the thing CLAUDE.md's own section warns about.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent

# Every design document the style governs. This started at three files with the other
# three held in a comment, which was the defect this file is about: a worklist nothing
# reads is a convention. The seven sites it listed were checked against the contract and
# substituted, so the list is gone and the gate covers all six.
GOVERNED = (
    "MVP.md",
    "KILLTEAM.md",
    "ROADMAP.md",
    "SPEC.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
)

NUMBER_WORDS = (
    "two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen"
    "|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty"
)

# A count of TWO OR MORE beside `endpoint`/`route`. One is exempt on purpose: "one
# route away" and "exactly one endpoint paginates" are ordinary English where no
# tallying is implied, and a reader cannot re-derive 1 into a different number.
LOOSE_COUNT = re.compile(
    rf"\b(?:[2-9]|[1-9][0-9,]*|{NUMBER_WORDS})\s+"
    rf"(?:\w+\s+){{0,2}}?(endpoints?|routes?)\b",
    re.I,
)

MAX_WORDS = 25

# Headings whose content is reference rather than argument. A sentence cap applied
# document-wide and honoured most of the time would exempt exactly the long,
# conditional sentences where ambiguity concentrates, so it is applied per section at
# full strength instead.
REFERENCE_SECTIONS = {
    "MVP.md": (
        "## API surface",
        "### Every table, and what it holds",
        "## What success looks like",
        "## Glossary",
    ),
}


def _governed_files() -> list[Path]:
    return [DOCS / name for name in GOVERNED if (DOCS / name).exists()]


@pytest.mark.parametrize("doc", _governed_files(), ids=lambda p: p.name)
def test_no_count_beside_a_loose_api_term(doc: Path) -> None:
    """`49 paths, 72 operations` is re-derivable; `72 endpoints` is not.

    This is the check that would have caught "thirteen nested game routes". The games
    router has 10 nested paths carrying 13 nested operations, so a reader verifying
    either figure concludes the other is stale — which is what happened, and the
    correct number was nearly "fixed" into a wrong one.
    """
    offenders = []
    for n, line in enumerate(doc.read_text().splitlines(), 1):
        # The rule's own prose has to be able to name the words it refuses.
        if "`endpoint`" in line or "`route`" in line:
            continue
        for m in LOOSE_COUNT.finditer(line):
            offenders.append(f"{doc.name}:{n}: {m.group(0)!r} — say `path` or `operation`")
    assert not offenders, "\n".join(offenders)


def _strip_markup(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)  # fenced blocks are not prose
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links keep their label
    return re.sub(r"[`*#>]", "", text)


def _section(body: str, heading: str) -> str:
    start = body.index(heading)
    depth = len(heading) - len(heading.lstrip("#"))
    nxt = re.compile(rf"^#{{1,{depth}}} ", re.M)
    m = nxt.search(body, start + len(heading))
    return body[start : m.start() if m else len(body)]


def _sentences(block: str) -> list[str]:
    # Table cells and list items are statements in their own right, so they are split
    # on as sentence boundaries — a 40-word cell is as hard to read as a 40-word
    # sentence, and the rule is one statement per cell.
    for chunk in re.split(r"\n|\|", block):
        chunk = _strip_markup(chunk).strip(" -·")
        for sent in re.split(r"(?<=[.!?:]) +", chunk):
            sent = sent.strip()
            if sent:
                yield sent


@pytest.mark.parametrize(
    ("doc_name", "heading"),
    [(d, h) for d, hs in REFERENCE_SECTIONS.items() for h in hs],
    ids=lambda v: v.replace(" ", "-").lstrip("#-"),
)
def test_reference_sections_keep_statements_short(doc_name: str, heading: str) -> None:
    """At most 25 words per statement, inside the reference sections only."""
    body = (DOCS / doc_name).read_text()
    assert heading in body, f"{doc_name} has no heading {heading!r} — update the map"
    offenders = [
        f"{len(s.split())}w: {s[:90]}"
        for s in _sentences(_section(body, heading))
        if len(s.split()) > MAX_WORDS
    ]
    assert not offenders, f"{doc_name} {heading}\n" + "\n".join(offenders)


def test_rfc2119_keywords_are_the_four_the_style_allows() -> None:
    """`SHALL`, `REQUIRED`, `RECOMMENDED` and `OPTIONAL` are synonyms of the four.

    Allowing both sets would break one-term-one-meaning inside the very mechanism
    adopted to remove ambiguity, so the style names four and this refuses the rest.
    """
    banned = re.compile(r"(?<![\w`])(SHALL NOT|SHALL|REQUIRED|RECOMMENDED|OPTIONAL)(?![\w`])")
    offenders = []
    for doc in _governed_files():
        for n, line in enumerate(doc.read_text().splitlines(), 1):
            if "`SHALL`" in line or "synonym" in line.lower():
                continue  # the style section names them in order to refuse them
            for m in banned.finditer(line):
                offenders.append(f"{doc.name}:{n}: {m.group(1)} — use MUST/MUST NOT/SHOULD/MAY")
    assert not offenders, "\n".join(offenders)
