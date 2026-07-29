"""The typed service-error hierarchy and its HTTP mapping (SPEC "Custom service errors")."""

import uuid

import pytest

from app.core.services.errors import (
    ConflictError,
    NotFoundError,
    UnitValidationError,
    ValidationError,
)
from app.core.services.service_unit import UnitService


def test_backward_compatible_subclassing():
    # NotFoundError still reads as a LookupError, and the *ValidationError /
    # ConflictError family still read as ValueError. The HTTP mapping now runs
    # entirely through the ServiceError handler (no bare-builtin fallbacks), but
    # the subclassing is kept so service-level tests can `pytest.raises(ValueError
    # / LookupError)` and so ServiceError still precedes them in the MRO.
    assert issubclass(NotFoundError, LookupError)
    assert issubclass(ConflictError, ValueError)
    assert issubclass(ValidationError, ValueError)


def test_validation_error_carries_field_and_status():
    err = UnitValidationError("category", "must be 'range' or 'melee'")
    assert err.field == "category"
    assert str(err) == "category: must be 'range' or 'melee'"
    assert err.status_code == 400


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
