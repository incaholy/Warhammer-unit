"""Kill Team catalog router — backed by `KillTeamService`.

**Read-only, with no write route at all** (KILLTEAM.md decision #21), which is the
one way this differs from the 40k catalog's admin write: an admin edit would be
silently undone by the next `make seed-kt`, which rewrites any row whose payload
differs. There are no `*_Create` or `*_Update` schemas here and no
`get_current_admin` dependency, and `test_the_kill_team_catalog_publishes_no_writes`
asserts that over the published OpenAPI document rather than per path (#49).

Four routes under one prefix:

    GET /api/v1/kill-team/factions        paged
    GET /api/v1/kill-team/teams           paged, ?faction_id=
    GET /api/v1/kill-team/teams/{id}      the team whole
    GET /api/v1/kill-team/universal       the rows no team owns

Nothing here decides legality. The composition arrives as the page's own words
(decision #44) and a roster may take any operative of its kill team, so there is
no `validate` and no budget to check -- that is what lets a custom game be built.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session, SQLModel

from app.api.pagination import Page, PageParams, paginate
from app.core.db.connection import get_session
from app.core.services.service_killteam import KillTeamService

router = APIRouter(prefix="/kill-team", tags=["kill team"])


def get_killteam_service(session: Session = Depends(get_session)) -> KillTeamService:
    return KillTeamService(session)


# --- schemas ---
# `created_at` / `updated_at` are deliberately absent from every one of these: they
# are seed bookkeeping, they change whenever a re-scrape rewrites a row, and a
# response that differs for no visible reason defeats caching. So is a nested row's
# parent FK -- a weapon under its operative needs no `operative_id` -- which is the
# same split the 40k catalog makes between `Subfaction_Read` and `Subfaction_ListRead`.


class KTFaction_Read(SQLModel):
    id: UUID
    name: str


class KillTeam_Read(SQLModel):
    # The faction's id and NOT its name (decision #46): one way to name a parent
    # across the API, and a rename cannot leave a stale copy in a cached response.
    # The frontend resolves it from the `/factions` listing, which it needs anyway.
    id: UUID
    name: str
    faction_id: UUID


class KTWeapon_Read(SQLModel):
    id: UUID
    name: str
    category: str  # "range" | "melee"
    range: int
    attacks: int
    hit: int
    normal_damage: int
    crit_damage: int
    weapon_rules: list[str]
    position: int


class KTAbility_Read(SQLModel):
    id: UUID
    name: str
    description: str
    position: int


class KTOperative_Read(SQLModel):
    id: UUID
    name: str
    apl: int
    move: int
    save: int
    wounds: int
    keywords: list[str]
    # `in_battle` means a CONDITIONAL block grants this datacard, so a game adds it
    # rather than a roster taking it (decision #20). It does NOT mean "no list offers
    # this": composition is text now, so whether a roster may take an operative is not
    # a question the catalog answers at all.
    availability: str
    position: int
    weapons: list[KTWeapon_Read] = []
    abilities: list[KTAbility_Read] = []


class KillTeamRule_Read(SQLModel):
    id: UUID
    name: str
    description: str
    # NULL for an always-on faction rule; a section name for one of a team's grouped
    # selectable options, e.g. Blades of Khaine's Aspects (decision #27).
    group: str | None
    position: int


class KTPloy_Read(SQLModel):
    id: UUID
    name: str
    kind: str  # "strategy" | "firefight"
    cp_cost: int
    description: str
    position: int


class KTEquipment_Read(SQLModel):
    id: UUID
    name: str
    description: str
    position: int


class KTSelectionRule_Read(SQLModel):
    """One printed thing from the composition (decisions #44, #45).

    Read in `position` order, indenting by `depth`, and this is the page's
    composition section back. Nothing is derived from it: no budget, no cap, no
    operative resolved -- `text` is what the page said.
    """

    id: UUID
    position: int
    depth: int
    kind: str  # "heading" | "line" | "restriction" | "note"
    text: str


class KillTeam_Detail(SQLModel):
    """A team whole: everything a reader of its page would see (decision #47).

    One request rather than several, because operatives are reachable only nested and
    a game snapshots the team's rules, its ploys and every datacard anyway (#24). Measured
    over the served responses: 17 KB to 47 KB, median 24 KB, each in nine queries flat.
    """

    id: UUID
    name: str
    faction_id: UUID
    rules: list[KillTeamRule_Read] = []
    ploys: list[KTPloy_Read] = []
    equipment: list[KTEquipment_Read] = []
    operatives: list[KTOperative_Read] = []
    selection_rules: list[KTSelectionRule_Read] = []


class Universal_Read(SQLModel):
    """The rows that belong to no kill team: `kill_team_id IS NULL` (decision #48).

    One route for both, because they are one concept and one thing a client wants --
    fetched once, kept for the session, used with every team. Folding them into each
    team detail instead would add 7.4 KB to every response and re-send it on every team
    view, to save a call that happens once per GAME.
    """

    ploys: list[KTPloy_Read] = []
    equipment: list[KTEquipment_Read] = []


# --- routes ---


@router.get("/factions", response_model=Page[KTFaction_Read])
def list_kt_factions(
    page: PageParams = Depends(),
    service: KillTeamService = Depends(get_killteam_service),
) -> Page[KTFaction_Read]:
    """The Kill Team faction list, alphabetically.

    A flat list of its own, one level deep (decision #12): no grand-alliance level
    above it and no link to the 40k faction tables. A faction is how a team is FOUND.
    """
    items = service.list_kt_factions(limit=page.limit, offset=page.offset)
    return paginate(items, service.count_kt_factions(), page)


@router.get("/teams", response_model=Page[KillTeam_Read])
def list_kill_teams(
    faction_id: UUID | None = None,
    page: PageParams = Depends(),
    service: KillTeamService = Depends(get_killteam_service),
) -> Page[KillTeam_Read]:
    """Kill teams, alphabetically, optionally narrowed to one faction.

    Alphabetical because that is the only order there is: decision #25 gives print
    order to every CHILD of a kill team, not to the teams themselves, so the site's
    nav order is not stored.

    A `faction_id` matching nothing is an empty page, not a 404 -- a filter naming
    nothing is a legitimate result, where a missing ROW asked for by id is not.
    """
    items = service.list_kill_teams(faction_id=faction_id, limit=page.limit, offset=page.offset)
    return paginate(items, service.count_kill_teams(faction_id), page)


@router.get("/teams/{kill_team_id}", response_model=KillTeam_Detail)
def get_kill_team(
    kill_team_id: UUID,
    service: KillTeamService = Depends(get_killteam_service),
) -> KillTeam_Detail:
    """One kill team with its rules, ploys, equipment, datacards and composition.

    404 for an unknown id, through the service's `NotFoundError` -- `app/main.py`
    registers one handler against `CodedError`, so nothing is mapped here.
    """
    return service.get_kill_team(kill_team_id)


@router.get("/universal", response_model=Universal_Read)
def read_universal(
    service: KillTeamService = Depends(get_killteam_service),
) -> Universal_Read:
    """Command Re-roll and the universal equipment list: the rows no team owns.

    Unpaginated, deliberately. This is one document rather than a collection -- one
    ploy and eleven pieces of equipment, static until a re-scrape -- so a client
    fetches it once and keeps it.
    """
    return Universal_Read(
        ploys=service.list_universal_ploys(),
        equipment=service.list_universal_equipment(),
    )
