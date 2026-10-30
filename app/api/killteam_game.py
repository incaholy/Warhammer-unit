"""Kill team games router — the current user's battles (`/me/kill-team/games`).

Identity comes from the JWT, never a path param, and every nested `{game_id}` route
goes through `get_owned_game`, which 404s unless the game belongs to the caller — so a
stranger's `game_id` reveals nothing. The same shape as the rosters router.

Fifteen routes:

    POST   /me/kill-team/games                                 start one from a roster
    GET    /me/kill-team/games                                 paged, lean
    GET    /me/kill-team/games/{id}                            the battle whole
    PATCH  /me/kill-team/games/{id}                            CP, VP, markers, ploys used
    DELETE /me/kill-team/games/{id}
    POST   /me/kill-team/games/{id}/operatives                 one a rule or kit granted
    PATCH  /me/kill-team/games/{id}/operatives/{row_id}        wounds, order, tokens
    POST   /me/kill-team/games/{id}/operatives/{row_id}/activate
    POST   /me/kill-team/games/{id}/operatives/{row_id}/transform
    POST   /me/kill-team/games/{id}/ploys                      spend one, CP and all
    POST   /me/kill-team/games/{id}/equipment                  take a piece
    PATCH  /me/kill-team/games/{id}/equipment/{row_id}         reveal it
    DELETE /me/kill-team/games/{id}/equipment/{row_id}
    POST   /me/kill-team/games/{id}/advance
    POST   /me/kill-team/games/{id}/undo

**`get_owned_game` loads the game SHALLOW.** The ownership check needs one fact, and
the detail is the whole bundle, so loading it to answer a yes/no would make a 204
DELETE cost as much as a full read. That is a defect the rosters router has (its
dependency eager-loads decision #52's bundle on all six nested routes) and this one
deliberately does not inherit.

Two routes are not in KILLTEAM.md's endpoint list, and both follow from decisions made
after it was written. `activate` is its own route because #61 made
`activated_in_turning_point` something the SERVER sets from the game's own turning
point rather than a field a caller supplies. Given that, `transform` is its own route
too rather than a `becomes_operative_id` smuggled into the operative PATCH: both are
operations rather than field sets, and one of each shape would be the odd thing.

Nothing here judges a battle. The equipment allowance is reported and not refused
(#57), how often an action may be used is the players' (#23), and so is how many
transforms a turning point allows (#1).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Field, Session, SQLModel

from app.api.deps import get_current_user
from app.api.fields import INT32_MAX, Name, WriteSchema
from app.api.pagination import Page, PageParams, paginate
from app.core.db.connection import get_session
from app.core.db.models import User
from app.core.db.models_killteam import KTGame, KTGameSide
from app.core.services.errors import NotFoundError
from app.core.services.service_killteam_game import EQUIPMENT_LIMIT, KTGameService

router = APIRouter(prefix="/me/kill-team/games", tags=["kill team games"])


def get_game_service(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> KTGameService:
    """The service, carrying who is calling so every event it writes names its actor (#70).

    The caller is supplied here rather than on each operation, because all twelve writing
    operations need it and none has a reason to differ. `get_owned_game` depends on this
    and on `get_current_user` as well; FastAPI resolves a dependency once per request, so
    the token is decoded once whichever path reaches it first.
    """
    return KTGameService(session, actor=current_user)


def get_owned_game(
    game_id: UUID,
    current_user: User = Depends(get_current_user),
    service: KTGameService = Depends(get_game_service),
) -> KTGame:
    """Load a game the current user owns, else 404 (hides existence).

    Shallow on purpose — see the module docstring. A route that needs the bundle asks
    for it once, at the end, for the response.
    """
    game = service.get_game_shallow(game_id)  # NotFoundError -> 404 if missing
    if game.owner_user_id != current_user.id:
        raise NotFoundError(f"game {game_id} not found")
    return game


# --- schemas ---
# The snapshots are typed rather than left as bare objects. `weapons` and `abilities`
# are display-only JSON on the row, so `list[dict]` would be honest about the column --
# and useless in `openapi.json`, where the frontend generates its types from this file
# and would get `Record<string, unknown>[]`. The shape is known, because the service
# writes it, so it is published.


class KTGameWeapon_Read(SQLModel):
    name: str
    category: str
    range: int
    attacks: int
    hit: int
    normal_damage: int
    crit_damage: int
    weapon_rules: list[str] = []


class KTGameAbility_Read(SQLModel):
    name: str
    description: str


class KTGameRule_Read(SQLModel):
    name: str
    description: str
    group: str | None = None


class KTGamePloy_Read(SQLModel):
    name: str
    kind: str
    cp_cost: int
    description: str
    #: True for one of the ploys that belong to no kill team (decision #48), so a screen
    #: can group "the team's" and "everyone's" without re-fetching the catalog.
    universal: bool


class KTGameOperative_Read(SQLModel):
    """One model in the battle: its card as it was snapshotted, then its state."""

    id: UUID
    position: int
    #: The catalog card this is a snapshot of. It MOVES on a transform (#19), so it is
    #: provenance rather than a key a client should resolve.
    operative_id: UUID
    name: str
    apl: int
    move: int
    save: int
    #: The card's maximum. `current_wounds` is the state.
    wounds: int
    keywords: list[str] = []
    weapons: list[KTGameWeapon_Read] = []
    abilities: list[KTGameAbility_Read] = []
    current_wounds: int
    order: str
    status: str
    #: WHICH turning point it activated in (#61), NULL if it has not. "Has it activated?"
    #: is this compared against the game's `turning_point`, not a flag.
    activated_in_turning_point: int | None
    tokens: list[str] = []
    actions_used: list[dict[str, Any]] = []
    source: str
    added_in_turning_point: int | None


class KTGameEquipment_Read(SQLModel):
    id: UUID
    equipment_id: UUID
    name: str
    text: str
    revealed: bool
    position: int


class KTGameEvent_Read(SQLModel):
    """One entry in the log (#8, #58), which is also what `undo` walks back over."""

    id: UUID
    sequence: int
    type: str
    turning_point: int
    payload: dict[str, Any] = {}
    #: Set once something reverted this entry, so a reader can show the history rather
    #: than a hole.
    undone_by: UUID | None
    #: Which half of the game this entry belongs to, or null where it is about the game
    #: itself -- the clock, the status, the initiative (#94).
    side: str | None
    #: WHO wrote it, which is a different question from `side` (#70). Both are null only
    #: for entries written before the columns existed; every new one carries them.
    actor_user_id: UUID | None
    #: The actor's name as it stood when the entry was written, stored rather than joined
    #: because no route resolves a user id to a name (#70).
    actor_display_name: str | None
    created_at: datetime


class KTGame_ListRead(SQLModel):
    """A row of the game listing: enough to choose one, and none of the bundle.

    Carries the score, because "which game was that?" is answered by who and how it
    went. Deliberately NOT the operatives: a game stores the roster detail's 24-42 KB
    as JSON (#52), so a page of fifty would be megabytes.

    Names the kill team and its faction for the same reason `KTRoster_ListRead` does --
    a deliberate exception to #46, since a player's own game list should not require
    fetching the catalog to render "Raveners, Tyranids".
    """

    id: UUID
    created_at: datetime
    opponent_name: str | None
    status: str
    turning_point: int
    phase: str
    kill_team_id: UUID
    kill_team_name: str
    faction_name: str
    victory_points: dict[str, int] = {}
    opponent_victory_points: int


class KTGameSide_Put(WriteSchema):
    """Describe one side of a battle (#92): name it, and give it a team if it has none.

    `display_name` is the only field a body must carry, which is what lets this one
    operation serve both halves. Naming your OWN side is `{display_name}` alone -- #76
    fixes the creator's team at creation, so there is no value to echo back from the read.

    `kill_team_id` is settable once. A side with no team takes the one given; naming a
    different team on a side that already has one is a 409 rather than a rewrite, because
    that side's operatives and equipment are keyed to it through the four-column leg.
    """

    display_name: Name
    kill_team_id: UUID | None = None


class KTGameSide_Update(WriteSchema):
    """One side's numbers (#87): either participant may set these on either half.

    `version` is the GAME's (#9), because one battle is one optimistic-locking unit. The
    four fields are the ones #67 moved off the game, and `PATCH /{game_id}` can still set
    them until the breaking change removes them there.
    """

    version: int | None = Field(default=None, ge=1, le=INT32_MAX)
    command_points: int | None = Field(default=None, ge=0, le=INT32_MAX)
    victory_points: dict[str, int] | None = None
    ploys_used: list[dict[str, Any]] | None = None
    choices: dict[str, Any] | None = None


class KTGame_Read(SQLModel):
    """A battle whole — everything the screen needs, and no catalog call (#24)."""

    id: UUID
    created_at: datetime
    roster_id: UUID
    kill_team_id: UUID
    kill_team_name: str
    faction_name: str
    opponent_name: str | None
    status: str
    turning_point: int
    phase: str
    #: NULL means nobody has rolled for it in this turning point (#62) -- never "the
    #: opponent has it".
    initiative: str | None
    command_points: int
    victory_points: dict[str, int] = {}
    opponent_victory_points: int
    markers: list[str] = []
    ploys_used: list[dict[str, Any]] = []
    choices: dict[str, Any] = {}
    #: Decision #9: send this back on a write and a stale one answers 409 rather than
    #: overwriting what the other tab did.
    version: int
    #: Decision #57: REPORTED, never enforced. `equipment` may be longer than this.
    equipment_limit: int = EQUIPMENT_LIMIT
    operatives: list[KTGameOperative_Read] = []
    equipment: list[KTGameEquipment_Read] = []
    rules: list[KTGameRule_Read] = []
    ploys: list[KTGamePloy_Read] = []
    events: list[KTGameEvent_Read] = []


class KTGame_Create(WriteSchema):
    roster_id: UUID
    opponent_name: Name | None = None


class KTGame_Update(WriteSchema):
    # `turning_point` and `phase` are absent on purpose: they move through `advance`,
    # which applies the resets that go with them (#61, #62), and a direct set would skip
    # those. `version` is not a field being set -- it is what this write expects to find.
    version: int | None = Field(default=None, ge=1, le=INT32_MAX)
    opponent_name: Name | None = None
    status: str | None = Field(default=None, max_length=16)
    initiative: str | None = Field(default=None, max_length=16)
    command_points: int | None = Field(default=None, ge=0, le=INT32_MAX)
    victory_points: dict[str, int] | None = None
    opponent_victory_points: int | None = Field(default=None, ge=0, le=INT32_MAX)
    markers: list[str] | None = None
    ploys_used: list[dict[str, Any]] | None = None
    choices: dict[str, Any] | None = None


class KTGameOperative_Add(WriteSchema):
    operative_id: UUID
    #: `equipment`, `rule`, or `opponent` for a model the host typed in (#18, #93).
    #: `roster` is refused: those arrive with the game rather than during it.
    source: str = Field(max_length=16)
    #: Which half fields it (#67). Defaults to the creator's, so a call written before
    #: K8 means what it always meant. The named side must already exist and field a team,
    #: which `PUT .../sides/{side}` is what does (#92).
    side: str = Field(default="player", max_length=16)


class KTGameOperative_Update(WriteSchema):
    version: int | None = Field(default=None, ge=1, le=INT32_MAX)
    current_wounds: int | None = Field(default=None, ge=0, le=INT32_MAX)
    order: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=16)
    tokens: list[str] | None = None
    actions_used: list[dict[str, Any]] | None = None


class KTGameOperative_Transform(WriteSchema):
    becomes_operative_id: UUID


class KTGameEquipment_Add(WriteSchema):
    equipment_id: UUID


class KTGamePloy_Use(WriteSchema):
    #: By NAME, not by id: the game plays from its own snapshot (#24) and never reads the
    #: catalog again, so a ploy's catalog id is not something this battle still knows.
    name: Name


def _listed(game: KTGame, player: KTGameSide) -> KTGame_ListRead:
    return KTGame_ListRead(
        id=game.id,
        created_at=game.created_at,
        opponent_name=game.opponent_name,
        status=game.status,
        turning_point=game.turning_point,
        phase=game.phase,
        kill_team_id=game.kill_team_id,
        kill_team_name=game.kill_team.name,
        faction_name=game.kill_team.faction.name,
        victory_points=player.victory_points,
        opponent_victory_points=game.opponent_victory_points,
    )


def _read(service: KTGameService, game_id: UUID) -> KTGame_Read:
    """Load one game whole and shape the detail response.

    The six writing routes all answered `_detail(service.get_game(game.id))`, and now all
    need the player side beside the game, so the pair is loaded in one place rather than
    repeated six times. The side comes from the SERVICE, because "every game has a player
    side" is #97's invariant and belongs behind the service boundary -- a presentation
    helper picking the row out of a list would be re-deciding it here.
    """
    game = service.get_game(game_id)
    return _detail(game, service.player_side(game))


def _detail(game: KTGame, player: KTGameSide) -> KTGame_Read:
    """A battle whole. No catalog service is injected here, deliberately — everything
    this response carries was snapshotted into the game itself (#22, #24)."""
    return KTGame_Read(
        id=game.id,
        created_at=game.created_at,
        roster_id=game.roster_id,
        kill_team_id=game.kill_team_id,
        kill_team_name=game.kill_team.name,
        faction_name=game.kill_team.faction.name,
        opponent_name=game.opponent_name,
        status=game.status,
        turning_point=game.turning_point,
        phase=game.phase,
        initiative=game.initiative,
        # The SIDE's four. `markers` stays the GAME's -- #67 keeps it shared, because a
        # marker is on the board rather than in a team's hands -- and the opponent's score
        # stays on the game until there is an opponent row to hold it.
        command_points=player.command_points,
        victory_points=player.victory_points,
        opponent_victory_points=game.opponent_victory_points,
        markers=game.markers,
        ploys_used=player.ploys_used,
        choices=player.choices,
        version=game.version,
        operatives=[
            KTGameOperative_Read.model_validate(row, from_attributes=True) for row in game.operatives
        ],
        equipment=[KTGameEquipment_Read.model_validate(row, from_attributes=True) for row in game.equipment],
        # The SIDE's snapshots. Both rows hold the same lists today -- #97 copies them at
        # creation -- and only the side's survives the last revision. The response shape
        # is unchanged: these stay top-level fields until the read is reshaped.
        rules=player.rules,
        ploys=player.ploys,
        events=[KTGameEvent_Read.model_validate(entry, from_attributes=True) for entry in game.events],
    )


# --- games ---


@router.post("", response_model=KTGame_Read, status_code=status.HTTP_201_CREATED)
def create_game(
    payload: KTGame_Create,
    current_user: User = Depends(get_current_user),
    service: KTGameService = Depends(get_game_service),
) -> KTGame_Read:
    """Start a battle from one of the caller's rosters, copying everything it needs.

    404 if the roster is not theirs: `create_game` writes the CALLER's id and the
    composite foreign key refuses a roster they do not own, so this cannot start a
    battle from a stranger's list even if the route forgot to look (#54).
    """
    game = service.create_game(current_user.id, payload.roster_id, payload.opponent_name)
    return _read(service, game.id)


@router.get("", response_model=Page[KTGame_ListRead])
def list_games(
    page: PageParams = Depends(),
    current_user: User = Depends(get_current_user),
    service: KTGameService = Depends(get_game_service),
) -> Page[KTGame_ListRead]:
    """The caller's games, newest first. Only ever the caller's."""
    games = service.list_games(current_user.id, limit=page.limit, offset=page.offset)
    return paginate(
        [_listed(game, service.player_side(game)) for game in games],
        service.count_games(current_user.id),
        page,
    )


@router.get("/{game_id}", response_model=KTGame_Read)
def get_game(
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGame_Read:
    return _read(service, game.id)


@router.patch("/{game_id}", response_model=KTGame_Read)
def update_game(
    payload: KTGame_Update,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGame_Read:
    """CP, VP, markers, the ploys spent. 409 if `version` is behind (#9)."""
    fields = payload.model_dump(exclude_unset=True)
    service.update_game(game.id, fields.pop("version", None), **fields)
    return _read(service, game.id)


@router.put("/{game_id}/sides/{side}", response_model=KTGame_Read)
def establish_side(
    side: str,
    payload: KTGameSide_Put,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGame_Read:
    """Name a side, and give it a team if it has none (#92).

    `PUT` rather than `POST` because the side is addressed by `{side}`, so the request is
    create-or-replace rather than append. Replace is an UPDATE: #94 points events at this
    row, so deleting it would be refused and cascading would destroy log rows.

    Owner-only, which `get_owned_game` already gives. #92 splits this from
    `PATCH .../sides/{side}` on authorization: describing a side is the HOST's act, where
    updating a score is either occupant's once K9 lands.
    """
    fields = payload.model_dump(exclude_unset=True)
    service.establish_side(
        game.id,
        side,
        display_name=fields["display_name"],
        kill_team_id=fields.get("kill_team_id"),
    )
    return _read(service, game.id)


@router.patch("/{game_id}/sides/{side}", response_model=KTGame_Read)
def update_side(
    side: str,
    payload: KTGameSide_Update,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGame_Read:
    """One side's CP, score, spent ploys and picks (#87). 409 if `version` is behind (#9).

    NOT scoped to the caller's own half: #87 lets either participant record anything on
    either side, with the actor on the event so the write is visible rather than silent.
    """
    fields = payload.model_dump(exclude_unset=True)
    service.update_side(game.id, side, fields.pop("version", None), **fields)
    return _read(service, game.id)


@router.delete("/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_game(
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> Response:
    service.delete_game(game.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- operatives in a battle ---


@router.post(
    "/{game_id}/operatives",
    response_model=KTGameOperative_Read,
    status_code=status.HTTP_201_CREATED,
)
def add_operative(
    payload: KTGameOperative_Add,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGameOperative_Read:
    """Field a datacard the battle produced — Gellerpox's vermin, a rule's grant (#18).

    Or one the host typed in for an opponent who holds no account (#93), which is what
    `side` selects. A body that omits it means the creator's half, so this stays additive.
    """
    row = service.add_operative(game.id, payload.operative_id, payload.source, payload.side)
    return KTGameOperative_Read.model_validate(row, from_attributes=True)


@router.patch("/{game_id}/operatives/{row_id}", response_model=KTGameOperative_Read)
def update_operative(
    row_id: UUID,
    payload: KTGameOperative_Update,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGameOperative_Read:
    """Wounds, order, tokens, the actions used. Zero wounds takes it out of the battle."""
    fields = payload.model_dump(exclude_unset=True)
    row = service.update_operative(game.id, row_id, fields.pop("version", None), **fields)
    return KTGameOperative_Read.model_validate(row, from_attributes=True)


@router.post("/{game_id}/operatives/{row_id}/activate", response_model=KTGameOperative_Read)
def activate_operative(
    row_id: UUID,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGameOperative_Read:
    """Mark it activated in the game's CURRENT turning point (#61).

    Its own route rather than a field on the PATCH, because the server reads the turning
    point off the game — so an activation cannot be recorded in one that is not
    happening. 400 on a second activation in the same turning point.
    """
    row = service.activate_operative(game.id, row_id)
    return KTGameOperative_Read.model_validate(row, from_attributes=True)


@router.post("/{game_id}/operatives/{row_id}/transform", response_model=KTGameOperative_Read)
def transform_operative(
    row_id: UUID,
    payload: KTGameOperative_Transform,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGameOperative_Read:
    """One card becomes another, in place (#19) — the same model on the table.

    The row keeps its id, tokens, actions used and position; only the catalog pointer
    moves and the snapshot is rewritten. How many transforms a turning point allows is
    the players' (#1), so nothing here counts them.
    """
    row = service.transform_operative(game.id, row_id, payload.becomes_operative_id)
    return KTGameOperative_Read.model_validate(row, from_attributes=True)


# --- this battle's equipment ---


@router.post(
    "/{game_id}/equipment",
    response_model=KTGameEquipment_Read,
    status_code=status.HTTP_201_CREATED,
)
def add_equipment(
    payload: KTGameEquipment_Add,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGameEquipment_Read:
    """Take a piece into this battle (#17), with its text copied.

    409 for the same piece twice, which is integrity. The ALLOWANCE is not checked
    (#57): the detail read reports `equipment_limit` and a fifth piece is accepted, so
    a custom game can be built.
    """
    row = service.add_equipment(game.id, payload.equipment_id)
    return KTGameEquipment_Read.model_validate(row, from_attributes=True)


@router.patch("/{game_id}/equipment/{row_id}", response_model=KTGameEquipment_Read)
def reveal_equipment(
    row_id: UUID,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGameEquipment_Read:
    """Reveal it — which can change what is on the table (Gellerpox's vermin, #18)."""
    row = service.reveal_equipment(game.id, row_id)
    return KTGameEquipment_Read.model_validate(row, from_attributes=True)


@router.delete("/{game_id}/equipment/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_equipment(
    row_id: UUID,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> Response:
    service.remove_equipment(game.id, row_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{game_id}/ploys", response_model=KTGame_Read)
def use_ploy(
    payload: KTGamePloy_Use,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGame_Read:
    """Spend a ploy: record it and deduct its CP in one operation.

    The cost comes from the game's own snapshot, so a client does not supply it and
    cannot forget to. One operation because it is one event -- `undo` then restores the
    CP and removes the entry together, where two PATCHes would let one be undone without
    the other.

    400 for a ploy this game's snapshot does not list, or if the CP will not cover it.
    How many ploys a turning point allows is the players' (#1). Returns the game whole,
    because two of its fields moved.

    Spends the CREATOR's pool. `POST .../sides/{side}/ploys` is what names a side (#65,
    #67), and this path is kept beside it so no shipped call breaks before the one
    breaking change. It is #77's "`use_ploy` becomes `POST .../sides/{side}/ploys`" that
    retires it.
    """
    service.use_ploy(game.id, payload.name)
    return _read(service, game.id)


@router.post("/{game_id}/sides/{side}/ploys", response_model=KTGame_Read)
def use_side_ploy(
    side: str,
    payload: KTGamePloy_Use,
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGame_Read:
    """Spend one of a side's ploys, from that side's own pool (#65, #67).

    #65's argument is that spending a ploy is ONE operation against ONE CP pool, and
    after #67 a game has two -- so the pool, the snapshot it is checked against and the
    shortfall message all name whose. Either participant may do it for either side, for
    the reason #87 gives.

    400 for a side this game has not described yet, for a ploy that side's snapshot does
    not list, or if that side's CP will not cover it.
    """
    service.use_ploy(game.id, payload.name, side)
    return _read(service, game.id)


# --- flow ---


@router.post("/{game_id}/advance", response_model=KTGame_Read)
def advance(
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGame_Read:
    """Next phase, next turning point, or finish the game.

    The server applies what goes with the move: initiative back to nobody for the new
    turning point's roll-off (#62), and nothing at all for activations, which are
    stamped with the turning point they happened in (#61). Advancing out of the fourth
    firefight phase FINISHES the game, and because that is an event like any other,
    `undo` reopens one closed by accident.
    """
    service.advance(game.id)
    return _read(service, game.id)


@router.post("/{game_id}/undo", response_model=KTGame_Read)
def undo(
    game: KTGame = Depends(get_owned_game),
    service: KTGameService = Depends(get_game_service),
) -> KTGame_Read:
    """Revert the newest change that still stands, and record having done so (#58).

    Returns the game whole rather than the compensating event, because an undo can move
    anything -- a wound, a turning point, a row that existed -- and a client that got
    only the event would have to re-read to know what changed. 400 when there is
    nothing left.
    """
    service.undo(game.id)
    return _read(service, game.id)
