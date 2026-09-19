"""API-layer auth dependencies.

These belong in the API layer, not `app/core/`: they wire FastAPI's request
machinery (`Depends`, the OAuth2 bearer scheme, the per-request session) to the
domain half in `app.core.security`. Turning a decode failure into a 401 with a
`WWW-Authenticate` header is a transport concern, so it lives here — keeping
`app.core` free of any web-framework import (enforced by `.importlinter`).
"""

from uuid import UUID

from fastapi import Depends, Query
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app.core.db.connection import get_session
from app.core.db.models import User
from app.core.security import ForbiddenError, UnauthorizedError, decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login", auto_error=False)


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


def get_current_user_optional(
    token: str | None = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User | None:
    """The caller, if there is one. Never raises.

    For endpoints that are public but behave differently for a signed-in user —
    the catalog is browsable signed-out, and `GET /units?owned=true` needs to know
    whose inventory to filter by. A bad or expired token is treated as anonymous
    rather than as an error: the endpoint is public, so the request is still valid;
    it is the caller's job to notice they are not signed in.

    Endpoints that *require* the parameter to mean something must check for None
    themselves and raise, rather than silently ignoring it.
    """
    if token is None:
        return None
    try:
        user_id = UUID(decode_token(token))
    except ValueError:
        return None
    return session.get(User, user_id)


def get_owned_by(
    owned: bool = Query(default=False, description="only units in the caller's inventory"),
    user: User | None = Depends(get_current_user_optional),
) -> UUID | None:
    """Whose inventory to filter a catalog listing by, or None for everything.

    The catalog is public, so `user` may be None — but `owned=true` from an
    anonymous caller cannot mean anything, and silently ignoring it would return
    the full catalog while the client believes it is showing an owned-only view.
    A parameter the server ignores is the worst outcome available, so it 401s.

    This is a dependency rather than a helper the endpoint calls, so that the
    three things a caller-aware endpoint needs — the `owned` query parameter, the
    optional caller, and the resolution between them — arrive together. Taking
    the dependency IS calling it, so an endpoint cannot declare the parameter and
    forget to apply it, which would accept `owned` and ignore it: the exact
    failure this function exists to prevent, one level up.

    FastAPI surfaces a dependency's own query parameters in the OpenAPI document,
    so `owned` stays part of the published contract.
    """
    if not owned:
        return None
    if user is None:
        raise UnauthorizedError("owned=true requires a signed-in user")
    return user.id


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise ForbiddenError()
    return user
