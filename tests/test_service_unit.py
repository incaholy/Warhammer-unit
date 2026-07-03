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

from app.core.db.models import Ability, Weapon
from app.core.services.service_unit import UnitService

# --- catalog factions/subfactions live on UnitService ---


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


def _weapon():
    return Weapon(
        name="Bolt rifle", category="range", range_inches=24, attacks="2",
        weapon_skill=3, strength=4, armor_piercing=1, damage="1",
    )


def test_update_unit(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    updated = svc.update_unit(unit.id, points=120, unit_name="Renamed")
    assert updated.points == 120
    assert updated.unit_name == "Renamed"


def test_update_unit_missing_raises_lookup_error(session):
    svc = UnitService(session)
    with pytest.raises(LookupError):
        svc.update_unit(uuid.uuid4(), points=1)


def test_update_unit_unknown_field_raises_value_error(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    with pytest.raises(ValueError):
        svc.update_unit(unit.id, not_a_field=1)


def test_delete_unit(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    svc.delete_unit(unit.id)
    with pytest.raises(LookupError):
        svc.get_unit(unit.id)


def test_delete_unit_missing_raises_lookup_error(session):
    svc = UnitService(session)
    with pytest.raises(LookupError):
        svc.delete_unit(uuid.uuid4())


def test_link_weapon(session, make_unit):
    unit = make_unit()
    weapon = _weapon()
    session.add(weapon)
    session.commit()
    session.refresh(weapon)
    svc = UnitService(session)
    result = svc.link_weapon(unit.id, weapon.id)
    assert [w.name for w in result.weapons] == ["Bolt rifle"]


def test_link_ability(session, make_unit):
    unit = make_unit()
    ability = Ability(name="Oath", description="reroll")
    session.add(ability)
    session.commit()
    session.refresh(ability)
    svc = UnitService(session)
    result = svc.link_ability(unit.id, ability.id)
    assert [a.name for a in result.abilities] == ["Oath"]


def test_create_weapon(session):
    svc = UnitService(session)
    weapon = svc.create_weapon(
        name="Bolt rifle", category="range", attacks="2", weapon_skill=3,
        strength=4, armor_piercing=1, damage="1", range_inches=24,
    )
    assert weapon.id is not None
    assert weapon.category == "range"
    assert weapon.range_inches == 24


def test_create_weapon_invalid_category_raises_value_error(session):
    svc = UnitService(session)
    with pytest.raises(ValueError):
        svc.create_weapon(
            name="Bad", category="psychic", attacks="1", weapon_skill=3,
            strength=4, armor_piercing=0, damage="1",
        )


def test_create_ability(session):
    svc = UnitService(session)
    ability = svc.create_ability(name="Oath of Moment", description="reroll")
    assert ability.id is not None
    assert ability.name == "Oath of Moment"


def test_create_faction(session):
    svc = UnitService(session)
    faction = svc.create_faction("Space Marines")
    assert faction.id is not None
    assert faction.name == "Space Marines"


def test_create_faction_duplicate_raises_value_error(session):
    svc = UnitService(session)
    svc.create_faction("Necrons")
    with pytest.raises(ValueError):
        svc.create_faction("Necrons")


def test_list_factions(session):
    svc = UnitService(session)
    svc.create_faction("A")
    svc.create_faction("B")
    assert len(svc.list_factions()) == 2


def test_create_subfaction(session, make_faction):
    f = make_faction()
    svc = UnitService(session)
    sub = svc.create_subfaction(f.id, "Ultramarines")
    assert sub.faction_id == f.id
    assert sub.name == "Ultramarines"


def test_create_subfaction_unknown_faction_raises_lookup_error(session):
    svc = UnitService(session)
    with pytest.raises(LookupError):
        svc.create_subfaction(uuid.uuid4(), "X")


def test_create_subfaction_duplicate_raises_value_error(session, make_faction):
    f = make_faction()
    svc = UnitService(session)
    svc.create_subfaction(f.id, "Sautekh")
    with pytest.raises(ValueError):
        svc.create_subfaction(f.id, "Sautekh")
