"""API-level error handling: unexpected faults return a generic body, never a
leak of internal exception text (SPEC "Error mapping").

These use their own TestClient with `raise_server_exceptions=False` so the
catch-all 500 handler's *response* is returned — the default TestClient
re-raises server exceptions instead of surfacing the response.
"""

import uuid

import pytest
from conftest import PrefixTestClient
from sqlalchemy.exc import IntegrityError

from app.core.db.connection import get_session
from app.core.services import service_unit
from app.main import app


@pytest.fixture(name="safe_client")
def safe_client_fixture(session):
    app.dependency_overrides[get_session] = lambda: session
    with PrefixTestClient(app, raise_server_exceptions=False) as c:
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


def test_request_validation_reshaped_to_one_shape(safe_client):
    # A malformed path param triggers FastAPI's RequestValidationError. It must
    # come back as our one error shape (a *string* detail + code + field), not the
    # default error *array* that stringifies to "[object Object]" on the client.
    resp = safe_client.get("/units/not-a-uuid")

    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], str)  # not a list — the R2 bug
    assert body["code"] == "REQUEST_VALIDATION"
    assert body["field"] == "unit_id"
