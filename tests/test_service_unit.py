"""Tests for UnitService (catalog).

These fail until app/core/services/service_unit.py exists with a UnitService
class that matches this contract:

    UnitService(session)
      create_unit(faction_id, unit_name, movement, toughness, armor_save,
                  wounds, leadership, objective_control, points,
                  invulnerable_save=None, subfaction_id=None, keywords=None)
          -> Unit ; raises LookupError if the faction does not exist
      get_unit(unit_id) -> Unit ; raises LookupError if not found
      list_units() -> list[Unit]
"""

import uuid

import pytest

from app.core.services.service_unit import UnitService


def _stats():
    return dict(
        movement=6, toughness=4, armor_save=3, wounds=2,
        leadership=6, objective_control=2, points=80,
    )


def test_create_unit(session, make_faction):
    f = make_faction()
    svc = UnitService(session)
    unit = svc.create_unit(faction_id=f.id, unit_name="Intercessor", **_stats())
    assert unit.id is not None
    assert unit.faction_id == f.id


def test_create_unit_with_keywords(session, make_faction):
    f = make_faction()
    svc = UnitService(session)
    unit = svc.create_unit(
        faction_id=f.id, unit_name="Intercessor", keywords=["Infantry"], **_stats()
    )
    assert unit.keywords == ["Infantry"]


def test_create_unit_unknown_faction_raises_lookup_error(session):
    svc = UnitService(session)
    with pytest.raises(LookupError):
        svc.create_unit(faction_id=uuid.uuid4(), unit_name="X", **_stats())


def test_get_unit(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    assert svc.get_unit(unit.id).id == unit.id


def test_get_unit_missing_raises_lookup_error(session):
    svc = UnitService(session)
    with pytest.raises(LookupError):
        svc.get_unit(uuid.uuid4())


def test_list_units(session, make_unit):
    make_unit()
    make_unit()
    svc = UnitService(session)
    assert len(svc.list_units()) == 2
