"""Users router — the current user's own account.

Registration lives in the `/auth` router; a user only ever reads *themselves*
here via `GET /me` (identity comes from the JWT, not a path param).
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import SQLModel

from app.core.db.models import User
from app.core.security import get_current_user

router = APIRouter(tags=["users"])


class User_Read(SQLModel):
    id: UUID
    username: str
    email: str


@router.get("/me", response_model=User_Read)
def get_me(current_user: User = Depends(get_current_user)) -> User_Read:
    return current_user
