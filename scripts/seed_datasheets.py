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
from pathlib import Path

from sqlmodel import Session, select

from app.core.db.connection import get_engine
from app.core.db.models import Ability, Faction, Subfaction, Unit, Weapon
from app.core.services.service_unit import UnitService

DATA_PATH = Path(__file__).parent / "data" / "datasheets.json"


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
        weapon = session.exec(select(Weapon).where(Weapon.name == w["name"])).first()
        if weapon is None:
            weapon = svc.create_weapon(**w)
            counts["weapons"] += 1
        weapon_ids[w["name"]] = weapon.id

    ability_ids: dict[str, object] = {}
    for a in data.get("abilities", []):
        ability = session.exec(select(Ability).where(Ability.name == a["name"])).first()
        if ability is None:
            ability = svc.create_ability(a["name"], a["description"])
            counts["abilities"] += 1
        ability_ids[a["name"]] = ability.id

    for u in data.get("units", []):
        faction_id = faction_ids[u["faction"]]
        sub = u.get("subfaction")
        subfaction_id = subfaction_ids[(u["faction"], sub)] if sub else None
        unit = session.exec(
            select(Unit).where(
                Unit.faction_id == faction_id, Unit.unit_name == u["unit_name"]
            )
        ).first()
        if unit is None:
            unit = svc.create_unit(
                faction_id=faction_id,
                unit_name=u["unit_name"],
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
        for wname in u.get("weapons", []):
            svc.link_weapon(unit.id, weapon_ids[wname])  # idempotent
        for aname in u.get("abilities", []):
            svc.link_ability(unit.id, ability_ids[aname])  # idempotent

    return counts


def main() -> None:
    data = json.loads(DATA_PATH.read_text())
    with Session(get_engine()) as session:
        counts = seed(session, data)
    print("seeded:", ", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing")


if __name__ == "__main__":
    main()
