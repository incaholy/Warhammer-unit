"""Inventory router — backed by InventoryService.

Nested under a user: `/users/{user_id}/inventory`. (Once auth lands, `user_id`
becomes the current-user dependency; see SPEC.md.)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session, SQLModel

from app.api.unit import Unit_Read
from app.core.db.connection import get_session
from app.core.services.service_inventory import InventoryService

router = APIRouter(prefix="/users/{user_id}/inventory", tags=["inventory"])


class UserUnit_Read(SQLModel):
    unit: Unit_Read
    amount: int


class InventoryAdd(SQLModel):
    unit_id: UUID
    amount: int = 1


class AmountSet(SQLModel):
    amount: int


def get_inventory_service(
    session: Session = Depends(get_session),
) -> InventoryService:
    return InventoryService(session)


@router.get("", response_model=list[UserUnit_Read])
def list_inventory(
    user_id: UUID, service: InventoryService = Depends(get_inventory_service)
) -> list[UserUnit_Read]:
    return service.list_inventory(user_id)


@router.post("", response_model=UserUnit_Read, status_code=status.HTTP_201_CREATED)
def add_unit(
    user_id: UUID,
    payload: InventoryAdd,
    service: InventoryService = Depends(get_inventory_service),
) -> UserUnit_Read:
    # Upsert (create or increment). Returns 201; the create/increment 200
    # distinction from the SPEC is simplified to always-201 for now.
    return service.add_unit(user_id, payload.unit_id, payload.amount)


@router.patch("/{unit_id}", response_model=UserUnit_Read)
def set_amount(
    user_id: UUID,
    unit_id: UUID,
    payload: AmountSet,
    service: InventoryService = Depends(get_inventory_service),
) -> UserUnit_Read:
    return service.set_amount(user_id, unit_id, payload.amount)


@router.delete("/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_unit(
    user_id: UUID,
    unit_id: UUID,
    service: InventoryService = Depends(get_inventory_service),
) -> Response:
    service.remove_unit(user_id, unit_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
