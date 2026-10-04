"""Kill team rosters router — the current user's rosters (`/me/kill-team/rosters`).

Identity comes from the JWT (`get_current_user`), never a path param, and the nested
`{roster_id}` routes go through `get_owned_roster`, which returns 404 unless the
roster belongs to the caller — so a stranger's `roster_id` reveals nothing. The same
shape as the armies router, for the same reason.

Eight routes:

    POST   /me/kill-team/rosters                              create
    GET    /me/kill-team/rosters                              paged, lean
    GET    /me/kill-team/rosters/{id}                         the roster whole
    PATCH  /me/kill-team/rosters/{id}                         rename, re-describe
    DELETE /me/kill-team/rosters/{id}
    POST   /me/kill-team/rosters/{id}/operatives              append one
    PATCH  /me/kill-team/rosters/{id}/operatives/{row_id}     move it
    DELETE /me/kill-team/rosters/{id}/operatives/{row_id}

The catalog half of this API has no writes at all (decision #21); this is the half a
player writes, and the only one. Nothing here judges a roster: any operative of its
kill team may be fielded, as often as the player likes (#50, #51), and there is no
`validate` because composition is the page's words rather than a rule to check
against (#28, #44).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Field, Session, SQLModel

from app.api.deps import get_current_user
from app.api.fields import INT32_MAX, Name, WriteSchema
from app.api.killteam import (
    KillTeamRule_Read,
    KTEquipment_Read,
    KTOperative_Read,
    KTPloy_Read,
)
from app.api.pagination import Page, PageParams, paginate
from app.core.db.connection import get_session
from app.core.db.models import User
from app.core.db.models_killteam import KTRoster
from app.core.services.errors import NotFoundError
from app.core.services.service_killteam import KillTeamService
from app.core.services.service_killteam_roster import KTRosterService

router = APIRouter(prefix="/me/kill-team/rosters", tags=["kill team rosters"])


def get_roster_service(session: Session = Depends(get_session)) -> KTRosterService:
    return KTRosterService(session)


def get_catalog_service(session: Session = Depends(get_session)) -> KillTeamService:
    """The catalog reader, for the two collections that belong to no kill team."""
    return KillTeamService(session)


def get_owned_roster(
    roster_id: UUID,
    current_user: User = Depends(get_current_user),
    service: KTRosterService = Depends(get_roster_service),
) -> KTRoster:
    """Load a roster the current user owns WITH its bundle, else 404 (hides existence).

    Only `GET /{roster_id}` uses this, because only it serves the bundle. Every write
    route takes `get_owned_roster_shallow` instead -- see below.
    """
    roster = service.get_roster(roster_id)  # NotFoundError -> 404 if missing
    if roster.owner_user_id != current_user.id:
        raise NotFoundError(f"roster {roster_id} not found")
    return roster


def get_owned_roster_shallow(
    roster_id: UUID,
    current_user: User = Depends(get_current_user),
    service: KTRosterService = Depends(get_roster_service),
) -> KTRoster:
    """The same ownership rule, without loading decision #52's bundle to check it.

    This is audit finding 12. Every write route used `get_owned_roster`, which eager-loads
    the team's faction, rules, ploys and equipment plus every operative's weapons and
    abilities -- and then threw all of it away. A 204 DELETE cost 13 queries on a
    six-operative roster; a `POST` returning ONE row cost 18.

    The 404-not-403 rule is identical and deliberately duplicated rather than
    parameterised: a flag like `bundle=True` would put the two readings of "owned" one
    typo apart, and the one that matters here is a security rule.

    A route that needs the bundle for its RESPONSE asks for it once, at the end.
    `KTGameService.get_game_shallow` and the games router do the same, which is where
    this shape came from.
    """
    roster = service.get_roster_shallow(roster_id)  # NotFoundError -> 404 if missing
    if roster.owner_user_id != current_user.id:
        raise NotFoundError(f"roster {roster_id} not found")
    return roster


# --- schemas ---
# `created_at` IS published here, unlike anywhere in the catalog: a catalog row's
# timestamp is seed bookkeeping that moves on every re-scrape, where a roster's is a
# fact about something the player did, and what the listing sorts by. `Army_Read`
# carries it for the same reason.


class KTRoster_ListRead(SQLModel):
    """A row of the roster listing: enough to choose one, and nothing heavier.

    Carries the kill team's NAME and its faction's, which is a deliberate exception to
    decision #46 ("a read names a parent by id, never a copy of its name"). #46 is
    about the catalog, where a client holds the faction listing anyway; a player's own
    roster list should not require fetching the catalog to render "Raveners, Tyranids".
    Deliberately NOT the operatives: a detail read is 24-42 KB (#52), so a page of
    fifty would be megabytes to answer what the name already answers.
    """

    id: UUID
    name: str
    description: str | None
    created_at: datetime
    kill_team_id: UUID
    kill_team_name: str
    faction_name: str


class KTRosterOperative_Read(SQLModel):
    """One operative on the roster: the row's own id, its place, and the datacard.

    The row id and not the operative's, because two rows may name the same operative
    (decision #50) — it is what `PATCH`/`DELETE` address.
    """

    id: UUID
    position: int
    operative: KTOperative_Read


class KTRoster_Read(SQLModel):
    """A roster whole, as decision #52 specifies it.

    The player's operatives with their datacards, then everything that applies while
    playing them: the team's rules, its ploys, its equipment, and the two collections
    that belong to no team. The equipment lists are the POOL a game chooses from — the
    choice itself is per game (#17), so a roster holds no equipment of its own.
    """

    id: UUID
    name: str
    description: str | None
    created_at: datetime
    kill_team_id: UUID
    kill_team_name: str
    faction_name: str
    operatives: list[KTRosterOperative_Read] = []
    rules: list[KillTeamRule_Read] = []
    ploys: list[KTPloy_Read] = []
    equipment: list[KTEquipment_Read] = []
    universal_ploys: list[KTPloy_Read] = []
    universal_equipment: list[KTEquipment_Read] = []


class KTRoster_Create(WriteSchema):
    kill_team_id: UUID
    name: Name
    description: str | None = None


class KTRoster_Update(WriteSchema):
    # `kill_team_id` is absent on purpose: changing it would orphan every operative on
    # the roster, and the composite foreign keys would refuse the write halfway through.
    # A caller that sends it anyway gets a 422 naming the field, from `WriteSchema`
    # forbidding extras -- NOT the 400 an earlier version of this comment claimed. The
    # service never sees the key, so it could not have refused it: before extras were
    # forbidden the request answered 200 with the roster unchanged.
    name: Name | None = None
    description: str | None = None


class KTRosterOperative_Create(WriteSchema):
    operative_id: UUID


class KTRosterOperative_Update(WriteSchema):
    # Absolute, not a delta, so a retried move is harmless (the reasoning of decision #7).
    # Bounded at the top only: the service raises a 400 for a negative position and that
    # status is tested, where anything past `INT32_MAX` had no refusal at all.
    position: int = Field(le=INT32_MAX)


def _listed(roster: KTRoster) -> KTRoster_ListRead:
    return KTRoster_ListRead(
        id=roster.id,
        name=roster.name,
        description=roster.description,
        created_at=roster.created_at,
        kill_team_id=roster.kill_team_id,
        kill_team_name=roster.kill_team.name,
        faction_name=roster.kill_team.faction.name,
    )


def _detail(roster: KTRoster, catalog: KillTeamService) -> KTRoster_Read:
    """A roster plus the reference that applies to it (decision #52)."""
    return KTRoster_Read(
        id=roster.id,
        name=roster.name,
        description=roster.description,
        created_at=roster.created_at,
        kill_team_id=roster.kill_team_id,
        kill_team_name=roster.kill_team.name,
        faction_name=roster.kill_team.faction.name,
        operatives=[
            KTRosterOperative_Read.model_validate(row, from_attributes=True) for row in roster.operatives
        ],
        rules=roster.kill_team.rules,
        ploys=roster.kill_team.ploys,
        equipment=roster.kill_team.equipment,
        universal_ploys=catalog.list_universal_ploys(),
        universal_equipment=catalog.list_universal_equipment(),
    )


# --- rosters ---


@router.post("", response_model=KTRoster_Read, status_code=status.HTTP_201_CREATED)
def create_roster(
    payload: KTRoster_Create,
    current_user: User = Depends(get_current_user),
    service: KTRosterService = Depends(get_roster_service),
    catalog: KillTeamService = Depends(get_catalog_service),
) -> KTRoster_Read:
    """A new, empty roster. 409 if the player already has one by that name."""
    roster = service.create_roster(current_user.id, payload.kill_team_id, payload.name, payload.description)
    return _detail(service.get_roster(roster.id), catalog)


@router.get("", response_model=Page[KTRoster_ListRead])
def list_rosters(
    page: PageParams = Depends(),
    current_user: User = Depends(get_current_user),
    service: KTRosterService = Depends(get_roster_service),
) -> Page[KTRoster_ListRead]:
    """The caller's rosters, oldest first. Only ever the caller's."""
    rosters = service.list_rosters(current_user.id, limit=page.limit, offset=page.offset)
    return paginate([_listed(roster) for roster in rosters], service.count_rosters(current_user.id), page)


@router.get("/{roster_id}", response_model=KTRoster_Read)
def get_roster(
    roster: KTRoster = Depends(get_owned_roster),
    catalog: KillTeamService = Depends(get_catalog_service),
) -> KTRoster_Read:
    return _detail(roster, catalog)


@router.patch("/{roster_id}", response_model=KTRoster_Read)
def update_roster(
    payload: KTRoster_Update,
    roster: KTRoster = Depends(get_owned_roster_shallow),
    service: KTRosterService = Depends(get_roster_service),
    catalog: KillTeamService = Depends(get_catalog_service),
) -> KTRoster_Read:
    service.update_roster(roster.id, **payload.model_dump(exclude_unset=True))
    return _detail(service.get_roster(roster.id), catalog)


@router.delete("/{roster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_roster(
    roster: KTRoster = Depends(get_owned_roster_shallow),
    service: KTRosterService = Depends(get_roster_service),
) -> Response:
    service.delete_roster(roster.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- operatives on a roster ---


@router.post(
    "/{roster_id}/operatives",
    response_model=KTRosterOperative_Read,
    status_code=status.HTTP_201_CREATED,
)
def add_operative(
    payload: KTRosterOperative_Create,
    roster: KTRoster = Depends(get_owned_roster_shallow),
    current_user: User = Depends(get_current_user),
    service: KTRosterService = Depends(get_roster_service),
) -> KTRosterOperative_Read:
    """Append one operative. A repeat is a second operative, not a 409.

    The armies router's equivalent is create-only, because an incrementing add is not
    retry-safe. There is no quantity here (decision #50), so a retried add appends --
    which is a real outcome, and a caller that did not mean it removes the extra row.
    """
    # The caller's id goes to the service rather than the roster's owner, so the row's
    # composite foreign key is a real cross-check against `kt_rosters` instead of a
    # tautology. `get_owned_roster` has already 404ed a roster the caller does not own.
    row = service.add_operative(roster.id, payload.operative_id, current_user.id)
    return KTRosterOperative_Read.model_validate(row, from_attributes=True)


@router.patch(
    "/{roster_id}/operatives/{row_id}",
    response_model=KTRosterOperative_Read,
)
def move_operative(
    row_id: UUID,
    payload: KTRosterOperative_Update,
    roster: KTRoster = Depends(get_owned_roster_shallow),
    service: KTRosterService = Depends(get_roster_service),
) -> KTRosterOperative_Read:
    """Set a row's position. Addressed by the ROW's id, not the operative's."""
    row = service.move_operative(roster.id, row_id, payload.position)
    return KTRosterOperative_Read.model_validate(row, from_attributes=True)


@router.delete(
    "/{roster_id}/operatives/{row_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_operative(
    row_id: UUID,
    roster: KTRoster = Depends(get_owned_roster_shallow),
    service: KTRosterService = Depends(get_roster_service),
) -> Response:
    service.remove_operative(roster.id, row_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
