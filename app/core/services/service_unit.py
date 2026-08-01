"""UnitService — the catalog of unit datasheets.

Session-injected. Raises `NotFoundError` for not-found, `ConflictError` for
duplicates, and `UnitValidationError` for bad input, per SPEC.md conventions.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.db.models import (
    FACTION_SUBFACTIONS,
    Ability,
    Army,
    ArmyUnit,
    Faction,
    FactionName,
    Subfaction,
    Unit,
    UserUnit,
    Weapon,
)
from app.core.services.errors import (
    ConflictError,
    NotFoundError,
    UnitValidationError,
)


class UnitService:
    # Fields a PATCH may set on a unit.
    _UPDATABLE = {
        "unit_name", "faction_id", "subfaction_id", "movement", "toughness",
        "armor_save", "wounds", "invulnerable_save", "leadership",
        "objective_control", "points", "keywords",
    }
    _WEAPON_UPDATABLE = {
        "name", "category", "attacks", "weapon_skill", "strength",
        "armor_piercing", "damage", "range_inches", "keywords",
    }
    _ABILITY_UPDATABLE = {"name", "description"}

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
            raise NotFoundError(f"faction {faction_id} not found")
        if subfaction_id is not None and self.session.get(Subfaction, subfaction_id) is None:
            raise NotFoundError(f"subfaction {subfaction_id} not found")

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
            raise NotFoundError(f"unit {unit_id} not found")
        return unit

    def _apply_unit_filters(
        self,
        statement,
        faction_id: Optional[UUID],
        subfaction_id: Optional[UUID],
        q: Optional[str],
    ):
        """Apply the shared `list_units`/`count_units` filters to a statement.

        `faction_id`/`subfaction_id` are exact matches; `q` is a
        case-insensitive substring match on the unit name.
        """
        if faction_id is not None:
            statement = statement.where(Unit.faction_id == faction_id)
        if subfaction_id is not None:
            statement = statement.where(Unit.subfaction_id == subfaction_id)
        if q:
            statement = statement.where(Unit.unit_name.ilike(f"%{q}%"))
        return statement

    def list_units(
        self,
        faction_id: Optional[UUID] = None,
        subfaction_id: Optional[UUID] = None,
        q: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Unit]:
        """A page of catalog units, filtered and ordered by name (stable paging)."""
        statement = self._apply_unit_filters(
            select(Unit), faction_id, subfaction_id, q
        )
        # Eager-load weapons/abilities so serializing the page doesn't lazy-load
        # them per unit (the N+1). selectinload batches each with one WHERE-IN.
        statement = (
            statement.options(
                selectinload(Unit.weapons), selectinload(Unit.abilities)
            )
            .order_by(Unit.unit_name)
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.exec(statement).all())

    def count_units(
        self,
        faction_id: Optional[UUID] = None,
        subfaction_id: Optional[UUID] = None,
        q: Optional[str] = None,
    ) -> int:
        """Total units matching the same filters as `list_units` (ignores paging)."""
        statement = self._apply_unit_filters(
            select(func.count(Unit.id)), faction_id, subfaction_id, q
        )
        return self.session.exec(statement).one()

    def update_unit(self, unit_id: UUID, **fields) -> Unit:
        unit = self.get_unit(unit_id)
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise UnitValidationError("fields", f"cannot update {sorted(unknown)}")
        if fields.get("faction_id") is not None and (
            self.session.get(Faction, fields["faction_id"]) is None
        ):
            raise NotFoundError(f"faction {fields['faction_id']} not found")
        if fields.get("subfaction_id") is not None and (
            self.session.get(Subfaction, fields["subfaction_id"]) is None
        ):
            raise NotFoundError(f"subfaction {fields['subfaction_id']} not found")

        for key, value in fields.items():
            setattr(unit, key, value)
        self.session.add(unit)
        self.session.commit()
        self.session.refresh(unit)
        return unit

    def delete_unit(self, unit_id: UUID) -> None:
        unit = self.get_unit(unit_id)
        # ArmyUnit/UserUnit reference units via RESTRICT FKs, so deleting a unit
        # that's in any army or inventory would raise a raw IntegrityError (500).
        # Guard it into a clean ConflictError (409) instead.
        if self._unit_is_referenced(unit_id):
            raise ConflictError(
                f"unit {unit_id} is in use by an army or inventory"
            )
        self.session.delete(unit)
        self.session.commit()

    def _unit_is_referenced(self, unit_id: UUID) -> bool:
        for model in (ArmyUnit, UserUnit):
            hit = self.session.exec(
                select(model).where(model.unit_id == unit_id).limit(1)
            ).first()
            if hit is not None:
                return True
        return False

    def create_weapon(
        self,
        name: str,
        category: str,
        attacks: str,
        weapon_skill: int,
        strength: int,
        armor_piercing: int,
        damage: str,
        range_inches: Optional[int] = None,
        keywords: Optional[list[str]] = None,
    ) -> Weapon:
        if category not in ("range", "melee"):
            raise UnitValidationError("category", "must be 'range' or 'melee'")
        weapon = Weapon(
            name=name,
            category=category,
            attacks=attacks,
            weapon_skill=weapon_skill,
            strength=strength,
            armor_piercing=armor_piercing,
            damage=damage,
            range_inches=range_inches,
            keywords=keywords or [],
        )
        self.session.add(weapon)
        self.session.commit()
        self.session.refresh(weapon)
        return weapon

    def list_weapons(self) -> list[Weapon]:
        return list(self.session.exec(select(Weapon)).all())

    def update_weapon(self, weapon_id: UUID, **fields) -> Weapon:
        weapon = self.session.get(Weapon, weapon_id)
        if weapon is None:
            raise NotFoundError(f"weapon {weapon_id} not found")
        unknown = set(fields) - self._WEAPON_UPDATABLE
        if unknown:
            raise UnitValidationError("fields", f"cannot update {sorted(unknown)}")
        if "category" in fields and fields["category"] not in ("range", "melee"):
            raise UnitValidationError("category", "must be 'range' or 'melee'")
        for key, value in fields.items():
            setattr(weapon, key, value)
        self.session.add(weapon)
        self.session.commit()
        self.session.refresh(weapon)
        return weapon

    def delete_weapon(self, weapon_id: UUID) -> None:
        weapon = self.session.get(Weapon, weapon_id)
        if weapon is None:
            raise NotFoundError(f"weapon {weapon_id} not found")
        # unit_weapons links cascade, so no reference guard is needed.
        self.session.delete(weapon)
        self.session.commit()

    def create_ability(self, name: str, description: str) -> Ability:
        ability = Ability(name=name, description=description)
        self.session.add(ability)
        self.session.commit()
        self.session.refresh(ability)
        return ability

    def list_abilities(self) -> list[Ability]:
        return list(self.session.exec(select(Ability)).all())

    def update_ability(self, ability_id: UUID, **fields) -> Ability:
        ability = self.session.get(Ability, ability_id)
        if ability is None:
            raise NotFoundError(f"ability {ability_id} not found")
        unknown = set(fields) - self._ABILITY_UPDATABLE
        if unknown:
            raise UnitValidationError("fields", f"cannot update {sorted(unknown)}")
        for key, value in fields.items():
            setattr(ability, key, value)
        self.session.add(ability)
        self.session.commit()
        self.session.refresh(ability)
        return ability

    def delete_ability(self, ability_id: UUID) -> None:
        ability = self.session.get(Ability, ability_id)
        if ability is None:
            raise NotFoundError(f"ability {ability_id} not found")
        # unit_abilities links cascade, so no reference guard is needed.
        self.session.delete(ability)
        self.session.commit()

    def link_weapon(self, unit_id: UUID, weapon_id: UUID) -> Unit:
        unit = self.get_unit(unit_id)
        weapon = self.session.get(Weapon, weapon_id)
        if weapon is None:
            raise NotFoundError(f"weapon {weapon_id} not found")
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
            raise NotFoundError(f"ability {ability_id} not found")
        if ability not in unit.abilities:
            unit.abilities.append(ability)
            self.session.add(unit)
            self.session.commit()
            self.session.refresh(unit)
        return unit

    def unlink_weapon(self, unit_id: UUID, weapon_id: UUID) -> Unit:
        """Detach a weapon from a unit; idempotent (no error if not linked)."""
        unit = self.get_unit(unit_id)
        weapon = self.session.get(Weapon, weapon_id)
        if weapon is not None and weapon in unit.weapons:
            unit.weapons.remove(weapon)
            self.session.add(unit)
            self.session.commit()
            self.session.refresh(unit)
        return unit

    def unlink_ability(self, unit_id: UUID, ability_id: UUID) -> Unit:
        """Detach an ability from a unit; idempotent (no error if not linked)."""
        unit = self.get_unit(unit_id)
        ability = self.session.get(Ability, ability_id)
        if ability is not None and ability in unit.abilities:
            unit.abilities.remove(ability)
            self.session.add(unit)
            self.session.commit()
            self.session.refresh(unit)
        return unit

    # ---- factions & subfactions (catalog reference data) ----

    def list_factions(self) -> list[Faction]:
        return list(self.session.exec(select(Faction)).all())

    def create_faction(self, name: str) -> Faction:
        # Guard the direct-session path (seed scripts, etc.) the same way the
        # API schema guards HTTP: the name must be a canonical faction.
        try:
            FactionName(name)
        except ValueError:
            allowed = ", ".join(f.value for f in FactionName)
            raise UnitValidationError(
                "name", f"{name!r} is not a recognized faction (allowed: {allowed})"
            ) from None
        if self.session.exec(select(Faction).where(Faction.name == name)).first():
            raise ConflictError(f"faction {name!r} already exists")
        faction = Faction(name=name)
        self.session.add(faction)
        self.session.commit()
        self.session.refresh(faction)
        return faction

    def create_subfaction(self, faction_id: UUID, name: str) -> Subfaction:
        faction = self.session.get(Faction, faction_id)
        if faction is None:
            raise NotFoundError(f"faction {faction_id} not found")
        # The subfaction must be one of the armies allowed under this faction.
        allowed = FACTION_SUBFACTIONS.get(FactionName(faction.name), ())
        if name not in allowed:
            raise UnitValidationError(
                "name",
                f"{name!r} is not a subfaction of {faction.name} "
                f"(allowed: {', '.join(allowed) or 'none'})",
            )
        clash = self.session.exec(
            select(Subfaction).where(
                Subfaction.faction_id == faction_id, Subfaction.name == name
            )
        ).first()
        if clash is not None:
            raise ConflictError(
                f"subfaction {name!r} already exists for that faction"
            )
        sub = Subfaction(faction_id=faction_id, name=name)
        self.session.add(sub)
        self.session.commit()
        self.session.refresh(sub)
        return sub

    def delete_subfaction(self, subfaction_id: UUID) -> None:
        sub = self.session.get(Subfaction, subfaction_id)
        if sub is None:
            raise NotFoundError(f"subfaction {subfaction_id} not found")
        # units.subfaction_id / armies.subfaction_id reference it via RESTRICT FKs,
        # so guard the delete into a ConflictError (409) rather than a 500.
        if self._subfaction_is_referenced(subfaction_id):
            raise ConflictError(
                f"subfaction {subfaction_id} is in use by a unit or army"
            )
        self.session.delete(sub)
        self.session.commit()

    def _subfaction_is_referenced(self, subfaction_id: UUID) -> bool:
        for model in (Unit, Army):
            hit = self.session.exec(
                select(model).where(model.subfaction_id == subfaction_id).limit(1)
            ).first()
            if hit is not None:
                return True
        return False
