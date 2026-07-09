"""Units router (catalog) — backed by UnitService.

Catalog writes are admin/seed in principle; gating is deferred until auth
(SPEC.md "Authentication & authorization").
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import Session, SQLModel

from app.core.db.connection import get_session
from app.core.security import get_current_admin
from app.core.services.service_unit import UnitService

router = APIRouter(prefix="/units", tags=["units"])


class Weapon_Read(SQLModel):
    id: UUID
    name: str
    category: str
    keywords: list[str]
    range_inches: Optional[int]
    attacks: str
    weapon_skill: int
    strength: int
    armor_piercing: int
    damage: str


class Ability_Read(SQLModel):
    id: UUID
    name: str
    description: str


class Unit_Read(SQLModel):
    id: UUID
    unit_name: str
    faction_id: UUID
    subfaction_id: Optional[UUID]
    movement: int
    toughness: int
    armor_save: int
    wounds: int
    invulnerable_save: Optional[int]
    leadership: int
    objective_control: int
    points: int
    keywords: list[str]
    weapons: list[Weapon_Read] = []
    abilities: list[Ability_Read] = []


class Unit_Create(SQLModel):
    faction_id: UUID
    unit_name: str
    movement: int
    toughness: int
    armor_save: int
    wounds: int
    leadership: int
    objective_control: int
    points: int
    invulnerable_save: Optional[int] = None
    subfaction_id: Optional[UUID] = None
    keywords: Optional[list[str]] = None


class Unit_Update(SQLModel):
    unit_name: Optional[str] = None
    faction_id: Optional[UUID] = None
    subfaction_id: Optional[UUID] = None
    movement: Optional[int] = None
    toughness: Optional[int] = None
    armor_save: Optional[int] = None
    wounds: Optional[int] = None
    invulnerable_save: Optional[int] = None
    leadership: Optional[int] = None
    objective_control: Optional[int] = None
    points: Optional[int] = None
    keywords: Optional[list[str]] = None


class WeaponLink(SQLModel):
    weapon_id: UUID


class AbilityLink(SQLModel):
    ability_id: UUID


def get_unit_service(session: Session = Depends(get_session)) -> UnitService:
    return UnitService(session)


@router.post(
    "",
    response_model=Unit_Read,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_admin)],
)
def create_unit(
    payload: Unit_Create, service: UnitService = Depends(get_unit_service)
) -> Unit_Read:
    return service.create_unit(**payload.model_dump())


@router.get("", response_model=list[Unit_Read])
def list_units(
    response: Response,
    faction_id: Optional[UUID] = None,
    subfaction_id: Optional[UUID] = None,
    q: Optional[str] = Query(default=None, description="case-insensitive name search"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: UnitService = Depends(get_unit_service),
) -> list[Unit_Read]:
    # Total across the filter (ignoring paging) so the catalog can show "N of M".
    response.headers["X-Total-Count"] = str(
        service.count_units(faction_id=faction_id, subfaction_id=subfaction_id, q=q)
    )
    return service.list_units(
        faction_id=faction_id,
        subfaction_id=subfaction_id,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/{unit_id}", response_model=Unit_Read)
def get_unit(
    unit_id: UUID, service: UnitService = Depends(get_unit_service)
) -> Unit_Read:
    return service.get_unit(unit_id)


@router.patch(
    "/{unit_id}",
    response_model=Unit_Read,
    dependencies=[Depends(get_current_admin)],
)
def update_unit(
    unit_id: UUID,
    payload: Unit_Update,
    service: UnitService = Depends(get_unit_service),
) -> Unit_Read:
    return service.update_unit(unit_id, **payload.model_dump(exclude_unset=True))


@router.delete(
    "/{unit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_admin)],
)
def delete_unit(
    unit_id: UUID, service: UnitService = Depends(get_unit_service)
) -> Response:
    service.delete_unit(unit_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{unit_id}/weapons",
    response_model=Unit_Read,
    dependencies=[Depends(get_current_admin)],
)
def link_weapon(
    unit_id: UUID,
    payload: WeaponLink,
    service: UnitService = Depends(get_unit_service),
) -> Unit_Read:
    return service.link_weapon(unit_id, payload.weapon_id)


@router.post(
    "/{unit_id}/abilities",
    response_model=Unit_Read,
    dependencies=[Depends(get_current_admin)],
)
def link_ability(
    unit_id: UUID,
    payload: AbilityLink,
    service: UnitService = Depends(get_unit_service),
) -> Unit_Read:
    return service.link_ability(unit_id, payload.ability_id)
