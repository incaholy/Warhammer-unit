"""SQLModel tables for the Kill Team catalog (KILLTEAM.md → "Catalog").

Kept apart from `models.py` on purpose: Kill Team and the 40k army list builder are
separate games (KILLTEAM.md decision #11), and the only thing shared with the 40k
tables is `TimestampMixin`. Every table name carries a `kt_` prefix so the two sets
sort together and can never collide (`kt_factions` vs `factions`).

Built in slices (ROADMAP K1). This module currently holds the top of the tree:

    KTFaction → KillTeam → KillTeamRule

The columns are provisional until `fire-team` merges; the scraped pages (K2) can
still change them.

Registering: nothing imports this module by accident, so anything that needs the
tables on `SQLModel.metadata` imports it explicitly — `alembic/env.py` for
autogenerate, `tests/conftest.py` for the test schema.
"""

from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, Relationship

from app.core.db.models import TimestampMixin


class KTFaction(TimestampMixin, table=True):
    """A Kill Team faction, e.g. "Tyranids" (KILLTEAM.md decision #12).

    Flat — one level, no Imperium / Chaos / Xenos above it — and deliberately not
    linked to the 40k `Faction` / `Subfaction` tables, which place the army on
    different levels depending on who it is. Only used to *find* a kill team; rules
    never hang off a faction (see `KillTeamRule`).
    """

    __tablename__ = "kt_factions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=128)

    # No cascade: a faction is a grouping, and deleting one by accident must not take
    # every kill team in it along. `passive_deletes="all"` stops the ORM from trying
    # to null out the children's `faction_id`, so the database's refusal (the FK has
    # no ON DELETE action) is what the caller sees.
    kill_teams: list["KillTeam"] = Relationship(back_populates="faction", passive_deletes="all")


class KillTeam(TimestampMixin, table=True):
    """A kill team, e.g. Raveners — the root everything else in the catalog hangs off."""

    __tablename__ = "kt_kill_teams"
    __table_args__ = (CheckConstraint("operative_count >= 1", name="ck_kt_kill_team_operative_count"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=128)
    faction_id: UUID = Field(foreign_key="kt_factions.id", index=True)

    # The set number of operatives a roster fields (Raveners: 5 — 1 Prime + 4 others).
    # A roster over *or* under this is reported by validate (KILLTEAM.md → "Roster
    # limits"); it's data, not code per team.
    operative_count: int

    faction: KTFaction = Relationship(back_populates="kill_teams")
    # A rule only exists as part of its kill team, so it goes with it: the FK
    # cascades in the database, and `cascade_delete` makes the ORM do the same when
    # the kill team is deleted through a session.
    rules: list["KillTeamRule"] = Relationship(back_populates="kill_team", cascade_delete=True)


class KillTeamRule(TimestampMixin, table=True):
    """A team-wide rule (Raveners: Burrow, Tunnel, Predatory Instincts).

    Belongs to the kill team, not the faction (KILLTEAM.md decision #13): two teams
    in the same faction can have different rules, and the same rule *name* can
    appear on two teams — hence uniqueness per team, not globally.
    """

    __tablename__ = "kt_kill_team_rules"
    __table_args__ = (UniqueConstraint("kill_team_id", "name"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kill_team_id: UUID = Field(foreign_key="kt_kill_teams.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=128)
    # Same name as the 40k `Ability.description`, for the same kind of text.
    description: str

    kill_team: KillTeam = Relationship(back_populates="rules")
