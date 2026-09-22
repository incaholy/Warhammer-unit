"""SQLModel tables for the Kill Team catalog (KILLTEAM.md → "Catalog").

Kept apart from `models.py` on purpose: Kill Team and the 40k army list builder are
separate games (KILLTEAM.md decision #11), and the only thing shared with the 40k
tables is `TimestampMixin`. Every table name carries a `kt_` prefix so the two sets
sort together and can never collide (`kt_factions` vs `factions`).

Built in slices (ROADMAP K1). This module currently holds:

    KTFaction → KillTeam → KillTeamRule
                        ├→ KTOperative → KTWeapon
                        │             └→ KTAbility
                        ├→ KTPloy     (kill_team_id NULL = every team can use it)
                        └→ KTEquipment (same: NULL = the universal list)

The columns are provisional until `fire-team` merges; the scraped pages (K2) can
still change them.

Registering: nothing imports this module by accident, so anything that needs the
tables on `SQLModel.metadata` imports it explicitly — `alembic/env.py` for
autogenerate, `tests/conftest.py` for the test schema.
"""

from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, Index, UniqueConstraint, text
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
    # Cascades two levels: an operative's weapons and abilities go with it.
    operatives: list["KTOperative"] = Relationship(back_populates="kill_team", cascade_delete=True)
    # A team's own ploys only. The universal ones (kill_team_id NULL) belong to no
    # team, so they are not in this list and are not deleted with one.
    ploys: list["KTPloy"] = Relationship(back_populates="kill_team", cascade_delete=True)
    # Its own equipment only; the universal list (kill_team_id NULL) is nobody's.
    equipment: list["KTEquipment"] = Relationship(back_populates="kill_team", cascade_delete=True)


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


