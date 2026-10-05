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

from app.core.db.models import User
from app.core.db.models_killteam import (
    KillTeam,
    KTEquipment,
    KTGame,
    KTGameEquipment,
    KTGameEvent,
    KTGameOperative,
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

    _STATUSES = {"setup", "in_progress", "finished"}
    _INITIATIVE = {"player", "opponent"}
    _OPERATIVE_STATUSES = {"reserve", "on_board", "incapacitated"}
    _ORDERS = {"engage", "conceal"}
    _SOURCES = {"roster", "equipment", "rule"}

    def __init__(self, session: Session):
        self.session = session

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
        the row.
        """
        if self.session.get(User, user_id) is None:
            raise NotFoundError(f"user {user_id} not found")
        roster = self._require_roster_with_operatives(roster_id)

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

        for row in roster.operatives:
            self.session.add(self._new_operative(game, row.operative, source="roster", position=row.position))
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
        return self._require_game(game_id)

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
            .options(selectinload(KTGame.kill_team))  # type: ignore[arg-type]
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

        touched = self._apply(game, fields)
        self._write(game, "game_updated", touched)
        return game

    def delete_game(self, game_id: UUID) -> None:
        """Delete a game and everything on it. The roster and the catalog are untouched."""
        self.session.delete(self._require_game(game_id))
        self.session.flush()

    # ---------------------------- operatives -----------------------------

    def add_operative(self, game_id: UUID, operative_id: UUID, source: str) -> KTGameOperative:
        """Field a datacard the battle produced — not one the roster brought.

        Decision #18: the set is not fixed at creation. Revealing Gellerpox's MUTOID
        VERMIN equipment grants four vermin (`source = 'equipment'`), and a team rule
        can do the same (`source = 'rule'`). `source = 'roster'` is refused here,
        because that is what `create_game` writes and a roster's operatives arrive with
        the game rather than during it.

        An `in_battle` datacard may only arrive this way (#20); the converse does not
        hold, and a `roster` one may be added too -- a rule that grants a second of
        something the roster already fields is a real case.
        """
        game = self._require_game(game_id)
        if source == "roster":
            raise KTGameValidationError("source", "a roster's operatives arrive with the game")
        if source not in self._SOURCES - {"roster"}:
            raise KTGameValidationError("source", f"must be one of {sorted(self._SOURCES)}")

        operative = self.session.get(KTOperative, operative_id)
        if operative is None:
            raise NotFoundError(f"operative {operative_id} not found")
        # Referential, not legal: the composite foreign key would refuse this row
        # anyway. Checked here so the caller gets a 404 naming the problem.
        if operative.kill_team_id != game.kill_team_id:
            raise NotFoundError(f"operative {operative_id} is not one of kill team {game.kill_team_id}'s")

        row = self._new_operative(
            game,
            operative,
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
        self._write(game, "operative_transformed", touched, target_id=row.id)
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
        self._write(game, "operative_updated", touched, target_id=row.id)
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
        self._write(game, "operative_activated", touched, target_id=row.id)
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
        ploy = next((p for p in game.ploys if p.get("name") == name), None)
        if ploy is None:
            raise KTGameValidationError("name", f"{name!r} is not one of this game's ploys")

        cost = ploy.get("cp_cost") or 0
        if cost > game.command_points:
            raise KTGameValidationError(
                "command_points",
                f"{name!r} costs {cost} CP and the game has {game.command_points}",
            )

        touched = self._apply(
            game,
            {
                "command_points": game.command_points - cost,
                "ploys_used": [*game.ploys_used, {"name": name, "turning_point": game.turning_point}],
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
        if self._equipment_row(game_id, equipment_id) is not None:
            raise ConflictError(f"{item.name!r} is already taken in this game", field="equipment_id")

        row = KTGameEquipment(
            game_id=game.id,
            equipment_id=item.id,
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
        )
        self.session.refresh(row)
        return row

    def reveal_equipment(self, game_id: UUID, row_id: UUID) -> KTGameEquipment:
        """Reveal a piece during the battle — which can change what is on the table."""
        game = self._require_game(game_id)
        row = self._require_equipment(game_id, row_id)
        touched = self._apply(row, {"revealed": True})
        self._write(game, "equipment_revealed", touched, target_id=row.id)
        return row

    def remove_equipment(self, game_id: UUID, row_id: UUID) -> None:
        """Put a piece back before the battle starts. The event carries the whole row,
        because undoing a deletion means recreating it and nothing else remembers it."""
        game = self._require_game(game_id)
        row = self._require_equipment(game_id, row_id)
        snapshot = {
            "equipment_id": str(row.equipment_id),
            "name": row.name,
            "text": row.text,
            "revealed": row.revealed,
            "position": row.position,
        }
        self.session.delete(row)
        self.session.flush()
        self._forget_collections(game)
        self._write(game, "equipment_removed", {"row": snapshot}, target_id=row_id, op="deleted")

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
        game = self._require_game(game_id)
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
        game = self._require_game(game_id)
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
        event.undone_by = compensating.id
        self.session.add(event)
        self.session.flush()
        return compensating

    # ------------------------------ helpers ------------------------------

    def _require_game(self, game_id: UUID, expected_version: int | None = None) -> KTGame:
        game = self.session.get(KTGame, game_id)
        if game is None:
            raise NotFoundError(f"game {game_id} not found")
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

    def _require_roster_with_operatives(self, roster_id: UUID) -> KTRoster:
        statement = (
            select(KTRoster)
            .where(KTRoster.id == roster_id)
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

    def _new_operative(
        self,
        game: KTGame,
        operative: KTOperative,
        *,
        source: str,
        position: int,
        added_in_turning_point: int | None = None,
    ) -> KTGameOperative:
        snapshot = self._snapshot(operative)
        return KTGameOperative(
            game_id=game.id,
            operative_id=operative.id,
            # Both reached by the composite legs, so both must match the game's.
            kill_team_id=game.kill_team_id,
            owner_user_id=game.owner_user_id,
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
    ) -> KTGameEvent:
        """Record what happened and bump the game's version.

        One place, so no mutation can forget either: an event that was never written is
        a change `undo` cannot reach, and a version that did not move lets the other tab
        overwrite this write without ever seeing a 409 (#9).
        """
        payload: dict[str, Any] = {"op": op}
        if target_id is not None:
            payload["target_id"] = str(target_id)
        payload.update(touched if op != "deleted" else {})
        if op == "deleted":
            payload["row"] = touched["row"]

        event = KTGameEvent(
            game_id=game.id,
            sequence=self._next_sequence(game.id),
            type=event_type,
            turning_point=game.turning_point,
            payload=payload,
        )
        game.version += 1
        self.session.add_all([event, game])
        self.session.flush()
        return event

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
        target = self._event_target(game, event)
        for field, change in event.payload.items():
            if field in {"op", "target_id", "row"} or not isinstance(change, dict):
                continue
            setattr(target, field, change["before"])
        self.session.add(target)

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
                name=row["name"],
                text=row["text"],
                revealed=row["revealed"],
                position=row["position"],
            )
        )

    def _event_target(self, game: KTGame, event: KTGameEvent):
        target_id = event.payload.get("target_id")
        if target_id is None:
            return game
        for model in (KTGameOperative, KTGameEquipment):
            row = self.session.get(model, UUID(target_id))
            if row is not None:
                return row
        raise NotFoundError(f"event {event.id} points at a row that is gone")

    def _validate_game_fields(self, game: KTGame, fields: dict[str, Any]) -> None:
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
            self._validate_ploys_used(game, fields["ploys_used"])

    @staticmethod
    def _validate_ploys_used(game: KTGame, entries) -> None:
        """A ploy recorded must be one this GAME's snapshot lists.

        The exact counterpart of `_validate_actions` against an operative's card (#23),
        and it was the missing half of that rule: actions were checked and ploys were
        not, so `ploys_used` accepted a ploy the team does not have. How OFTEN a ploy may
        be used is a rule, so the same entry twice is accepted.
        """
        if not isinstance(entries, list):
            raise KTGameValidationError("ploys_used", "must be a list of entries")
        known = {p["name"] for p in game.ploys if isinstance(p, dict) and "name" in p}
        for entry in entries:
            if not isinstance(entry, dict) or "name" not in entry:
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
            if not isinstance(entry, dict) or "name" not in entry:
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
