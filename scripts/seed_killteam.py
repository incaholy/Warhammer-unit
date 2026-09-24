"""Load the Kill Team catalog from scripts/data/killteam.json.

    python -m scripts.seed_killteam
    make seed-kt

Written by `make scrape-kt`, which resolves every name while both halves of a page are
in hand -- so this script looks rows up by natural key and never guesses.

Running it again brings the database up to the payload: a row is created if absent and
REWRITTEN if the source changed it, so a rebalanced stat or a reworded ploy lands on the
next scrape. A team's selection lists are the exception -- they are replaced as a whole
(see `_seed_composition`), because a list's identity is its print position.

What that leaves is a SUPERSET of the payload, not a match: outside the composition,
rows the source has REMOVED or RENAMED stay behind, and nothing marks them as stale
(`updated_at` cannot -- it only moves when a row is written). A withdrawn keyword cap
therefore keeps being enforced and a renamed team leaves its old subtree beside the new
one. Deleting safely is a question about the rosters that will reference these rows, so
it belongs with K4.

Everything happens in one transaction, so a team that fails mid-run leaves nothing
half-written.

No service layer yet (that is ROADMAP K3), so this writes the models directly. Every
rule still applies: the constraints in `models_killteam.py` are what reject a bad row,
and a seed cannot get past them any more than an API request could.

killteam.json shape:

    {
      "kill_teams": [
        { "name", "faction",
          "rules":      [ { "name", "description" }, ... ],
          "ploys":      [ { "name", "kind", "description", "cp_cost"? }, ... ],
          "equipment":  [ { "name", "description" }, ... ],
          "operatives": [ { "name", "apl", "move", "save", "wounds", "keywords",
                            "weapons":   [ { "name", "category", "range"?, "attacks",
                                             "hit", "normal_damage", "crit_damage",
                                             "rules" }, ... ],
                            "abilities": [ { "name", "description" }, ... ] }, ... ],
          "selection_lists": [ { "label", "budget", "position", "restriction_text"?,
                                 "options": [ { "operative", "cost", "models",
                                                "max_selections"?,
                                                "loadout_options" }, ... ] }, ... ],
          "keyword_caps":    [ { "keyword", "max_operatives" }, ... ] }, ...
      ],
      "universal_ploys":     [ { "name", "kind", "description", "cp_cost"? }, ... ],
      "universal_equipment": [ { "name", "description" }, ... ],
      "skipped":             [ { "team", "reason" }, ... ]
    }

`skipped` names the teams the scraper could not read (ambiguous pages, KILLTEAM.md → K6).
It is reported at the end of a run: the catalog loads fine without them, but a seed that
printed only its own counts would look like a complete one.
"""

import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.db.connection import get_engine
from app.core.db.models_killteam import (
    DEFAULT_PLOY_CP_COST,
    KillTeam,
    KillTeamRule,
    KTAbility,
    KTEquipment,
    KTFaction,
    KTOperative,
    KTPloy,
    KTSelectionList,
    KTSelectionOption,
    KTSelectionRestriction,
    KTWeapon,
    default_range,
)

DATA_PATH = Path(__file__).parent / "data" / "killteam.json"

COUNT_KEYS = (
    "factions",
    "kill_teams",
    "rules",
    "ploys",
    "equipment",
    "operatives",
    "weapons",
    "abilities",
    "selection_lists",
    "selection_options",
    "keyword_caps",
    "universal_ploys",
    "universal_equipment",
    "updated",  # rows that already existed and had at least one column rewritten
    "compositions_replaced",  # teams whose selection lists were rewritten wholesale
)


class SeedError(Exception):
    """A problem in killteam.json — a bad cross-reference or a malformed record."""


def _upsert(
    session: Session,
    model,
    counts: dict,
    count_key: str,
    values: dict[str, Any] | None = None,
    **keys,
):
    """The row matching `keys`: created if absent, brought up to date if present.

    `keys` is the row's NATURAL key, and in every case here it is also a UNIQUE
    constraint — a faction's name, a weapon's (operative, name, category), a list's
    (kill_team, position), an option's (list, operative). Keeping those two identical is
    the whole trick: the lookup cannot disagree with what the database considers the
    same row, so a second run finds the first run's work instead of colliding with it.
    Anything extra in the key is a guess — keying a list on its budget as well as its
    position made a balance change look like a new list and hit the constraint.

    A `None` in `keys` is matched with `IS NULL`, which is how the universal ploys and
    equipment (`kill_team_id = NULL`) are found. SQLAlchemy would render `column == None`
    that way too; `is_()` says it outright rather than leaning on that.

    `values` are REWRITTEN on an existing row. The catalog is derived data: when the
    source changes an operative's wounds, the payload is right and the stored row is
    stale, and create-once would leave the old value silently in place. A column whose
    value comes from a database default is left OUT of `values` by the caller, so a
    re-run does not overwrite it with `None`.

    Counts the row under `count_key` when it was created, and under `"updated"` when an
    existing row actually changed.
    """
    row = session.exec(select(model).where(*_match(model, keys))).first()
    if row is None:
        row = model(**keys, **(values or {}))
        session.add(row)
        session.flush()  # so the row has its id for the children that reference it
        counts[count_key] += 1
        return row

    changed = [field for field, value in (values or {}).items() if getattr(row, field) != value]
    for field in changed:
        setattr(row, field, values[field])
    counts["updated"] += bool(changed)
    return row


