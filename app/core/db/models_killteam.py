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
                        └→ KTSelectionRule  (the composition, as printed text)

    User → KTRoster → KTRosterOperative → KTOperative   (K4)

The columns are provisional until `fire-team` merges; the scraped pages (K2) can
still change them.

Registering: nothing imports this module by accident, so anything that needs the
tables on `SQLModel.metadata` imports it explicitly — `alembic/env.py` for
autogenerate, `tests/conftest.py` for the test schema.
"""

from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from app.core.db.models import TimestampMixin

# A list of short strings: an operative's keywords, a weapon's rules, an option's printed
# loadouts. JSONB on Postgres and plain JSON on SQLite (the test tier), because `json`
# cannot carry a GIN index -- so a "which operatives have this keyword?" filter would have
# no way to be indexed, and a keyword is exactly the sort of thing a catalog gets filtered
# by. Declared once and shared: the three columns hold the same shape for the same reason.
STRING_LIST = JSON().with_variant(JSONB(), "postgresql")


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
    # ONE convention, no per-table judgement (decision #25): every child of a kill team
    # carries `position` and is ordered by it, because the page's order is information
    # everywhere -- the leader is printed first, Strategy Ploys before Firefight Ploys, a
    # rule beside the rule that refers to it, and a team's chosen options grouped together.
    # Rows have no order of their own, so without this each list comes back in whatever the
    # storage gives, which held by luck until a `VACUUM FULL` or a rewritten row moved one.
    #
    # Since decision #44 there is no exception: a restriction sentence is a selection rule
    # with a printed position like everything else.
    rules: list["KillTeamRule"] = Relationship(
        back_populates="kill_team",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "KillTeamRule.position"},
    )
    # Cascades two levels: an operative's weapons and abilities go with it.
    operatives: list["KTOperative"] = Relationship(
        back_populates="kill_team",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "KTOperative.position"},
    )
    # A team's own ploys only. The universal ones (kill_team_id NULL) belong to no
    # team, so they are not in this list and are not deleted with one.
    ploys: list["KTPloy"] = Relationship(
        back_populates="kill_team",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "KTPloy.position"},
    )
    # Its own equipment only; the universal list (kill_team_id NULL) is nobody's.
    equipment: list["KTEquipment"] = Relationship(
        back_populates="kill_team",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "KTEquipment.position"},
    )
    # The composition as the page prints it: lines, restriction sentences and notes in
    # one ordered run (see KTSelectionRule). Text, not structure -- the catalog describes
    # composition and never enforces it (decision #28).
    selection_rules: list["KTSelectionRule"] = Relationship(
        back_populates="kill_team",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "KTSelectionRule.position"},
    )


class KillTeamRule(TimestampMixin, table=True):
    """A team-wide rule (Raveners: Burrow, Tunnel, Predatory Instincts).

    Belongs to the kill team, not the faction (KILLTEAM.md decision #13): two teams
    in the same faction can have different rules, and the same rule *name* can
    appear on two teams — hence uniqueness per team, not globally.
    """

    __tablename__ = "kt_kill_team_rules"
    __table_args__ = (
        UniqueConstraint("kill_team_id", "name"),
        CheckConstraint("position >= 0", name="ck_kt_kill_team_rule_position"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kill_team_id: UUID = Field(foreign_key="kt_kill_teams.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=128)
    # Same name as the 40k `Ability.description`, for the same kind of text.
    description: str
    # The page section a CHOSEN rule was printed under, NULL for an always-on faction rule
    # (decision #27). Blades of Khaine's 15 Aspect Techniques are grouped by Aspect and
    # Exodite Dragon Masters' 15 Upgrades by operative type, and the group is what says
    # which operatives may take which -- the name alone is not usable.
    group: str | None = Field(default=None, max_length=128, index=True)
    position: int = Field(default=0)  # print order (decision #25)

    kill_team: KillTeam = Relationship(back_populates="rules")


class KTOperative(TimestampMixin, table=True):
    """One operative on a kill team's roster list, with its datacard stats.

    Whether a roster may take it, and how many times, is NOT here -- and since decision
    #44 it is not anywhere in the catalog either. The composition states it in the page's
    own words (`KTSelectionRule`) and nothing derives a limit from them, which is what
    lets a custom game be built. A roster offers every operative of its kill team (K4).
    """

    __tablename__ = "kt_operatives"
    __table_args__ = (
        UniqueConstraint("kill_team_id", "name"),
        # Redundant to the primary key, and there for `KTRosterOperative`'s composite
        # foreign key to point at: a target must be provably unique. It was deleted with
        # the selection-options table (#44) and is back for the roster (#50's section),
        # which needs the same guarantee for the same reason.
        UniqueConstraint("kill_team_id", "id", name="uq_kt_operative_team_id"),
        CheckConstraint("apl >= 1", name="ck_kt_operative_apl"),
        CheckConstraint("move >= 0 AND save >= 0 AND wounds >= 0", name="ck_kt_operative_stats_non_negative"),
        CheckConstraint("position >= 0", name="ck_kt_operative_position"),
        # Decision #20's vocabulary, same style as a weapon's category and a ploy's kind.
        CheckConstraint("availability IN ('roster', 'in_battle')", name="ck_kt_operative_availability"),
        # What JSONB was chosen for (decision #26): "which operatives carry this keyword?"
        # is `keywords @> '["LEADER"]'`, and only a GIN index can serve that. Declared
        # once, so `alembic check` stays quiet and the migration carries it; on Postgres
        # it is a GIN index and on SQLite a plain b-tree over the JSON text, which that
        # backend will never use -- its half of `json_list_contains` scans with
        # `json_each`. Harmless there: 454 rows, and the test tier rebuilds the schema
        # per test anyway.
        Index("ix_kt_operative_keywords", "keywords", postgresql_using="gin"),
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

    keywords: list[str] = Field(default_factory=list, sa_type=STRING_LIST, nullable=False)
    # Print order (decision #25). A page prints the LEADER first, in 46 of the 48 teams a
    # different operative than the alphabetically first one, and which operative a team is
    # built around is information.
    position: int = Field(default=0)
    # Whether a ROSTER may take this operative (decision #20). `in_battle` means it arrives
    # during a game instead -- Gellerpox Infected's three Mutoid Vermin, which the MUTOID
    # VERMIN equipment adds "for the battle", so no selection list offers them and none
    # should. K5's "add an operative" screen is what needs this list.
    #
    # It does NOT mean "every operative no list offers". It is set only from a CONDITIONAL
    # composition block, and Gellerpox print the only one across the 48 pages. Chaos Cult's
    # Chaos Mutant and Chaos Torment are offered by no list either -- they are gained
    # mid-game through "Accursed Gifts" and "Mutation" -- and they stay `roster`, because
    # nothing on the page marks them structurally. So `availability == "roster"` is not a
    # test for rosterability: "is this operative offered by some selection list" is, and it
    # is a join, not a column.
    availability: str = Field(default="roster", max_length=16, index=True)

    kill_team: KillTeam = Relationship(back_populates="operatives")
    # In the order the card prints them (decision #25), which is the whole point of the
    # `position` column: a datacard read at the table has to look like the datacard.
    weapons: list["KTWeapon"] = Relationship(
        back_populates="operative",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "KTWeapon.position"},
    )
    abilities: list["KTAbility"] = Relationship(
        back_populates="operative",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "KTAbility.position"},
    )


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
        CheckConstraint("position >= 0", name="ck_kt_weapon_position"),
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
    weapon_rules: list[str] = Field(default_factory=list, sa_type=STRING_LIST, nullable=False)

    # Where the datacard prints this profile (decision #25). Rows have no inherent order,
    # and alphabetical is not a datacard: a card lists ranged profiles then melee, and the
    # same name can appear in both (Sanctifiers' brazier), so sorting by name interleaves
    # the two halves of one weapon. Deliberately NOT unique per operative -- the seed
    # rewrites positions in place when a page reorders its profiles, and a unique
    # constraint would collide with whichever row has not moved yet.
    position: int = Field(default=0)

    operative: KTOperative = Relationship(back_populates="weapons")


class KTAbility(TimestampMixin, table=True):
    """An operative's ability or unique action (Raveners: Toxic Lunge, Burrow).

    One table for both: they read the same way on the datacard (a name and its text),
    and nothing in the tracker needs to tell them apart -- the players do.
    """

    __tablename__ = "kt_abilities"
    __table_args__ = (
        UniqueConstraint("operative_id", "name"),
        CheckConstraint("position >= 0", name="ck_kt_ability_position"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    operative_id: UUID = Field(foreign_key="kt_operatives.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=128)
    description: str
    position: int = Field(default=0)  # print order, as on the card (decision #25)

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
        CheckConstraint("position >= 0", name="ck_kt_ploy_position"),
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
    # otherwise -- the core rules print Command Re-roll's, and Blades of Khaine and
    # Inquisitorial Agent each print their four firefight ploys' inline (8 of 384).
    # As with `KTWeapon.range`, the seed writes this value explicitly rather than relying
    # on the default, so a cost the source stops printing reverts instead of sticking.
    cp_cost: int = Field(default=DEFAULT_PLOY_CP_COST)
    description: str
    # Print order (decision #25). The pages print Strategy Ploys before Firefight Ploys,
    # which ordering by `kind` reverses -- "firefight" sorts before "strategy".
    position: int = Field(default=0)

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
        CheckConstraint("position >= 0", name="ck_kt_equipment_position"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # NULL = the universal list, which belongs to no team and is not deleted with one.
    kill_team_id: UUID | None = Field(
        default=None, foreign_key="kt_kill_teams.id", ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=128)
    description: str
    position: int = Field(default=0)  # print order (decision #25)

    kill_team: KillTeam | None = Relationship(back_populates="equipment")


class KTSelectionRule(TimestampMixin, table=True):
    """One printed thing from a kill team's composition, kept as TEXT.

    Replaces three tables -- selection lists, their options and the keyword caps --
    which held `budget`, `cost`, `models`, `shape`, `max_selections` and a cap per
    keyword. That was enforcement machinery with no enforcer: decision #28 already said
    the catalog DESCRIBES composition and never decides legality, and keeping the
    columns anyway is what produced every problem in that area. A budget that summed to
    43 selections for a team that fields 7. A cap on COMBAT SERVITOR matching none of
    the seven operatives that carry it, because the datacard prints the phrase as two
    keywords. Two conditional clauses stored at opposite strictnesses because a column
    had to pick one. Four rows that looked identical while meaning four different
    things. Text is exact where a column had to approximate, and a tracker that allows
    CUSTOM games should not imply a restriction it will not apply.

    So this is the page's own words, in the page's own order, and nothing derived. A
    roster offers every operative of its kill team (K4), and these rows are what a
    player reads beside it.

    `kind` says what the page printed:

      `heading`      an ally's name above a requisition group ("Death Korps")
      `line`         one printed bullet, at the `depth` the page indents it to
      `restriction`  the sentence under a tree ("Other than WARRIOR operatives, ...")
      `note`         a footnote body or a designer's-note callout (decision #30)

    `position` is the only ordering, and it spans the whole composition -- bullets,
    sentences and notes interleaved as printed -- so `UNIQUE(kill_team_id, position)`
    holds and the whole subtree is replaced rather than rewritten when a page changes.
    `text` is never empty and only a `line` carries a `depth`: both are CHECKs, not
    conventions, because an audit inserted rows breaking each and the database took them.
    Position plus depth is the printed document: read in order, indenting by depth, and
    you have the page's composition section back.
    """

    __tablename__ = "kt_selection_rules"
    __table_args__ = (
        UniqueConstraint("kill_team_id", "position"),
        CheckConstraint("position >= 0", name="ck_kt_selection_rule_position"),
        CheckConstraint("depth >= 0", name="ck_kt_selection_rule_depth"),
        CheckConstraint(
            "kind IN ('heading', 'line', 'restriction', 'note')",
            name="ck_kt_selection_rule_kind",
        ),
        # Two invariants this table's docstring states, now enforced rather than observed.
        # Both held across all 897 rows, but by the behaviour of the one writer: an audit
        # inserted a rule with empty text and a heading at depth 3 and the database took
        # both. A documented fact that nothing checks is a convention.
        #
        # A rule IS its text -- an empty one renders as a blank line in a printed section
        # and means nothing. The parser came within one list comprehension of storing one.
        CheckConstraint("length(trim(text)) > 0", name="ck_kt_selection_rule_text"),
        # Indent belongs to a bullet. A heading, a restriction sentence and a note are each
        # printed at the top level, so a depth on one of them would be meaningless rather
        # than merely unused.
        CheckConstraint("kind = 'line' OR depth = 0", name="ck_kt_selection_rule_depth_kind"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kill_team_id: UUID = Field(foreign_key="kt_kill_teams.id", ondelete="CASCADE", index=True)
    # Print order across the WHOLE composition, not per kind.
    position: int = Field(default=0)
    kind: str = Field(max_length=16, index=True)
    # The line, sentence, note or heading as printed.
    text: str
    # How far the bullet is indented, for a `line`: 0 for a composition line, 1 for one of
    # its entries, 2 for an entry's own weapon options. The page prints composition as an
    # indented list and the indent carries meaning -- "Servo-claw; meltagun" is a loadout
    # FOR the COMBAT SERVITOR above it, not a sibling of it. Flattening lost that on 45 of
    # the 85 lines with entries. Always 0 for a heading, restriction or note.
    depth: int = Field(default=0)

    kill_team: KillTeam = Relationship(back_populates="selection_rules")


class KTRoster(TimestampMixin, table=True):
    """A player's kill team roster: which operatives they field, for one kill team.

    Mirrors `Army`, with four deliberate differences, all recorded: no points of any
    kind, so no `points_limit` (#50's section); no equipment, because a game picks that
    (#17); no `validate`, because nothing narrows what a roster may take (#28, #44); and
    a row of `KTRosterOperative` is ONE operative rather than a count of them (#50).
    """

    __tablename__ = "kt_rosters"
    __table_args__ = (
        # Redundant to the primary key, and there for `KTRosterOperative`'s composite
        # foreign key: it is what makes "this operative belongs to this roster's kill
        # team" a thing the database can check rather than a service remembering to.
        UniqueConstraint("kill_team_id", "id", name="uq_kt_roster_team_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # delete a user -> their rosters go with them, as an army does
    owner_user_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    # The team this roster is FOR. Not nullable: a roster with no kill team could hold no
    # operative, since every operative belongs to one.
    kill_team_id: UUID = Field(foreign_key="kt_kill_teams.id", index=True)
    name: str = Field(max_length=128)
    description: str | None = Field(default=None)

    kill_team: KillTeam = Relationship()
    operatives: list["KTRosterOperative"] = Relationship(
        back_populates="roster",
        cascade_delete=True,
        # `position` then `id`: a player's positions need not be distinct (decision
        # #53), and `position` alone would leave two tied rows in whatever order the plan
        # happened to yield -- differing between a `selectinload` and a join, and between
        # this relationship and `KTRosterService.list_roster_operatives`, which has always
        # ordered by both. The catalog's relationships need no tie-breaker: their
        # positions are assigned sequentially by the scraper, so a tie there is a seed
        # defect rather than something a request can cause.
        sa_relationship_kwargs={"order_by": "KTRosterOperative.position, KTRosterOperative.id"},
    )


class KTRosterOperative(TimestampMixin, table=True):
    """One operative on a roster -- an individual, not a count (decision #50).

    Taking two Warriors is two rows. An `amount` works for `ArmyUnit` because a 40k unit
    is a group you move and shoot as one; a Kill Team operative activates, takes wounds
    and holds its order and tokens by itself, and a game gives each one its own record
    with its own stats. `{operative: Warrior, amount: 2}` cannot say which of the two is
    wounded, and decision #19's transform -- which keeps a row's id, tokens and board
    status while its catalog pointer moves -- cannot apply to half a row.

    So there is **no** `UNIQUE(roster_id, operative_id)`: a repeat is legitimate, add
    appends, and a row is addressed by its own id.

    `kill_team_id` is carried so the two composite foreign keys below can reach both
    parents through it, which is what makes a roster holding ANOTHER team's operative
    unrepresentable rather than merely checked.
    """

    __tablename__ = "kt_roster_operatives"
    __table_args__ = (
        # Both legs go through `kill_team_id`, so a row cannot name a roster of one team
        # and an operative of another: the pair has to exist on both sides.
        ForeignKeyConstraint(
            ["kill_team_id", "roster_id"],
            ["kt_rosters.kill_team_id", "kt_rosters.id"],
            ondelete="CASCADE",
            name="fk_kt_roster_operative_roster",
        ),
        ForeignKeyConstraint(
            ["kill_team_id", "operative_id"],
            ["kt_operatives.kill_team_id", "kt_operatives.id"],
            name="fk_kt_roster_operative_operative",
        ),
        CheckConstraint("position >= 0", name="ck_kt_roster_operative_position"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    roster_id: UUID = Field(index=True)
    operative_id: UUID = Field(index=True)
    # Reached by both composite foreign keys above, which is why it is stored rather than
    # read through the roster.
    kill_team_id: UUID = Field(index=True)
    # The player's own order, which is theirs to set -- unlike every `position` in the
    # catalog, which is the page's (decision #25).
    #
    # A SORT HINT, not an index (decision #53). `move_operative` sets it absolutely and
    # shifts nothing, so the values need be neither contiguous nor distinct: moving the
    # third row to 0 leaves two rows at 0. That is deliberate -- an absolute set is
    # retry-safe and one query -- so there is no `UNIQUE(roster_id, position)` and every
    # reader breaks a tie with `id`.
    position: int = Field(default=0)

    # `overlaps` on both: the two composite foreign keys share `kill_team_id`, so each
    # relationship writes a column the other also writes. SQLAlchemy cannot tell that is
    # intended -- it is the whole point, since the shared column is what ties the pair to
    # one team -- so both are told about the other.
    roster: KTRoster = Relationship(
        back_populates="operatives",
        sa_relationship_kwargs={"overlaps": "operative"},
    )
    operative: KTOperative = Relationship(
        sa_relationship_kwargs={"overlaps": "operatives,roster"},
    )
