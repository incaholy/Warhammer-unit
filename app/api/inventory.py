"""Inventory router — the current user's owned units (`/me/inventory`).

Identity comes from the JWT (`get_current_user`), not a path param, so a user
can only ever touch their own inventory.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Field, Session, SQLModel

from app.api.unit import Unit_Read
from app.core.db.connection import get_session
from app.core.db.models import User
from app.core.security import get_current_user
from app.core.services.service_inventory import InventoryService

router = APIRouter(prefix="/me/inventory", tags=["inventory"])


class UserUnit_Read(SQLModel):
    unit: Unit_Read
    amount: int


class InventoryAdd(SQLModel):
    unit_id: UUID
    amount: int = Field(default=1, ge=1)


class AmountSet(SQLModel):
    amount: int


def get_inventory_service(
    session: Session = Depends(get_session),
) -> InventoryService:
    return InventoryService(session)


@router.get("", response_model=list[UserUnit_Read])
def list_inventory(
    current_user: User = Depends(get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> list[UserUnit_Read]:
    return service.list_inventory(current_user.id)


@router.post("", response_model=UserUnit_Read, status_code=status.HTTP_201_CREATED)
def add_unit(
    payload: InventoryAdd,
    response: Response,
    current_user: User = Depends(get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> UserUnit_Read:
    # Upsert: 201 when creating the row, 200 when incrementing an existing one.
    entry, created = service.add_unit(
        current_user.id, payload.unit_id, payload.amount
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return entry


@router.patch("/{unit_id}", response_model=UserUnit_Read)
def set_amount(
    unit_id: UUID,
    payload: AmountSet,
    current_user: User = Depends(get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> UserUnit_Read:
    return service.set_amount(current_user.id, unit_id, payload.amount)


@router.delete("/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_unit(
    unit_id: UUID,
    current_user: User = Depends(get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> Response:
    service.remove_unit(current_user.id, unit_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