def _match(model, keys: dict[str, Any]):
    """`keys` as WHERE clauses, with `IS NULL` where the value is None."""
    for field, value in keys.items():
        column = getattr(model, field)
        yield column.is_(None) if value is None else column == value


def _ploy_values(ploy: dict) -> dict[str, Any]:
    # A page that prints no cost means the default, which is WRITTEN rather than left to
    # the column: a column default only fires on INSERT, so omitting the field would
    # freeze a cost the source has since stopped printing (KTPloy.cp_cost says the same).
    printed = ploy.get("cp_cost")
    return {
        "kind": ploy["kind"],
        "description": ploy["description"],
        "cp_cost": DEFAULT_PLOY_CP_COST if printed is None else printed,
    }


def _seed_operative(session: Session, team: KillTeam, data: dict, counts: dict) -> KTOperative:
    operative = _upsert(
        session,
        KTOperative,
        counts,
        "operatives",
        {
            "apl": data["apl"],
            "move": data["move"],
            "save": data["save"],
            "wounds": data["wounds"],
            "keywords": data["keywords"],
        },
        kill_team_id=team.id,
        name=data["name"],
    )

    for weapon in data.get("weapons", []):
        printed = weapon["range"]
        _upsert(
            session,
            KTWeapon,
            counts,
            "weapons",
            {
                # A melee profile prints no range, so the shared rule supplies one
                # (decision #15). Written, not omitted: see `_ploy_values`.
                "range": default_range(weapon["category"]) if printed is None else printed,
                "attacks": weapon["attacks"],
                "hit": weapon["hit"],
                "normal_damage": weapon["normal_damage"],
                "crit_damage": weapon["crit_damage"],
                "weapon_rules": weapon["rules"],
            },
            operative_id=operative.id,
            name=weapon["name"],
            category=weapon["category"],
        )

    for ability in data.get("abilities", []):
        _upsert(
            session,
            KTAbility,
            counts,
            "abilities",
            {"description": ability["description"]},
            operative_id=operative.id,
            name=ability["name"],
        )
    return operative


def _shape(lists) -> list:
    """A composition as one comparable value, independent of row order.

    Options come back from the database in no particular order, so both sides are sorted
    -- lists by position, options by operative name, each unique within its parent.
    """
    return sorted(
        (
            position,
            budget,
            label,
            restriction,
            tuple(sorted(options, key=lambda option: option[0])),
        )
        for position, budget, label, restriction, options in lists
    )


def _seed_composition(session: Session, team: KillTeam, data: dict, operatives: dict, counts: dict) -> None:
    """Replace the team's selection lists when they differ from the payload.

    An upsert is wrong for this one subtree. A list is identified by its `position` --
    the page's print order -- because nothing else on the line is stable. So a page that
    gains, loses or reorders a line does not change one row, it SHIFTS them all: every
    surviving row would keep its own options while being rewritten with the next list's
    label and budget, leaving a budget-1 list offering two operatives and an option
    costing more than the whole budget. A configuration that was never printed anywhere,
    reported as a tidy incremental update.

    Composition is small, derived, and -- until K4 gives rosters something to reference
    -- pointed at by nothing, so the honest operation is replace-if-changed: compare the
    whole subtree, and when it differs delete the team's lists (options follow by
    cascade) and write the payload's. A team whose composition matches is not touched at
    all, which is the common case on a re-run.
    """
    wanted = []
    for listing in data.get("selection_lists", []):
        options = []
        for option in listing.get("options", []):
            if option["operative"] not in operatives:
                # The scraper resolved this name against this page's datacards, so a
                # miss here means the payload is inconsistent -- not something to guess
                # past, since the option would offer an operative nobody can field.
                raise SeedError(
                    f"{data['name']}: selection option names {option['operative']!r}, "
                    f"which is not one of the team's operatives"
                )
            options.append(
                (
                    option["operative"],
                    option["cost"],
                    option["models"],
                    option["max_selections"],
                    tuple(option["loadout_options"]),
                )
            )
        wanted.append(
            (
                listing["position"],
                listing["budget"],
                listing["label"],
                listing.get("restriction_text"),
                options,
            )
        )

    existing = session.exec(select(KTSelectionList).where(KTSelectionList.kill_team_id == team.id)).all()
    stored = [
        (
            row.position,
            row.budget,
            row.label,
            row.restriction_text,
            [
                (
                    option.operative.name,
                    option.cost,
                    option.models,
                    option.max_selections,
                    tuple(option.loadout_options),
                )
                for option in row.options
            ],
        )
        for row in existing
    ]
    if _shape(stored) == _shape(wanted):
        return

    for row in existing:
        session.delete(row)
    session.flush()  # the DELETEs must land before an INSERT reuses (kill_team_id, position)

    for position, budget, label, restriction, options in wanted:
        row = KTSelectionList(
            kill_team_id=team.id,
            position=position,
            budget=budget,
            label=label,
            restriction_text=restriction,
        )
        session.add(row)
        session.flush()  # so the options have a list id
        for name, cost, models, max_selections, loadouts in options:
            session.add(
                KTSelectionOption(
                    selection_list_id=row.id,
                    operative_id=operatives[name].id,
                    # Carried explicitly: it is what both composite foreign keys reach
                    # the option's parents through.
                    kill_team_id=team.id,
                    cost=cost,
                    models=models,
                    max_selections=max_selections,
                    loadout_options=list(loadouts),
                )
            )

    if existing:
        # Counted per team, not per row: "3 selection lists created" would be a lie about
        # a page that moved one line.
        counts["compositions_replaced"] += 1
    else:
        counts["selection_lists"] += len(wanted)
        counts["selection_options"] += sum(len(options) for *_, options in wanted)