class KTOperative(TimestampMixin, table=True):
    """One operative on a kill team's roster list, with its datacard stats.

    The two roster limits live here as data, not as code per team (KILLTEAM.md →
    "Roster limits"): `required` marks an operative a roster cannot omit (Raveners'
    Prime), and `max_per_roster` caps how many may be taken -- 1 for each specialist,
    NULL for one that can be taken freely (Warriors), still bounded by the kill
    team's `operative_count`.
    """

    __tablename__ = "kt_operatives"
    __table_args__ = (
        UniqueConstraint("kill_team_id", "name"),
        CheckConstraint("apl >= 1", name="ck_kt_operative_apl"),
        CheckConstraint("move >= 0 AND save >= 0 AND wounds >= 0", name="ck_kt_operative_stats_non_negative"),
        # NULL means "no limit"; a limit of 0 would mean "cannot be taken", which is
        # what leaving the operative off the list already says.
        CheckConstraint(
            "max_per_roster IS NULL OR max_per_roster >= 1", name="ck_kt_operative_max_per_roster"
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kill_team_id: UUID = Field(foreign_key="kt_kill_teams.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=128, index=True)

    # The 2024 stat line: APL, Move, Save, Wounds. `save = 3` means 3+, the same
    # convention as the 40k `Unit.armor_save`.
    apl: int
    move: int
    save: int
    wounds: int

    keywords: list[str] = Field(default_factory=list, sa_type=JSON, nullable=False)

    required: bool = Field(default=False)
    max_per_roster: int | None = Field(default=None)

    kill_team: KillTeam = Relationship(back_populates="operatives")
    weapons: list["KTWeapon"] = Relationship(back_populates="operative", cascade_delete=True)
    abilities: list["KTAbility"] = Relationship(back_populates="operative", cascade_delete=True)


def _range_for_category(context) -> int:
    """The default `range` for a weapon whose caller gave none.

    A context-sensitive column default: one column, a value that depends on this
    row's `category`. SQL's `DEFAULT` takes a single value and cannot look at another
    column, and a SQLModel `model_validator` is not an option either -- table models
    skip validation, so a validator never runs. This does run, on every ORM and Core
    insert, and lives in the models file where the schema is documented.
    """
    params = context.get_current_parameters()
    return 1 if params.get("category") == "melee" else 2


class KTWeapon(TimestampMixin, table=True):
    """One weapon profile on an operative's datacard.

    Owned by that operative rather than shared through a link table (decision #14):
    a datacard lists its own profiles, and two operatives' weapons of the same name
    can differ.

    A 2024 profile is ATK / HIT / DMG / WR, where DMG is split into normal and
    critical. Damage is stored as the two integers the page prints, not as its "4/5"
    string, because the roster and the game tracker compare and sum them.
    """

    __tablename__ = "kt_weapons"
    __table_args__ = (
        UniqueConstraint("operative_id", "name"),
        # The same two values as the 40k `Weapon.category`, so one vocabulary covers
        # both games.
        CheckConstraint("category IN ('range', 'melee')", name="ck_kt_weapon_category"),
        # A default only fires when the column is omitted, so this is what holds an
        # explicit 0 from a future parser out.
        CheckConstraint("range >= 1", name="ck_kt_weapon_range"),
        CheckConstraint(
            "attacks >= 0 AND hit >= 0 AND normal_damage >= 0 AND crit_damage >= 0",
            name="ck_kt_weapon_stats_non_negative",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    operative_id: UUID = Field(foreign_key="kt_operatives.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=128)
    category: str = Field(max_length=8)  # "range" | "melee"

    # Decision #15: the number from a printed `Range x` rule when the page states
    # one, otherwise 1 for melee and 2 for range. NULL-typed in Python because the
    # value is assigned at insert; the column itself is NOT NULL, with a
    # `server_default` covering a raw-SQL insert that names no range.
    range: int | None = Field(
        default=None,
        nullable=False,
        sa_column_kwargs={"default": _range_for_category, "server_default": text("1")},
    )

    attacks: int
    hit: int  # "3+" is stored as 3, like `save`
    normal_damage: int
    crit_damage: int

    # "Rending", "Silent", "Range 3", "Piercing 1" -- names with their parameters, as
    # printed. `Range x` is also lifted into `range` above; it stays here because the
    # rules list is what the datacard shows.
    weapon_rules: list[str] = Field(default_factory=list, sa_type=JSON, nullable=False)

    operative: KTOperative = Relationship(back_populates="weapons")


class KTAbility(TimestampMixin, table=True):
    """An operative's ability or unique action (Raveners: Toxic Lunge, Burrow).

    One table for both: they read the same way on the datacard (a name and its text),
    and nothing in the tracker needs to tell them apart -- the players do.
    """

    __tablename__ = "kt_abilities"
    __table_args__ = (UniqueConstraint("operative_id", "name"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    operative_id: UUID = Field(foreign_key="kt_operatives.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=128)
    description: str

    operative: KTOperative = Relationship(back_populates="abilities")


class KTPloy(TimestampMixin, table=True):
    """A ploy: a CP-priced option a player may use during a game.

    Two kinds, as the datacard pages divide them: `strategy` (played in the Strategy
    phase) and `firefight` (played during activations).

    Almost every ploy belongs to one kill team, so `kill_team_id` is a plain FK --
    except that **Command Re-roll is usable by every kill team**. That is stored as a
    NULL `kill_team_id` rather than copied onto each team, so there is one row to
    correct and a team's ploy list is "its own, plus the universal ones".

    NULL costs one constraint: Postgres treats two NULLs as distinct, so
    `UniqueConstraint(kill_team_id, name)` would happily take a second
    "Command Re-roll". The partial index below closes that -- unique on `name` among
    the rows that have no kill team.
    """

    __tablename__ = "kt_ploys"
    __table_args__ = (
        UniqueConstraint("kill_team_id", "name"),
        Index(
            "uq_kt_ploy_universal_name",
            "name",
            unique=True,
            sqlite_where=text("kill_team_id IS NULL"),
            postgresql_where=text("kill_team_id IS NULL"),
        ),
        CheckConstraint("kind IN ('strategy', 'firefight')", name="ck_kt_ploy_kind"),
        CheckConstraint("cp_cost >= 0", name="ck_kt_ploy_cp_cost"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # NULL = every kill team may use it (Command Re-roll). A team's own ploys go
    # with the team; the universal rows are not anyone's to delete.
    kill_team_id: UUID | None = Field(
        default=None, foreign_key="kt_kill_teams.id", ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=128)
    kind: str = Field(max_length=16)  # "strategy" | "firefight"
    # The datacard pages do not print costs, so this is what a ploy costs unless the
    # source says otherwise (Command Re-roll is 1 CP).
    cp_cost: int = Field(default=1)
    description: str

    kill_team: KillTeam | None = Relationship(back_populates="ploys")


class KTEquipment(TimestampMixin, table=True):
    """A piece of equipment a player may select for a game.

    Same ownership shape as `KTPloy`: a team's own equipment carries its
    `kill_team_id`, and the universal list -- available to every kill team -- is
    stored once with NULL, guarded by a partial unique index because two NULLs are
    distinct to the database.

    No cost column: equipment is selected from a list up to an allowance rather than
    bought. The allowance, and the rule that an option cannot be taken twice in one
    game, belong to the roster (KILLTEAM.md → "Equipment"), not to the catalog entry.

    Equipment whose effect is a weapon profile keeps that profile in `description`
    for now; `KTWeapon` belongs to an operative. See KILLTEAM.md for the upgrade path
    if the saved pages show profiles worth structuring.
    """

    __tablename__ = "kt_equipment"
    __table_args__ = (
        UniqueConstraint("kill_team_id", "name"),
        Index(
            "uq_kt_equipment_universal_name",
            "name",
            unique=True,
            sqlite_where=text("kill_team_id IS NULL"),
            postgresql_where=text("kill_team_id IS NULL"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # NULL = the universal list, which belongs to no team and is not deleted with one.
    kill_team_id: UUID | None = Field(
        default=None, foreign_key="kt_kill_teams.id", ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=128)
    description: str

    kill_team: KillTeam | None = Relationship(back_populates="equipment")
