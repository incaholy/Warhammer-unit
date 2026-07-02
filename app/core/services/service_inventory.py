"""InventoryService — the units a user physically owns (the `user_unit` table).

Session-injected. `LookupError` for not-found, `ValueError` for bad amounts,
per SPEC.md conventions.
"""

from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from app.core.db.models import Unit, User, UserUnit


class InventoryService:
    def __init__(self, session: Session):
        self.session = session

    def add_unit(self, user_id: UUID, unit_id: UUID, amount: int = 1) -> UserUnit:
        self._require_user(user_id)
        self._require_unit(unit_id)
        entry = self._find_entry(user_id, unit_id)
        if entry is None:
            entry = UserUnit(owner_user_id=user_id, unit_id=unit_id, amount=amount)
            self.session.add(entry)
        else:
            entry.amount += amount  # upsert: increment
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def set_amount(self, user_id: UUID, unit_id: UUID, amount: int) -> UserUnit:
        if amount < 1:
            raise ValueError("amount must be >= 1 (use remove_unit to remove)")
        entry = self._find_entry(user_id, unit_id)
        if entry is None:
            raise LookupError(f"unit {unit_id} is not in {user_id}'s inventory")
        entry.amount = amount
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def remove_unit(self, user_id: UUID, unit_id: UUID) -> None:
        entry = self._find_entry(user_id, unit_id)
        if entry is None:
            raise LookupError(f"unit {unit_id} is not in {user_id}'s inventory")
        self.session.delete(entry)
        self.session.commit()

    def list_inventory(self, user_id: UUID) -> list[UserUnit]:
        return list(
            self.session.exec(
                select(UserUnit).where(UserUnit.owner_user_id == user_id)
            ).all()
        )

    # ------------------------------ helpers ------------------------------

    def _require_user(self, user_id: UUID) -> None:
        if self.session.get(User, user_id) is None:
            raise LookupError(f"user {user_id} not found")

    def _require_unit(self, unit_id: UUID) -> None:
        if self.session.get(Unit, unit_id) is None:
            raise LookupError(f"unit {unit_id} not found")

    def _find_entry(self, user_id: UUID, unit_id: UUID) -> Optional[UserUnit]:
        return self.session.exec(
            select(UserUnit).where(
                UserUnit.owner_user_id == user_id, UserUnit.unit_id == unit_id
            )
        ).first()
