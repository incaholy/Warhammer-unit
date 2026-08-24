"""No updatable NOT NULL column may be nulled by a PATCH.

Regression for PR #4 review finding 1: the guard was added to `update_army` but
not to `update_unit` / `update_weapon` / `update_ability`, so
`PATCH /units/{id} {"unit_name": null}` reached the database and came back as a
misleading 409 from the IntegrityError backstop.

The per-endpoint HTTP regressions live next to their resources (test_api_unit,
test_api_faction, test_api_army). This is the structural guard: it derives its
cases from `_NOT_NULLABLE`, which is itself derived from the mapped tables — so a
NOT NULL column added later is covered here without anyone remembering to add a
case, which is the failure mode that produced the bug in the first place.
"""

import pytest
from sqlmodel import Session

from app.core.db.models import Ability, Weapon
from app.core.services.service_army import ArmyService, ArmyValidationError
from app.core.services.service_unit import UnitService, UnitValidationError


def _assert_every_null_is_rejected(update, target_id, not_nullable, error_cls):
    """Each NOT NULL field, sent as an explicit null, raises with that field named."""
    assert not_nullable, "expected at least one NOT NULL updatable column"
    for field in sorted(not_nullable):
        with pytest.raises(error_cls) as exc_info:
            update(target_id, **{field: None})
        assert exc_info.value.field == field


def test_update_unit_rejects_a_null_on_every_not_null_column(session: Session, make_unit):
    unit = make_unit()
    svc = UnitService(session)
    _assert_every_null_is_rejected(svc.update_unit, unit.id, UnitService._NOT_NULLABLE, UnitValidationError)


def test_update_weapon_rejects_a_null_on_every_not_null_column(session: Session):
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
    session.flush()
    _assert_every_null_is_rejected(
        svc.update_weapon, weapon.id, UnitService._WEAPON_NOT_NULLABLE, UnitValidationError
    )


def test_update_ability_rejects_a_null_on_every_not_null_column(session: Session):
    svc = UnitService(session)
    ability = svc.create_ability(name="Oath of Moment", description="reroll")
    session.flush()
    _assert_every_null_is_rejected(
        svc.update_ability, ability.id, UnitService._ABILITY_NOT_NULLABLE, UnitValidationError
    )


def test_update_army_rejects_a_null_on_every_not_null_column(session: Session, make_army):
    army = make_army()
    svc = ArmyService(session)
    _assert_every_null_is_rejected(svc.update_army, army.id, ArmyService._NOT_NULLABLE, ArmyValidationError)


def test_the_guard_sets_are_derived_not_hand_written(session: Session):
    """The sets must track the schema — an added NOT NULL column joins them for free."""
    assert "unit_name" in UnitService._NOT_NULLABLE
    assert "subfaction_id" not in UnitService._NOT_NULLABLE  # nullable: may be cleared
    assert "invulnerable_save" not in UnitService._NOT_NULLABLE  # nullable: may be cleared
    assert Weapon.__table__.columns["range_inches"].nullable
    assert "range_inches" not in UnitService._WEAPON_NOT_NULLABLE
    assert UnitService._ABILITY_NOT_NULLABLE == frozenset(
        Ability.__table__.columns[c].name for c in ("name", "description")
    )
    assert ArmyService._NOT_NULLABLE == frozenset({"name", "faction_id"})
