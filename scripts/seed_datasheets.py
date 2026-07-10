"""Bulk-load the datasheet catalog from scripts/data/datasheets.json.

    python -m scripts.seed_datasheets
    make seed

Drives `UnitService` (not raw SQL), so the same FK checks, CHECK constraints, and
faction/subfaction validation that guard the API also guard the seed. Idempotent:
rows are matched by natural key and created only if absent, so re-running after
editing the JSON adds the new rows without duplicating existing ones. Needs only
`DATABASE_URL`; no admin/auth (it's an operator action).

datasheets.json shape (all names must be canonical — factions from `FactionName`,
subfactions from `FACTION_SUBFACTIONS`; see scripts/data/README.md):

    {
      "factions":  [ { "name": <faction>, "subfactions": [ <name>, ... ] }, ... ],
      "weapons":   [ { "name", "category", "attacks", "weapon_skill", "strength",
                       "armor_piercing", "damage", "range_inches"?, "keywords"? }, ... ],
      "abilities": [ { "name", "description" }, ... ],
      "units":     [ { "unit_name", "faction", "subfaction"?, "movement", "toughness",
                       "armor_save", "wounds", "leadership", "objective_control",
                       "points", "invulnerable_save"?, "keywords"?,
                       "weapons": [ <name>, ... ], "abilities": [ <name>, ... ] }, ... ]
    }
"""

import json
import sys
from pathlib import Path

from sqlmodel import Session, select

from app.core.db.connection import get_engine
from app.core.db.models import Ability, Faction, Subfaction, Unit, Weapon
from app.core.services.service_unit import UnitService

DATA_PATH = Path(__file__).parent / "data" / "datasheets.json"


class SeedError(Exception):
    """A problem in datasheets.json — a bad cross-reference or malformed record."""


def _ref(mapping: dict, key, what: str):
    """Look up a name in one of the id maps, with a clear message if it's absent."""
    try:
        return mapping[key]
    except KeyError:
        raise SeedError(f"{what}: {key!r} is not defined in datasheets.json") from None


def seed(session: Session, data: dict) -> dict:
    """Get-or-create every row in `data`; returns how many were newly created."""
    svc = UnitService(session)
    counts = {"factions": 0, "subfactions": 0, "weapons": 0, "abilities": 0, "units": 0}

    faction_ids: dict[str, object] = {}
    subfaction_ids: dict[tuple[str, str], object] = {}
    for f in data.get("factions", []):
        faction = session.exec(select(Faction).where(Faction.name == f["name"])).first()
        if faction is None:
            faction = svc.create_faction(f["name"])
            counts["factions"] += 1
        faction_ids[f["name"]] = faction.id
        for sub_name in f.get("subfactions", []):
            sub = session.exec(
                select(Subfaction).where(
                    Subfaction.faction_id == faction.id, Subfaction.name == sub_name
                )
            ).first()
            if sub is None:
                sub = svc.create_subfaction(faction.id, sub_name)
                counts["subfactions"] += 1
            subfaction_ids[(f["name"], sub_name)] = sub.id

    weapon_ids: dict[str, object] = {}
    for w in data.get("weapons", []):
        try:
            weapon = session.exec(select(Weapon).where(Weapon.name == w["name"])).first()
            if weapon is None:
                weapon = svc.create_weapon(**w)
                counts["weapons"] += 1
            weapon_ids[w["name"]] = weapon.id
        except (KeyError, TypeError) as exc:
            raise SeedError(f"bad weapon entry {w.get('name', w)!r}: {exc}") from None

    ability_ids: dict[str, object] = {}
    for a in data.get("abilities", []):
        try:
            ability = session.exec(select(Ability).where(Ability.name == a["name"])).first()
            if ability is None:
                ability = svc.create_ability(a["name"], a["description"])
                counts["abilities"] += 1
            ability_ids[a["name"]] = ability.id
        except KeyError as exc:
            raise SeedError(f"bad ability entry {a.get('name', a)!r}: {exc}") from None

    for u in data.get("units", []):
        try:
            name = u["unit_name"]
            faction_id = _ref(
                faction_ids, u["faction"], f"unit {name!r} references unknown faction"
            )
            sub = u.get("subfaction")
            subfaction_id = (
                _ref(subfaction_ids, (u["faction"], sub),
                     f"unit {name!r} references unknown subfaction")
                if sub else None
            )
            unit = session.exec(
                select(Unit).where(Unit.faction_id == faction_id, Unit.unit_name == name)
            ).first()
            if unit is None:
                unit = svc.create_unit(
                    faction_id=faction_id,
                    unit_name=name,
                    movement=u["movement"],
                    toughness=u["toughness"],
                    armor_save=u["armor_save"],
                    wounds=u["wounds"],
                    leadership=u["leadership"],
                    objective_control=u["objective_control"],
                    points=u["points"],
                    invulnerable_save=u.get("invulnerable_save"),
                    subfaction_id=subfaction_id,
                    keywords=u.get("keywords"),
                )
                counts["units"] += 1
        except KeyError as exc:
            raise SeedError(
                f"unit {u.get('unit_name', '?')!r} is missing required field {exc}"
            ) from None
        for wname in u.get("weapons", []):
            svc.link_weapon(unit.id, _ref(weapon_ids, wname, f"unit {name!r} links unknown weapon"))
        for aname in u.get("abilities", []):
            svc.link_ability(unit.id, _ref(ability_ids, aname, f"unit {name!r} links unknown ability"))

    return counts


def _fail(message: str) -> None:
    print(f"seed error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    try:
        raw = DATA_PATH.read_text()
    except OSError as exc:
        _fail(f"cannot read {DATA_PATH}: {exc}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(f"{DATA_PATH} is not valid JSON: {exc}")
    try:
        with Session(get_engine()) as session:
            counts = seed(session, data)
    except (SeedError, ValueError, TypeError, LookupError) as exc:
        # SeedError = bad reference/shape; ValueError family = the service's
        # ConflictError/*ValidationError; LookupError = NotFoundError.
        _fail(str(exc))
    print("seeded:", ", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing")


if __name__ == "__main__":
    main()
