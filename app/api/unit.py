from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import SQLModel, Field

from app.core.services.service_unit import UnitService

class Unit_Create(SQLModel):
    name :str = Field(min_length= 1, max_length=256)
    type: str = Field(min_length= 1, max_length=256)
    faction:str = Field(min_length= 1, max_length=256)
    movement:int
    tough:int
    save:int
    wound:int
    leader:int
    objective: int

class Unit_Read(SQLModel):
    name :str 
    type: str 
    faction:str
    movement:int
    tough:int
    save:int
    wound:int
    leader:int
    objective: int


def get_unit_service()-> UnitService:
    return UnitService()


router = APIRouter(prefix= "/units", tags= ["units"])
#make sure create_unit has raise error if arguement is missing
@router.post("", response_model= Unit_Read, status_code= 201)
def create_unit(
    payload: Unit_Create, service: UnitService = Depends(get_unit_service) 
)-> Unit_Read:
    
    try:
        created = service.create_unit(
            name= payload.name,
            type= payload.type,
            faction= payload.faction,
            movement= payload.movement,
            tough= payload.tough,
            save= payload.save,
            wound= payload.wound,
            leader= payload.leader,
            objective= payload.objective
        )
        return created
    except TypeError as exce:
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail= "one of arguements is not int correctly "
        )