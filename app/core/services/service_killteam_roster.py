"""KTRosterService — a player's kill team rosters and the operatives on them.

Session-injected. `NotFoundError` for not-found, `ConflictError` for a duplicate
name, and `KTRosterValidationError` for bad input, per the SPEC conventions.

Kept apart from `KillTeamService`, which is the CATALOG and read-only (decision
#21): a roster is the player's own data and the only Kill Team thing they write.
This service reads catalog rows to check a reference exists; it never writes one.

**Nothing here decides what a roster may contain.** A roster may field any
operative of its kill team, as many times as the player likes (decisions #50,
#51), and composition is the page's own words shown beside it rather than a rule
the catalog applies (#28, #44). So there is no `validate`, no budget, no cap and
no refusal -- which is what lets a custom game be built. The checks that remain
are referential, not legal: does this kill team exist, is this operative one of
its own, is this row on this roster.
"""

from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.db.columns import not_nullable_fields
from app.core.db.models import User
from app.core.db.models_killteam import (
    KillTeam,
    KTGame,
    KTOperative,
    KTRoster,
    KTRosterOperative,
)
from app.core.errors import CodedError, ErrorCode
from app.core.services.errors import ConflictError, NotFoundError


class KTRosterValidationError(CodedError, ValueError):
    """Bad roster input."""

    code = ErrorCode.VALIDATION

    def __init__(self, field: str, message: str):
        text = f"{field}: {message}"
        super().__init__(text)
        self.message = text
        self.field = field


