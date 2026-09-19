"""Observability: request IDs, the logging filter, and the Sentry no-op (R7)."""

import logging

from app import observability
from app.observability import REQUEST_ID_HEADER, _request_id_ctx


def test_response_carries_a_request_id_header(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers[REQUEST_ID_HEADER]  # a generated id


def test_inbound_request_id_is_echoed(client):
    resp = client.get("/health", headers={REQUEST_ID_HEADER: "caller-supplied-123"})
    assert resp.headers[REQUEST_ID_HEADER] == "caller-supplied-123"


def test_error_body_carries_the_request_id(client):
    # a 422 (RequestValidationError) — the id ties the body to its log line
    resp = client.get("/units/not-a-uuid")
    assert resp.status_code == 422
    assert resp.json()["request_id"] == resp.headers[REQUEST_ID_HEADER]


def test_logging_filter_injects_the_current_request_id():
    _request_id_ctx.set("rid-under-test")
    record = logging.LogRecord("app.x", logging.INFO, __file__, 1, "hi", None, None)
    assert observability._RequestIdFilter().filter(record) is True
    assert record.request_id == "rid-under-test"


def test_unhandled_500_logs_the_traceback():
    # The 500 handler must capture the traceback via `exc_info=exc`, NOT
    # `logger.exception()`: FastAPI runs sync exception handlers in a worker thread
    # where the thread-local `sys.exc_info()` is empty, so `logger.exception()`
    # would log "NoneType: None". This drives `_unhandled` directly (no HTTP, no
    # threadpool, no DB — so it's deterministic) with `sys.exc_info()` deliberately
    # empty: only `exc_info=exc` can then record the traceback.
    import io
    import logging

    from starlette.requests import Request

    from app import main

    try:
        raise RuntimeError("kaboom-secret")
    except RuntimeError as exc:
        err = exc  # captured out of the except block -> sys.exc_info() is now empty

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("test", 80),
    }
    request = Request(scope)
    request.state.request_id = "rid-test"

    buf = io.StringIO()
    handler = logging.getLogger("app").handlers[0]
    original = handler.stream
    handler.setStream(buf)
    try:
        main._unhandled(request, err)
    finally:
        handler.setStream(original)

    log = buf.getvalue()
    assert "Traceback" in log and "RuntimeError: kaboom-secret" in log


def test_sentry_init_is_a_noop_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(observability, "_sentry_enabled", False)
    observability.init_sentry()  # must not raise
    assert observability._sentry_enabled is False
