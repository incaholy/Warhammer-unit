"""FastAPI application entry point.

Run locally with:

    uvicorn app.main:app --reload

Routers call services and let their exceptions bubble up to the handlers below,
which map them to HTTP status codes (SPEC.md "Error mapping"). Routers are
mounted at the bottom.
"""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.army import router as army_router
from app.api.auth import router as auth_router
from app.api.errors import CODE_STATUS
from app.api.faction import router as faction_router
from app.api.inventory import router as inventory_router
from app.api.unit import router as unit_router
from app.api.user import router as user_router
from app.core.errors import ErrorCode
from app.core.security import ForbiddenError, UnauthorizedError
from app.core.services.errors import ConflictError, NotFoundError
from app.core.services.service_army import ArmyValidationError
from app.core.services.service_inventory import InventoryValidationError
from app.core.services.service_unit import UnitValidationError

app = FastAPI(title="Warhammer Unit Backend")

logger = logging.getLogger("app")

# CORS fallback for a cross-origin frontend. The primary path is same-origin via
# a proxy (SPEC "Frontend integration"), so this only matters when the frontend is
# hosted elsewhere. Allow-list from ALLOWED_ORIGINS (comma-separated); never "*".
# Auth is a Bearer header, so credentials/cookies stay off.
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# --- service exception -> HTTP mapping (keeps routers thin) ---


def _service_error(request: Request, exc: Exception) -> JSONResponse:
    # Each service error carries a semantic `code` / `message` / optional `field`;
    # the API layer maps code -> HTTP status (app/api/errors.py). Registered per
    # concrete class below (there is no shared base) — never a blanket
    # ValueError/LookupError handler, which would catch library exceptions and
    # leak their messages.
    body = {"detail": exc.message, "code": exc.code}
    if exc.field is not None:
        body["field"] = exc.field
    # 401s carry the auth challenge header, per the OAuth2 bearer convention.
    headers = {"WWW-Authenticate": "Bearer"} if exc.code == ErrorCode.UNAUTHORIZED else None
    return JSONResponse(status_code=CODE_STATUS[exc.code], content=body, headers=headers)


# One handler, registered per concrete class (no shared base to catch through).
# Add a new coded error here so it maps to its status instead of a generic 500.
_SERVICE_ERRORS = (
    NotFoundError,
    ConflictError,
    UnauthorizedError,
    ForbiddenError,
    UnitValidationError,
    ArmyValidationError,
    InventoryValidationError,
)
for _service_exc in _SERVICE_ERRORS:
    app.add_exception_handler(_service_exc, _service_error)


# Backstop for DB-constraint violations that slip past the service-layer guards
# (e.g. a race between a uniqueness check and the insert). Return a clean 409 —
# never the raw driver message, which can expose column/constraint internals.
@app.exception_handler(IntegrityError)
def _integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
    logger.warning("integrity error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=CODE_STATUS[ErrorCode.CONFLICT],
        content={"detail": "conflict with an existing resource", "code": ErrorCode.CONFLICT},
    )


# Catch-all for anything not handled above — an unexpected server fault. Log the
# full traceback for diagnosis, but return a generic body so no internal detail
# (stack frames, values, library messages) ever reaches the client. Deliberately
# NO bare ValueError/TypeError/LookupError handlers: those builtins are raised
# all over the stdlib and libraries, so echoing str(exc) would leak internals,
# and a TypeError (almost always a bug) would be mislabelled a client 400.
@app.exception_handler(Exception)
def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=CODE_STATUS[ErrorCode.INTERNAL],
        content={"detail": "internal server error", "code": ErrorCode.INTERNAL},
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


# --- routers ---

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(unit_router)
app.include_router(faction_router)
app.include_router(inventory_router)
app.include_router(army_router)
