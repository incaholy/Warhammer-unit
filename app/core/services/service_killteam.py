"""KillTeamService — the Kill Team catalog, read-only.

Session-injected, like the other services. It raises `NotFoundError` and nothing
else: the catalog is reference data with no write route at all (KILLTEAM.md
decision #21), so there is no input to validate and no uniqueness to clash with.
An admin edit would be undone by the next `make seed-kt`, which rewrites any row
whose payload differs.

Reads only, and deliberately no legality anywhere: composition is DESCRIPTION
(decision #28). This service hands back rows; whether a roster satisfies them is
K4's report, never this layer's refusal.
"""

from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.db.columns import json_list_contains
from app.core.db.models_killteam import (
    KillTeam,
    KTEquipment,
    KTFaction,
    KTOperative,
    KTPloy,
    KTSelectionRule,
)
from app.core.services.errors import NotFoundError


class KillTeamService:
    """Factions, the kill teams under them, and one team's whole datacard set."""

    def __init__(self, session: Session):
        self.session = session

    # --- factions ---------------------------------------------------------

    def list_kt_factions(self, limit: int = 50, offset: int = 0) -> list[KTFaction]:
        """The Kill Team faction list, alphabetically.

        Alphabetical because that is the only order there is: a faction is a way to
        FIND a team (decision #12) and carries no `position`. Unlike the 40k
        catalog's factions these are not an enum -- they come from the site's own
        nav, so the rows are the whole truth and there is no constant to publish.
        """
        statement = select(KTFaction).order_by(KTFaction.name, KTFaction.id).offset(offset).limit(limit)
        return list(self.session.exec(statement).all())

    def count_kt_factions(self) -> int:
        return self.session.exec(select(func.count(KTFaction.id))).one()

    # --- kill teams -------------------------------------------------------

    def list_kill_teams(
        self, faction_id: UUID | None = None, limit: int = 50, offset: int = 0
    ) -> list[KillTeam]:
        """Kill teams, alphabetically, optionally narrowed to one faction.

        `KillTeam` carries no `position` either: decision #25 gives print order to
        every CHILD of a team, not to the teams themselves, so the site's nav order
        is not stored and alphabetical is the only ordering available.

        The faction is eager-loaded because a listing names it -- "Raveners
        (Tyranids)" -- and a lazy load would be one query per row.
        """
        statement = select(KillTeam).options(selectinload(KillTeam.faction))  # type: ignore[arg-type]
        if faction_id is not None:
            statement = statement.where(KillTeam.faction_id == faction_id)
        statement = statement.order_by(KillTeam.name, KillTeam.id).offset(offset).limit(limit)
        return list(self.session.exec(statement).all())

    def count_kill_teams(self, faction_id: UUID | None = None) -> int:
        statement = select(func.count(KillTeam.id))
        if faction_id is not None:
            statement = statement.where(KillTeam.faction_id == faction_id)
        return self.session.exec(statement).one()

    # --- one team, whole ---------------------------------------------------

    def get_kill_team(self, kill_team_id: UUID) -> KillTeam:
        """One kill team with everything a reader of its page would see.

        Eager-loaded in a FLAT number of queries -- nine, whatever the team's size: the
        team and its faction, one query per collection, and one each for the two nested
        levels (an operative's weapons and abilities). Lazy loading would be a query per
        operative per collection.

        `selectinload` rather than `joinedload` throughout, including the
        many-to-one faction: a join would multiply the team row by every collection
        row and make the result set the product of nine independent lists.

        Nothing is sorted here. Every relationship declares its own `order_by`
        (decision #25), so print order arrives with the rows -- and `keyword_caps`
        orders by keyword, since a cap is read out of a sentence and has no printed
        position of its own.

        `selection_rules` is the composition as the page prints it -- text with an indent
        depth, nothing derived (decision #28). Read in `position` order, indenting by
        `depth`, and it is the page's composition section back.
        """
        statement = (
            select(KillTeam)
            .where(KillTeam.id == kill_team_id)
            .options(
                selectinload(KillTeam.faction),  # type: ignore[arg-type]
                selectinload(KillTeam.rules),  # type: ignore[arg-type]
                selectinload(KillTeam.ploys),  # type: ignore[arg-type]
                selectinload(KillTeam.equipment),  # type: ignore[arg-type]
                selectinload(KillTeam.selection_rules),  # type: ignore[arg-type]
                selectinload(KillTeam.operatives).selectinload(KTOperative.weapons),  # type: ignore[arg-type]
                selectinload(KillTeam.operatives).selectinload(KTOperative.abilities),  # type: ignore[arg-type]
            )
        )
        team = self.session.exec(statement).first()
        if team is None:
            raise NotFoundError(f"kill team {kill_team_id} not found")
        return team

    # --- the composition on its own ---------------------------------------

    def list_selection_rules(self, kill_team_id: UUID) -> list[KTSelectionRule]:
        """A team's composition, without its datacards.

        The team detail already carries these (decision #47), so this exists for a reader
        that wants the printed rules and nothing else -- a roster view beside its picker.
        It saves little on its own: composition is 2-29% of a detail response, median 10%,
        where the operatives and their datacards are 27-71%.

        Raises `NotFoundError` for an unknown team rather than returning an empty list,
        because "this team has no composition" and "there is no such team" are different
        answers and only one of them is a 404.
        """
        if self.session.get(KillTeam, kill_team_id) is None:
            raise NotFoundError(f"kill team {kill_team_id} not found")
        statement = (
            select(KTSelectionRule)
            .where(KTSelectionRule.kill_team_id == kill_team_id)
            .order_by(KTSelectionRule.position)
        )
        return list(self.session.exec(statement).all())

    # --- operatives across teams ------------------------------------------

    def list_operatives(
        self,
        kill_team_id: UUID | None = None,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[KTOperative]:
        """Operatives, optionally narrowed to one team or one keyword.

        The cross-team view the nested form cannot give: "every operative with LEADER",
        "compare these two teams' Warriors". Ordered by team then printed position, so a
        page of them still arrives in an order a reader recognises.

        Weapons and abilities are eager-loaded, because an operative without its datacard
        is not much of an answer and lazily it would be two queries per row.
        """
        statement = select(KTOperative).options(
            selectinload(KTOperative.weapons),  # type: ignore[arg-type]
            selectinload(KTOperative.abilities),  # type: ignore[arg-type]
        )
        statement = self._narrow_operatives(statement, kill_team_id, keyword)
        statement = statement.order_by(KTOperative.kill_team_id, KTOperative.position, KTOperative.id).offset(
            offset
        )
        return list(self.session.exec(statement.limit(limit)).all())

    def count_operatives(self, kill_team_id: UUID | None = None, keyword: str | None = None) -> int:
        statement = self._narrow_operatives(select(func.count(KTOperative.id)), kill_team_id, keyword)
        return self.session.exec(statement).one()

    def _narrow_operatives(self, statement, kill_team_id: UUID | None, keyword: str | None):
        """The two filters, shared so the listing and the count can never disagree."""
        if kill_team_id is not None:
            statement = statement.where(KTOperative.kill_team_id == kill_team_id)
        if keyword:
            # A keyword is stored upper case, as a datacard prints it, so the filter is
            # matched exactly rather than case-insensitively -- a substring match would
            # make `GUN` find `GUN SERVITOR`, which is a different keyword.
            statement = statement.where(
                json_list_contains(
                    KTOperative.__table__.c.keywords,
                    keyword.upper(),
                    dialect=self.session.get_bind().dialect.name,
                )
            )
        return statement

    # --- the rows that belong to no team ----------------------------------

    def list_universal_ploys(self) -> list[KTPloy]:
        """The ploys every kill team may use: `kill_team_id` IS NULL.

        One row rather than a copy per team (Command Re-roll from the core rules), so
        a team's ploy list reads as "its own, plus these". Unpaginated: there is one.
        """
        statement = (
            select(KTPloy)
            .where(KTPloy.kill_team_id.is_(None))  # type: ignore[union-attr]
            .order_by(KTPloy.position, KTPloy.name)
        )
        return list(self.session.exec(statement).all())

    def list_universal_equipment(self) -> list[KTEquipment]:
        """The universal equipment list, which belongs to no team either.

        Equipment is chosen per GAME rather than per roster (decision #17), so this
        is reference data a game screen reads, not something a roster holds.
        """
        statement = (
            select(KTEquipment)
            .where(KTEquipment.kill_team_id.is_(None))  # type: ignore[union-attr]
            .order_by(KTEquipment.position, KTEquipment.name)
        )
        return list(self.session.exec(statement).all())
