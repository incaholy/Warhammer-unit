"""The typed service-error hierarchy and its HTTP mapping (SPEC "Custom service errors")."""

import uuid

import pytest

from app.core.errors import ErrorCode
from app.core.services.errors import (
    ConflictError,
    NotFoundError,
)
from app.core.services.service_unit import UnitService, UnitValidationError


def test_builtin_inheritance():
    # There is no shared base: each error inherits the builtin it maps to, so
    # service-level tests can `pytest.raises(ValueError / LookupError)`. The API
    # layer registers a handler per concrete class (app/main.py) rather than
    # catching a shared base or the builtins (which would swallow library errors).
    assert issubclass(NotFoundError, LookupError)
    assert issubclass(ConflictError, ValueError)
    assert issubclass(UnitValidationError, ValueError)


def test_validation_error_carries_field_and_code():
    err = UnitValidationError("category", "must be 'range' or 'melee'")
    assert err.field == "category"
    assert str(err) == "category: must be 'range' or 'melee'"
    assert err.code == ErrorCode.VALIDATION


def test_service_raises_notfound(session):
    svc = UnitService(session)
    with pytest.raises(NotFoundError):
        svc.get_unit(uuid.uuid4())


def test_service_raises_conflict_on_duplicate(session):
    svc = UnitService(session)
    svc.create_faction("Chaos")
    with pytest.raises(ConflictError):
        svc.create_faction("Chaos")


def test_conflict_maps_to_409(admin_client):
    admin_client.post("/factions", json={"name": "Imperium"})
    resp = admin_client.post("/factions", json={"name": "Imperium"})
    assert resp.status_code == 409


def test_not_found_maps_to_404(client):
    resp = client.get(f"/units/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_request_validation_reports_every_bad_field(client):
    # Pydantic finds all problems at once; the body surfaces them all (ROADMAP R9/C).
    # {email: bad} also omits username and password -> three errors in one response.
    resp = client.post("/auth/register", json={"email": "not-an-email"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "REQUEST_VALIDATION"
    fields = {e["field"] for e in body["errors"]}
    assert {"email", "username", "password"} <= fields
    # every element carries the uniform {code, field, detail} shape
    assert all({"code", "field", "detail"} == set(e) for e in body["errors"])
    # the top-level mirrors the first error (back-compat)
    assert body["detail"] == body["errors"][0]["detail"]


def test_every_error_body_carries_a_uniform_errors_array(client):
    # a single-error case (404) still has a one-element errors[] mirroring the top level
    resp = client.get(f"/units/{uuid.uuid4()}")
    body = resp.json()
    assert body["errors"] == [{"code": body["code"], "field": None, "detail": body["detail"]}]
