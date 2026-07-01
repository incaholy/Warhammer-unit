"""Tests for ArmyService.

These fail until app/core/services/service_army.py exists with an ArmyService
class that matches this contract:

    ArmyService(session)
      create_army(user_id, name, faction_id, subfaction_id=None, description=None)
          -> Army ; raises LookupError if the user or faction does not exist
      get_army(army_id) -> Army ; raises LookupError if not found
      list_armies(user_id) -> list[Army]
      delete_army(army_id) -> None
      add_unit(army_id, unit_id, amount=1) -> ArmyUnit
          upsert: increments amount if the unit is already in the army.
          raises LookupError if the army or unit does not exist
      set_amount(army_id, unit_id, amount) -> ArmyUnit
          absolute set; raises ValueError if amount < 1
      remove_unit(army_id, unit_id) -> None
      list_army_units(army_id) -> list[ArmyUnit]
      shortfall(army_id) -> list of rows with .unit, .in_list, .owned, .need
          need = max(0, in_list - owned) against the army owner's inventory
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.core.db.models import ArmyUnit, UserUnit
from app.core.services.service_army import ArmyService


def test_create_army(session, make_user, make_faction):
    user = make_user()
    f = make_faction()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="Vigil", faction_id=f.id)
    assert army.id is not None
    assert army.owner_user_id == user.id
    assert army.faction_id == f.id


def test_create_army_unknown_user_raises_lookup_error(session, make_faction):
    f = make_faction()
    svc = ArmyService(session)
    with pytest.raises(LookupError):
        svc.create_army(user_id=uuid.uuid4(), name="X", faction_id=f.id)


def test_list_armies(session, make_user, make_faction):
    user = make_user()
    f = make_faction()
    svc = ArmyService(session)
    svc.create_army(user_id=user.id, name="A1", faction_id=f.id)
    svc.create_army(user_id=user.id, name="A2", faction_id=f.id)
    assert len(svc.list_armies(user.id)) == 2


def test_delete_army(session, make_army):
    army = make_army()
    svc = ArmyService(session)
    svc.delete_army(army.id)
    with pytest.raises(LookupError):
        svc.get_army(army.id)


def test_add_unit_to_army(session, make_army, make_unit):
    army = make_army()
    unit = make_unit()
    svc = ArmyService(session)
    entry = svc.add_unit(army.id, unit.id, amount=2)
    assert entry.amount == 2


def test_add_unit_twice_increments_amount(session, make_army, make_unit):
    army = make_army()
    unit = make_unit()
    svc = ArmyService(session)
    svc.add_unit(army.id, unit.id, amount=2)
    entry = svc.add_unit(army.id, unit.id, amount=3)
    assert entry.amount == 5  # upsert increments


def test_add_unit_unknown_unit_raises_lookup_error(session, make_army):
    army = make_army()
    svc = ArmyService(session)
    with pytest.raises(LookupError):
        svc.add_unit(army.id, uuid.uuid4(), amount=1)


def test_set_amount(session, make_army, make_unit):
    army = make_army()
    unit = make_unit()
    svc = ArmyService(session)
    svc.add_unit(army.id, unit.id, amount=2)
    entry = svc.set_amount(army.id, unit.id, amount=5)
    assert entry.amount == 5


def test_set_amount_below_one_raises_value_error(session, make_army, make_unit):
    army = make_army()
    unit = make_unit()
    svc = ArmyService(session)
    svc.add_unit(army.id, unit.id, amount=2)
    with pytest.raises(ValueError):
        svc.set_amount(army.id, unit.id, amount=0)


def test_remove_unit(session, make_army, make_unit):
    army = make_army()
    unit = make_unit()
    svc = ArmyService(session)
    svc.add_unit(army.id, unit.id, amount=2)
    svc.remove_unit(army.id, unit.id)
    assert svc.list_army_units(army.id) == []


def test_shortfall_reports_what_to_buy(session, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id)
    svc.add_unit(army.id, unit.id, amount=3)  # the list wants 3
    session.add(UserUnit(owner_user_id=user.id, unit_id=unit.id, amount=1))  # owns 1
    session.commit()

    short = svc.shortfall(army.id)
    assert len(short) == 1
    assert short[0].need == 2  # 3 in list - 1 owned


def test_add_unit_to_nonexistent_army_raises_lookup_error(session, make_unit):
    unit = make_unit()
    svc = ArmyService(session)
    with pytest.raises(LookupError):
        svc.add_unit(uuid.uuid4(), unit.id, amount=1)


def test_army_may_include_a_unit_the_user_does_not_own(
    session, make_user, make_faction, make_unit
):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id)
    # the user owns none of this unit; adding it to the army is still allowed
    entry = svc.add_unit(army.id, unit.id, amount=2)
    assert entry.amount == 2


def test_delete_army_cascades_army_units(session, make_army, make_unit):
    army = make_army()
    unit = make_unit()
    svc = ArmyService(session)
    svc.add_unit(army.id, unit.id, amount=2)
    svc.delete_army(army.id)
    remaining = session.execute(
        select(func.count()).select_from(ArmyUnit).where(ArmyUnit.army_id == army.id)
    ).scalar_one()
    assert remaining == 0


def test_shortfall_empty_when_inventory_covers_the_list(
    session, make_user, make_faction, make_unit
):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id)
    svc.add_unit(army.id, unit.id, amount=2)  # the list wants 2
    session.add(UserUnit(owner_user_id=user.id, unit_id=unit.id, amount=5))  # owns 5
    session.commit()

    assert svc.shortfall(army.id) == []
