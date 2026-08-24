"""Observability: request IDs, structured (JSON) logs, and optional Sentry.

The three are one feature (ARCHITECTURE §2.4 / ROADMAP R7). A request ID is
generated — or echoed from an inbound `X-Request-ID` — per request and stashed in
a context variable. A logging filter reads that variable, so every log line
emitted while handling a request carries the ID with nothing to thread through
call args. The same ID goes into the response header, the error body, and (when a
`SENTRY_DSN` is set) the Sentry event's tags — so a user's report, its log lines,
and its captured exception all join on one key.

Lives at the `app` top level, not `app.core`: it wires the web framework
(middleware, `request.state`) and so must stay out of the framework-free core
(enforced by `.importlinter`).
"""

import json
import logging
import logging.config
import os
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, Request

REQUEST_ID_HEADER = "X-Request-ID"

# Set per request by the middleware; read by the logging filter and the error
# handlers. Default None covers log lines emitted outside a request (startup).
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

_sentry_enabled = False


def current_request_id() -> str | None:
    """The current request's ID, or None outside a request."""
    return _request_id_ctx.get()


class _RequestIdFilter(logging.Filter):
    """Attach the current request ID to every record so the formatter can emit it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line — machine-parseable for a log aggregator."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    """Configure the `app` logger to emit JSON lines carrying the request ID.
    `disable_existing_loggers=False` leaves uvicorn's own loggers alone."""
    level = level or os.getenv("LOG_LEVEL", "INFO")
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": _RequestIdFilter}},
            "formatters": {"json": {"()": _JsonFormatter}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                    "filters": ["request_id"],
                }
            },
            "loggers": {"app": {"handlers": ["stdout"], "level": level, "propagate": False}},
        }
    )


def init_sentry() -> None:
    """Initialize Sentry only when `SENTRY_DSN` is set — a no-op locally and in
    tests, so nothing is sent and nothing needs mocking."""
    global _sentry_enabled
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("APP_ENV", "dev"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0")),
    )
    _sentry_enabled = True


async def _request_id_middleware(request: Request, call_next):
    rid = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
    _request_id_ctx.set(rid)  # task-local; each request runs in its own context
    request.state.request_id = rid
    if _sentry_enabled:
        import sentry_sdk

        sentry_sdk.set_tag("request_id", rid)
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = rid
    return response


def install_observability(app: FastAPI) -> None:
    """Wire logging, Sentry, and the request-ID middleware onto the app."""
    configure_logging()
    init_sentry()
    app.middleware("http")(_request_id_middleware)
