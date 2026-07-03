"""UnitService — the catalog of unit datasheets.

Session-injected. Raises `LookupError` for not-found, per SPEC.md conventions.
"""

from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from app.core.db.models import Ability, Faction, Subfaction, Unit, Weapon


class UnitService:
    # Fields a PATCH may set on a unit.
    _UPDATABLE = {
        "unit_name", "faction_id", "subfaction_id", "movement", "toughness",
        "armor_save", "wounds", "invulnerable_save", "leadership",
        "objective_control", "points", "keywords",
    }

    def __init__(self, session: Session):
        self.session = session

    def create_unit(
        self,
        faction_id: UUID,
        unit_name: str,
        movement: int,
        toughness: int,
        armor_save: int,
        wounds: int,
        leadership: int,
        objective_control: int,
        points: int,
        invulnerable_save: Optional[int] = None,
        subfaction_id: Optional[UUID] = None,
        keywords: Optional[list[str]] = None,
    ) -> Unit:
        if self.session.get(Faction, faction_id) is None:
            raise LookupError(f"faction {faction_id} not found")
        if subfaction_id is not None and self.session.get(Subfaction, subfaction_id) is None:
            raise LookupError(f"subfaction {subfaction_id} not found")

        unit = Unit(
            faction_id=faction_id,
            unit_name=unit_name,
            movement=movement,
            toughness=toughness,
            armor_save=armor_save,
            wounds=wounds,
            leadership=leadership,
            objective_control=objective_control,
            points=points,
            invulnerable_save=invulnerable_save,
            subfaction_id=subfaction_id,
            keywords=keywords or [],
        )
        self.session.add(unit)
        self.session.commit()
        self.session.refresh(unit)
        return unit

    def get_unit(self, unit_id: UUID) -> Unit:
        unit = self.session.get(Unit, unit_id)
        if unit is None:
            raise LookupError(f"unit {unit_id} not found")
        return unit

    def list_units(self) -> list[Unit]:
        return list(self.session.exec(select(Unit)).all())

    def update_unit(self, unit_id: UUID, **fields) -> Unit:
        unit = self.get_unit(unit_id)
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise ValueError(f"cannot update fields: {sorted(unknown)}")
        if fields.get("faction_id") is not None and (
            self.session.get(Faction, fields["faction_id"]) is None
        ):
            raise LookupError(f"faction {fields['faction_id']} not found")
        if fields.get("subfaction_id") is not None and (
            self.session.get(Subfaction, fields["subfaction_id"]) is None
        ):
            raise LookupError(f"subfaction {fields['subfaction_id']} not found")

        for key, value in fields.items():
            setattr(unit, key, value)
        self.session.add(unit)
        self.session.commit()
        self.session.refresh(unit)
        return unit

    def delete_unit(self, unit_id: UUID) -> None:
        unit = self.get_unit(unit_id)
        self.session.delete(unit)
        self.session.commit()

    def link_weapon(self, unit_id: UUID, weapon_id: UUID) -> Unit:
        unit = self.get_unit(unit_id)
        weapon = self.session.get(Weapon, weapon_id)
        if weapon is None:
            raise LookupError(f"weapon {weapon_id} not found")
        if weapon not in unit.weapons:
            unit.weapons.append(weapon)
            self.session.add(unit)
            self.session.commit()
            self.session.refresh(unit)
        return unit

    def link_ability(self, unit_id: UUID, ability_id: UUID) -> Unit:
        unit = self.get_unit(unit_id)
        ability = self.session.get(Ability, ability_id)
        if ability is None:
            raise LookupError(f"ability {ability_id} not found")
        if ability not in unit.abilities:
            unit.abilities.append(ability)
            self.session.add(unit)
            self.session.commit()
            self.session.refresh(unit)
        return unit

    # ---- factions & subfactions (catalog reference data) ----

    def list_factions(self) -> list[Faction]:
        return list(self.session.exec(select(Faction)).all())

    def create_faction(self, name: str) -> Faction:
        if self.session.exec(select(Faction).where(Faction.name == name)).first():
            raise ValueError(f"faction {name!r} already exists")
        faction = Faction(name=name)
        self.session.add(faction)
        self.session.commit()
        self.session.refresh(faction)
        return faction

    def create_subfaction(self, faction_id: UUID, name: str) -> Subfaction:
        if self.session.get(Faction, faction_id) is None:
            raise LookupError(f"faction {faction_id} not found")
        clash = self.session.exec(
            select(Subfaction).where(
                Subfaction.faction_id == faction_id, Subfaction.name == name
            )
        ).first()
        if clash is not None:
            raise ValueError(
                f"subfaction {name!r} already exists for that faction"
            )
        sub = Subfaction(faction_id=faction_id, name=name)
        self.session.add(sub)
        self.session.commit()
        self.session.refresh(sub)
        return sub
