"""Tests for ArmyService — armies + their units, points_total, shortfall, validate."""

import uuid

import pytest
from sqlalchemy import func, select

from app.core.db.models import ArmyUnit, UserUnit
from app.core.services.errors import ConflictError
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


def test_update_army(session, make_army):
    army = make_army()
    svc = ArmyService(session)
    updated = svc.update_army(army.id, name="Renamed", description="new note")
    assert updated.name == "Renamed"
    assert updated.description == "new note"


def test_update_army_missing_raises_lookup_error(session):
    svc = ArmyService(session)
    with pytest.raises(LookupError):
        svc.update_army(uuid.uuid4(), name="X")


def test_update_army_unknown_field_raises_value_error(session, make_army):
    army = make_army()
    svc = ArmyService(session)
    with pytest.raises(ValueError):
        svc.update_army(army.id, not_a_field=1)


def test_add_unit_to_army(session, make_army, make_unit):
    army = make_army()
    unit = make_unit()
    svc = ArmyService(session)
    entry = svc.add_unit(army.id, unit.id, amount=2)
    assert entry.amount == 2


def test_add_unit_twice_conflicts(session, make_army, make_unit):
    # Create-only: adding a unit already in the army is a conflict, not an
    # increment (that would not be retry-safe). Change the amount via set_amount.
    army = make_army()
    unit = make_unit()
    svc = ArmyService(session)
    svc.add_unit(army.id, unit.id, amount=2)
    with pytest.raises(ConflictError):
        svc.add_unit(army.id, unit.id, amount=3)


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


def test_army_may_include_a_unit_the_user_does_not_own(session, make_user, make_faction, make_unit):
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


def test_shortfall_empty_when_inventory_covers_the_list(session, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id)
    svc.add_unit(army.id, unit.id, amount=2)  # the list wants 2
    session.add(UserUnit(owner_user_id=user.id, unit_id=unit.id, amount=5))  # owns 5
    session.commit()

    assert svc.shortfall(army.id) == []


# ------------------------------ roster (points + validate) ------------------------------


def test_create_army_with_points_limit(session, make_user, make_faction):
    user = make_user()
    f = make_faction()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id, points_limit=2000)
    assert army.points_limit == 2000


def test_points_total(session, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id)
    unit = make_unit(faction=f, points=100)
    svc.add_unit(army.id, unit.id, amount=3)
    assert svc.points_total(army.id) == 300


def test_validate_ok(session, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id, points_limit=2000)
    svc.add_unit(army.id, make_unit(faction=f, points=80).id, amount=2)  # 160 pts, same faction
    report = svc.validate(army.id)
    assert report.ok is True
    assert report.points_total == 160
    assert report.issues == []


def test_validate_over_points(session, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id, points_limit=100)
    svc.add_unit(army.id, make_unit(faction=f, points=80).id, amount=2)  # 160 > 100
    report = svc.validate(army.id)
    assert report.ok is False
    assert any(i.kind == "over_points" for i in report.issues)


def test_validate_no_limit_ignores_points(session, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id)  # no limit
    svc.add_unit(army.id, make_unit(faction=f, points=80).id, amount=100)
    report = svc.validate(army.id)
    assert report.ok is True
    assert all(i.kind != "over_points" for i in report.issues)


def test_validate_wrong_faction(session, make_user, make_faction, make_unit):
    user = make_user()
    f1 = make_faction()
    f2 = make_faction()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f1.id)
    svc.add_unit(army.id, make_unit(faction=f2).id, amount=1)  # different faction
    report = svc.validate(army.id)
    assert report.ok is False
    assert any(i.kind == "wrong_faction" for i in report.issues)


def test_validate_wrong_subfaction(session, make_user, make_faction, make_subfaction, make_unit):
    user = make_user()
    f = make_faction()
    sub_a = make_subfaction(faction=f)
    sub_b = make_subfaction(faction=f)
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f.id, subfaction_id=sub_a.id)
    svc.add_unit(army.id, make_unit(faction=f, subfaction_id=sub_b.id).id, amount=1)
    report = svc.validate(army.id)
    assert report.ok is False
    assert any(i.kind == "wrong_subfaction" for i in report.issues)


def test_add_unit_below_one_raises(session, make_army, make_unit):
    from app.core.services.service_army import ArmyValidationError

    army = make_army()
    unit = make_unit()
    svc = ArmyService(session)
    with pytest.raises(ArmyValidationError):
        svc.add_unit(army.id, unit.id, amount=0)


def test_roster_lookup_guards_missing_unit(session):
    # shortfall/points_total/validate all route catalog lookups through
    # _unit_or_404, so a missing unit surfaces as NotFoundError (404) rather than
    # an AttributeError (500). (A dangling ArmyUnit can't be built normally — the
    # FK is RESTRICT — so we exercise the guard helper directly.)
    from app.core.services.errors import NotFoundError

    svc = ArmyService(session)
    with pytest.raises(NotFoundError):
        svc._unit_or_404(uuid.uuid4())


def test_validate_reports_multiple_issues(session, make_user, make_faction, make_unit):
    # one over-costed, wrong-faction unit trips both Tier-1 and Tier-2 at once
    user = make_user()
    f1, f2 = make_faction(), make_faction()
    svc = ArmyService(session)
    army = svc.create_army(user_id=user.id, name="A", faction_id=f1.id, points_limit=100)
    svc.add_unit(army.id, make_unit(faction=f2, points=80).id, amount=2)  # 160 > 100 + wrong faction
    report = svc.validate(army.id)
    assert report.ok is False
    kinds = {i.kind for i in report.issues}
    assert "over_points" in kinds
    assert "wrong_faction" in kinds
    assert report.points_total == 160
