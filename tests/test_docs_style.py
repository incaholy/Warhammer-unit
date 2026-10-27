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
# `(?<![#\w])` is load-bearing: these files reference decisions as `#48` and roadmap
# entries as `R12` constantly, and a bare `\b` matches between `#` and `4`. Without it,
# "#48 justifies the route" reads as a count of 48 routes -- which it did, on the first
# document to put a decision reference within two words of the word.
LOOSE_COUNT = re.compile(
    rf"(?<![#\w])(?:[2-9]|[1-9][0-9,]*|{NUMBER_WORDS})\s+"
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


# ---------------------------------------------------------------------------
# A measurement of a tracked file, restated without saying WHEN it was taken.
# ---------------------------------------------------------------------------

FILE_REF = re.compile(r"`[\w./-]+\.(?:md|py|json|ts|tsx|mjs|yml|toml|cfg)`")

# `99 lines`, `100,867-byte`, `2,112,565 bytes`, `24 KB`. The lookbehind keeps `#48`
# and `R12` out, for the reason `LOOSE_COUNT` documents.
FILE_MEASURE = re.compile(r"(?<![#\w])[\d,]+[\s-](?:lines?|bytes?|KB)\b", re.I)

# Seven to forty hex characters: a short SHA through a full one.
COMMIT_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")

# EMPTY, and that is the point. The six measurements this gate was written against were
# corrected in the commit that emptied it, so the list is a record of a closed class
# rather than a standing licence. A new entry here needs the same justification the
# originals had: it is a ratchet, and it only ever shrinks.
KNOWN_UNPINNED: tuple[str, ...] = ()


def _statements(text: str) -> list[str]:
    """One statement per unit, with prose unwrapped and table rows kept whole.

    Markdown prose wraps, so a filename and its measurement sit on different LINES
    constantly -- `ROADMAP.md` names `SPEC.md` on one line and its byte count on the
    next, which a line-scanner cannot see. Paragraphs are therefore unwrapped before
    splitting. A table row is its own statement: flattening a table joins unrelated
    cells and reports the wrong row.
    """
    para: list[str] = []
    out: list[str] = []

    def flush() -> None:
        if para:
            out.extend(re.split(r"(?<=[.;])\s+", " ".join(" ".join(para).split())))
            para.clear()

    for line in text.split("\n"):
        if line.lstrip().startswith("|"):
            flush()
            out.append(line)
        elif not line.strip():
            flush()
        else:
            para.append(line)
    flush()
    return out


def _unpinned_measurements(doc: Path) -> list[str]:
    return [
        st.strip()
        for st in _statements(doc.read_text(encoding="utf-8"))
        if FILE_REF.search(st) and FILE_MEASURE.search(st) and not COMMIT_SHA.search(st)
    ]


@pytest.mark.parametrize("doc", _governed_files(), ids=lambda p: p.name)
def test_a_file_measurement_names_the_commit_it_was_taken_at(doc: Path) -> None:
    """`README.md is 99 lines` rots; `99 lines at 753f552` does not.

    `CLAUDE.md` already says it: "where a count is measured at a commit, say which
    commit". Nothing enforced it, and the class this gate exists for is not
    inattention -- `bb7f94e` on this branch BOTH rewrote `README.md` to 132 lines and
    wrote "99 lines" into `ROADMAP.md`. One commit created the fact and the falsehood
    about it, with the author looking straight at both.

    `CODE-REVIEW.md:152` is the counter-example and the model: it pins its count to a
    commit and is still true a month later, while the two unpinned copies of the same
    measurement rotted.

    The rule is narrow on purpose. It fires only where a backticked FILE and a
    line/byte count share one statement, so "287 bytes a row" and "24-42 KB" -- which
    measure payloads rather than files -- are untouched.
    """
    offenders = [st for st in _unpinned_measurements(doc) if not any(k in st for k in KNOWN_UNPINNED)]
    assert offenders == [], (
        f"{doc.name}: a tracked file's size is restated with no commit. "
        f"Pin it, cite the document that owns it, or drop the number: {offenders}"
    )


def test_every_known_unpinned_measurement_still_exists() -> None:
    """The half that makes the list above a RATCHET rather than a licence.

    Without this, a fixed entry stays in `KNOWN_UNPINNED` forever and the next
    regression is waved through on a permission nobody remembers granting.
    """
    live = [st for doc in _governed_files() for st in _unpinned_measurements(doc)]
    fixed = [k for k in KNOWN_UNPINNED if not any(k in st for st in live)]
    assert fixed == [], f"these no longer offend -- delete them from KNOWN_UNPINNED: {fixed}"


# The Order table at the top of `ROADMAP.md` is a hand-maintained index of the sections
# below it, so it goes stale every time one is added. R13 went in without a row last
# round and R17 did it again this round, which is the signal that the gap is structural
# and not inattention.
ROADMAP_HEADING = re.compile(r"^## (R\d+)\.", re.M)
ORDER_ROW = re.compile(r"^\|\s*\d+\s*\|\s*\[(R\d+)\]", re.M)

# Entries with a heading and no row in the Order table. A RATCHET, read with the test
# below: an entry leaves this list when it is indexed and nothing may be added.
KNOWN_UNINDEXED: frozenset[str] = frozenset({"R17"})


def _unindexed_entries() -> list[str]:
    text = (DOCS / "ROADMAP.md").read_text(encoding="utf-8")
    indexed = set(ORDER_ROW.findall(text))
    return [e for e in ROADMAP_HEADING.findall(text) if e not in indexed]


def test_every_roadmap_entry_appears_in_the_order_table() -> None:
    """An entry nothing links to is an entry nobody schedules.

    R17 was reachable only by scrolling to its own heading: the Order table stopped at
    R16, the status line below it still said "R13 to R16", and `SPEC.md` still described
    the range as `R1`-`R16`. Three sites, and the entry was written by someone who had
    just read all three.

    This is the second instance. R13 was found the same way one round earlier, which is
    what makes it a class rather than a slip -- a reader cannot schedule what the index
    does not list, and the index is the only part of the document a reader is expected
    to read in full.

    The gate is the heading, not the prose. A status line counts entries and will drift
    again; the table is the one place a missing entry is unambiguous.
    """
    offenders = [e for e in _unindexed_entries() if e not in KNOWN_UNINDEXED]
    assert offenders == [], (
        f"ROADMAP.md: these entries have a heading and no row in the Order table. "
        f"Add a row, or drop the heading: {offenders}"
    )


def test_every_known_unindexed_entry_is_still_unindexed() -> None:
    """The half that makes the list above a RATCHET rather than a licence.

    Same argument as `test_every_known_unpinned_measurement_still_exists`: a fixed entry
    left in the allowlist spends its permission on the next regression.
    """
    live = _unindexed_entries()
    fixed = sorted(e for e in KNOWN_UNINDEXED if e not in live)
    assert fixed == [], f"these are indexed now -- delete them from KNOWN_UNINDEXED: {fixed}"
