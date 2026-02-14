from collections.abc import Sequence as SequenceABC
from typing import  Dict, Optional

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
    
    def delete_unit(
            self,
            id: int
    ):
        unit = self.get_unit(id)
        self.session.delete(unit)
        self.session.commit

        return unit
    
    def update_unit(
            self,
            id: int,
            field: str,
            new: int | str 
        )->Unit:
        
        unit = self.get_unit(id)
        
        if not hasattr(unit, field):
            raise AttributeError("{field} is not an attribute of the unit")

        attribute_value = getattr(unit, field)

        if not isinstance(new, type(attribute_value)):
            raise TypeError("{unit} is not the same {field}")
        
        setattr(unit, field, new)
        
        self.session.add(unit)
        self.session.commit()
        self.session.refresh(unit)
        
        return unit
        