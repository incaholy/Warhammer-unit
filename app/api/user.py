"""Users router — the current user's own account.

Registration lives in the `/auth` router; a user only ever reads *themselves*
here via `GET /me` (identity comes from the JWT, not a path param).
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session, SQLModel

from app.core.db.connection import get_session
from app.core.db.models import User
from app.core.security import get_current_admin, get_current_user
from app.core.services.service_user import UserService

router = APIRouter(tags=["users"])


class User_Read(SQLModel):
    id: UUID
    username: str
    email: str


class AdminUpdate(SQLModel):
    is_admin: bool


class UserAdmin_Read(SQLModel):
    # Admin-only view — unlike User_Read, it surfaces the admin flag.
    id: UUID
    username: str
    email: str
    is_admin: bool


def get_user_service(session: Session = Depends(get_session)) -> UserService:
    return UserService(session)


@router.get("/me", response_model=User_Read)
def get_me(current_user: User = Depends(get_current_user)) -> User_Read:
    return current_user


@router.patch(
    "/users/{user_id}",
    response_model=UserAdmin_Read,
    dependencies=[Depends(get_current_admin)],
)
def set_user_admin(
    user_id: UUID,
    payload: AdminUpdate,
    service: UserService = Depends(get_user_service),
) -> UserAdmin_Read:
    """Grant/revoke admin on a user (admin only). The first admin is still
    bootstrapped out of band via `make create-admin`."""
    return service.set_admin(user_id, payload.is_admin)
