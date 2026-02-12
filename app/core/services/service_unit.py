from collections.abc import Sequence as SequenceABC
from typing import Any, Dict, List, Optional

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
    
#put in filters 
    def list_units(
            self,
            filter: Optional[Dict[str, any]] = None,
            offset: int = 0,
            limit: Optional[int] = 50
    )-> list[Unit]:
        
        statment = select(Unit)

        if filter:
            for field_name, value in filter.items():
                if value is None or not hasattr(Unit, field_name):
                    continue
                column = getattr(Unit, field_name)

                if isinstance(value, SequenceABC) and not isinstance(value, (str, bytes)):
                    statement = statement.where(column.in_(value))
                else:
                    statement = statement.where(column == value)

        if offset:
            statement = statement.offset(max(offset, 0))

        if limit:
            statement = statement.limit(limit)

        results = self.session.exec(statment)

        return results.all()