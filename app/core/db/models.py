from typing import Any, Dict, List, Optional

from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy import Column, CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB


class Unit(SQLModel):
    id: Optional[int] = Field(default= None, primary_key= True)
    name: str
    faction: str
    movement: int
    toughness: int
    save: int
    wounds: int
    leadership_value: int
    objective_control: int
    weapons: list[str]
    abilities: list[str]
    keywords: list[str]



class Infantry(Unit):
    __tablename__ = "infantry"


class Vehicle(Unit):
    __tablename__ = "vehicles"


class Characters(Unit):
    __tablename__ = "characters"


#melee range is 1 inch (best guess)
class weapons(SQLModel):
    __tablename__ = "weapons"
    id: Optional[int] = Field(default= None, primary_key= True)
    attacks: int 
    weapon_strength: int
    save: int
    armour_penetration: int
    damage: int 
