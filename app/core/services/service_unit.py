from pydantic import BaseModel, Field, ValidationError
from sqlmodel import select

from app.core.db.connection import get_session
from app.core.db.models import Unit
#because changed from Unit_service to unit_service (the uppercase U) vs code might not have been confused on what to track 
#so git will see the differences but vs code will not see it becuase it is on unit_service and that is not tracked
#Unit_service is being tracked but since no changed to that one then no diff decorator


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


    def get_unit(
            self,
            id: int
    )-> Unit:
        
        unit = self.session.get(Unit, id)
        
        if not unit:
            return "unit does not exist"
        
        return unit