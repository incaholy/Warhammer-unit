"""FastAPI application entry point.

Run locally with:

    uvicorn app.main:app --reload

Routers call services and let their exceptions bubble up to the handlers below,
which map them to HTTP status codes (SPEC.md "Error mapping"). Routers are
mounted at the bottom.
"""

import logging
import os

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
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
from app.core.errors import CodedError, ErrorCode
from app.observability import REQUEST_ID_HEADER, install_observability

app = FastAPI(title="Warhammer Unit Backend")

logger = logging.getLogger("app")

# Request ID + structured JSON logging + optional Sentry (ROADMAP R7). Installed
# early so every request — including error responses below — carries an
# X-Request-ID that ties the user's report to its log line and Sentry event.
install_observability(app)

# CORS fallback for a cross-origin frontend. The primary path is same-origin via
# a proxy (SPEC "Frontend integration"), so this only matters when the frontend is
# hosted elsewhere. Allow-list from ALLOWED_ORIGINS (comma-separated); never "*".
# Auth is a Bearer header, so credentials/cookies stay off.
#
# `expose_headers` is not optional here: a browser hides EVERY response header
# from cross-origin JavaScript unless the server names it, and it fails silently —
# `headers.get()` just returns null. SPEC.md BUG1 is exactly this bug reaching
# production (the catalog capped at 25 units because `X-Total-Count` was invisible),
# and it hid locally because the Vite proxy makes dev same-origin. R7's
# X-Request-ID is a response header on the same path, so it is named below.
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )


# --- service exception -> HTTP mapping (keeps routers thin) ---


def _error_response(
    request: Request,
    *,
    status: int,
    detail: str,
    code: ErrorCode,
    field: str | None = None,
    errors: list[dict[str, object]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    # The one place an error body is built. Shape (ROADMAP R9, option C):
    #   {detail, code, field?, request_id, errors: [{code, field, detail}, ...]}
    # `errors` is a UNIFORM array on every error body — a single element for most
    # failures, all of them for a multi-field request-validation (422). The
    # top-level detail/code/field mirror errors[0] for back-compat. request_id is
    # the correlation key, also set on the X-Request-ID header here so it's present
    # even on the catch-all 500 (whose handler runs outside the request-ID middleware).
    if errors is None:
        errors = [{"code": code, "field": field, "detail": detail}]
    body: dict[str, object] = {"detail": detail, "code": code, "errors": errors}
    if field is not None:
        body["field"] = field
    rid = getattr(request.state, "request_id", None)
    if rid:
        body["request_id"] = rid
    response = JSONResponse(status_code=status, content=body, headers=headers)
    if rid:
        response.headers[REQUEST_ID_HEADER] = rid
    return response


def _service_error(request: Request, exc: CodedError) -> JSONResponse:
    # Each service error carries a semantic `code` / `message` / optional `field`;
    # the API layer maps code -> HTTP status (app/api/errors.py). Registered once
    # against `CodedError` below — never a blanket ValueError/LookupError handler,
    # which would catch library exceptions and leak their messages.
    # 401s carry the auth challenge header, per the OAuth2 bearer convention.
    headers = {"WWW-Authenticate": "Bearer"} if exc.code == ErrorCode.UNAUTHORIZED else None
    return _error_response(
        request,
        status=CODE_STATUS[exc.code],
        detail=exc.message,
        code=exc.code,
        field=exc.field,
        headers=headers,
    )


# ONE registration covers the whole family: Starlette resolves a handler by walking
# the exception's MRO, so every `CodedError` subclass lands here — including one
# added tomorrow. This replaced a hand-maintained tuple of concrete classes, where
# forgetting an entry silently turned a well-formed 400 into a generic 500, and
# only on the path that raised it. `CodedError` is our own marker base rather than
# a builtin, so this can never swallow a library `ValueError`/`LookupError`.
app.add_exception_handler(CodedError, _service_error)


# Reshape FastAPI's default 422 (a raw error *array*) into the one error shape.
# A distinct REQUEST_VALIDATION code (-> 422) keeps a malformed request
# distinguishable from a business-rule VALIDATION (-> 400), and code->status holds.
@app.exception_handler(RequestValidationError)
def _request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic finds *every* bad field at once; surface them all in `errors` so a
    # form learns its three problems in one round-trip, not three (ROADMAP R9/C).
    # loc is like ("body", "email") or ("path", "unit_id"); drop the location
    # prefix to get the field name (dotted for a nested body field).
    errors = [
        {
            "code": ErrorCode.REQUEST_VALIDATION,
            "field": ".".join(str(p) for p in e.get("loc", ())[1:]) or None,
            "detail": e.get("msg", "invalid request"),
        }
        for e in exc.errors()
    ] or [{"code": ErrorCode.REQUEST_VALIDATION, "field": None, "detail": "invalid request"}]
    first = errors[0]
    return _error_response(
        request,
        status=CODE_STATUS[ErrorCode.REQUEST_VALIDATION],
        detail=first["detail"],
        code=ErrorCode.REQUEST_VALIDATION,
        field=first["field"],
        errors=errors,
    )


# Backstop for DB-constraint violations that slip past the service-layer guards
# (e.g. a race between a uniqueness check and the insert). Return a clean 409 —
# never the raw driver message, which can expose column/constraint internals.
@app.exception_handler(IntegrityError)
def _integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
    logger.warning("integrity error on %s %s", request.method, request.url.path, exc_info=exc)
    return _error_response(
        request,
        status=CODE_STATUS[ErrorCode.CONFLICT],
        detail="conflict with an existing resource",
        code=ErrorCode.CONFLICT,
    )


# Catch-all for anything not handled above — an unexpected server fault. Log the
# full traceback for diagnosis, but return a generic body so no internal detail
# (stack frames, values, library messages) ever reaches the client. Deliberately
# NO bare ValueError/TypeError/LookupError handlers: those builtins are raised
# all over the stdlib and libraries, so echoing str(exc) would leak internals,
# and a TypeError (almost always a bug) would be mislabelled a client 400.
@app.exception_handler(Exception)
def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Pass the exception explicitly (exc_info=exc), NOT logger.exception(), which
    # reads the thread-local sys.exc_info(): FastAPI runs sync exception handlers
    # in a worker thread where that context is empty, so the traceback would be
    # lost. (_integrity_error above does the same for the same reason.)
    logger.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return _error_response(
        request,
        status=CODE_STATUS[ErrorCode.INTERNAL],
        detail="internal server error",
        code=ErrorCode.INTERNAL,
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


# --- routers ---
# Every resource router mounts under a versioned prefix so a future breaking
# change can ship as /api/v2 without breaking existing clients. `/health` stays
# unversioned (above) on purpose — platform tooling wants a path that survives
# version bumps. See ROADMAP R5 / ARCHITECTURE §2.1.

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth_router)
api_v1.include_router(user_router)
api_v1.include_router(unit_router)
api_v1.include_router(faction_router)
api_v1.include_router(inventory_router)
api_v1.include_router(army_router)
app.include_router(api_v1)
