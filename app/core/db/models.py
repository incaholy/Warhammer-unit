"""SQLModel table definitions.

Two halves:
- Catalog (shared, admin-owned): factions, subfactions, units, abilities,
  weapons. Units point at a faction (and optionally a subfaction), and link to
  abilities/weapons through association tables.
- Collection (per-user): a user owns armies and an inventory (user_unit). Both
  armies (army_units) and the inventory point at catalog units with an amount.

Schema follows app/core/db/test_units.md.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    UniqueConstraint,
    func,
)


class TimestampMixin(SQLModel):
    """created_at / updated_at, shared by every non-association table."""

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now(), "onupdate": func.now()},
        nullable=False,
    )


# ============================ Catalog ============================

class Faction(TimestampMixin, table=True):
    __tablename__ = "factions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=128)

    subfactions: list["Subfaction"] = Relationship(back_populates="faction")


class Subfaction(TimestampMixin, table=True):
    __tablename__ = "subfactions"
    __table_args__ = (UniqueConstraint("faction_id", "name"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # delete a faction -> its subfactions go with it
    faction_id: UUID = Field(
        foreign_key="factions.id", ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=128)

    faction: Faction = Relationship(back_populates="subfactions")


class UnitAbility(SQLModel, table=True):
    """Association table: which abilities a unit has (many-to-many)."""

    __tablename__ = "unit_abilities"

    unit_id: UUID = Field(
        foreign_key="units.id", ondelete="CASCADE", primary_key=True
    )
    ability_id: UUID = Field(
        foreign_key="abilities.id", ondelete="CASCADE", primary_key=True
    )


class UnitWeapon(SQLModel, table=True):
    """Association table: which weapons a unit has (many-to-many)."""

    __tablename__ = "unit_weapons"

    unit_id: UUID = Field(
        foreign_key="units.id", ondelete="CASCADE", primary_key=True
    )
    weapon_id: UUID = Field(
        foreign_key="weapons.id", ondelete="CASCADE", primary_key=True
    )


class Ability(TimestampMixin, table=True):
    __tablename__ = "abilities"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(max_length=128)
    description: str

    units: list["Unit"] = Relationship(
        back_populates="abilities", link_model=UnitAbility
    )


class Weapon(TimestampMixin, table=True):
    __tablename__ = "weapons"
    __table_args__ = (
        CheckConstraint("category IN ('range', 'melee')", name="ck_weapon_category"),
        CheckConstraint(
            "weapon_skill >= 0 AND strength >= 0 AND armor_piercing >= 0",
            name="ck_weapon_stats_non_negative",
        ),
        CheckConstraint(
            "range_inches IS NULL OR range_inches >= 0",
            name="ck_weapon_range_non_negative",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(max_length=128)
    category: str = Field(max_length=8)  # "range" | "melee"
    keywords: list[str] = Field(default_factory=list, sa_type=JSON, nullable=False)

    # null range = melee (inferred from category)
    range_inches: Optional[int] = Field(default=None)
    attacks: str = Field(max_length=16)  # may be dice notation, e.g. "D6"
    weapon_skill: int  # BS for ranged, WS for melee
    strength: int
    armor_piercing: int
    damage: str = Field(max_length=16)  # may be dice notation, e.g. "D3"

    units: list["Unit"] = Relationship(
        back_populates="weapons", link_model=UnitWeapon
    )


class Unit(TimestampMixin, table=True):
    __tablename__ = "units"
    __table_args__ = (
        CheckConstraint(
            "movement >= 0 AND toughness >= 0 AND armor_save >= 0 "
            "AND wounds >= 0 AND leadership >= 0 AND objective_control >= 0 "
            "AND points >= 0",
            name="ck_unit_stats_non_negative",
        ),
        CheckConstraint(
            "invulnerable_save IS NULL OR invulnerable_save >= 0",
            name="ck_unit_invuln_non_negative",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    unit_name: str = Field(max_length=128, index=True)

    # faction is intrinsic; subfaction is an optional restriction (null = any)
    faction_id: UUID = Field(foreign_key="factions.id", index=True)
    subfaction_id: Optional[UUID] = Field(
        default=None, foreign_key="subfactions.id", index=True
    )

    movement: int
    toughness: int
    armor_save: int
    wounds: int
    invulnerable_save: Optional[int] = Field(default=None)
    leadership: int
    objective_control: int
    points: int

    keywords: list[str] = Field(default_factory=list, sa_type=JSON, nullable=False)

    faction: Faction = Relationship()
    subfaction: Optional[Subfaction] = Relationship()
    abilities: list[Ability] = Relationship(
        back_populates="units", link_model=UnitAbility
    )
    weapons: list[Weapon] = Relationship(
        back_populates="units", link_model=UnitWeapon
    )


# ========================== Collection ==========================

class User(TimestampMixin, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=64)
    email: str = Field(unique=True, index=True, max_length=255)
    password_hash: str

    armies: list["Army"] = Relationship(back_populates="owner")
    inventory: list["UserUnit"] = Relationship(back_populates="owner")


class Army(TimestampMixin, table=True):
    __tablename__ = "armies"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # delete a user -> their armies go with them
    owner_user_id: UUID = Field(
        foreign_key="users.id", ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=128)
    description: Optional[str] = Field(default=None)

    faction_id: UUID = Field(foreign_key="factions.id", index=True)
    subfaction_id: Optional[UUID] = Field(
        default=None, foreign_key="subfactions.id", index=True
    )

    owner: User = Relationship(back_populates="armies")
    units: list["ArmyUnit"] = Relationship(back_populates="army")
    faction: Faction = Relationship()
    subfaction: Optional[Subfaction] = Relationship()


class UserUnit(TimestampMixin, table=True):
    """A user's inventory: how many of a catalog unit they own."""

    __tablename__ = "user_unit"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "unit_id"),
        CheckConstraint("amount >= 0", name="ck_user_unit_amount_non_negative"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # delete a user -> their inventory goes with them
    owner_user_id: UUID = Field(
        foreign_key="users.id", ondelete="CASCADE", index=True
    )
    unit_id: UUID = Field(foreign_key="units.id", index=True)
    amount: int

    owner: User = Relationship(back_populates="inventory")
    unit: Unit = Relationship()


class ArmyUnit(TimestampMixin, table=True):
    """A unit in an army list, and how many are in it."""

    __tablename__ = "army_units"
    __table_args__ = (
        UniqueConstraint("army_id", "unit_id"),
        CheckConstraint("amount >= 0", name="ck_army_unit_amount_non_negative"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # delete an army -> its entries go with it (catalog units are untouched)
    army_id: UUID = Field(foreign_key="armies.id", ondelete="CASCADE", index=True)
    unit_id: UUID = Field(foreign_key="units.id", index=True)
    amount: int

    army: Army = Relationship(back_populates="units")
    unit: Unit = Relationship()
