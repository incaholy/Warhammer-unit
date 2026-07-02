"""UnitService — the catalog of unit datasheets.

Session-injected. Raises `LookupError` for not-found, per SPEC.md conventions.
"""

from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from app.core.db.models import Faction, Subfaction, Unit


class UnitService:
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
