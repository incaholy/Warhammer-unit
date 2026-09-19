"""The typed service-error hierarchy and its HTTP mapping (SPEC "Custom service errors")."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import CodedError, ErrorCode
from app.core.security import ForbiddenError, UnauthorizedError
from app.core.services.errors import (
    ConflictError,
    NotFoundError,
)
from app.core.services.service_army import ArmyValidationError
from app.core.services.service_inventory import InventoryValidationError
from app.core.services.service_unit import UnitService, UnitValidationError


def test_builtin_inheritance():
    # Each error still inherits the builtin it maps to, so service-level tests can
    # `pytest.raises(ValueError / LookupError)`. The API layer catches the
    # `CodedError` marker base instead of the builtins, which would swallow library
    # errors and leak their messages.
    assert issubclass(NotFoundError, LookupError)
    assert issubclass(ConflictError, ValueError)
    assert issubclass(UnitValidationError, ValueError)


def test_every_coded_error_inherits_the_marker_base():
    # The API layer registers ONE handler, for CodedError. An error that carries a
    # `code` but skips the base would fall through to the catch-all and become a
    # generic 500 — silently, and only on the path that raises it. This is that
    # guard: it walks the live class tree rather than a hand-written list, so a new
    # error class is covered here the moment it exists.
    known = {
        NotFoundError,
        ConflictError,
        UnauthorizedError,
        ForbiddenError,
        UnitValidationError,
        ArmyValidationError,
        InventoryValidationError,
    }
    for cls in known:
        assert issubclass(cls, CodedError), f"{cls.__name__} does not inherit CodedError"

    def walk(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from walk(sub)

    coded_but_unrooted = [
        cls
        for cls in walk(Exception)
        if cls.__module__.startswith("app.")
        and isinstance(getattr(cls, "code", None), ErrorCode)
        and not issubclass(cls, CodedError)
    ]
    assert coded_but_unrooted == []


def test_a_new_coded_error_is_mapped_without_touching_main():
    # The point of the marker base: an error class that main.py has never heard of
    # still maps to its status, because Starlette resolves handlers by walking the
    # exception's MRO. Under the old per-class tuple this returned 500.
    from app.main import _service_error

    class BrandNewError(CodedError, ValueError):
        code = ErrorCode.CONFLICT

        def __init__(self):
            super().__init__("invented in a test")
            self.message = "invented in a test"
            self.field = "somewhere"

    probe = FastAPI()
    probe.add_exception_handler(CodedError, _service_error)

    @probe.get("/boom")
    def boom():
        raise BrandNewError()

    resp = TestClient(probe, raise_server_exceptions=False).get("/boom")
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"
    assert resp.json()["field"] == "somewhere"


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
