"""Factions router (catalog) — backed by UnitService.

Uses full paths (no shared prefix) because `/factions` and `/subfactions` are
sibling resources. Catalog writes are admin/seed in principle.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session, SQLModel

from app.api.unit import Ability_Read, Weapon_Read
from app.core.db.connection import get_session
from app.core.db.models import FACTION_SUBFACTIONS, FactionName
from app.core.security import get_current_admin
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
    # Restricted to the canonical faction list: an unknown value (e.g. a
    # misspelling) is rejected with 422, and the allowed set is published in the
    # OpenAPI schema (so a frontend can render a dropdown).
    name: FactionName


class Subfaction_Create(SQLModel):
    faction_id: UUID
    name: str


class Weapon_Create(SQLModel):
    name: str
    category: str
    attacks: str
    weapon_skill: int
    strength: int
    armor_piercing: int
    damage: str
    range_inches: Optional[int] = None
    keywords: Optional[list[str]] = None


class Ability_Create(SQLModel):
    name: str
    description: str


def get_catalog_service(session: Session = Depends(get_session)) -> UnitService:
    return UnitService(session)


@router.get("/factions", response_model=list[Faction_Read])
def list_factions(
    service: UnitService = Depends(get_catalog_service),
) -> list[Faction_Read]:
    return service.list_factions()


@router.get("/factions/taxonomy", response_model=dict[str, list[str]])
def faction_taxonomy() -> dict[str, list[str]]:
    """The allowed subfactions under each faction (for building dropdowns).

    Sourced from the `FACTION_SUBFACTIONS` map, not the DB — these are the values
    `create_subfaction` will accept, whether or not any have been created yet.
    """
    return {faction.value: list(subs) for faction, subs in FACTION_SUBFACTIONS.items()}


@router.post(
    "/factions",
    response_model=Faction_Read,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_admin)],
)
def create_faction(
    payload: Faction_Create, service: UnitService = Depends(get_catalog_service)
) -> Faction_Read:
    return service.create_faction(payload.name)


@router.post(
    "/subfactions",
    response_model=Subfaction_Read,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_admin)],
)
def create_subfaction(
    payload: Subfaction_Create,
    service: UnitService = Depends(get_catalog_service),
) -> Subfaction_Read:
    return service.create_subfaction(payload.faction_id, payload.name)


@router.delete(
    "/subfactions/{subfaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_admin)],
)
def delete_subfaction(
    subfaction_id: UUID, service: UnitService = Depends(get_catalog_service)
) -> Response:
    service.delete_subfaction(subfaction_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/weapons",
    response_model=Weapon_Read,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_admin)],
)
def create_weapon(
    payload: Weapon_Create, service: UnitService = Depends(get_catalog_service)
) -> Weapon_Read:
    return service.create_weapon(**payload.model_dump())


@router.get("/weapons", response_model=list[Weapon_Read])
def list_weapons(
    service: UnitService = Depends(get_catalog_service),
) -> list[Weapon_Read]:
    return service.list_weapons()


@router.post(
    "/abilities",
    response_model=Ability_Read,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_admin)],
)
def create_ability(
    payload: Ability_Create, service: UnitService = Depends(get_catalog_service)
) -> Ability_Read:
    return service.create_ability(payload.name, payload.description)


@router.get("/abilities", response_model=list[Ability_Read])
def list_abilities(
    service: UnitService = Depends(get_catalog_service),
) -> list[Ability_Read]:
    return service.list_abilities()
