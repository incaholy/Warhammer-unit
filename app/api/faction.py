"""Factions router (catalog) — backed by UnitService.

Uses full paths (no shared prefix) because `/factions` and `/subfactions` are
sibling resources. Catalog writes are admin/seed in principle.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel import Session, SQLModel

from app.core.db.connection import get_session
from app.core.services.service_unit import UnitService

router = APIRouter(tags=["catalog"])


class Subfaction_Read(SQLModel):
    id: UUID
    name: str


class Faction_Read(SQLModel):
    id: UUID
    name: str
    subfactions: list[Subfaction_Read] = []


class Faction_Create(SQLModel):
    name: str


class Subfaction_Create(SQLModel):
    faction_id: UUID
    name: str


def get_catalog_service(session: Session = Depends(get_session)) -> UnitService:
    return UnitService(session)


@router.get("/factions", response_model=list[Faction_Read])
def list_factions(
    service: UnitService = Depends(get_catalog_service),
) -> list[Faction_Read]:
    return service.list_factions()


@router.post(
    "/factions", response_model=Faction_Read, status_code=status.HTTP_201_CREATED
)
def create_faction(
    payload: Faction_Create, service: UnitService = Depends(get_catalog_service)
) -> Faction_Read:
    return service.create_faction(payload.name)


@router.post(
    "/subfactions",
    response_model=Subfaction_Read,
    status_code=status.HTTP_201_CREATED,
)
def create_subfaction(
    payload: Subfaction_Create,
    service: UnitService = Depends(get_catalog_service),
) -> Subfaction_Read:
    return service.create_subfaction(payload.faction_id, payload.name)
