"""Tests for UnitService — the catalog (units, factions, subfactions, weapons, abilities)."""

import uuid

import pytest

from app.core.db.models import Ability, Weapon
from app.core.services.errors import (
    ConflictError,
    NotFoundError,
)
from app.core.services.service_unit import UnitService, UnitValidationError


def _svc_weapon(svc, name="Bolt rifle"):
    return svc.create_weapon(
        name=name,
        category="range",
        attacks="2",
        weapon_skill=3,
        strength=4,
        armor_piercing=1,
        damage="1",
        range_inches=24,
    )


# --- catalog factions/subfactions live on UnitService ---


def _stats():
    return dict(
        movement=6,
        toughness=4,
        armor_save=3,
        wounds=2,
        leadership=6,
        objective_control=2,
        points=80,
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
    unit = svc.create_unit(faction_id=f.id, unit_name="Intercessor", keywords=["Infantry"], **_stats())
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
        name="Bolt rifle",
        category="range",
        range_inches=24,
        attacks="2",
        weapon_skill=3,
        strength=4,
        armor_piercing=1,
        damage="1",
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


def test_delete_unit_in_use_raises_conflict(session, make_user, make_unit):
    from app.core.db.models import UserUnit
    from app.core.services.errors import ConflictError

    user = make_user()
    unit = make_unit()
    session.add(UserUnit(owner_user_id=user.id, unit_id=unit.id, amount=1))
    session.commit()
    svc = UnitService(session)
    with pytest.raises(ConflictError):
        svc.delete_unit(unit.id)


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
        name="Bolt rifle",
        category="range",
        attacks="2",
        weapon_skill=3,
        strength=4,
        armor_piercing=1,
        damage="1",
        range_inches=24,
    )
    assert weapon.id is not None
    assert weapon.category == "range"
    assert weapon.range_inches == 24


