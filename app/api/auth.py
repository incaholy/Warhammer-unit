"""Auth router — registration and login (both public).

`register` creates a user (hashing the password). `login` uses the standard
OAuth2 password form (`username` may be a username or email) and returns a JWT.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Field, Session, SQLModel

from app.api.user import User_Read
from app.core.db.connection import get_session
from app.core.security import create_access_token
from app.core.services.service_auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


class Register_Create(SQLModel):
    username: str
    # Not full email-format validation for now — just guard against empty/too-short
    # values (a real address is at least like "a@b.c"). Rejected with a 422.
    email: str = Field(min_length=5)
    password: str


class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


def get_auth_service(session: Session = Depends(get_session)) -> AuthService:
    return AuthService(session)


@router.post(
    "/register", response_model=User_Read, status_code=status.HTTP_201_CREATED
)
def register(
    payload: Register_Create, service: AuthService = Depends(get_auth_service)
) -> User_Read:
    return service.register(payload.username, payload.email, payload.password)


@router.post("/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    service: AuthService = Depends(get_auth_service),
) -> Token:
    user = service.authenticate(form.username, form.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=create_access_token(str(user.id)))
