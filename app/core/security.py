"""Password hashing, JWT tokens, and the coded auth errors.

Config comes from the environment (loaded from `.env`):
  SECRET_KEY                   — JWT signing key. REQUIRED unless APP_ENV=dev.
  APP_ENV                      — "dev" (default) enables a throwaway key; any
                                 other value requires a real SECRET_KEY.
  ACCESS_TOKEN_EXPIRE_MINUTES  — token lifetime (default 2880 = 2 days)

Deliberately free of any web-framework import: the FastAPI auth dependencies
(`get_current_user`, the bearer scheme) live in `app/api/deps.py`, so the
service layer can hash passwords and mint tokens without depending on transport.
The `app.core` layer must not import `fastapi` — enforced by `.importlinter`.
"""

import os
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.errors import CodedError, ErrorCode

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
# no status->code reverse lookup. Mapped by `app/main.py`'s `CodedError` handler.
# Framework-free value types, so they stay in the domain module even though the
# dependencies that raise them now live in `app/api/deps.py`.


class UnauthorizedError(CodedError):
    """Missing or invalid credentials. → 401 (the handler adds `WWW-Authenticate`)."""

    code = ErrorCode.UNAUTHORIZED
    field = None

    def __init__(self, message: str = "could not validate credentials"):
        super().__init__(message)
        self.message = message


class ForbiddenError(CodedError):
    """Authenticated but not permitted. → 403."""

    code = ErrorCode.FORBIDDEN
    field = None

    def __init__(self, message: str = "admin only"):
        super().__init__(message)
        self.message = message