def test_create_weapon_invalid_category_raises_value_error(session):
    svc = UnitService(session)
    with pytest.raises(ValueError):
        svc.create_weapon(
            name="Bad",
            category="psychic",
            attacks="1",
            weapon_skill=3,
            strength=4,
            armor_piercing=0,
            damage="1",
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
    svc.create_faction("Chaos")
    with pytest.raises(ValueError):
        svc.create_faction("Chaos")


def test_list_factions(session):
    svc = UnitService(session)
    svc.create_faction("Imperium")
    svc.create_faction("Xenos")
    assert len(svc.list_factions()) == 2


def test_create_faction_unknown_name_raises_value_error(session):
    svc = UnitService(session)
    with pytest.raises(ValueError):
        svc.create_faction("Nekrons")  # misspelling — not a canonical faction


def test_create_subfaction(session):
    svc = UnitService(session)
    faction = svc.create_faction("Space Marines")
    sub = svc.create_subfaction(faction.id, "Ultramarines")
    assert sub.faction_id == faction.id
    assert sub.name == "Ultramarines"


def test_create_subfaction_unknown_faction_raises_lookup_error(session):
    svc = UnitService(session)
    with pytest.raises(LookupError):
        svc.create_subfaction(uuid.uuid4(), "X")


def test_create_subfaction_wrong_faction_raises_value_error(session):
    svc = UnitService(session)
    faction = svc.create_faction("Space Marines")
    with pytest.raises(ValueError):
        svc.create_subfaction(faction.id, "Necrons")  # Necrons is a Xenos army


def test_create_subfaction_duplicate_raises_value_error(session):
    svc = UnitService(session)
    faction = svc.create_faction("Space Marines")
    svc.create_subfaction(faction.id, "Ultramarines")
    with pytest.raises(ValueError):
        svc.create_subfaction(faction.id, "Ultramarines")


# ---- weapons: update / delete / list (service level) ----


def test_list_weapons(session):
    svc = UnitService(session)
    _svc_weapon(svc)
    assert [w.name for w in svc.list_weapons()] == ["Bolt rifle"]


def test_update_weapon(session):
    svc = UnitService(session)
    w = _svc_weapon(svc)
    assert svc.update_weapon(w.id, strength=5).strength == 5


def test_update_weapon_bad_category_raises(session):
    svc = UnitService(session)
    w = _svc_weapon(svc)
    with pytest.raises(UnitValidationError):
        svc.update_weapon(w.id, category="psychic")


def test_update_weapon_unknown_field_raises(session):
    svc = UnitService(session)
    w = _svc_weapon(svc)
    with pytest.raises(UnitValidationError):
        svc.update_weapon(w.id, bogus=1)


def test_update_weapon_missing_raises_not_found(session):
    svc = UnitService(session)
    with pytest.raises(NotFoundError):
        svc.update_weapon(uuid.uuid4(), strength=5)


def test_delete_weapon(session):
    svc = UnitService(session)
    w = _svc_weapon(svc)
    svc.delete_weapon(w.id)
    assert svc.list_weapons() == []


def test_delete_weapon_missing_raises_not_found(session):
    svc = UnitService(session)
    with pytest.raises(NotFoundError):
        svc.delete_weapon(uuid.uuid4())


# ---- abilities: update / delete / list (service level) ----


def test_list_abilities(session):
    svc = UnitService(session)
    svc.create_ability("Oath", "reroll")
    assert [a.name for a in svc.list_abilities()] == ["Oath"]


def test_update_ability(session):
    svc = UnitService(session)
    a = svc.create_ability("Oath", "old")
    assert svc.update_ability(a.id, description="new").description == "new"


def test_update_ability_unknown_field_raises(session):
    svc = UnitService(session)
    a = svc.create_ability("Oath", "d")
    with pytest.raises(UnitValidationError):
        svc.update_ability(a.id, bogus=1)


def test_delete_ability_missing_raises_not_found(session):
    svc = UnitService(session)
    with pytest.raises(NotFoundError):
        svc.delete_ability(uuid.uuid4())


# ---- link / unlink error + idempotency paths ----


def test_link_weapon_unknown_unit_raises(session):
    svc = UnitService(session)
    w = _svc_weapon(svc)
    with pytest.raises(NotFoundError):
        svc.link_weapon(uuid.uuid4(), w.id)


def test_link_weapon_unknown_weapon_raises(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    with pytest.raises(NotFoundError):
        svc.link_weapon(unit.id, uuid.uuid4())


def test_link_ability_unknown_ability_raises(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    with pytest.raises(NotFoundError):
        svc.link_ability(unit.id, uuid.uuid4())


def test_unlink_weapon(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    w = _svc_weapon(svc)
    svc.link_weapon(unit.id, w.id)
    result = svc.unlink_weapon(unit.id, w.id)
    assert result.weapons == []


def test_unlink_weapon_not_linked_is_idempotent(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    w = _svc_weapon(svc)
    # never linked -> no error, still returns the unit
    assert svc.unlink_weapon(unit.id, w.id).weapons == []


# ---- count_units (service level) ----


def test_count_units(session, make_faction, make_unit):
    f = make_faction()
    make_unit(faction=f)
    make_unit(faction=f)
    make_unit(unit_name="Zzz")  # different faction
    svc = UnitService(session)
    assert svc.count_units() == 3
    assert svc.count_units(faction_id=f.id) == 2
    assert svc.count_units(q="zzz") == 1


# ---- delete_subfaction (service level) ----


def test_delete_subfaction_service(session):
    svc = UnitService(session)
    faction = svc.create_faction("Space Marines")
    sub = svc.create_subfaction(faction.id, "Ultramarines")
    svc.delete_subfaction(sub.id)
    # a fresh subfaction of the same name can be created again
    assert svc.create_subfaction(faction.id, "Ultramarines").name == "Ultramarines"


def test_delete_subfaction_missing_raises_not_found(session):
    svc = UnitService(session)
    with pytest.raises(NotFoundError):
        svc.delete_subfaction(uuid.uuid4())


def test_delete_subfaction_in_use_raises_conflict(session, make_unit):
    svc = UnitService(session)
    faction = svc.create_faction("Space Marines")
    sub = svc.create_subfaction(faction.id, "Ultramarines")
    make_unit(subfaction_id=sub.id)  # a unit references it
    with pytest.raises(ConflictError):
        svc.delete_subfaction(sub.id)


# ---- delete cascades the link rows (no reference guard needed for weapons/abilities) ----


def test_delete_weapon_cascades_unit_link(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    w = _svc_weapon(svc)
    svc.link_weapon(unit.id, w.id)
    assert [x.name for x in svc.get_unit(unit.id).weapons] == ["Bolt rifle"]
    svc.delete_weapon(w.id)  # succeeds despite the link (unit_weapons cascades)
    assert svc.get_unit(unit.id).weapons == []


def test_delete_ability_cascades_unit_link(session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    a = svc.create_ability("Oath", "reroll")
    svc.link_ability(unit.id, a.id)
    assert [x.name for x in svc.get_unit(unit.id).abilities] == ["Oath"]
    svc.delete_ability(a.id)  # succeeds despite the link (unit_abilities cascades)
    assert svc.get_unit(unit.id).abilities == []
