from typing import Any, Dict, List, Optional

from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy import Column, CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB


class Unit(SQLModel):
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