"""Armies router — the current user's armies (`/me/armies`).

Identity comes from the JWT (`get_current_user`), not a path param. The nested
`{army_id}` routes go through `get_owned_army`, which returns 404 unless the army
belongs to the current user — so a stranger's `army_id` reveals nothing.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Field, Session, SQLModel

from app.api.unit import Unit_Read
from app.core.db.connection import get_session
from app.core.db.models import Army, User
from app.core.security import get_current_user
from app.core.services.service_army import ArmyService

router = APIRouter(prefix="/me/armies", tags=["armies"])


class ArmyUnit_Read(SQLModel):
    unit: Unit_Read
    amount: int


class Army_Read(SQLModel):
    id: UUID
    name: str
    faction_id: UUID
    subfaction_id: Optional[UUID]
    description: Optional[str]
    points_limit: Optional[int]
    points_total: int = 0  # computed; set by the route
    units: list[ArmyUnit_Read] = []


class Army_Create(SQLModel):
    name: str
    faction_id: UUID
    subfaction_id: Optional[UUID] = None
    description: Optional[str] = None
    points_limit: Optional[int] = None


class Army_Update(SQLModel):
    name: Optional[str] = None
    faction_id: Optional[UUID] = None
    subfaction_id: Optional[UUID] = None
    description: Optional[str] = None
    points_limit: Optional[int] = None


class ArmyUnitAdd(SQLModel):
    unit_id: UUID
    amount: int = Field(default=1, ge=1)


class AmountSet(SQLModel):
    amount: int


class Shortfall_Read(SQLModel):
    unit: Unit_Read
    in_list: int
    owned: int
    need: int


class ValidationIssue_Read(SQLModel):
    kind: str
    detail: str
    unit: Optional[Unit_Read] = None


class Validation_Read(SQLModel):
    ok: bool
    points_total: int
    points_limit: Optional[int]
    issues: list[ValidationIssue_Read]


def get_army_service(session: Session = Depends(get_session)) -> ArmyService:
    return ArmyService(session)


def get_owned_army(
    army_id: UUID,
    current_user: User = Depends(get_current_user),
    service: ArmyService = Depends(get_army_service),
) -> Army:
    """Load an army the current user owns, else 404 (hides existence)."""
    army = service.get_army(army_id)  # LookupError -> 404 if missing
    if army.owner_user_id != current_user.id:
        raise LookupError(f"army {army_id} not found")
    return army


def _army_read(service: ArmyService, army: Army) -> Army_Read:
    """Serialize an Army plus its computed points_total (kept out of the ORM)."""
    data = Army_Read.model_validate(army, from_attributes=True)
    data.points_total = service.points_total(army.id)
    return data


# ------------------------------ armies ------------------------------

@router.post("", response_model=Army_Read, status_code=status.HTTP_201_CREATED)
def create_army(
    payload: Army_Create,
    current_user: User = Depends(get_current_user),
    service: ArmyService = Depends(get_army_service),
) -> Army_Read:
    army = service.create_army(
        user_id=current_user.id,
        name=payload.name,
        faction_id=payload.faction_id,
        subfaction_id=payload.subfaction_id,
        description=payload.description,
        points_limit=payload.points_limit,
    )
    return _army_read(service, army)


@router.get("", response_model=list[Army_Read])
def list_armies(
    current_user: User = Depends(get_current_user),
    service: ArmyService = Depends(get_army_service),
) -> list[Army_Read]:
    return [_army_read(service, army) for army in service.list_armies(current_user.id)]


@router.get("/{army_id}", response_model=Army_Read)
def get_army(
    army: Army = Depends(get_owned_army),
    service: ArmyService = Depends(get_army_service),
) -> Army_Read:
    return _army_read(service, army)


@router.patch("/{army_id}", response_model=Army_Read)
def update_army(
    payload: Army_Update,
    army: Army = Depends(get_owned_army),
    service: ArmyService = Depends(get_army_service),
) -> Army_Read:
    updated = service.update_army(army.id, **payload.model_dump(exclude_unset=True))
    return _army_read(service, updated)


@router.delete("/{army_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_army(
    army: Army = Depends(get_owned_army),
    service: ArmyService = Depends(get_army_service),
) -> Response:
    service.delete_army(army.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{army_id}/shortfall", response_model=list[Shortfall_Read])
def shortfall(
    army: Army = Depends(get_owned_army),
    service: ArmyService = Depends(get_army_service),
) -> list[Shortfall_Read]:
    return service.shortfall(army.id)


@router.get("/{army_id}/validate", response_model=Validation_Read)
def validate(
    army: Army = Depends(get_owned_army),
    service: ArmyService = Depends(get_army_service),
) -> Validation_Read:
    return service.validate(army.id)


# -------------------------- units in an army --------------------------

@router.post(
    "/{army_id}/units", response_model=ArmyUnit_Read, status_code=status.HTTP_201_CREATED
)
def add_unit(
    payload: ArmyUnitAdd,
    response: Response,
    army: Army = Depends(get_owned_army),
    service: ArmyService = Depends(get_army_service),
) -> ArmyUnit_Read:
    # Upsert: 201 when creating the row, 200 when incrementing an existing one.
    existed = any(
        u.unit_id == payload.unit_id for u in service.list_army_units(army.id)
    )
    entry = service.add_unit(army.id, payload.unit_id, payload.amount)
    if existed:
        response.status_code = status.HTTP_200_OK
    return entry


@router.patch("/{army_id}/units/{unit_id}", response_model=ArmyUnit_Read)
def set_amount(
    unit_id: UUID,
    payload: AmountSet,
    army: Army = Depends(get_owned_army),
    service: ArmyService = Depends(get_army_service),
) -> ArmyUnit_Read:
    return service.set_amount(army.id, unit_id, payload.amount)


@router.delete(
    "/{army_id}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_unit(
    unit_id: UUID,
    army: Army = Depends(get_owned_army),
    service: ArmyService = Depends(get_army_service),
) -> Response:
    service.remove_unit(army.id, unit_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
