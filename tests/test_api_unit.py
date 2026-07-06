"""API tests for the units router (catalog: reads public, writes admin-only)."""

import uuid

from app.core.db.models import Ability, Weapon


def _unit_payload(faction_id):
    return {
        "faction_id": str(faction_id),
        "unit_name": "Intercessor",
        "movement": 6, "toughness": 4, "armor_save": 3, "wounds": 2,
        "leadership": 6, "objective_control": 2, "points": 80,
    }


def test_create_and_get_unit(admin_client, make_faction):
    f = make_faction()
    resp = admin_client.post("/units", json=_unit_payload(f.id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["unit_name"] == "Intercessor"
    assert body["weapons"] == [] and body["abilities"] == []
    got = admin_client.get(f"/units/{body['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


def test_create_unit_unknown_faction_returns_404(admin_client):
    resp = admin_client.post("/units", json=_unit_payload(uuid.uuid4()))
    assert resp.status_code == 404


def test_list_units_is_public(client, make_unit):
    make_unit()
    make_unit()
    resp = client.get("/units")  # no auth required for reads
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_create_unit_requires_admin(auth_client, make_faction):
    f = make_faction()
    resp = auth_client.post("/units", json=_unit_payload(f.id))  # non-admin
    assert resp.status_code == 403


def test_create_unit_requires_auth(client, make_faction):
    f = make_faction()
    resp = client.post("/units", json=_unit_payload(f.id))  # no token
    assert resp.status_code == 401


def test_update_unit(admin_client, make_faction):
    f = make_faction()
    unit = admin_client.post("/units", json=_unit_payload(f.id)).json()
    resp = admin_client.patch(f"/units/{unit['id']}", json={"points": 120})
    assert resp.status_code == 200
    assert resp.json()["points"] == 120


def test_delete_unit(admin_client, make_faction):
    f = make_faction()
    unit = admin_client.post("/units", json=_unit_payload(f.id)).json()
    resp = admin_client.delete(f"/units/{unit['id']}")
    assert resp.status_code == 204
    assert admin_client.get(f"/units/{unit['id']}").status_code == 404


def test_link_weapon(admin_client, session, make_faction):
    f = make_faction()
    unit = admin_client.post("/units", json=_unit_payload(f.id)).json()
    weapon = Weapon(
        name="Bolt rifle", category="range", range_inches=24, attacks="2",
        weapon_skill=3, strength=4, armor_piercing=1, damage="1",
    )
    session.add(weapon)
    session.commit()
    session.refresh(weapon)
    resp = admin_client.post(
        f"/units/{unit['id']}/weapons", json={"weapon_id": str(weapon.id)}
    )
    assert resp.status_code == 200
    assert [w["name"] for w in resp.json()["weapons"]] == ["Bolt rifle"]


def test_link_ability(admin_client, session, make_faction):
    f = make_faction()
    unit = admin_client.post("/units", json=_unit_payload(f.id)).json()
    ability = Ability(name="Oath", description="reroll")
    session.add(ability)
    session.commit()
    session.refresh(ability)
    resp = admin_client.post(
        f"/units/{unit['id']}/abilities", json={"ability_id": str(ability.id)}
    )
    assert resp.status_code == 200
    assert [a["name"] for a in resp.json()["abilities"]] == ["Oath"]
