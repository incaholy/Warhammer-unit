"""Password hashing, JWT tokens, and the auth dependencies.

Config comes from the environment (loaded from `.env`):
  SECRET_KEY                   — JWT signing key. REQUIRED unless APP_ENV=dev.
  APP_ENV                      — "dev" (default) enables a throwaway key; any
                                 other value requires a real SECRET_KEY.
  ACCESS_TOKEN_EXPIRE_MINUTES  — token lifetime (default 2880 = 2 days)
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlmodel import Session

from app.core.db.connection import get_session
from app.core.db.models import User
from app.core.errors import ErrorCode

load_dotenv()

_DEV_SECRET = "dev-secret-change-me"


def _resolve_secret_key(app_env: str, secret_key: str | None) -> str:
    """The JWT signing key. A throwaway default is allowed only in dev; any other
    environment must set `SECRET_KEY`, so a prod deploy can't silently ship a
    publicly-known key (which would make admin tokens forgeable)."""
    if secret_key is None:
        if app_env != "dev":
            raise RuntimeError(
                "SECRET_KEY must be set when APP_ENV != 'dev' "
                "(an unset key would sign JWTs with a public default)"
            )
        return _DEV_SECRET
    return secret_key


SECRET_KEY = _resolve_secret_key(os.getenv("APP_ENV", "dev"), os.getenv("SECRET_KEY"))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "2880"))

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


# ------------------------------ passwords ------------------------------


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


# ------------------------------- tokens --------------------------------


def create_access_token(subject: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": subject, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str:
    """Return the token's subject (the user id), or raise ValueError."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError("invalid token") from exc
    subject = payload.get("sub")
    if subject is None:
        raise ValueError("token missing subject")
    return subject


# ------------------------------ auth errors ----------------------------
# Coded like the service errors: they carry a `code` (+ message, and `field=None`
# for the shared handler), so the API layer maps them by `code` — no HTTPException,
# no status->code reverse lookup. Registered in `app/main.py`'s `_SERVICE_ERRORS`.


class UnauthorizedError(Exception):
    """Missing or invalid credentials. → 401 (the handler adds `WWW-Authenticate`)."""

    code = ErrorCode.UNAUTHORIZED
    field = None

    def __init__(self, message: str = "could not validate credentials"):
        super().__init__(message)
        self.message = message


class ForbiddenError(Exception):
    """Authenticated but not permitted. → 403."""

    code = ErrorCode.FORBIDDEN
    field = None

    def __init__(self, message: str = "admin only"):
        super().__init__(message)
        self.message = message


# ---------------------------- dependencies -----------------------------


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    # oauth2_scheme has auto_error=False, so a missing header arrives here as None
    # (rather than FastAPI's own uncoded 401) — we raise our coded error instead.
    if token is None:
        raise UnauthorizedError()
    try:
        user_id = UUID(decode_token(token))
    except ValueError:
        raise UnauthorizedError() from None
    user = session.get(User, user_id)
    if user is None:
        raise UnauthorizedError()
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise ForbiddenError()
    return user
