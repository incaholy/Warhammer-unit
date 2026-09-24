"""SQLModel tables for the Kill Team catalog (KILLTEAM.md → "Catalog").

Kept apart from `models.py` on purpose: Kill Team and the 40k army list builder are
separate games (KILLTEAM.md decision #11), and the only thing shared with the 40k
tables is `TimestampMixin`. Every table name carries a `kt_` prefix so the two sets
sort together and can never collide (`kt_factions` vs `factions`).

Built in slices (ROADMAP K1). This module currently holds:

    KTFaction → KillTeam → KillTeamRule
                        ├→ KTOperative → KTWeapon
                        │             └→ KTAbility
                        ├→ KTPloy       (kill_team_id NULL = every team can use it)
                        ├→ KTEquipment  (same: NULL = the universal list)
                        ├→ KTSelectionList → KTSelectionOption → KTOperative
                        └→ KTSelectionRestriction  (team-wide keyword caps)

The columns are provisional until `fire-team` merges; the scraped pages (K2) can
still change them.

Registering: nothing imports this module by accident, so anything that needs the
tables on `SQLModel.metadata` imports it explicitly — `alembic/env.py` for
autogenerate, `tests/conftest.py` for the test schema.
"""

from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
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

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=128)
    faction_id: UUID = Field(foreign_key="kt_factions.id", index=True)

    # No `operative_count`: a page states its composition as budgeted LISTS, and with
    # weighted costs a headcount stops being a fact -- Brood Brother spends 4
    # selections and can field more models than that. "Is this roster legal?" is
    # answered per list (decision #16), not by counting rows.

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
    selection_lists: list["KTSelectionList"] = Relationship(back_populates="kill_team", cascade_delete=True)
    # Team-wide, not per list: see KTSelectionRestriction.
    keyword_caps: list["KTSelectionRestriction"] = Relationship(
        back_populates="kill_team", cascade_delete=True
    )


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

    Whether a roster may take it, and how many times, is NOT here: that is stated by
    the selection list offering it, and two lists can offer the same operative on
    different terms. `KTSelectionOption` carries it (KILLTEAM.md decision #16).
    """

    __tablename__ = "kt_operatives"
    __table_args__ = (
        UniqueConstraint("kill_team_id", "name"),
        # Redundant to the primary key, and there for `KTSelectionOption`'s composite
        # foreign key to point at: a target must be provably unique.
        UniqueConstraint("kill_team_id", "id", name="uq_kt_operative_team_id"),
        CheckConstraint("apl >= 1", name="ck_kt_operative_apl"),
        CheckConstraint("move >= 0 AND save >= 0 AND wounds >= 0", name="ck_kt_operative_stats_non_negative"),
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

    kill_team: KillTeam = Relationship(back_populates="operatives")
    weapons: list["KTWeapon"] = Relationship(back_populates="operative", cascade_delete=True)
    abilities: list["KTAbility"] = Relationship(back_populates="operative", cascade_delete=True)
    offered_by: list["KTSelectionOption"] = Relationship(back_populates="operative", cascade_delete=True)


def default_range(category: str | None) -> int:
    """The `range` a weapon profile gets when its page prints no `Range x` rule.

    Public because two callers need the same answer: the column default below, and the
    seed, which has to write this value EXPLICITLY when a source that used to print a
    range stops printing one. A column default only fires on INSERT, so a seed that
    merely omitted the field would leave the withdrawn number frozen in place.
    """
    return 1 if category == "melee" else 2


def _range_for_category(context) -> int:
    """`default_range` as a context-sensitive column default.

    One column, a value that depends on this row's `category`. SQL's `DEFAULT` takes a
    single value and cannot look at another column, and a SQLModel `model_validator` is
    not an option either -- table models skip validation, so a validator never runs.
    This does run, on every ORM and Core insert.
    """
    return default_range(context.get_current_parameters().get("category"))


class KTWeapon(TimestampMixin, table=True):
    """One weapon profile on an operative's datacard.

    Owned by that operative rather than shared through a link table (decision #14):
    a datacard lists its own profiles, and two operatives' weapons of the same name
    can differ.

    An operative may carry the same weapon NAME twice, once per category: a brazier
    that can be fired and swung is one weapon with two profiles.

    A 2024 profile is ATK / HIT / DMG / WR, where DMG is split into normal and
    critical. Damage is stored as the two integers the page prints, not as its "4/5"
    string, because the roster and the game tracker compare and sum them.
    """

    __tablename__ = "kt_weapons"
    __table_args__ = (
        # Per CATEGORY, not per operative: one weapon can print a ranged and a melee
        # profile under one name. Sanctifiers' Missionary carries "Brazier of holy fire"
        # both ways -- ranged with Saturate and Torrent, melee with Shock -- and it is
        # the only such case across the 48 teams, which is exactly the kind of single
        # exception a unique constraint turns into a failed seed.
        UniqueConstraint("operative_id", "name", "category"),
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


DEFAULT_PLOY_CP_COST = 1
"""What a ploy costs when its page prints no cost. Shared with the seed, as above."""


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
    # Most pages print no cost, so this is what a ploy costs unless the source says
    # otherwise -- the core rules print Command Re-roll's, and Blades of Khaine prints
    # its four firefight ploys' inline. As with `KTWeapon.range`, the seed writes this
    # value explicitly rather than relying on the default, so a cost the source stops
    # printing reverts instead of sticking.
    cp_cost: int = Field(default=DEFAULT_PLOY_CP_COST)
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


class KTSelectionList(TimestampMixin, table=True):
    """One of the lists a roster is built from (KILLTEAM.md decision #16).

    A page states its composition as budgeted lists rather than as a headcount with a
    leader: "1 RAVENER PRIME operative", then "4 RAVENER operatives selected from the
    following list". Each is a list with a `budget` -- how many selections may be
    spent on it -- and its options.

    This covers the shapes a flag could not. A budget of 1 over a single option IS
    "required", so nothing needs marking as such; a budget of 1 over several options is
    "choose one of these"; and an option costing two selections (Brood Brother's Magus)
    spends the budget without another column anywhere else.

    `restriction_text` keeps the sentence printed beside the list -- the one the caps
    were read from ("Other than WARRIOR operatives, your kill team can only include
    each operative on this list once"). The structured columns drive validation; the
    sentence is what lets a human check the parse read it correctly.
    """

    __tablename__ = "kt_selection_lists"
    __table_args__ = (
        UniqueConstraint("kill_team_id", "position"),
        # As above: the target of a composite foreign key from `KTSelectionOption`.
        UniqueConstraint("kill_team_id", "id", name="uq_kt_selection_list_team_id"),
        CheckConstraint("budget >= 1", name="ck_kt_selection_list_budget"),
        CheckConstraint("position >= 0", name="ck_kt_selection_list_position"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kill_team_id: UUID = Field(foreign_key="kt_kill_teams.id", ondelete="CASCADE", index=True)
    # As printed, so a roster builder can show the page's own wording.
    label: str = Field(max_length=256)
    # Selections this list may spend, NOT models: an option may cost more than one.
    budget: int
    position: int  # print order
    restriction_text: str | None = Field(default=None)

    kill_team: KillTeam = Relationship(back_populates="selection_lists")
    # `overlaps` is the price of the composite foreign keys below: this relationship
    # and `KTOperative.offered_by` both write an option's `kill_team_id`, which is
    # deliberate -- it is one column reached through two parents -- and SQLAlchemy wants
    # that stated rather than inferred.
    options: list["KTSelectionOption"] = Relationship(
        back_populates="selection_list",
        cascade_delete=True,
        sa_relationship_kwargs={"overlaps": "offered_by"},
    )


class KTSelectionOption(TimestampMixin, table=True):
    """One operative a list offers, and on what terms.

    `cost` is what taking it spends from the list's budget (Brood Brother's Magus
    counts as two selections). `models` is how many operatives one selection puts on
    the table -- "2 PSYCHIC FAMILIAR operatives (still counts as one selection)" is
    `cost=1, models=2`, a different axis from cost and so its own column.
    `max_selections` caps repeats, NULL meaning no limit beyond the budget: Raveners
    allow each specialist once and Warriors freely.

    `loadout_options` holds the weapon loadouts the page prints for this entry, and is
    **display only -- nothing validates it**. Wyrmblade offers "GUNNER with flamer and
    gun butt", "GUNNER with grenade launcher and gun butt" and "GUNNER with webber and
    gun butt": one operative with a weapon choice, so it is ONE option (12 of the 48
    teams repeat an operative like that) with its printed variants kept here. A list,
    not a string, because there is usually more than one.

    Which weapons a roster actually took is recorded when the roster is built (K4) and
    snapshotted into a game (K5), from the operative's own profiles. The catalog says
    what an operative CAN use; it deliberately does not encode which combinations are
    legal -- that is a rule, and decision #1 leaves rules to the players.
    """

    __tablename__ = "kt_selection_options"
    __table_args__ = (
        UniqueConstraint("selection_list_id", "operative_id"),
        # An option's list and its operative must belong to the SAME kill team. Two
        # plain foreign keys cannot say that -- each only promises "some list" and
        # "some operative" -- so the option carries `kill_team_id` and reaches both
        # parents THROUGH it. Offering another team's operative then fails in the
        # database rather than needing a service to remember to check.
        ForeignKeyConstraint(
            ["kill_team_id", "selection_list_id"],
            ["kt_selection_lists.kill_team_id", "kt_selection_lists.id"],
            ondelete="CASCADE",
            name="fk_kt_selection_option_list_same_team",
        ),
        ForeignKeyConstraint(
            ["kill_team_id", "operative_id"],
            ["kt_operatives.kill_team_id", "kt_operatives.id"],
            ondelete="CASCADE",
            name="fk_kt_selection_option_operative_same_team",
        ),
        CheckConstraint("cost >= 1", name="ck_kt_selection_option_cost"),
        CheckConstraint("models >= 1", name="ck_kt_selection_option_models"),
        CheckConstraint(
            "max_selections IS NULL OR max_selections >= 1",
            name="ck_kt_selection_option_max_selections",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Denormalised on purpose: it is what makes the two composite foreign keys above
    # possible, and they in turn keep it honest -- it cannot disagree with either
    # parent's team.
    kill_team_id: UUID = Field(index=True)
    selection_list_id: UUID = Field(index=True)
    operative_id: UUID = Field(index=True)
    cost: int = Field(default=1)
    models: int = Field(default=1)
    max_selections: int | None = Field(default=None)
    # Display only. See the class docstring: not validated, and not the record of what
    # a roster took.
    loadout_options: list[str] = Field(default_factory=list, sa_type=JSON, nullable=False)

    selection_list: KTSelectionList = Relationship(
        back_populates="options", sa_relationship_kwargs={"overlaps": "offered_by"}
    )
    operative: KTOperative = Relationship(
        back_populates="offered_by", sa_relationship_kwargs={"overlaps": "options,selection_list"}
    )


class KTSelectionRestriction(TimestampMixin, table=True):
    """A cap on how many operatives CARRYING A KEYWORD a kill team may include.

    Deathwatch: "your kill team can only include each operative on this list once, and
    can only include up to one GRAVIS operative." The first half is per-option
    (`KTSelectionOption.max_selections`); the second is a different shape, and
    `max_selections` cannot express it -- GRAVIS is carried by several entries, so
    capping each at one still allows two GRAVIS operatives.

    Not an exception: 13 of the 48 kill teams state a cap like this (Novitiates two
    PURGATUS, Hunter Clade one DIKTAT, Farstalker Kinband two HOUND, ...), which is
    what earns it a table rather than a sentence in `restriction_text`. Left
    unstructured, `validate` would quietly approve illegal rosters for a quarter of
    the teams.

    Evaluated against `KTOperative.keywords`, which the catalog already stores, so the
    rule needs nothing beyond the keyword and its limit. A team may carry several.

    Scoped to the KILL TEAM rather than a list, because that is what the sentence says:
    "your kill team can only include up to one GRAVIS operative", where the repeat clause
    beside it says "each operative on this list once". Brood Brother is the proof -- it
    caps BROODCOVEN, and the operatives carrying that keyword are offered by a different
    list than the one the sentence follows, so a list-scoped cap could never have applied.

    The keyword is the phrase as printed, matched by WORDS: Battleclade's datacards carry
    COMBAT and SERVITOR separately while its cap names "COMBAT SERVITOR", and Pathfinders
    carries "WEAPONS EXPERT" as one (`KeywordCap.matches` in the scraper).
    """

    __tablename__ = "kt_selection_restrictions"
    __table_args__ = (
        UniqueConstraint("kill_team_id", "keyword"),
        CheckConstraint("max_operatives >= 1", name="ck_kt_selection_restriction_max"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kill_team_id: UUID = Field(foreign_key="kt_kill_teams.id", ondelete="CASCADE", index=True)
    # Stored as keywords are: upper case, as printed on a datacard.
    keyword: str = Field(max_length=128, index=True)
    max_operatives: int

    kill_team: KillTeam = Relationship(back_populates="keyword_caps")
