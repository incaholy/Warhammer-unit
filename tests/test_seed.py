"""The seed machinery (scripts.seed_datasheets.seed) — get-or-create + idempotency.

Uses an inline sample (not the shipped datasheets.json, which ships empty for the
operator to fill), so this tests the loader, not any particular catalog content.
"""

from app.core.services.service_unit import UnitService
from scripts.seed_datasheets import seed

SAMPLE = {
    "factions": [{"name": "Xenos", "subfactions": ["Necrons"]}],
    "weapons": [
        {
            "name": "Gauss flayer", "category": "range", "attacks": "1",
            "weapon_skill": 4, "strength": 4, "armor_piercing": 0, "damage": "1",
            "range_inches": 24, "keywords": ["Rapid Fire 1"],
        }
    ],
    "abilities": [{"name": "Reanimation Protocols", "description": "Return slain models."}],
    "units": [
        {
            "unit_name": "Necron Warriors", "faction": "Xenos", "subfaction": "Necrons",
            "movement": 5, "toughness": 4, "armor_save": 4, "wounds": 1,
            "leadership": 6, "objective_control": 2, "points": 100,
            "keywords": ["Infantry"],
            "weapons": ["Gauss flayer"], "abilities": ["Reanimation Protocols"],
        }
    ],
}


def test_seed_creates_rows_and_links(session):
    counts = seed(session, SAMPLE)
    assert counts == {
        "factions": 1, "subfactions": 1, "weapons": 1, "abilities": 1, "units": 1
    }
    units = UnitService(session).list_units()
    assert len(units) == 1
    unit = units[0]
    assert [w.name for w in unit.weapons] == ["Gauss flayer"]
    assert [a.name for a in unit.abilities] == ["Reanimation Protocols"]


def test_seed_is_idempotent(session):
    seed(session, SAMPLE)
    second = seed(session, SAMPLE)
    assert second == {k: 0 for k in second}  # nothing created the second time
    assert len(UnitService(session).list_units()) == 1  # no duplicate unit


def test_seed_rejects_non_canonical_faction(session):
    import pytest

    from app.core.services.errors import UnitValidationError

    with pytest.raises(UnitValidationError):
        seed(session, {"factions": [{"name": "Nekrons", "subfactions": []}]})


def test_seed_unknown_weapon_ref_raises_seed_error(session):
    import pytest

    from scripts.seed_datasheets import SeedError, seed

    data = {
        "factions": [{"name": "Xenos", "subfactions": ["Necrons"]}],
        "weapons": [], "abilities": [],
        "units": [{
            "unit_name": "Ghost", "faction": "Xenos", "subfaction": "Necrons",
            "movement": 5, "toughness": 4, "armor_save": 4, "wounds": 1,
            "leadership": 6, "objective_control": 2, "points": 10,
            "weapons": ["Nonexistent Gun"], "abilities": [],
        }],
    }
    with pytest.raises(SeedError):
        seed(session, data)


def test_seed_missing_unit_field_raises_seed_error(session):
    import pytest

    from scripts.seed_datasheets import SeedError, seed

    data = {
        "factions": [{"name": "Xenos", "subfactions": []}],
        "weapons": [], "abilities": [],
        "units": [{"unit_name": "Incomplete", "faction": "Xenos"}],  # missing stats
    }
    with pytest.raises(SeedError):
        seed(session, data)
