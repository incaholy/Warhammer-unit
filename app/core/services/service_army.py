"""ArmyService — a user's armies and the units inside them.

Session-injected. `NotFoundError` for not-found, `ArmyValidationError` for bad
input, per SPEC.md conventions.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.db.models import Army, ArmyUnit, Faction, Subfaction, Unit, User, UserUnit
from app.core.services.errors import ArmyValidationError, NotFoundError


@dataclass
class Shortfall:
    """One under-owned unit in an army: how many the list wants vs. how many the
    owner has, and the number still to buy."""

    unit: Unit
    in_list: int
    owned: int
    need: int


@dataclass
class ValidationIssue:
    """One legality problem with an army list."""

    kind: str  # "over_points" | "wrong_faction" | "wrong_subfaction"
    detail: str
    unit: Unit | None = None  # the offending unit, when applicable


@dataclass
class ValidationReport:
    ok: bool
    points_total: int
    points_limit: int | None
    issues: list[ValidationIssue]


class ArmyService:
    # Fields a PATCH may set on an army.
    _UPDATABLE = {"name", "description", "faction_id", "subfaction_id", "points_limit"}
    # Of those, the ones backed by NOT NULL columns — an explicit null must be
    # rejected, not written. The rest may be cleared with an explicit null.
    _NOT_NULLABLE = {"name", "faction_id"}

    def __init__(self, session: Session):
        self.session = session

    # ------------------------------ armies ------------------------------

    def create_army(
        self,
        user_id: UUID,
        name: str,
        faction_id: UUID,
        subfaction_id: UUID | None = None,
        description: str | None = None,
        points_limit: int | None = None,
    ) -> Army:
        if self.session.get(User, user_id) is None:
            raise NotFoundError(f"user {user_id} not found")
        if self.session.get(Faction, faction_id) is None:
            raise NotFoundError(f"faction {faction_id} not found")
        if subfaction_id is not None and self.session.get(Subfaction, subfaction_id) is None:
            raise NotFoundError(f"subfaction {subfaction_id} not found")

        army = Army(
            owner_user_id=user_id,
            name=name,
            faction_id=faction_id,
            subfaction_id=subfaction_id,
            description=description,
            points_limit=points_limit,
        )
        self.session.add(army)
        self.session.commit()
        self.session.refresh(army)
        return army

    def get_army(self, army_id: UUID) -> Army:
        army = self.session.get(Army, army_id)
        if army is None:
            raise NotFoundError(f"army {army_id} not found")
        return army

    def list_armies(self, user_id: UUID) -> list[Army]:
        # Eager-load the units subtree (units -> unit -> weapons/abilities) so
        # serializing each Army_Read doesn't lazy-load it per army (the N+1).
        statement = (
            select(Army)
            .where(Army.owner_user_id == user_id)
            .options(
                selectinload(Army.units).selectinload(ArmyUnit.unit).selectinload(Unit.weapons),
                selectinload(Army.units).selectinload(ArmyUnit.unit).selectinload(Unit.abilities),
            )
        )
        return list(self.session.exec(statement).all())

    def delete_army(self, army_id: UUID) -> None:
        army = self.get_army(army_id)
        # Remove the army's entries first so the ORM delete doesn't try to null
        # their (NOT NULL) army_id. The DB FK also cascades; this is explicit.
        self.session.execute(sa_delete(ArmyUnit).where(ArmyUnit.army_id == army_id))
        self.session.delete(army)
        self.session.commit()

    def update_army(self, army_id: UUID, **fields) -> Army:
        army = self.get_army(army_id)
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise ArmyValidationError("fields", f"cannot update {sorted(unknown)}")
        # A PATCH that explicitly sends null for a NOT NULL column survives
        # `exclude_unset`, slips past the existence guards below (they only act on
        # non-null values), and would hit a DB IntegrityError. Reject it as a
        # clean 400 up front.
        for field in sorted(fields):
            if field in self._NOT_NULLABLE and fields[field] is None:
                raise ArmyValidationError(field, "cannot be null")
        if fields.get("faction_id") is not None and (self.session.get(Faction, fields["faction_id"]) is None):
            raise NotFoundError(f"faction {fields['faction_id']} not found")
        if fields.get("subfaction_id") is not None and (
            self.session.get(Subfaction, fields["subfaction_id"]) is None
        ):
            raise NotFoundError(f"subfaction {fields['subfaction_id']} not found")

        for key, value in fields.items():
            setattr(army, key, value)
        self.session.add(army)
        self.session.commit()
        self.session.refresh(army)
        return army

    # -------------------------- units in an army --------------------------

    def add_unit(self, army_id: UUID, unit_id: UUID, amount: int = 1) -> tuple[ArmyUnit, bool]:
        """Upsert; returns `(entry, created)` so the API can pick 201 vs 200
        without re-querying the army's units."""
        if amount < 1:
            raise ArmyValidationError("amount", "must be >= 1")
        self._require_army(army_id)
        self._require_unit(unit_id)
        entry = self._find_entry(army_id, unit_id)
        created = entry is None
        if created:
            entry = ArmyUnit(army_id=army_id, unit_id=unit_id, amount=amount)
            self.session.add(entry)
        else:
            entry.amount += amount  # upsert: increment
        self.session.commit()
        self.session.refresh(entry)
        return entry, created

    def set_amount(self, army_id: UUID, unit_id: UUID, amount: int) -> ArmyUnit:
        if amount < 1:
            raise ArmyValidationError("amount", "must be >= 1 (use remove_unit to remove)")
        entry = self._find_entry(army_id, unit_id)
        if entry is None:
            raise NotFoundError(f"unit {unit_id} is not in army {army_id}")
        entry.amount = amount
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def remove_unit(self, army_id: UUID, unit_id: UUID) -> None:
        entry = self._find_entry(army_id, unit_id)
        if entry is None:
            raise NotFoundError(f"unit {unit_id} is not in army {army_id}")
        self.session.delete(entry)
        self.session.commit()

    def list_army_units(self, army_id: UUID) -> list[ArmyUnit]:
        return list(self.session.exec(select(ArmyUnit).where(ArmyUnit.army_id == army_id)).all())

    def shortfall(self, army_id: UUID) -> list[Shortfall]:
        army = self.get_army(army_id)
        owned = {
            uu.unit_id: uu.amount
            for uu in self.session.exec(
                select(UserUnit).where(UserUnit.owner_user_id == army.owner_user_id)
            ).all()
        }
        rows: list[Shortfall] = []
        for entry in self.session.exec(select(ArmyUnit).where(ArmyUnit.army_id == army_id)).all():
            have = owned.get(entry.unit_id, 0)
            need = max(0, entry.amount - have)
            if need > 0:
                rows.append(
                    Shortfall(
                        unit=self._unit_or_404(entry.unit_id),
                        in_list=entry.amount,
                        owned=have,
                        need=need,
                    )
                )
        return rows

    # ------------------------------ roster ------------------------------

    def points_total(self, army_id: UUID) -> int:
        self.get_army(army_id)  # LookupError if missing
        # One SUM aggregate instead of a per-entry session.get loop: the DB joins
        # army_units to units and sums amount * points in a single query, sending
        # back just the total (NULL -> 0 for an empty army).
        total = self.session.exec(
            select(func.sum(ArmyUnit.amount * Unit.points))
            .join(Unit, ArmyUnit.unit_id == Unit.id)
            .where(ArmyUnit.army_id == army_id)
        ).one()
        return total or 0

    def validate(self, army_id: UUID) -> ValidationReport:
        army = self.get_army(army_id)
        issues: list[ValidationIssue] = []
        total = 0
        for entry in self.session.exec(select(ArmyUnit).where(ArmyUnit.army_id == army_id)).all():
            unit = self._unit_or_404(entry.unit_id)
            total += entry.amount * unit.points
            if unit.faction_id != army.faction_id:
                issues.append(
                    ValidationIssue(
                        kind="wrong_faction",
                        detail=f"{unit.unit_name} is not in the army's faction",
                        unit=unit,
                    )
                )
            if unit.subfaction_id is not None and unit.subfaction_id != army.subfaction_id:
                issues.append(
                    ValidationIssue(
                        kind="wrong_subfaction",
                        detail=f"{unit.unit_name} is restricted to another subfaction",
                        unit=unit,
                    )
                )
        if army.points_limit is not None and total > army.points_limit:
            issues.append(
                ValidationIssue(
                    kind="over_points",
                    detail=f"list is {total} pts, over the {army.points_limit} pt limit",
                )
            )
        return ValidationReport(
            ok=not issues,
            points_total=total,
            points_limit=army.points_limit,
            issues=issues,
        )

    # ------------------------------ helpers ------------------------------

    def _require_army(self, army_id: UUID) -> None:
        if self.session.get(Army, army_id) is None:
            raise NotFoundError(f"army {army_id} not found")

    def _require_unit(self, unit_id: UUID) -> None:
        if self.session.get(Unit, unit_id) is None:
            raise NotFoundError(f"unit {unit_id} not found")

    def _unit_or_404(self, unit_id: UUID) -> Unit:
        """Load a unit referenced by an ArmyUnit; a `NotFoundError` (not an
        AttributeError/500) if the catalog row is somehow missing."""
        unit = self.session.get(Unit, unit_id)
        if unit is None:
            raise NotFoundError(f"unit {unit_id} not found")
        return unit

    def _find_entry(self, army_id: UUID, unit_id: UUID) -> ArmyUnit | None:
        return self.session.exec(
            select(ArmyUnit).where(ArmyUnit.army_id == army_id, ArmyUnit.unit_id == unit_id)
        ).first()
