"""Users router — backed by UserService.

Routes stay thin: they call the service and let its exceptions bubble up to the
app-level handlers (`LookupError` → 404, `ValueError` → 400) in `app/main.py`.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel import Session, SQLModel

from app.core.db.connection import get_session
from app.core.services.service_user import UserService

router = APIRouter(prefix="/users", tags=["users"])


class User_Create(SQLModel):
    username: str
    email: str
    password_hash: str


class User_Read(SQLModel):
    id: UUID
    username: str
    email: str


def get_user_service(session: Session = Depends(get_session)) -> UserService:
    return UserService(session)


@router.post("", response_model=User_Read, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: User_Create, service: UserService = Depends(get_user_service)
) -> User_Read:
    return service.create_user(
        username=payload.username,
        email=payload.email,
        password_hash=payload.password_hash,
    )


@router.get("/{user_id}", response_model=User_Read)
def get_user(
    user_id: UUID, service: UserService = Depends(get_user_service)
) -> User_Read:
    return service.get_user(user_id)
