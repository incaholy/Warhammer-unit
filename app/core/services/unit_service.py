from pydantic import BaseModel, Field, ValidationError
from sqlmodel import select

from app.core.db.connection import get_session
from app.core.db.models import Unit



class UnitService():
    def __init__(self):
        self.session = get_session()

    def create_unit(
            self,
            name :str, 
            type: str,
            faction:str,
            movement:int,
            tough:int,
            save:int,
            wound:int,
            leader:int,
            objective: int
            )-> Unit:
        
        new_unit = Unit(
            name= name,
            unit_type=type,
            faction=faction, 
            movement=movement,
            toughness=tough,
            save=save,
            wounds=wound,
            leadership= leader,
            objective_control= objective
        )

        self.session.add(new_unit)
        self.session.commit(new_unit)
        self.session.refresh(new_unit)

        return new_unit
