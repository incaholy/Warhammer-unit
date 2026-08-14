"""Tests for InventoryService — a user's owned units (the user_unit table)."""

import uuid

import pytest

from app.core.db.models import Army
from app.core.services.service_inventory import InventoryService


def test_add_unit_to_inventory(session, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    svc = InventoryService(session)
    entry, created = svc.add_unit(user.id, unit.id, amount=3)
    assert entry.amount == 3
    assert created is True


def test_add_unit_twice_increments_amount(session, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    svc = InventoryService(session)
    svc.add_unit(user.id, unit.id, amount=1)
    entry, created = svc.add_unit(user.id, unit.id, amount=2)
    assert entry.amount == 3  # upsert increments
    assert created is False  # second add hits the existing row


def test_add_unit_unknown_user_raises_lookup_error(session, make_unit):
    unit = make_unit()
    svc = InventoryService(session)
    with pytest.raises(LookupError):
        svc.add_unit(uuid.uuid4(), unit.id, amount=1)


def test_set_amount(session, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    svc = InventoryService(session)
    svc.add_unit(user.id, unit.id, amount=1)
    entry = svc.set_amount(user.id, unit.id, amount=4)
    assert entry.amount == 4


def test_set_amount_below_one_raises_value_error(session, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    svc = InventoryService(session)
    svc.add_unit(user.id, unit.id, amount=1)
    with pytest.raises(ValueError):
        svc.set_amount(user.id, unit.id, amount=0)


def test_remove_unit(session, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    svc = InventoryService(session)
    svc.add_unit(user.id, unit.id, amount=1)
    svc.remove_unit(user.id, unit.id)
    assert svc.list_inventory(user.id) == []


def test_list_inventory(session, make_user, make_unit):
    user = make_user()
    u1 = make_unit()
    u2 = make_unit()
    svc = InventoryService(session)
    svc.add_unit(user.id, u1.id, amount=1)
    svc.add_unit(user.id, u2.id, amount=2)
    assert len(svc.list_inventory(user.id)) == 2


def test_add_unit_unknown_unit_raises_lookup_error(session, make_user):
    user = make_user()
    svc = InventoryService(session)
    with pytest.raises(LookupError):
        svc.add_unit(user.id, uuid.uuid4(), amount=1)


def test_removing_inventory_entry_leaves_armies_untouched(session, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    army = Army(owner_user_id=user.id, faction_id=f.id, name="A")
    session.add(army)
    session.commit()

    svc = InventoryService(session)
    svc.add_unit(user.id, unit.id, amount=1)
    svc.remove_unit(user.id, unit.id)

    # selling a model (removing inventory) leaves the user's armies untouched
    assert session.get(Army, army.id) is not None


def test_add_unit_below_one_raises(session, make_user, make_unit):
    from app.core.services.service_inventory import InventoryValidationError

    user = make_user()
    unit = make_unit()
    svc = InventoryService(session)
    with pytest.raises(InventoryValidationError):
        svc.add_unit(user.id, unit.id, amount=0)
