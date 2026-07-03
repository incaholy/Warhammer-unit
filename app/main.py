"""FastAPI application entry point.

Run locally with:

    uvicorn app.main:app --reload

Routers call services and let their exceptions bubble up to the handlers below,
which map them to HTTP status codes (SPEC.md "Error mapping"). Routers are
mounted at the bottom.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.unit import router as unit_router
from app.api.user import router as user_router

app = FastAPI(title="Warhammer Unit Backend")


# --- service exception -> HTTP mapping (keeps routers thin) ---

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

app.include_router(user_router)
app.include_router(unit_router)
