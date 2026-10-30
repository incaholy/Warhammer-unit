"""KTGameService — a battle, and everything that changes during one.

Session-injected, with the SPEC conventions: `NotFoundError` for not-found,
`ConflictError` for a stale write, and `KTGameValidationError` for bad input.

**A game writes only its own rows** (decision #21). It is created from a roster and
COPIES what it needs -- every datacard it will play with (#22), the team's rules and
both ploy lists (#24), the text of each piece of equipment taken -- so nothing here
reads the catalog once the game exists, a re-scrape cannot change a battle in
progress, and a finished game still shows the cards as they were played.

What this service enforces is **bookkeeping, not rules** (#1). Wounds stay within the
card's own maximum, an operative activates once a turning point, CP never goes
negative, a game has four turning points, and an action must be one the operative's
own snapshot lists. How often that action may be used, whether a ploy was legal, which
Tac Op was allowed -- those are the players'. The equipment allowance is REPORTED and
not refused (#57), which is the same reasoning: a custom game has to be buildable.

Every mutation records an event (#8, #58) carrying the fields it touched with their
`before` and `after`, which is what lets `undo` revert without replaying. `undo` writes
those values back, appends a compensating event and marks the original -- so the log
keeps its append-only property and an undo is itself part of the record.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.db.columns import not_nullable_fields
from app.core.db.models import User
from app.core.db.models_killteam import (
    KillTeam,
    KTEquipment,
    KTGame,
    KTGameEquipment,
    KTGameEvent,
    KTGameOperative,
    KTGameSide,
    KTOperative,
    KTPloy,
    KTRoster,
    KTRosterOperative,
)
from app.core.errors import CodedError, ErrorCode
from app.core.services.errors import ConflictError, NotFoundError

#: The pieces of equipment a kill team may take into a battle. A CONSTANT the read
#: reports, never a refusal (decision #57): no team's page states a different number,
#: and refusing the fifth piece would stop a custom game being built.
EQUIPMENT_LIMIT = 4

#: A game of Kill Team 2024 is four turning points, each a strategy phase then a
#: firefight phase. Held by a CHECK as well; named here so `advance` reads as the rule.
LAST_TURNING_POINT = 4
PHASES = ("strategy", "firefight")

#: The two sides of a battle (#68). ONE set serves both this and `initiative`, because
#: #68's whole point is that `initiative` names a side row rather than carrying a loose
#: label -- two sets with the same contents is the shape that agrees until one is edited.
SIDES = frozenset({"player", "opponent"})

#: Which SIDE each of `_UPDATABLE`'s fields belongs to after #67. One map, because two
#: callers need the same answer: `_write` scopes a `game_updated` event with it, and
#: #96's per-field undo routing will send each field to its own row with it. Keeping them
#: apart would be two lists that agree until one is edited.
#:
#: The fields named in neither set belong to NO side -- `status`, `initiative` and
#: `markers` stay on `kt_games` (#76) -- which is why they are absent rather than listed
#: against the game. A field that is simply forgotten would read the same as one
#: deliberately game-level, so `_validate_game_fields` is what refuses an unknown name.
PLAYER_FIELDS = frozenset({"command_points", "victory_points", "ploys_used", "choices"})
OPPONENT_FIELDS = frozenset({"opponent_name", "opponent_victory_points"})

#: The event type `undo` writes. The walk-back skips these, so undo never reverts its
#: own work -- there is no redo (#58).
UNDO_EVENT = "undone"


class KTGameValidationError(CodedError, ValueError):
    """Bad game input."""

    code = ErrorCode.VALIDATION

    def __init__(self, field: str, message: str):
        text = f"{field}: {message}"
        super().__init__(text)
        self.message = text
        self.field = field


class KTGameService:
    """A player's games, and the state a battle writes."""

    # What a PATCH on the game itself may set. `turning_point` and `phase` are absent
    # on purpose: they move through `advance`, which applies the resets that go with
    # them, and letting a caller set them directly would skip those.
    _UPDATABLE = {
        "opponent_name",
        "status",
        "initiative",
        "command_points",
        "victory_points",
        "opponent_victory_points",
        "markers",
        "ploys_used",
        "choices",
    }
    # What a PATCH on one operative may set. `activated_in_turning_point` is absent for
    # the same reason: `activate_operative` sets it to the game's OWN turning point, so
    # a caller cannot record an activation in a turning point that is not happening.
    _OPERATIVE_UPDATABLE = {"current_wounds", "order", "status", "tokens", "actions_used"}

    # Of those, the ones mapped to a NOT NULL column -- derived from the tables rather
    # than restated, the way `KTRosterService` does it, so a column that becomes
    # nullable cannot leave a stale name behind. `initiative` is deliberately absent
    # from the game's set: it IS nullable, because null means "not rolled yet" (#62).
    # Derived from BOTH tables, because `_UPDATABLE`'s fields no longer live on one.
    # `not_nullable_fields` SKIPS a name that is not a column on the model it is given,
    # so deriving from `KTGame` alone would quietly stop covering the four that moved
    # the moment the last revision drops them -- and a PATCH sending null would reach
    # the database's NOT NULL as a 500 instead of being refused as a 400. The union is
    # correct while both tables carry them too: a field NOT NULL on either is one this
    # service must refuse a null for.
    _NOT_NULLABLE = not_nullable_fields(KTGame, _UPDATABLE) | not_nullable_fields(KTGameSide, _UPDATABLE)
    _OPERATIVE_NOT_NULLABLE = not_nullable_fields(KTGameOperative, _OPERATIVE_UPDATABLE)

    _STATUSES = {"setup", "in_progress", "finished"}
    _INITIATIVE = SIDES  # #68: initiative names a side, so it is the same vocabulary
    _OPERATIVE_STATUSES = {"reserve", "on_board", "incapacitated"}
    _ORDERS = {"engage", "conceal"}
    #: Why a row is in a game. `'opponent'` means the host typed it in for a side that
    #: holds no account (#93) -- a different answer from `'equipment'` or `'rule'`, which
    #: is why it is a value of its own rather than a reuse. It is NOT correlated with
    #: `side`: an opponent may reveal equipment that grants models, so `'equipment'` on
    #: the opponent's half is ordinary, and nothing here forbids the mirror case.
    _SOURCES = {"roster", "equipment", "rule", "opponent"}

    def __init__(self, session: Session, actor: User | None = None):
        self.session = session
        #: Who is writing, recorded on every event this service appends (#70). The log
        #: could not say who did anything with two writers, which made undo "a button
        #: with an invisible target" -- and #70's answer is the actor column plus a label
        #: rather than a second undo stack, because the defect was never SHARED undo but
        #: INVISIBLE shared undo.
        #:
        #: Optional so the service is constructible without a request -- a seed or a
        #: console has no caller -- and NOT because a route may omit it. Every game route
        #: authenticates, `get_game_service` supplies the caller, and
        #: `test_every_event_a_route_writes_names_its_actor` is what holds that shut.
        self.actor = actor

    # ------------------------------- games -------------------------------

    def create_game(self, user_id: UUID, roster_id: UUID, opponent_name: str | None = None) -> KTGame:
        """Start a battle from a roster, copying everything it will need.

        The copy is the point (decisions #22, #24): one datacard snapshot per roster
        row, the team's rules, its ploys together with the universal ones, and from
        here on nothing reads the catalog. An `in_battle` datacard is NOT copied -- a
        roster cannot hold one (#20) and the ones a rule or a piece of equipment grants
        arrive through `add_operative` when they are granted.

        `owner_user_id` is the CALLER's, not the roster's, for the reason #54 gives:
        written independently, the composite foreign key refuses the insert unless the
        caller really owns that roster, so a route that forgot to check could not land
        the row. The roster is ALSO read scoped to the caller, so a stranger's id is a
        404 rather than the foreign key's 409 -- see `_require_roster_with_operatives`.
        """
        if self.session.get(User, user_id) is None:
            raise NotFoundError(f"user {user_id} not found")
        roster = self._require_roster_with_operatives(roster_id, user_id)

        game = KTGame(
            owner_user_id=user_id,
            kill_team_id=roster.kill_team_id,
            roster_id=roster.id,
            opponent_name=opponent_name,
            rules=[self._rule_snapshot(rule) for rule in roster.kill_team.rules],
            ploys=[self._ploy_snapshot(ploy) for ploy in self._playable_ploys(roster.kill_team)],
        )
        self.session.add(game)
        self.session.flush()
        player = self._new_player_side(game)
        self.session.add(player)
        self.session.flush()

        for row in roster.operatives:
            self.session.add(
                self._new_operative(
                    game, row.operative, side=player, source="roster", position=row.position
                )
            )
        self.session.flush()
        self.session.refresh(game)
        return game

    def get_game(self, game_id: UUID) -> KTGame:
        """One game whole: its operatives, its equipment and its log.

        Eager-loaded in a flat number of queries whatever the game's size, for the same
        reason `KTRosterService.get_roster` is -- this is the read a battle screen makes,
        and lazily each operative would cost a query of its own.

        The snapshots need no joins at all, which is the shape paying off: `rules`,
        `ploys`, `weapons` and `abilities` are columns on these rows, not relationships.
        """
        statement = (
            select(KTGame)
            .where(KTGame.id == game_id)
            .options(
                selectinload(KTGame.kill_team),  # type: ignore[arg-type]
                selectinload(KTGame.sides),  # type: ignore[arg-type]
                selectinload(KTGame.operatives),  # type: ignore[arg-type]
                selectinload(KTGame.equipment),  # type: ignore[arg-type]
                selectinload(KTGame.events),  # type: ignore[arg-type]
            )
            # `populate_existing`, which is load-bearing rather than defensive: without it a
            # read that follows a WRITE in the same session gets the collection as it was
            # when first loaded. SQLAlchemy returns the identity-mapped object and leaves
            # an already-populated collection alone, so an operative added a moment ago is
            # simply absent -- and inconsistently so, since a flush that happened to expire
            # the parent hides it. The router's shape makes this the normal case: the
            # ownership dependency loads the row, the route mutates it, and then reads it
            # back for the response.
            .execution_options(populate_existing=True)
        )
        game = self.session.exec(statement).first()
        if game is None:
            raise NotFoundError(f"game {game_id} not found")
        return game

    def get_game_shallow(self, game_id: UUID) -> KTGame:
        """The game row alone — for an ownership check, not for a response.

        The router's dependency needs one fact ("does the caller own this?") and the
        detail read is the bundle: operatives with their snapshots, equipment and the
        whole event log. Loading that to answer a yes/no is what makes a 204 DELETE cost
        as much as a full read, which is a defect the roster router has and this one
        does not inherit.
        """
        return self._require_game(game_id, allow_finished=True)  # a read; see #66

    def list_games(self, user_id: UUID, limit: int = 50, offset: int = 0) -> list[KTGame]:
        """A player's games, newest first — and deliberately without the bundle.

        Newest first, unlike rosters: a roster list is a library you scroll, where a
        game list is "what did I play recently". `created_at` then `id`, so paging is
        stable across a timestamp tie.

        The snapshots are NOT loaded. A game holds the roster detail's 24-42 KB as
        stored JSON, so a page of fifty would be megabytes to answer which games exist.
        """
        statement = (
            select(KTGame)
            .where(KTGame.owner_user_id == user_id)
            .options(
                selectinload(KTGame.kill_team),  # type: ignore[arg-type]
                # Eager, because `_listed` reads the player side's `victory_points`: one
                # query for the page rather than one per game.
                selectinload(KTGame.sides),  # type: ignore[arg-type]
            )
            .order_by(KTGame.created_at.desc(), KTGame.id)  # type: ignore[attr-defined]
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.exec(statement).all())

    def count_games(self, user_id: UUID) -> int:
        return self.session.exec(select(func.count(KTGame.id)).where(KTGame.owner_user_id == user_id)).one()

    def update_game(self, game_id: UUID, expected_version: int | None = None, **fields) -> KTGame:
        """Set what a player tracks on the game itself: CP, VP, markers, ploys used."""
        game = self._require_game(game_id, expected_version)
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise KTGameValidationError("fields", f"cannot update {sorted(unknown)}")
        self._validate_game_fields(game, fields)

        touched = self._apply_routed(game, fields)
        self._write(game, "game_updated", touched)
        return game

    def delete_game(self, game_id: UUID) -> None:
        """Delete a game and everything on it. The roster and the catalog are untouched."""
        # Deleting is not editing: a finished game is the one most likely to be deleted.
        self.session.delete(self._require_game(game_id, allow_finished=True))
        self.session.flush()

    # ---------------------------- operatives -----------------------------

    def establish_side(
        self,
        game_id: UUID,
        side: str,
        *,
        display_name: str,
        kill_team_id: UUID | None = None,
    ) -> KTGame:
        """Describe one side of a battle: give it a name, and a team if it has none (#92).

        The operation K8 was missing. #67's reason to exist is "an opponent with no account
        whose side is filled in by hand", and the slice as specified moved `opponent_name`
        off the game while naming no operation that writes it -- so the hand-filled
        opponent was deleted with no replacement. This is the replacement, and it is also
        what sets the PLAYER row's `display_name`, which #77 leaves NULL at creation.

        A team is settable ONCE. A side with none takes the one given; a `PUT` naming a
        DIFFERENT team on a side that already has one is a conflict rather than a rewrite.
        #76 fixes the creator's team at creation, and an established opponent's team is
        what their operatives and equipment are keyed to through the four-column leg -- so
        changing it would be refused by the database anyway, once that side holds a row.
        Refusing it here means the answer is a 409 naming the field rather than whatever
        the constraint happens to raise.

        Omitting `kill_team_id` is therefore the ordinary way to RENAME a side, including
        your own, with no value to echo back from the read.

        Replace is an UPDATE, never a delete-and-insert (#92): #94 points events at this
        row, so deleting it would be refused and cascading the delete would destroy log
        rows #8 and #58 forbid losing.
        """
        game = self._require_game(game_id)
        if side not in SIDES:
            raise KTGameValidationError("side", f"must be one of {sorted(SIDES)}")

        if kill_team_id is not None:
            team = self.session.get(KillTeam, kill_team_id)
            if team is None:
                raise NotFoundError(f"kill team {kill_team_id} not found")
            # The FIFTH write path to need this. A withdrawn team is still resolvable for
            # the games and rosters that already name it -- that is the point of flagging
            # rather than deleting -- but it cannot be NEWLY taken up (#55).
            if team.withdrawn:
                raise NotFoundError(f"kill team {kill_team_id} is no longer in the catalog")

        row = self._side_row(game_id, side)
        if row is None:
            # `occupant_user_id` and `roster_id` stay NULL: no account holds a
            # hand-described side (#74), and the opponent's roster is provenance that only
            # a join can supply (#69, #86).
            row = KTGameSide(game_id=game.id, owner_user_id=game.owner_user_id, side=side)
            self.session.add(row)
            self.session.flush()
        elif kill_team_id is not None and row.kill_team_id not in (None, kill_team_id):
            raise ConflictError(
                f"side {side!r} already fields kill team {row.kill_team_id}",
                field="kill_team_id",
            )

        changes: dict[str, Any] = {"display_name": display_name}
        if kill_team_id is not None:
            changes["kill_team_id"] = kill_team_id

        # `op="fields"`, not `"created"`, even on the first write. Undoing an
        # establishment restores both columns to what they were -- NULL, which is #77's
        # own backfilled shape -- and LEAVES the row, because `_undo_created` deletes its
        # target and deleting this row is what #92 refuses.
        touched = self._apply(row, changes)
        self._write(game, "side_updated", touched, target_id=row.id, side=side)
        return game

    def add_operative(
        self, game_id: UUID, operative_id: UUID, source: str, side: str = "player"
    ) -> KTGameOperative:
        """Field a datacard the battle produced — not one the roster brought.

        Decision #18: the set is not fixed at creation. Revealing Gellerpox's MUTOID
        VERMIN equipment grants four vermin (`source = 'equipment'`), and a team rule
        can do the same (`source = 'rule'`). `source = 'roster'` is refused here,
        because that is what `create_game` writes and a roster's operatives arrive with
        the game rather than during it.

        An `in_battle` datacard may only arrive this way (#20); the converse does not
        hold, and a `roster` one may be added too -- a rule that grants a second of
        something the roster already fields is a real case.

        `side` defaults to the creator's half, so the shipped call is unchanged. Naming
        the opponent's is what #93 adds: a host who described an opponent (#92) can now
        field that opponent's models, with `source = 'opponent'` recording that the host
        typed them in.
        """
        game = self._require_game(game_id)
        if source == "roster":
            raise KTGameValidationError("source", "a roster's operatives arrive with the game")
        if source not in self._SOURCES - {"roster"}:
            raise KTGameValidationError("source", f"must be one of {sorted(self._SOURCES)}")
        if side not in SIDES:
            raise KTGameValidationError("side", f"must be one of {sorted(SIDES)}")

        # Both preconditions are the four-column leg's, stated here so the caller gets a
        # message rather than a constraint violation. The leg needs
        # `(owner_user_id, kill_team_id, game_id, side)` to name a real row, and under
        # MATCH SIMPLE a NULL anywhere in it skips the check entirely -- so a side with no
        # team would take the row silently and leave it unprotected.
        target = self._side_row(game_id, side)
        if target is None:
            raise KTGameValidationError(
                "side", f"this game has no {side} side yet -- describe it first"
            )
        if target.kill_team_id is None:
            raise KTGameValidationError(
                "side", f"the {side} side has no kill team yet -- give it one first"
            )

        operative = self.session.get(KTOperative, operative_id)
        if operative is None:
            raise NotFoundError(f"operative {operative_id} not found")
        # Against the SIDE's team, not the game's (#93). The game's is the creator's (#76),
        # so comparing against it rejected every datacard belonging to the other team --
        # correct while a game held one team, and the exact guard that refused the case
        # #67 exists to serve. Referential rather than legal: the composite foreign key
        # would refuse the row anyway, and this is here so the caller gets a 404 that
        # names the problem.
        if operative.kill_team_id != target.kill_team_id:
            raise NotFoundError(
                f"operative {operative_id} is not one of kill team {target.kill_team_id}'s"
            )
        # Refused if the catalog has WITHDRAWN it (#55): the source no longer names this
        # row, so a picker that offered it was stale. Rows already in a game or on a
        # roster are untouched and keep resolving -- that is the whole reason #55 flags
        # rather than deletes. Deliberately NOT applied to `transform_operative`: a model
        # already on the table changing into something the catalog has since dropped is
        # the case the flag exists to survive.
        if operative.withdrawn:
            raise NotFoundError(f"operative {operative_id} is no longer in the catalog")

        row = self._new_operative(
            game,
            operative,
            side=target,
            source=source,
            position=self._next_position(game.id),
            added_in_turning_point=game.turning_point,
        )
        self.session.add(row)
        self.session.flush()
        self._write(
            game,
            "operative_added",
            {"operative": {"before": None, "after": operative.name}},
            target_id=row.id,
            op="created",
            side=row.side,
        )
        self.session.refresh(row)
        return row

    def transform_operative(self, game_id: UUID, row_id: UUID, becomes_operative_id: UUID) -> KTGameOperative:
        """One datacard becomes another, IN PLACE (decision #19).

        Chaos Cult's Mutation turns a Devotee into a Mutant and a Mutant into a Torment.
        It is the same miniature on the table, so the row keeps its id, its tokens, the
        actions it has used and where it stands; only the catalog pointer moves and the
        snapshot is rewritten.

        How many transforms a turning point allows is the players' (#1) -- it is a
        query over the event log, not a column, and not refused here.
        """
        game = self._require_game(game_id)
        row = self._require_operative(game_id, row_id)
        becomes = self.session.get(KTOperative, becomes_operative_id)
        if becomes is None:
            raise NotFoundError(f"operative {becomes_operative_id} not found")
        if becomes.kill_team_id != game.kill_team_id:
            raise NotFoundError(
                f"operative {becomes_operative_id} is not one of kill team {game.kill_team_id}'s"
            )

        changes = self._snapshot(becomes) | {"operative_id": becomes.id}
        # A card with fewer wounds than the model currently has is a state the schema
        # refuses, so it is resolved here rather than left to an IntegrityError.
        if row.current_wounds > becomes.wounds:
            changes["current_wounds"] = becomes.wounds

        touched = self._apply(row, changes)
        self._write(game, "operative_transformed", touched, target_id=row.id, side=row.side)
        return row

    def update_operative(
        self, game_id: UUID, row_id: UUID, expected_version: int | None = None, **fields
    ) -> KTGameOperative:
        """Wounds, order, tokens, actions used — what a battle writes to one model."""
        game = self._require_game(game_id, expected_version)
        row = self._require_operative(game_id, row_id)
        unknown = set(fields) - self._OPERATIVE_UPDATABLE
        if unknown:
            raise KTGameValidationError("fields", f"cannot update {sorted(unknown)}")
        self._validate_operative_fields(row, fields)

        # Bookkeeping, not a rule: an operative on zero wounds is out of the battle, and
        # a caller that has to remember to say so would eventually not.
        #
        # Both directions, which matters more than it looks. Setting the status on the way
        # down and not on the way UP is asymmetric automation, and asymmetric is worse
        # than none: because something maintains the field, a player trusts it, and it
        # would be right only half the time. The way up is the CORRECTION path -- someone
        # typed 0 and meant 5 -- which is exactly when they are already flustered, and
        # since `activate_operative` now refuses an incapacitated model, a stale label
        # there would leave a live operative unable to act for the rest of the game.
        #
        # `setdefault`, so a caller that names a status explicitly keeps it, and only
        # `incapacitated` is cleared: an operative in `reserve` that gains wounds is
        # still off the board.
        if fields.get("current_wounds") == 0:
            fields.setdefault("status", "incapacitated")
        elif fields.get("current_wounds") and row.status == "incapacitated":
            fields.setdefault("status", "on_board")

        touched = self._apply(row, fields)
        self._write(game, "operative_updated", touched, target_id=row.id, side=row.side)
        return row

    def activate_operative(self, game_id: UUID, row_id: UUID) -> KTGameOperative:
        """Mark this operative as having activated in the game's CURRENT turning point.

        Not a field a caller sets (#61): the service reads the turning point off the
        game, so an activation cannot be recorded in one that is not happening, and
        "has it activated?" stays a comparison rather than a flag anyone must clear.

        Refused for an operative on zero wounds, and only for that -- see the note in
        `update_operative` about why the incapacitation it reads has to be symmetric.
        """
        game = self._require_game(game_id)
        row = self._require_operative(game_id, row_id)
        # Bookkeeping, not a rule: a model that is gone cannot act, and an activation
        # recorded against it is a wrong row in the log rather than a play anyone made.
        # Deliberately NOT extended to `reserve`: whether an operative can arrive and act
        # in one turning point is a RULE, and #1 says the players apply those.
        if row.status == "incapacitated":
            raise KTGameValidationError("status", f"{row.name} is incapacitated and cannot activate")
        if row.activated_in_turning_point == game.turning_point:
            raise KTGameValidationError(
                "activated_in_turning_point",
                f"{row.name} has already activated in turning point {game.turning_point}",
            )

        touched = self._apply(row, {"activated_in_turning_point": game.turning_point})
        self._write(game, "operative_activated", touched, target_id=row.id, side=row.side)
        return row

    def use_ploy(self, game_id: UUID, name: str) -> KTGame:
        """Spend a ploy: record it AND deduct its CP, in one operation.

        The two halves were separate fields a caller set independently, which meant a
        player could log a ploy without paying for it -- and CP is the one number in a
        game with mechanical consequence. The cost is not something the client has to
        know either: it is in the game's own snapshot (#24), so the server reads it.

        One operation rather than two writes, because it is one event. `undo` then
        restores the CP and removes the entry TOGETHER; two PATCHes would be two events
        and undoing once would leave a ploy logged that had been paid for, or paid-for CP
        with no ploy to show it.

        Refused for a ploy this game's snapshot does not list -- the same bookkeeping as
        `actions_used` against an operative's card (#23) -- and refused if the CP will not
        cover it, which the `ck_kt_game_command_points` CHECK would refuse anyway. How
        many ploys a turning point allows is a RULE, so it is not counted here (#1), and
        `ploys_used` and `command_points` stay directly writable for a custom game.
        """
        game = self._require_game(game_id)
        # The SIDE's snapshot, not the game's. Both hold the same list today -- #97 copies
        # it at creation -- and only one of them survives the last revision.
        side = self.player_side(game)
        ploy = next((p for p in side.ploys if p.get("name") == name), None)
        if ploy is None:
            raise KTGameValidationError("name", f"{name!r} is not one of this game's ploys")

        # The SIDE's pool. #65's argument is that spending a ploy is one operation against
        # ONE CP pool, and after #67 there are two -- so the question "has the CP" has to
        # name whose. The message still says "the game has", which is what a one-sided
        # battle means to a player; it becomes the side's name when the opponent can spend.
        cost = ploy.get("cp_cost") or 0
        if cost > side.command_points:
            raise KTGameValidationError(
                "command_points",
                f"{name!r} costs {cost} CP and the game has {side.command_points}",
            )

        # Routed rather than applied to the game, so the two fields land on the side and
        # `undo` takes them off the same row -- `_undo_fields` uses the same `_owner_of`.
        touched = self._apply_routed(
            game,
            {
                "command_points": side.command_points - cost,
                "ploys_used": [*side.ploys_used, {"name": name, "turning_point": game.turning_point}],
            },
        )
        self._write(game, "ploy_used", touched)
        return game

    # ----------------------------- equipment -----------------------------

    def add_equipment(self, game_id: UUID, equipment_id: UUID) -> KTGameEquipment:
        """Take a piece of equipment into this battle, with its text copied.

        The allowance is NOT checked (decision #57): a read reports `equipment_limit`
        beside what was taken and the fifth piece is accepted, because the rules on
        selection are shown rather than applied. What IS refused is the same piece
        twice, which `UNIQUE(game_id, equipment_id)` makes unrepresentable -- integrity
        rather than a rule.
        """
        game = self._require_game(game_id)
        item = self.session.get(KTEquipment, equipment_id)
        if item is None:
            raise NotFoundError(f"equipment {equipment_id} not found")
        # Either the team's own or the universal list, which belongs to no team (#48).
        # The schema cannot express this pair, so it is checked here.
        if item.kill_team_id is not None and item.kill_team_id != game.kill_team_id:
            raise NotFoundError(f"equipment {equipment_id} is not available to kill team {game.kill_team_id}")
        # Withdrawn equipment cannot be newly taken either (#55).
        if item.withdrawn:
            raise NotFoundError(f"equipment {equipment_id} is no longer in the catalog")
        if self._equipment_row(game_id, equipment_id) is not None:
            raise ConflictError(f"{item.name!r} is already taken in this game", field="equipment_id")

        row = KTGameEquipment(
            game_id=game.id,
            equipment_id=item.id,
            # The creator's half, for the same reason `_new_operative` says: no operation
            # takes equipment for the opponent yet. `uq_kt_game_equipment` now names
            # `side`, so this is also what makes the duplicate check below meaningful --
            # without it every row would collide on NULL being distinct from nothing.
            side="player",
            name=item.name,
            text=item.description,
            position=self._next_equipment_position(game.id),
        )
        self.session.add(row)
        self.session.flush()
        self._write(
            game,
            "equipment_added",
            {"equipment": {"before": None, "after": item.name}},
            target_id=row.id,
            op="created",
            side=row.side,
        )
        self.session.refresh(row)
        return row

    def reveal_equipment(self, game_id: UUID, row_id: UUID) -> KTGameEquipment:
        """Reveal a piece during the battle — which can change what is on the table."""
        game = self._require_game(game_id)
        row = self._require_equipment(game_id, row_id)
        touched = self._apply(row, {"revealed": True})
        self._write(game, "equipment_revealed", touched, target_id=row.id, side=row.side)
        return row

    def remove_equipment(self, game_id: UUID, row_id: UUID) -> None:
        """Put a piece back before the battle starts. The event carries the whole row,
        because undoing a deletion means recreating it and nothing else remembers it."""
        game = self._require_game(game_id)
        row = self._require_equipment(game_id, row_id)
        snapshot = {
            "equipment_id": str(row.equipment_id),
            # Carried because `_undo_deleted` rebuilds the row from this and nothing else
            # remembers it. Without it undo recreated an UNSIDED piece, which voids
            # `uq_kt_game_equipment` while `side` is nullable and fails outright once the
            # last revision makes it NOT NULL.
            "side": row.side,
            "name": row.name,
            "text": row.text,
            "revealed": row.revealed,
            "position": row.position,
        }
        self.session.delete(row)
        self.session.flush()
        self._forget_collections(game)
        self._write(
            game,
            "equipment_removed",
            {"row": snapshot},
            target_id=row_id,
            op="deleted",
            side=snapshot["side"],
        )

    # ------------------------------- flow --------------------------------

    def advance(self, game_id: UUID) -> KTGame:
        """Move to the next phase, or the next turning point, or finish the game.

        `strategy` -> `firefight` within a turning point; `firefight` -> the next
        turning point's `strategy`, which puts initiative back to nobody for its
        roll-off (#62). Advancing out of the fourth turning point's firefight phase
        FINISHES the game rather than refusing: advance is then the one control that
        moves a game through every state including its end, and because the finish is
        an event like any other, `undo` reopens a game closed by accident.

        Activations need no clearing (#61) -- they are stamped with the turning point
        they happened in, so they stop counting the moment it changes.
        """
        # `advance` refuses a finished game itself, with a more specific message.
        game = self._require_game(game_id, allow_finished=True)
        if game.status == "finished":
            raise KTGameValidationError("status", "the game is already finished")

        if game.phase == "strategy":
            changes: dict[str, Any] = {"phase": "firefight"}
        elif game.turning_point < LAST_TURNING_POINT:
            changes = {
                "phase": "strategy",
                "turning_point": game.turning_point + 1,
                "initiative": None,
            }
        else:
            changes = {"status": "finished"}
        # A game that was still in setup is under way the moment it advances.
        if game.status == "setup" and changes.get("status") != "finished":
            changes["status"] = "in_progress"

        touched = self._apply(game, changes)
        self._write(game, "advanced", touched)
        return game

    def undo(self, game_id: UUID) -> KTGameEvent:
        """Revert the newest event that still stands, and record having done so.

        Walks back over events the log has already undone AND over the compensating
        events themselves, so pressing undo twice goes two steps back rather than
        returning where it started -- there is no redo (#58).

        Three kinds of event, because three kinds of change: a field change writes its
        `before` values back, a row that was created is deleted, and a row that was
        deleted is recreated from the copy its event carried.
        """
        # The way back OUT of a finished game (#66): undoing the event that finished
        # it is how a premature finish is fixed, so there is no `reopen` route.
        game = self._require_game(game_id, allow_finished=True)
        event = self._newest_standing_event(game_id)
        if event is None:
            raise KTGameValidationError("undo", "this game has nothing left to undo")

        payload = event.payload or {}
        op = payload.get("op", "fields")
        if op == "created":
            self._undo_created(event)
            self._forget_collections(game)
        elif op == "deleted":
            self._undo_deleted(game, event)
        else:
            self._undo_fields(game, event)

        compensating = self._write(
            game,
            UNDO_EVENT,
            {"undoes": {"before": None, "after": event.type}},
            target_id=event.id,
            op="undo",
        )
        assert compensating is not None  # `op="undo"` always carries `undoes`
        event.undone_by = compensating.id
        self.session.add(event)
        self.session.flush()
        return compensating

    # ------------------------------ helpers ------------------------------

    def _require_game(
        self, game_id: UUID, expected_version: int | None = None, *, allow_finished: bool = False
    ) -> KTGame:
        """The game, refusing the write if it is already finished (decision #66).

        The refusal defaults to ON and is opted OUT of, rather than the other way round,
        for the reason `_write` is one place: a mutation added later is covered the day
        it arrives, where an opt-in list would quietly not cover it. Four callers opt
        out, each for a stated reason:

          `get_game_shallow`  a READ. A finished game is still fully readable.
          `delete_game`       deleting is not editing, and a finished game is the one
                              most likely to be deleted.
          `undo`              the way back OUT (#58, and the K8 decisions). Undoing the
                              event that finished the game is how a premature finish is
                              fixed, which is why no `reopen` route is needed.
          `advance`           refuses on its own with a more specific message.
        """
        game = self.session.get(KTGame, game_id)
        if game is None:
            raise NotFoundError(f"game {game_id} not found")
        if game.status == "finished" and not allow_finished:
            raise KTGameValidationError("status", "this game is finished")
        if expected_version is not None and expected_version != game.version:
            # Decision #9: the other tab moved on, so this write is answered rather than
            # applied over the top of it.
            raise ConflictError(
                f"game {game_id} has moved on (version {game.version}, not {expected_version})",
                field="version",
            )
        return game

    def _require_operative(self, game_id: UUID, row_id: UUID) -> KTGameOperative:
        row = self.session.get(KTGameOperative, row_id)
        if row is None or row.game_id != game_id:
            raise NotFoundError(f"operative {row_id} is not in game {game_id}")
        return row

    def _require_equipment(self, game_id: UUID, row_id: UUID) -> KTGameEquipment:
        row = self.session.get(KTGameEquipment, row_id)
        if row is None or row.game_id != game_id:
            raise NotFoundError(f"equipment {row_id} is not in game {game_id}")
        return row

    def _require_roster_with_operatives(self, roster_id: UUID, user_id: UUID) -> KTRoster:
        """The caller's roster, loaded whole. Scoped to the OWNER, not just the id.

        Unscoped, this was the one route in the Kill Team surface where a stranger's id
        answered differently from a missing one: the composite foreign key refused the
        insert, so the row never landed, but it surfaced as 409 "conflict with an
        existing resource" where a roster that does not exist is 404 -- an oracle for
        whether an id is real. Scoping the read makes both answers 404, and leaves the
        foreign key as what it was meant to be: the backstop, not the check.
        """
        statement = (
            select(KTRoster)
            .where(KTRoster.id == roster_id, KTRoster.owner_user_id == user_id)
            .options(
                selectinload(KTRoster.kill_team).selectinload(KillTeam.rules),  # type: ignore[arg-type]
                selectinload(KTRoster.kill_team).selectinload(KillTeam.ploys),  # type: ignore[arg-type]
                selectinload(KTRoster.operatives)  # type: ignore[arg-type]
                .selectinload(KTRosterOperative.operative)
                .selectinload(KTOperative.weapons),
                selectinload(KTRoster.operatives)  # type: ignore[arg-type]
                .selectinload(KTRosterOperative.operative)
                .selectinload(KTOperative.abilities),
            )
        )
        roster = self.session.exec(statement).first()
        if roster is None:
            raise NotFoundError(f"roster {roster_id} not found")
        return roster

    def _playable_ploys(self, team: KillTeam) -> list[KTPloy]:
        """The team's own ploys and the universal ones, which belong to no team (#24)."""
        universal = self.session.exec(
            select(KTPloy).where(KTPloy.kill_team_id.is_(None)).order_by(KTPloy.position)  # type: ignore[union-attr]
        ).all()
        return list(team.ploys) + list(universal)

    @staticmethod
    def _snapshot(operative: KTOperative) -> dict[str, Any]:
        """The datacard as a game stores it (#22): the stat line, and the profiles whole.

        `weapons` and `abilities` are display-only documents. Nothing reads a field back
        out of them to decide anything, which is what lets them keep the page's shape.
        """
        return {
            "name": operative.name,
            "apl": operative.apl,
            "move": operative.move,
            "save": operative.save,
            "wounds": operative.wounds,
            "keywords": list(operative.keywords),
            "weapons": [
                {
                    "name": w.name,
                    "category": w.category,
                    "range": w.range,
                    "attacks": w.attacks,
                    "hit": w.hit,
                    "normal_damage": w.normal_damage,
                    "crit_damage": w.crit_damage,
                    "weapon_rules": list(w.weapon_rules),
                }
                for w in operative.weapons
            ],
            "abilities": [{"name": a.name, "description": a.description} for a in operative.abilities],
        }

    @staticmethod
    def _rule_snapshot(rule) -> dict[str, Any]:
        return {"name": rule.name, "description": rule.description, "group": rule.group}

    @staticmethod
    def _ploy_snapshot(ploy: KTPloy) -> dict[str, Any]:
        return {
            "name": ploy.name,
            "kind": ploy.kind,
            "cp_cost": ploy.cp_cost,
            "description": ploy.description,
            "universal": ploy.kill_team_id is None,
        }

    def _new_player_side(self, game: KTGame) -> KTGameSide:
        """The creator's half of a new game, inserted with the game itself (#97).

        #74 says a side row is created "once that side's kill team is known, not at game
        creation", and that rule GIVES this rather than forbidding it: the player's team is
        known at creation, from the roster, and the opponent's is not. The opponent's row
        still waits for #92's `PUT` or #86's join.

        It is not optional. `fk_kt_game_operative_side` refuses `side = 'player'` with no
        matching row, so without this a new game could not hold a sided operative, while
        #77's backfill gives every game that already exists one -- pre-K8 games would work
        and post-K8 games would not.

        The snapshot columns are copied from the game rather than rebuilt, so the two agree
        at creation by construction instead of by two code paths happening to match. The
        MUTABLE ones go stale from here until the writes move onto the side, which is
        deliberate: nothing reads this row yet, the same sense in which #74 calls
        `occupant_user_id` "written here and deliberately unread".

        `display_name` stays NULL. The game never named the creator's side, #92's `PUT` is
        what sets one, and the team's name is already reachable through `kill_team_id` --
        the same reasoning #77's backfill applies to the rows it writes.
        """
        return KTGameSide(
            game_id=game.id,
            owner_user_id=game.owner_user_id,
            side="player",
            kill_team_id=game.kill_team_id,
            occupant_user_id=game.owner_user_id,
            roster_id=game.roster_id,
            command_points=game.command_points,
            victory_points=game.victory_points,
            rules=game.rules,
            ploys=game.ploys,
            ploys_used=game.ploys_used,
            choices=game.choices,
        )

    def _new_operative(
        self,
        game: KTGame,
        operative: KTOperative,
        *,
        side: KTGameSide,
        source: str,
        position: int,
        added_in_turning_point: int | None = None,
    ) -> KTGameOperative:
        snapshot = self._snapshot(operative)
        return KTGameOperative(
            game_id=game.id,
            operative_id=operative.id,
            # All three reached by the four-column leg into `kt_game_sides`, so all three
            # are taken FROM that row rather than from the game. `kill_team_id` is the
            # SIDE's, which is the change #93 calls for: the game's is the creator's (#76)
            # and comparing against it rejected every datacard of the other team -- right
            # while a game held one team, wrong the moment it holds two.
            #
            # `owner_user_id` stays the GAME's owner, which is also what the side row
            # holds: #74 makes that column the creator on BOTH halves, because the
            # four-column leg targets a `kt_games` row and an account-less opponent has no
            # id to put there.
            kill_team_id=side.kill_team_id,
            owner_user_id=game.owner_user_id,
            side=side.side,
            position=position,
            source=source,
            added_in_turning_point=added_in_turning_point,
            # A model arrives at full health and hidden, which is where a battle starts.
            current_wounds=operative.wounds,
            **snapshot,
        )

    def _apply(self, row, changes: dict[str, Any]) -> dict[str, Any]:
        """Set fields on a row and return what the event should carry.

        Only fields that actually CHANGED are reported, so an event never claims to
        have touched something it left alone -- and an undo of it restores exactly what
        this write replaced, no more.
        """
        touched: dict[str, Any] = {}
        for field, after in changes.items():
            before = getattr(row, field)
            if before == after:
                continue
            touched[field] = {"before": self._plain(before), "after": self._plain(after)}
            setattr(row, field, after)
        self.session.add(row)
        return touched

    @staticmethod
    def _plain(value):
        """JSON-safe: a UUID in a payload has to survive a round trip through the column."""
        return str(value) if isinstance(value, UUID) else value

    def _write(
        self,
        game: KTGame,
        event_type: str,
        touched: dict[str, Any],
        *,
        target_id: UUID | None = None,
        op: str = "fields",
        side: str | None = None,
    ) -> KTGameEvent | None:
        """Record what happened and bump the game's version.

        One place, so no mutation can forget either: an event that was never written is
        a change `undo` cannot reach, and a version that did not move lets the other tab
        overwrite this write without ever seeing a 409 (#9).

        A write that changed NOTHING records nothing. `_apply` reports only the fields
        that actually moved, so sending a field its current value leaves `touched` empty
        -- and an empty event is worse than useless: `undo` walks back one event at a
        time, so the next undo spends itself reverting the event that did nothing and
        the real change below it survives. A player who tapped the same value twice
        found undo silently doing nothing. It also bumped `version`, 409-ing the other
        tab over a write that never happened.

        Only `op="fields"` can be empty; `created`, `deleted` and `undo` each pass a
        payload of their own, so the guard cannot swallow one of those.
        """
        if not touched:
            return None
        payload: dict[str, Any] = {"op": op}
        if target_id is not None:
            payload["target_id"] = str(target_id)
        payload.update(touched if op != "deleted" else {})
        if op == "deleted":
            payload["row"] = touched["row"]

        event = KTGameEvent(
            game_id=game.id,
            sequence=self._next_sequence(game.id),
            side=side if side is not None else self._scope_of(game.id, touched),
            actor_user_id=self.actor.id if self.actor else None,
            # STORED, not joined (#70): no route resolves a UUID to a name -- `GET /me` is
            # self-only and `PATCH /users/{user_id}` is admin-gated -- so a client holding
            # a bare id could not satisfy the contract. Storing it also means a later
            # rename leaves old entries reading as they did when they were written.
            #
            # `username` is the INTERIM, not the answer. `users` holds no display name
            # until DM2 adds one, and this is the single line that changes when it does.
            actor_display_name=self.actor.username if self.actor else None,
            type=event_type,
            turning_point=game.turning_point,
            payload=payload,
        )
        game.version += 1
        self.session.add_all([event, game])
        self.session.flush()
        return event

    def _owner_of(self, game: KTGame, field: str):
        """The ROW that holds a field, which is a different question from its scope.

        `_scope_of` answers which side an event is ABOUT; this answers which row the
        column is ON. They differ during the move: a write to `opponent_victory_points` is
        about the opponent, and still lands on `kt_games` because that column has not
        moved yet. Conflating them would send a write to a row that has no such column.

        `OPPONENT_FIELDS` are deliberately absent. They move onto the opponent side when
        #92's `PUT` exists to create one -- until then there is often no row to write to,
        and #74 makes that the ordinary state rather than an error.
        """
        if field in PLAYER_FIELDS:
            return self.player_side(game)
        return game

    def _apply_routed(self, game: KTGame, changes: dict[str, Any]) -> dict[str, Any]:
        """Apply each field to the row that owns it, and return ONE merged payload (#96).

        The payload stays flat and keyed by field name, exactly as a single-row write
        produced, so the log's shape is unchanged by the move and an event written before
        K8 reads the same as one written after.

        This is the forward half of #96's per-field routing; `_undo_fields` is the reverse
        half and uses the same `_owner_of`, because a field restored to a different row
        from the one it was taken off is the defect that made #96 necessary.
        """
        by_owner: list[tuple[Any, dict[str, Any]]] = []
        for field, after in changes.items():
            owner = self._owner_of(game, field)
            for row, subset in by_owner:
                if row is owner:
                    subset[field] = after
                    break
            else:
                by_owner.append((owner, {field: after}))

        touched: dict[str, Any] = {}
        for row, subset in by_owner:
            touched.update(self._apply(row, subset))
        return touched

    def _scope_of(self, game_id: UUID, touched: dict[str, Any]) -> str | None:
        """Which side a game-level write belongs to, or None for the game itself (#94).

        The same rule the historical backfill applied, so a log written before and after
        K8 reads one way. A write naming only player fields is the player's; only opponent
        fields, the opponent's; and a write naming BOTH stays None, because `side` holds
        one value and #94 already reads None as "about the GAME" -- the honest description
        of one call that moved both halves.

        The axis is player against OPPONENT, not player against game. `status`,
        `initiative` and `markers` belong to no side, so they do not compete: a write
        touching `command_points` and `status` is the player's, because exactly one SIDE
        appears in it.

        A side this game has no ROW for scopes to None, and that guard is load-bearing
        rather than defensive. `opponent_name` and `opponent_victory_points` are writable
        today on a game with no opponent side (#97 creates only the player's), so without
        it `fk_kt_game_event_side` refuses the event and a shipped operation starts
        failing. It is also honest: those two are still columns on `kt_games` until the
        last revision drops them, so until an opponent side exists the write really IS
        about the game. The same guard sits in the backfill revision, for the same reason.

        Callers that already KNOW the side pass it instead, which is every write aimed at
        a row -- a row carries its own `side` and needs no inference.
        """
        mine = not PLAYER_FIELDS.isdisjoint(touched)
        theirs = not OPPONENT_FIELDS.isdisjoint(touched)
        if mine == theirs:
            return None
        side = "player" if mine else "opponent"
        return side if self._side_row(game_id, side) is not None else None

    def player_side(self, game: KTGame) -> KTGameSide:
        """The creator's half, which every game has.

        Guaranteed present rather than hoped for: #97 inserts it with the game, and #77's
        backfill gave one to every game that predates the slice. So its absence is a
        broken invariant rather than a missing resource, and raising `LookupError` says
        that -- a `NotFoundError` would answer 404 for a game that plainly exists.

        Reads the loaded collection when there is one, so `get_game`'s bundle stays a flat
        number of queries. `get_game_shallow` loads no sides, and SQLAlchemy lazy-loads
        them on first touch there, which is one query on a path that was already doing
        several.
        """
        for row in game.sides:
            if row.side == "player":
                return row
        raise LookupError(f"game {game.id} has no player side, which #97 makes impossible")

    def _side_row(self, game_id: UUID, side: str) -> KTGameSide | None:
        """One side of a game, or None where that half has no row yet.

        The opponent's is absent until #92's `PUT` or #86's join creates it, which is the
        ordinary state rather than an error -- #74 makes `KTGame_Read.opponent` null until
        then.
        """
        statement = select(KTGameSide).where(
            KTGameSide.game_id == game_id,
            KTGameSide.side == side,
        )
        return self.session.exec(statement).first()

    def _forget_collections(self, game: KTGame) -> None:
        """Expire the game's loaded collections after a row was DELETED.

        A deleted row stays in the identity map and the parent's collection still points
        at it, so the next read -- which uses `populate_existing` so it sees writes --
        would try to refresh a deleted instance and raise `InvalidRequestError`. Expiring
        the collections makes that read load them from the database instead.

        Called immediately after a deletion and BEFORE the event is written, which is
        the part that matters: `cascade_delete` makes the next flush walk the game's
        collections, and a deleted instance still sitting in one raises there rather
        than at the read. Never called after a field change, where `populate_existing`
        alone is correct and an expire would cost a round trip for nothing.
        """
        self.session.expire(game, ["operatives", "equipment", "events"])

    def _newest_standing_event(self, game_id: UUID) -> KTGameEvent | None:
        """The newest event that is neither already undone nor an undo itself."""
        statement = (
            select(KTGameEvent)
            .where(
                KTGameEvent.game_id == game_id,
                KTGameEvent.undone_by.is_(None),  # type: ignore[union-attr]
                KTGameEvent.type != UNDO_EVENT,
            )
            .order_by(KTGameEvent.sequence.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        return self.session.exec(statement).first()

    def _undo_fields(self, game: KTGame, event: KTGameEvent) -> None:
        """Put each field's `before` value back on the row that HOLDS it (#96).

        One target per event was the old shape and cannot survive #67. `_UPDATABLE`'s
        fields land in more than one place now, so a single `PATCH` setting `status` and
        `command_points` produced ONE event spanning two rows -- and `setattr` for a
        column the target does not have raises a bare `ValueError`, which is not a
        `CodedError`, so `app/main.py` has no handler and the player gets an unexplained
        500. #96 records why a migration could not fix this: the data is not broken, the
        reader was too narrow.

        An event aimed at a ROW still resolves that row, and `_owner_of` is not consulted
        for it -- an operative's `current_wounds` belongs to the operative, not to a side.
        The routing applies to the game-level write, which is the only kind whose fields
        were split.
        """
        target = self._event_target(game, event)
        # A LIST with an identity check, not a set: a SQLModel row is not hashable, so
        # `set()` raised `TypeError: unhashable type` for every undo that reached here.
        touched_rows: list[Any] = []
        for field, change in event.payload.items():
            if field in {"op", "target_id", "row"} or not isinstance(change, dict):
                continue
            row = self._owner_of(game, field) if target is game else target
            setattr(row, field, change["before"])
            if not any(seen is row for seen in touched_rows):
                touched_rows.append(row)
        # `target` when nothing was touched, so an event carrying no field changes still
        # marks its row -- the shape the single-target version had.
        self.session.add_all(touched_rows or [target])

    def _undo_created(self, event: KTGameEvent) -> None:
        target_id = UUID(event.payload["target_id"])
        for model in (KTGameOperative, KTGameEquipment):
            row = self.session.get(model, target_id)
            if row is not None:
                self.session.delete(row)
                self.session.flush()
                return

    def _undo_deleted(self, game: KTGame, event: KTGameEvent) -> None:
        row = event.payload["row"]
        self.session.add(
            KTGameEquipment(
                id=UUID(event.payload["target_id"]),
                game_id=game.id,
                equipment_id=UUID(row["equipment_id"]),
                # `event.side` is the fallback for an event written before the snapshot
                # carried one. It is the RIGHT fallback rather than a convenient one: the
                # backfill scoped every historical `equipment_removed` to `'player'`, which
                # is the side the removed piece belonged to. Scope earns its keep here.
                side=row.get("side") or event.side,
                name=row["name"],
                text=row["text"],
                revealed=row["revealed"],
                position=row["position"],
            )
        )

    def _event_target(self, game: KTGame, event: KTGameEvent):
        """The row an event names, or the GAME when it names none (#94).

        `KTGameSide` joins the probe because an operation scoped to one side writes that
        side's id here, and without it #94's own warning came true: the probe found
        neither of the other two and answered "points at a row that is gone" -- a 404 for
        a row that plainly exists.

        It is probed LAST, after the two that every battle writes constantly, so the
        common path still answers on its first `get`.

        A note rather than a fix: none of the three probes checks that the row it found
        belongs to THIS game. It is safe because `_write` only ever stores the id of a row
        it just handled, so no cross-game id can reach the payload -- but it is safe by
        the writer's discipline rather than by construction. ROADMAP K8 gives the audit of
        `_undo_created` and `_undo_deleted`, which share this shape, to K9; this belongs
        with it rather than being changed here, since filtering would turn an
        already-impossible case into a different answer.
        """
        target_id = event.payload.get("target_id")
        if target_id is None:
            return game
        for model in (KTGameOperative, KTGameEquipment, KTGameSide):
            row = self.session.get(model, UUID(target_id))
            if row is not None:
                return row
        raise NotFoundError(f"event {event.id} points at a row that is gone")

    @staticmethod
    def _refuse_nulls(fields: dict[str, Any], not_nullable: frozenset[str]) -> None:
        """A null for a NOT NULL column is a 400, not whatever the database says next.

        Every per-field check below is written `fields.get(x) is not None and ...`, which
        skips null rather than refusing it, and nothing caught it centrally -- so a null
        reached the database and surfaced two different wrong ways:

          `status`, `order`        NOT NULL fired, the `IntegrityError` backstop turned it
                                  into a 409 "conflict with an existing resource", and a
                                  full driver traceback went to the log.
          `choices`, `tokens`,     NOT NULL did NOT fire. SQLAlchemy's `JSON` stores Python
          `markers`, `ploys_used`, `None` as the JSON value `null` rather than SQL NULL, so
          `actions_used`          the write SUCCEEDED and the RESPONSE model rejected it --
                                  an unhandled `ValidationError`, i.e. a 500, after the row
                                  had already changed.

        One guard, so both become the 400 they always were. Driven by the column's own
        `nullable`, so `initiative` -- where null means "not rolled yet" (#62) -- is
        untouched.
        """
        for field in sorted(fields):
            if field in not_nullable and fields[field] is None:
                raise KTGameValidationError(field, "cannot be null")

    def _validate_game_fields(self, game: KTGame, fields: dict[str, Any]) -> None:
        self._refuse_nulls(fields, self._NOT_NULLABLE)
        if fields.get("status") is not None and fields["status"] not in self._STATUSES:
            raise KTGameValidationError("status", f"must be one of {sorted(self._STATUSES)}")
        if fields.get("initiative") is not None and fields["initiative"] not in self._INITIATIVE:
            raise KTGameValidationError(
                "initiative", f"must be one of {sorted(self._INITIATIVE)}, or null until rolled"
            )
        for field in ("command_points", "opponent_victory_points"):
            if field in fields and (fields[field] is None or fields[field] < 0):
                raise KTGameValidationError(field, "cannot be negative")
        if "victory_points" in fields:
            self._validate_victory_points(fields["victory_points"])
        if "ploys_used" in fields:
            self._validate_ploys_used(self.player_side(game).ploys, fields["ploys_used"])

    @staticmethod
    def _validate_ploys_used(snapshot: list, entries) -> None:
        """A ploy recorded must be one this SIDE's snapshot lists.

        Takes the snapshot rather than the game, because after #67 the list belongs to a
        side and the caller is what knows which one. It also keeps this a static method:
        handing it the game would have made it reach for a session to find the side.

        The exact counterpart of `_validate_actions` against an operative's card (#23),
        and it was the missing half of that rule: actions were checked and ploys were
        not, so `ploys_used` accepted a ploy the team does not have. How OFTEN a ploy may
        be used is a rule, so the same entry twice is accepted.
        """
        if not isinstance(entries, list):
            raise KTGameValidationError("ploys_used", "must be a list of entries")
        known = {p["name"] for p in snapshot if isinstance(p, dict) and "name" in p}
        for entry in entries:
            # `isinstance(..., str)` rather than just present: `known` is a SET, so a
            # name that was a dict or a list raised `TypeError: unhashable type` on the
            # membership test below -- an unhandled 500 from a well-formed request.
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                raise KTGameValidationError("ploys_used", "each entry needs a name")
            if entry["name"] not in known:
                raise KTGameValidationError(
                    "ploys_used", f"{entry['name']!r} is not one of this game's ploys"
                )

    @staticmethod
    def _validate_victory_points(value) -> None:
        """A document, so the schema holds nothing — these are the rules it would have.

        The SOURCES are the players' (#1, #59), but a score is still a count: every
        value has to be a non-negative whole number, or a listing's total is nonsense.
        """
        if not isinstance(value, dict):
            raise KTGameValidationError("victory_points", "must be an object keyed by source")
        for source, points in value.items():
            if not isinstance(points, int) or isinstance(points, bool) or points < 0:
                raise KTGameValidationError(
                    "victory_points", f"{source!r} must be a non-negative whole number"
                )

    def _validate_operative_fields(self, row: KTGameOperative, fields: dict[str, Any]) -> None:
        self._refuse_nulls(fields, self._OPERATIVE_NOT_NULLABLE)
        if "current_wounds" in fields:
            wounds = fields["current_wounds"]
            if wounds is None or not 0 <= wounds <= row.wounds:
                raise KTGameValidationError(
                    "current_wounds", f"must be between 0 and {row.wounds}, this card's maximum"
                )
        if fields.get("order") is not None and fields["order"] not in self._ORDERS:
            raise KTGameValidationError("order", f"must be one of {sorted(self._ORDERS)}")
        if fields.get("status") is not None and fields["status"] not in self._OPERATIVE_STATUSES:
            raise KTGameValidationError("status", f"must be one of {sorted(self._OPERATIVE_STATUSES)}")
        if "actions_used" in fields:
            self._validate_actions(row, fields["actions_used"])

    @staticmethod
    def _validate_actions(row: KTGameOperative, actions) -> None:
        """An action recorded must be one this operative's OWN snapshot lists (#23).

        How OFTEN each may be used is a rule, so it is shown and not enforced -- the
        same entry twice is legitimate. That it exists on the card at all is bookkeeping,
        and catching a typo here is the difference between a tracker and a notepad.
        """
        if not isinstance(actions, list):
            raise KTGameValidationError("actions_used", "must be a list of entries")
        known = {ability["name"] for ability in row.abilities if isinstance(ability, dict)}
        for entry in actions:
            # `isinstance(..., str)` rather than just present: `known` is a SET, so a
            # name that was a dict or a list raised `TypeError: unhashable type` on the
            # membership test below -- an unhandled 500 from a well-formed request.
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                raise KTGameValidationError("actions_used", "each entry needs a name")
            if entry["name"] not in known:
                raise KTGameValidationError(
                    "actions_used", f"{entry['name']!r} is not on {row.name}'s datacard"
                )

    def _equipment_row(self, game_id: UUID, equipment_id: UUID) -> KTGameEquipment | None:
        return self.session.exec(
            select(KTGameEquipment).where(
                KTGameEquipment.game_id == game_id,
                KTGameEquipment.equipment_id == equipment_id,
            )
        ).first()

    def _next_sequence(self, game_id: UUID) -> int:
        highest = self.session.exec(
            select(func.max(KTGameEvent.sequence)).where(KTGameEvent.game_id == game_id)
        ).one()
        return 1 if highest is None else highest + 1

    def _next_position(self, game_id: UUID) -> int:
        highest = self.session.exec(
            select(func.max(KTGameOperative.position)).where(KTGameOperative.game_id == game_id)
        ).one()
        return 0 if highest is None else highest + 1

    def _next_equipment_position(self, game_id: UUID) -> int:
        highest = self.session.exec(
            select(func.max(KTGameEquipment.position)).where(KTGameEquipment.game_id == game_id)
        ).one()
        return 0 if highest is None else highest + 1