def _seed_kill_team(session: Session, data: dict, counts: dict) -> None:
    faction = _upsert(session, KTFaction, counts, "factions", name=data["faction"])
    team = _upsert(session, KillTeam, counts, "kill_teams", {"faction_id": faction.id}, name=data["name"])

    for rule in data.get("rules", []):
        _upsert(
            session,
            KillTeamRule,
            counts,
            "rules",
            {"description": rule["description"]},
            kill_team_id=team.id,
            name=rule["name"],
        )

    for ploy in data.get("ploys", []):
        _upsert(session, KTPloy, counts, "ploys", _ploy_values(ploy), kill_team_id=team.id, name=ploy["name"])

    for item in data.get("equipment", []):
        _upsert(
            session,
            KTEquipment,
            counts,
            "equipment",
            {"description": item["description"]},
            kill_team_id=team.id,
            name=item["name"],
        )

    operatives = {
        operative["name"]: _seed_operative(session, team, operative, counts)
        for operative in data.get("operatives", [])
    }

    _seed_composition(session, team, data, operatives, counts)

    for cap in data.get("keyword_caps", []):
        _upsert(
            session,
            KTSelectionRestriction,
            counts,
            "keyword_caps",
            {"max_operatives": cap["max_operatives"]},
            kill_team_id=team.id,
            keyword=cap["keyword"],
        )


def seed(session: Session, data: dict) -> dict[str, int]:
    """Load every row in `data`; returns how many were created, plus how many changed."""
    counts = dict.fromkeys(COUNT_KEYS, 0)
    if not data.get("kill_teams"):
        raise SeedError("killteam.json lists no kill teams — run `make scrape-kt` first")

    try:
        for team in data["kill_teams"]:
            _seed_kill_team(session, team, counts)

        # Available to every kill team, so stored with NO kill team. The partial unique
        # index on those rows is what stops a second Command Re-roll; `kill_team_id=None`
        # here is matched with IS NULL, so these never collide with a team's own ploy of the
        # same name.
        for ploy in data.get("universal_ploys", []):
            _upsert(
                session,
                KTPloy,
                counts,
                "universal_ploys",
                _ploy_values(ploy),
                kill_team_id=None,
                name=ploy["name"],
            )

        for item in data.get("universal_equipment", []):
            _upsert(
                session,
                KTEquipment,
                counts,
                "universal_equipment",
                {"description": item["description"]},
                kill_team_id=None,
                name=item["name"],
            )

        session.commit()
    except Exception:
        # All or nothing. The rows written before the failure are only FLUSHED, so a
        # caller that keeps this session would otherwise still see them -- and could
        # commit a catalog no single scrape ever produced.
        session.rollback()
        raise
    return counts


def _fail(message: str) -> None:
    print(f"seed error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    try:
        raw = DATA_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read {DATA_PATH}: {exc} — run `make scrape-kt` first")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(f"{DATA_PATH} is not valid JSON: {exc}")
    try:
        with Session(get_engine()) as session:
            counts = seed(session, data)
    except (SeedError, KeyError, ValueError, TypeError, LookupError, SQLAlchemyError) as exc:
        # SQLAlchemyError included because the constraints in `models_killteam.py` are
        # what reject a bad row, and they raise IntegrityError -- which is none of the
        # builtins above. The session rolls back either way; this is about the message.
        _fail(str(exc))
    written = ", ".join(f"{count} {name}" for name, count in counts.items() if count)
    # flushed so the note below, which goes to stderr, cannot overtake it in a terminal
    print("seeded:", written or "nothing new — the database already matches the payload", flush=True)

    skipped = data.get("skipped") or []
    if skipped:
        # Not a failure -- the other teams are complete and usable -- but the counts above
        # would otherwise read as a whole catalog.
        print(
            f"\nincomplete: {len(skipped)} kill team(s) were not in the payload, "
            f"because the scraper could not read their pages:",
            file=sys.stderr,
        )
        for entry in skipped:
            print(f"  {entry['team']}: {entry['reason']}", file=sys.stderr)


if __name__ == "__main__":
    main()
