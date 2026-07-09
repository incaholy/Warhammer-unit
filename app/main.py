"""FastAPI application entry point.

Run locally with:

    uvicorn app.main:app --reload

Routers call services and let their exceptions bubble up to the handlers below,
which map them to HTTP status codes (SPEC.md "Error mapping"). Routers are
mounted at the bottom.
"""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.army import router as army_router
from app.api.auth import router as auth_router
from app.api.faction import router as faction_router
from app.api.inventory import router as inventory_router
from app.api.unit import router as unit_router
from app.api.user import router as user_router
from app.core.services.errors import ServiceError

app = FastAPI(title="Warhammer Unit Backend")

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

@app.exception_handler(ServiceError)
def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
    # Typed service errors carry their own status + optional field. Chosen over
    # the builtin handlers below because ServiceError precedes LookupError /
    # ValueError in each subclass's MRO.
    body = {"detail": exc.message}
    if exc.field is not None:
        body["field"] = exc.field
    return JSONResponse(status_code=exc.status_code, content=body)


# Fallbacks for any un-migrated raises of the plain builtins.
@app.exception_handler(LookupError)
def _not_found(request: Request, exc: LookupError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
def _bad_request(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(TypeError)
def _bad_request_type(request: Request, exc: TypeError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


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
