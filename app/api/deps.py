"""API-layer auth dependencies.

These belong in the API layer, not `app/core/`: they wire FastAPI's request
machinery (`Depends`, the OAuth2 bearer scheme, the per-request session) to the
domain half in `app.core.security`. Turning a decode failure into a 401 with a
`WWW-Authenticate` header is a transport concern, so it lives here — keeping
`app.core` free of any web-framework import (enforced by `.importlinter`).
"""

from uuid import UUID

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app.core.db.connection import get_session
from app.core.db.models import User
from app.core.security import ForbiddenError, UnauthorizedError, decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


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