class KTRosterService:
    """A player's rosters, and the operatives on them."""

    # Fields a PATCH may set. `kill_team_id` is NOT among them: changing a roster's team
    # would orphan every operative on it, and the composite foreign keys would refuse the
    # write halfway through. Build a new roster instead.
    _UPDATABLE = {"name", "description"}
    # Of those, the ones backed by NOT NULL columns, read off the mapped table rather than
    # hand-listed so a new NOT NULL column cannot be forgotten here.
    _NOT_NULLABLE = not_nullable_fields(KTRoster, _UPDATABLE)

    def __init__(self, session: Session):
        self.session = session

    # ------------------------------ rosters ------------------------------

    def create_roster(
        self,
        user_id: UUID,
        kill_team_id: UUID,
        name: str,
        description: str | None = None,
    ) -> KTRoster:
        """A new, empty roster for one kill team.

        The team is fixed at creation and not updatable: every operative on the roster
        is held to it by a composite foreign key (#50's section), so changing it later
        would mean emptying the roster first.
        """
        if self.session.get(User, user_id) is None:
            raise NotFoundError(f"user {user_id} not found")
        if self.session.get(KillTeam, kill_team_id) is None:
            raise NotFoundError(f"kill team {kill_team_id} not found")
        self._require_name_free(user_id, name)

        roster = KTRoster(
            owner_user_id=user_id,
            kill_team_id=kill_team_id,
            name=name,
            description=description,
        )
        self.session.add(roster)
        self.session.flush()
        self.session.refresh(roster)
        return roster

    def get_roster(self, roster_id: UUID) -> KTRoster:
        """One roster with its operatives and their datacards.

        Everything decision #52 says a roster read carries, minus the two universal lists,
        which belong to no team and are read separately: the roster's operatives with
        their datacards, and the team's rules, ploys and equipment. It is what a player
        has in front of them while building and then playing.

        Eager-loaded throughout, in a flat number of queries whatever the roster's size:
        measured at 13 for 1, 5, 10 and 20 operatives alike (10 for an empty roster,
        where the operative loads have nothing to run against). Lazily, each operative's
        weapons and abilities would be two queries per ROW -- and a detail read is the one
        place that cost would land, since the listing deliberately carries none of this.

        Whether the CALLER may see it is the router's business: `get_owned_roster`
        answers that, and 404s rather than 403s so existence is not disclosed.
        """
        statement = (
            select(KTRoster)
            .where(KTRoster.id == roster_id)
            .options(
                selectinload(KTRoster.kill_team).selectinload(KillTeam.faction),  # type: ignore[arg-type]
                selectinload(KTRoster.kill_team).selectinload(KillTeam.rules),  # type: ignore[arg-type]
                selectinload(KTRoster.kill_team).selectinload(KillTeam.ploys),  # type: ignore[arg-type]
                selectinload(KTRoster.kill_team).selectinload(KillTeam.equipment),  # type: ignore[arg-type]
                selectinload(KTRoster.operatives)  # type: ignore[arg-type]
                .selectinload(KTRosterOperative.operative)
                .selectinload(KTOperative.weapons),
                selectinload(KTRoster.operatives)  # type: ignore[arg-type]
                .selectinload(KTRosterOperative.operative)
                .selectinload(KTOperative.abilities),
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
        roster = self.session.exec(statement).first()
        if roster is None:
            raise NotFoundError(f"roster {roster_id} not found")
        return roster

    def get_roster_shallow(self, roster_id: UUID) -> KTRoster:
        """The roster row alone — for an ownership check, not for a response.

        The router's dependency needs one fact ("does the caller own this?") where
        `get_roster` is decision #52's whole bundle: the team's faction, rules, ploys and
        equipment, plus every operative's weapons and abilities. Loading that to answer a
        yes/no made a 204 DELETE cost as much as a GET -- audit finding 12 -- and is what
        `KTGameService.get_game_shallow` exists to avoid repeating.

        It also made the `populate_existing` fix on `get_roster` expensive: re-loading a
        collection costs queries only because the dependency had loaded it in the first
        place.
        """
        return self._require_roster(roster_id)

    def list_rosters(self, user_id: UUID, limit: int = 50, offset: int = 0) -> list[KTRoster]:
        """A player's rosters, oldest first: a name, a kill team and its faction.

        `created_at` then `id`, the way armies are listed: the id breaks a timestamp tie
        so paging is stable.

        Deliberately NOT the operatives, and so not their datacards either. A listing
        answers "which rosters do I have?", and a roster's detail read is 24-42 KB
        (decision #52) -- a page of fifty of those is megabytes to answer a question the
        name and the team already answer. The team's faction comes along because that is
        how a team is named to a reader ("Raveners, Tyranids"), and one more query for
        the whole page is cheaper than the client resolving it.
        """
        statement = (
            select(KTRoster)
            .where(KTRoster.owner_user_id == user_id)
            .options(
                selectinload(KTRoster.kill_team).selectinload(KillTeam.faction),  # type: ignore[arg-type]
            )
            .order_by(KTRoster.created_at, KTRoster.id)
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.exec(statement).all())

    def count_rosters(self, user_id: UUID) -> int:
        return self.session.exec(
            select(func.count(KTRoster.id)).where(KTRoster.owner_user_id == user_id)
        ).one()

    def update_roster(self, roster_id: UUID, **fields) -> KTRoster:
        roster = self._require_roster(roster_id)
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise KTRosterValidationError("fields", f"cannot update {sorted(unknown)}")
        # A PATCH that explicitly sends null for a NOT NULL column survives
        # `exclude_unset` and would reach the database as an IntegrityError. Rejected
        # here as a clean 400 instead.
        for field in sorted(fields):
            if field in self._NOT_NULLABLE and fields[field] is None:
                raise KTRosterValidationError(field, "cannot be null")
        if "name" in fields:
            self._require_name_free(roster.owner_user_id, fields["name"], except_id=roster.id)

        for key, value in fields.items():
            setattr(roster, key, value)
        self.session.add(roster)
        self.session.flush()
        self.session.refresh(roster)
        return roster

    def delete_roster(self, roster_id: UUID) -> None:
        """Delete a roster and the rows on it. Catalog operatives are untouched.

        Refused while a GAME was played from it (decision #60), because a battle keeps
        its roster link so a screen can always say which list it was played from.
        `fk_kt_game_roster` carries no `ondelete`, so the database refuses this anyway --
        the check is here so the caller learns WHY. Without it the generic
        `IntegrityError` backstop answers "conflict with an existing resource", which a
        client cannot tell from any other 409.

        The same shape as `UnitService.delete_unit`, which refuses a catalog unit an army
        or an inventory references, and which #60 cited as the house pattern.
        """
        roster = self._require_roster(roster_id)
        played = self.session.exec(select(func.count(KTGame.id)).where(KTGame.roster_id == roster_id)).one()
        if played:
            raise ConflictError(
                f"roster {roster.name!r} is played by "
                f"{played} game{'s' if played != 1 else ''} and cannot be deleted"
            )
        self.session.delete(roster)  # `cascade_delete` takes its operatives
        self.session.flush()

    # ------------------------ operatives on a roster ------------------------

    def add_operative(self, roster_id: UUID, operative_id: UUID, owner_user_id: UUID) -> KTRosterOperative:
        """Field one more operative -- an APPEND, not a create-or-409.

        A roster row is an individual, not a count (decision #50), so fielding the same
        datacard twice is two rows and a repeat is legitimate. That is the one place this
        deliberately diverges from `ArmyService.add_unit`, which is create-only because
        an incrementing add is not retry-safe: there is no quantity to increment here, so
        a retried add appends a second operative, which is a real outcome rather than a
        double-applied one. A caller that must not double-add removes the extra row by id.

        The new row goes last. `position` is the PLAYER's order, so it is theirs to
        rearrange with `move_operative`.

        `owner_user_id` is the CALLER's, taken from the authenticated user rather than
        copied off the roster this method just loaded. That is the whole point of the
        column: copying it from the roster would make the composite foreign key vacuous
        -- consistent by construction, never able to fire -- where writing the caller's
        makes the database refuse the insert unless the caller really owns that roster.
        The router's `get_owned_roster` 404s first, so this is a backstop; it is there
        so a route that forgets the check cannot write the row regardless.
        """
        roster = self._require_roster(roster_id)
        operative = self.session.get(KTOperative, operative_id)
        if operative is None:
            raise NotFoundError(f"operative {operative_id} not found")
        # Referential, not legal: an operative belongs to exactly one kill team, and the
        # composite foreign keys would refuse this row anyway. Checked here so the caller
        # gets a 404 naming the problem rather than an IntegrityError.
        if operative.kill_team_id != roster.kill_team_id:
            raise NotFoundError(f"operative {operative_id} is not one of kill team {roster.kill_team_id}'s")
        # Refused if the catalog has WITHDRAWN it (#55): the source no longer names this
        # row, so a picker that offered it was stale. Rows already on a roster or in a
        # game are untouched and keep resolving -- that is the whole reason #55 flags
        # rather than deletes. Referential, not legal, like the cross-team check beside it.
        if operative.withdrawn:
            raise NotFoundError(f"operative {operative_id} is no longer in the catalog")

        row = KTRosterOperative(
            roster_id=roster.id,
            operative_id=operative_id,
            # Carried so both composite foreign keys reach their parents through it.
            kill_team_id=roster.kill_team_id,
            # The caller's, NOT `roster.owner_user_id` -- see the note above.
            owner_user_id=owner_user_id,
            position=self._next_position(roster.id),
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return row

    def move_operative(self, roster_id: UUID, row_id: UUID, position: int) -> KTRosterOperative:
        """Set one row's position. Absolute, not a delta, so a retry is harmless.

        Addressed by the ROW's id rather than the operative's, because two rows may name
        the same operative (decision #50).

        **Shifts nothing.** `position` is a sort hint rather than an index (decision
        #53), so moving the third row to 0 leaves two rows at 0 and the gap it left
        behind. Renumbering the roster instead would make `position` the player's exact
        order at the cost of an UPDATE across every row per move; ties are cheaper and
        every reader breaks them with `id`, which is why both this service's reader and
        the `KTRoster.operatives` relationship order by `(position, id)`.
        """
        if position < 0:
            raise KTRosterValidationError("position", "cannot be negative")
        row = self._require_row(roster_id, row_id)
        row.position = position
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return row

    def remove_operative(self, roster_id: UUID, row_id: UUID) -> None:
        """Take one row off the roster. By row id, for the same reason as above."""
        self.session.delete(self._require_row(roster_id, row_id))
        self.session.flush()

    def list_roster_operatives(self, roster_id: UUID) -> list[KTRosterOperative]:
        """The roster's rows in the player's order, with the operative each names.

        **No route reaches this, deliberately.** A roster detail already carries its
        operatives (decision #52), and a subset route would cost API surface no client
        has asked for -- `add_operative` and `move_operative` both return the affected
        row, so a client never has to re-read to stay in step.

        It is here for K5: creating a game copies these rows, one game record per roster
        row (decision #22), and wants exactly this shape -- the rows in `(position, id)`
        order with the operative each names eager-loaded -- rather than the detail
        read's bundle of rules, ploys and two equipment lists.
        """
        self._require_roster(roster_id)
        statement = (
            select(KTRosterOperative)
            .where(KTRosterOperative.roster_id == roster_id)
            .options(selectinload(KTRosterOperative.operative))  # type: ignore[arg-type]
            .order_by(KTRosterOperative.position, KTRosterOperative.id)
        )
        return list(self.session.exec(statement).all())

    # ------------------------------- helpers -------------------------------

    def _require_roster(self, roster_id: UUID) -> KTRoster:
        roster = self.session.get(KTRoster, roster_id)
        if roster is None:
            raise NotFoundError(f"roster {roster_id} not found")
        return roster

    def _require_row(self, roster_id: UUID, row_id: UUID) -> KTRosterOperative:
        """One row, and only if it is on THIS roster.

        Scoped to the roster so a row id from someone else's roster reads as missing
        rather than as something the caller may touch.
        """
        row = self.session.get(KTRosterOperative, row_id)
        if row is None or row.roster_id != roster_id:
            raise NotFoundError(f"roster entry {row_id} is not on roster {roster_id}")
        return row

    def _require_name_free(self, user_id: UUID, name: str, except_id: UUID | None = None) -> None:
        """One name per player, so a list of rosters is readable.

        Checked here for the MESSAGE and constrained by `uq_kt_roster_owner_name` for the
        guarantee. This used to be a check alone, on the reasoning that two players may
        both call a roster "Raveners" so the schema has nothing to say about it -- which
        is right about a GLOBAL unique and wrong about a scoped one. Without the
        constraint, two concurrent creates both run this SELECT, both find nothing and
        both insert, because Postgres reads committed rows only.

        So the check is not load-bearing for correctness; it is load-bearing for the
        error. It answers 409 naming `name`, where the bare constraint would reach the
        generic `IntegrityError` backstop and say "conflict with an existing resource".
        The same split as `add_equipment` and `KTGameEvent.sequence`.
        """
        statement = select(KTRoster.id).where(KTRoster.owner_user_id == user_id, KTRoster.name == name)
        if except_id is not None:
            statement = statement.where(KTRoster.id != except_id)
        if self.session.exec(statement).first() is not None:
            raise ConflictError(f"a roster called {name!r} already exists", field="name")

    def _next_position(self, roster_id: UUID) -> int:
        """One past the roster's last row, so an add appends."""
        highest = self.session.exec(
            select(func.max(KTRosterOperative.position)).where(KTRosterOperative.roster_id == roster_id)
        ).one()
        return 0 if highest is None else highest + 1
