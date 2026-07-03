"""Armies router — backed by ArmyService.

Nested under a user: `/users/{user_id}/armies`, with the army's units nested
one level deeper. (Once auth lands, `user_id` becomes the current-user
dependency; see SPEC.md.)
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session, SQLModel

from app.api.unit import Unit_Read
from app.core.db.connection import get_session
from app.core.services.service_army import ArmyService

router = APIRouter(prefix="/users/{user_id}/armies", tags=["armies"])


class ArmyUnit_Read(SQLModel):
    unit: Unit_Read
    amount: int


class Army_Read(SQLModel):
    id: UUID
    name: str
    faction_id: UUID
    subfaction_id: Optional[UUID]
    description: Optional[str]
    units: list[ArmyUnit_Read] = []


class Army_Create(SQLModel):
    name: str
    faction_id: UUID
    subfaction_id: Optional[UUID] = None
    description: Optional[str] = None


class Army_Update(SQLModel):
    name: Optional[str] = None
    faction_id: Optional[UUID] = None
    subfaction_id: Optional[UUID] = None
    description: Optional[str] = None


class ArmyUnitAdd(SQLModel):
    unit_id: UUID
    amount: int = 1


class AmountSet(SQLModel):
    amount: int


class Shortfall_Read(SQLModel):
    unit: Unit_Read
    in_list: int
    owned: int
    need: int


def get_army_service(session: Session = Depends(get_session)) -> ArmyService:
    return ArmyService(session)


# ------------------------------ armies ------------------------------

@router.post("", response_model=Army_Read, status_code=status.HTTP_201_CREATED)
def create_army(
    user_id: UUID,
    payload: Army_Create,
    service: ArmyService = Depends(get_army_service),
) -> Army_Read:
    return service.create_army(
        user_id=user_id,
        name=payload.name,
        faction_id=payload.faction_id,
        subfaction_id=payload.subfaction_id,
        description=payload.description,
    )


@router.get("", response_model=list[Army_Read])
def list_armies(
    user_id: UUID, service: ArmyService = Depends(get_army_service)
) -> list[Army_Read]:
    return service.list_armies(user_id)


@router.get("/{army_id}", response_model=Army_Read)
def get_army(
    user_id: UUID, army_id: UUID, service: ArmyService = Depends(get_army_service)
) -> Army_Read:
    return service.get_army(army_id)


@router.patch("/{army_id}", response_model=Army_Read)
def update_army(
    user_id: UUID,
    army_id: UUID,
    payload: Army_Update,
    service: ArmyService = Depends(get_army_service),
) -> Army_Read:
    return service.update_army(army_id, **payload.model_dump(exclude_unset=True))


@router.delete("/{army_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_army(
    user_id: UUID, army_id: UUID, service: ArmyService = Depends(get_army_service)
) -> Response:
    service.delete_army(army_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{army_id}/shortfall", response_model=list[Shortfall_Read])
def shortfall(
    user_id: UUID, army_id: UUID, service: ArmyService = Depends(get_army_service)
) -> list[Shortfall_Read]:
    return service.shortfall(army_id)


# -------------------------- units in an army --------------------------

@router.post(
    "/{army_id}/units", response_model=ArmyUnit_Read, status_code=status.HTTP_201_CREATED
)
def add_unit(
    user_id: UUID,
    army_id: UUID,
    payload: ArmyUnitAdd,
    response: Response,
    service: ArmyService = Depends(get_army_service),
) -> ArmyUnit_Read:
    # Upsert: 201 when creating the row, 200 when incrementing an existing one.
    existed = any(
        u.unit_id == payload.unit_id for u in service.list_army_units(army_id)
    )
    entry = service.add_unit(army_id, payload.unit_id, payload.amount)
    if existed:
        response.status_code = status.HTTP_200_OK
    return entry


@router.patch("/{army_id}/units/{unit_id}", response_model=ArmyUnit_Read)
def set_amount(
    user_id: UUID,
    army_id: UUID,
    unit_id: UUID,
    payload: AmountSet,
    service: ArmyService = Depends(get_army_service),
) -> ArmyUnit_Read:
    return service.set_amount(army_id, unit_id, payload.amount)


@router.delete(
    "/{army_id}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_unit(
    user_id: UUID,
    army_id: UUID,
    unit_id: UUID,
    service: ArmyService = Depends(get_army_service),
) -> Response:
    service.remove_unit(army_id, unit_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
