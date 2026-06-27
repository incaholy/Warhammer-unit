from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)

class user(table= True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    username: str = Field(sa_column=Column(String(64), nullable=False))
    password_hash: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
    


class Unit(SQLModel, table= True):
    __tablename__ = "units"

    id: Optional[int] = Field(default= None, primary_key= True)
    
    name: str
    unit_type: str #infantry vehicle or character maybe change later
    faction: str
    
    movement: int
    toughness: int
    save: int
    wounds: int
    leadership: int
    objective_control: int
    
    weapons: list["Weapon"] = Relationship(back_populates= "unit")
    abilities: list["Ability"] = Relationship(back_populates= "unit")
    keywords: list["Keyword"] = Relationship(back_populates= "unit")



#melee range is 1 inch (best guess)
class Weapon(SQLModel, table = True):
    __tablename__ = "weapons"

    id: Optional[int] = Field(default= None, primary_key= True)
    name: str
    unit_id: int = Field(foreign_key="units.id", index= True)

    unit: Unit = Relationship(back_populates= "weapons")
    profiles: List["WeaponProfile"] = Relationship(back_populates= "weapon")


class WeaponProfile(SQLModel, table = True):
    __tablename__ = "weapon_profiles"

    id: Optional[int] = Field(default= None, primary_key= True)
    weapon_id: int = Field(foreign_key= "weapons.id", index= True)

    profile_name: str = "standard"

    range_inches: int
    attacks: str
    weapon_strength: int
    save: int
    armour_penetration: int
    damage: int 

    weapon: Weapon = Relationship(back_populates= "profiles")


class Ability(SQLModel, table = True):
    __tablename__ = "abilities"

    id: Optional[int] = Field(default= None, primary_key= True)
    unit_id: int = Field(foreign_key= "units.id", index= True)

    name:str
    category: str
    description: str

    unit: Unit = Relationship(back_populates= "abilities")



class Keyword(SQLModel, table = True):
    __tablename__ = "keywords"

    id: Optional[int] = Field(default= None, primary_key= True)
    unit_id: int = Field(foreign_key= "units.id", index= True)

    keyword: str
    keyword_type: str = "normal"

    unit: Unit = Relationship(back_populates= "keywords")
