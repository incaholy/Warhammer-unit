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


def test_unhandled_500_logs_the_traceback(session, monkeypatch):
    # The 500 handler is sync, so FastAPI runs it in a worker thread where
    # sys.exc_info() is empty; the traceback must be captured via exc_info=exc,
    # not logger.exception(). Assert the emitted JSON log line actually carries it.
    #
    # Self-contained: set the get_session override and use a raise-safe client in
    # a `with` block (the proven pattern) so the request reliably runs against the
    # test session and returns the 500 response instead of hitting the real engine.
    import io
    import logging

    from conftest import PrefixTestClient

    from app.core.db.connection import get_session
    from app.core.services import service_unit
    from app.main import app

    def boom(self, unit_id):
        raise RuntimeError("kaboom-secret")

    monkeypatch.setattr(service_unit.UnitService, "get_unit", boom)

    app.dependency_overrides[get_session] = lambda: session
    buf = io.StringIO()
    handler = logging.getLogger("app").handlers[0]
    original = handler.stream
    handler.setStream(buf)
    try:
        with PrefixTestClient(app, raise_server_exceptions=False) as safe:
            safe.get("/units/11111111-1111-1111-1111-111111111111")
    finally:
        handler.setStream(original)
        app.dependency_overrides.clear()

    log = buf.getvalue()
    assert "Traceback" in log and "RuntimeError: kaboom-secret" in log


def test_sentry_init_is_a_noop_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(observability, "_sentry_enabled", False)
    observability.init_sentry()  # must not raise
    assert observability._sentry_enabled is False
