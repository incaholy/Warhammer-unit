"""InventoryService — the units a user physically owns (the `user_unit` table).

Session-injected. `NotFoundError` for not-found, `InventoryValidationError` for
bad amounts, per SPEC.md conventions.
"""

from uuid import UUID

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.db.models import Unit, User, UserUnit
from app.core.errors import ErrorCode
from app.core.services.errors import NotFoundError


class InventoryValidationError(ValueError):
    """Bad inventory input."""

    code = ErrorCode.VALIDATION

    def __init__(self, field: str, message: str):
        text = f"{field}: {message}"
        super().__init__(text)
        self.message = text
        self.field = field


class InventoryService:
    def __init__(self, session: Session):
        self.session = session

    def add_unit(self, user_id: UUID, unit_id: UUID, amount: int = 1) -> tuple[UserUnit, bool]:
        """Upsert; returns `(entry, created)` so the API can pick 201 vs 200
        without re-querying the inventory."""
        if amount < 1:
            raise InventoryValidationError("amount", "must be >= 1")
        self._require_user(user_id)
        self._require_unit(unit_id)
        entry = self._find_entry(user_id, unit_id)
        created = entry is None
        if created:
            entry = UserUnit(owner_user_id=user_id, unit_id=unit_id, amount=amount)
            self.session.add(entry)
        else:
            entry.amount += amount  # upsert: increment
        self.session.commit()
        self.session.refresh(entry)
        return entry, created

    def set_amount(self, user_id: UUID, unit_id: UUID, amount: int) -> UserUnit:
        if amount < 1:
            raise InventoryValidationError("amount", "must be >= 1 (use remove_unit to remove)")
        entry = self._find_entry(user_id, unit_id)
        if entry is None:
            raise NotFoundError(f"unit {unit_id} is not in {user_id}'s inventory")
        entry.amount = amount
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def remove_unit(self, user_id: UUID, unit_id: UUID) -> None:
        entry = self._find_entry(user_id, unit_id)
        if entry is None:
            raise NotFoundError(f"unit {unit_id} is not in {user_id}'s inventory")
        self.session.delete(entry)
        self.session.commit()

    def list_inventory(self, user_id: UUID, q: str | None = None) -> list[UserUnit]:
        statement = select(UserUnit).where(UserUnit.owner_user_id == user_id)
        if q:
            statement = statement.join(Unit, UserUnit.unit_id == Unit.id).where(
                Unit.unit_name.ilike(f"%{q}%")
            )
        # Eager-load each entry's unit and that unit's weapons/abilities so
        # serialization doesn't lazy-load them per row (the N+1).
        statement = statement.options(
            selectinload(UserUnit.unit).selectinload(Unit.weapons),
            selectinload(UserUnit.unit).selectinload(Unit.abilities),
        )
        return list(self.session.exec(statement).all())

    # ------------------------------ helpers ------------------------------

    def _require_user(self, user_id: UUID) -> None:
        if self.session.get(User, user_id) is None:
            raise NotFoundError(f"user {user_id} not found")

    def _require_unit(self, unit_id: UUID) -> None:
        if self.session.get(Unit, unit_id) is None:
            raise NotFoundError(f"unit {unit_id} not found")

    def _find_entry(self, user_id: UUID, unit_id: UUID) -> UserUnit | None:
        return self.session.exec(
            select(UserUnit).where(UserUnit.owner_user_id == user_id, UserUnit.unit_id == unit_id)
        ).first()
