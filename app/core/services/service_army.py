"""ArmyService — a user's armies and the units inside them.

Session-injected. `LookupError` for not-found, `ValueError` for bad amounts,
per SPEC.md conventions.
"""

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlmodel import Session, select

from app.core.db.models import Army, ArmyUnit, Faction, Subfaction, Unit, User, UserUnit


@dataclass
class Shortfall:
    """One under-owned unit in an army: how many the list wants vs. how many the
    owner has, and the number still to buy."""

    unit: Unit
    in_list: int
    owned: int
    need: int


class ArmyService:
    def __init__(self, session: Session):
        self.session = session

    # ------------------------------ armies ------------------------------

    def create_army(
        self,
        user_id: UUID,
        name: str,
        faction_id: UUID,
        subfaction_id: Optional[UUID] = None,
        description: Optional[str] = None,
    ) -> Army:
        if self.session.get(User, user_id) is None:
            raise LookupError(f"user {user_id} not found")
        if self.session.get(Faction, faction_id) is None:
            raise LookupError(f"faction {faction_id} not found")
        if subfaction_id is not None and self.session.get(Subfaction, subfaction_id) is None:
            raise LookupError(f"subfaction {subfaction_id} not found")

        army = Army(
            owner_user_id=user_id,
            name=name,
            faction_id=faction_id,
            subfaction_id=subfaction_id,
            description=description,
        )
        self.session.add(army)
        self.session.commit()
        self.session.refresh(army)
        return army

    def get_army(self, army_id: UUID) -> Army:
        army = self.session.get(Army, army_id)
        if army is None:
            raise LookupError(f"army {army_id} not found")
        return army

    def list_armies(self, user_id: UUID) -> list[Army]:
        return list(
            self.session.exec(select(Army).where(Army.owner_user_id == user_id)).all()
        )

    def delete_army(self, army_id: UUID) -> None:
        army = self.get_army(army_id)
        # Remove the army's entries first so the ORM delete doesn't try to null
        # their (NOT NULL) army_id. The DB FK also cascades; this is explicit.
        self.session.execute(sa_delete(ArmyUnit).where(ArmyUnit.army_id == army_id))
        self.session.delete(army)
        self.session.commit()

    # -------------------------- units in an army --------------------------

    def add_unit(self, army_id: UUID, unit_id: UUID, amount: int = 1) -> ArmyUnit:
        self._require_army(army_id)
        self._require_unit(unit_id)
        entry = self._find_entry(army_id, unit_id)
        if entry is None:
            entry = ArmyUnit(army_id=army_id, unit_id=unit_id, amount=amount)
            self.session.add(entry)
        else:
            entry.amount += amount  # upsert: increment
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def set_amount(self, army_id: UUID, unit_id: UUID, amount: int) -> ArmyUnit:
        if amount < 1:
            raise ValueError("amount must be >= 1 (use remove_unit to remove)")
        entry = self._find_entry(army_id, unit_id)
        if entry is None:
            raise LookupError(f"unit {unit_id} is not in army {army_id}")
        entry.amount = amount
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def remove_unit(self, army_id: UUID, unit_id: UUID) -> None:
        entry = self._find_entry(army_id, unit_id)
        if entry is None:
            raise LookupError(f"unit {unit_id} is not in army {army_id}")
        self.session.delete(entry)
        self.session.commit()

    def list_army_units(self, army_id: UUID) -> list[ArmyUnit]:
        return list(
            self.session.exec(select(ArmyUnit).where(ArmyUnit.army_id == army_id)).all()
        )

    def shortfall(self, army_id: UUID) -> list[Shortfall]:
        army = self.get_army(army_id)
        owned = {
            uu.unit_id: uu.amount
            for uu in self.session.exec(
                select(UserUnit).where(UserUnit.owner_user_id == army.owner_user_id)
            ).all()
        }
        rows: list[Shortfall] = []
        for entry in self.session.exec(
            select(ArmyUnit).where(ArmyUnit.army_id == army_id)
        ).all():
            have = owned.get(entry.unit_id, 0)
            need = max(0, entry.amount - have)
            if need > 0:
                rows.append(
                    Shortfall(
                        unit=self.session.get(Unit, entry.unit_id),
                        in_list=entry.amount,
                        owned=have,
                        need=need,
                    )
                )
        return rows

    # ------------------------------ helpers ------------------------------

    def _require_army(self, army_id: UUID) -> None:
        if self.session.get(Army, army_id) is None:
            raise LookupError(f"army {army_id} not found")

    def _require_unit(self, unit_id: UUID) -> None:
        if self.session.get(Unit, unit_id) is None:
            raise LookupError(f"unit {unit_id} not found")

    def _find_entry(self, army_id: UUID, unit_id: UUID) -> Optional[ArmyUnit]:
        return self.session.exec(
            select(ArmyUnit).where(
                ArmyUnit.army_id == army_id, ArmyUnit.unit_id == unit_id
            )
        ).first()
