"""FastAPI application entry point.

Run locally with:

    uvicorn app.main:app --reload

This is intentionally bare-bones. Routers and exception handlers are mounted
here as they're built (see SPEC.md "API layer"):

    # from app.api.unit import router as unit_router
    # app.include_router(unit_router)

    # from fastapi.responses import JSONResponse
    # @app.exception_handler(LookupError)   -> 404
    # @app.exception_handler(ValueError)    -> 400
"""

from fastapi import FastAPI

app = FastAPI(title="Warhammer Unit Backend")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check — confirms the app is up before any routes exist."""
    return {"status": "ok"}
