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
