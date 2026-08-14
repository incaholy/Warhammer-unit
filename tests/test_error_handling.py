"""API-level error handling: unexpected faults return a generic body, never a
leak of internal exception text (SPEC "Error mapping").

These use their own TestClient with `raise_server_exceptions=False` so the
catch-all 500 handler's *response* is returned — the default TestClient
re-raises server exceptions instead of surfacing the response.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.db.connection import get_session
from app.core.services import service_unit
from app.main import app


@pytest.fixture(name="safe_client")
def safe_client_fixture(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_unexpected_error_returns_generic_500(safe_client, monkeypatch):
    secret = "SECRET-internal-detail-xyz"

    def boom(self, unit_id):
        raise RuntimeError(secret)

    monkeypatch.setattr(service_unit.UnitService, "get_unit", boom)

    resp = safe_client.get(f"/units/{uuid.uuid4()}")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "internal server error", "code": "INTERNAL"}
    assert secret not in resp.text  # the raw exception message must not leak


def test_integrity_error_maps_to_generic_409(safe_client, monkeypatch):
    def boom(self, unit_id):
        raise IntegrityError(
            "INSERT INTO units ...",
            {},
            Exception("UNIQUE constraint failed: units.secret_col"),
        )

    monkeypatch.setattr(service_unit.UnitService, "get_unit", boom)

    resp = safe_client.get(f"/units/{uuid.uuid4()}")

    assert resp.status_code == 409
    assert resp.json() == {"detail": "conflict with an existing resource", "code": "CONFLICT"}
    assert "UNIQUE constraint" not in resp.text  # DB internals must not leak
