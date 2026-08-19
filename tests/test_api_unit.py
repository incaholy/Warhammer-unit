"""API tests for the units router (catalog: reads public, writes admin-only)."""

import uuid

from app.core.db.models import Ability, Weapon


def _unit_payload(faction_id):
    return {
        "faction_id": str(faction_id),
        "unit_name": "Intercessor",
        "movement": 6,
        "toughness": 4,
        "armor_save": 3,
        "wounds": 2,
        "leadership": 6,
        "objective_control": 2,
        "points": 80,
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
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 2
    assert body["limit"] == 50 and body["offset"] == 0


def test_list_units_filters_by_faction(client, make_faction, make_unit):
    f1, f2 = make_faction(), make_faction()
    make_unit(faction=f1)
    make_unit(faction=f1)
    make_unit(faction=f2)
    resp = client.get("/units", params={"faction_id": str(f1.id)})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert all(u["faction_id"] == str(f1.id) for u in items)


def test_list_units_filters_by_subfaction(client, make_subfaction, make_unit):
    sub = make_subfaction()
    make_unit(faction=None, subfaction_id=sub.id, unit_name="Sub unit")
    make_unit()  # different faction, no subfaction
    resp = client.get("/units", params={"subfaction_id": str(sub.id)})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["subfaction_id"] == str(sub.id)


def test_list_units_name_search_is_case_insensitive(client, make_unit):
    make_unit(unit_name="Intercessor")
    make_unit(unit_name="Terminator")
    resp = client.get("/units", params={"q": "inter"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [u["unit_name"] for u in items] == ["Intercessor"]


def test_list_units_total_in_body(client, make_unit):
    for name in ("Alpha", "Bravo", "Charlie"):
        make_unit(unit_name=name)
    resp = client.get("/units", params={"limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2  # page is limited
    assert body["total"] == 3  # but the total is the full count (in the body)


def test_list_units_total_respects_filter(client, make_faction, make_unit):
    f = make_faction()
    make_unit(faction=f)
    make_unit(faction=f)
    make_unit()  # different faction
    resp = client.get("/units", params={"faction_id": str(f.id)})
    assert resp.json()["total"] == 2


def test_list_units_paginates_in_stable_order(client, make_unit):
    for name in ("Charlie", "Alpha", "Bravo"):  # inserted out of order
        make_unit(unit_name=name)
    page1 = client.get("/units", params={"limit": 2, "offset": 0}).json()["items"]
    page2 = client.get("/units", params={"limit": 2, "offset": 2}).json()["items"]
    assert [u["unit_name"] for u in page1] == ["Alpha", "Bravo"]
    assert [u["unit_name"] for u in page2] == ["Charlie"]


def test_list_units_pages_stably_with_duplicate_names(client, make_unit):
    # Ties on the sort column (unit_name) must not skip or repeat rows across page
    # boundaries. The id tiebreaker makes the order total/deterministic. ROADMAP R4.
    for _ in range(5):
        make_unit(unit_name="Same Name")
    seen: list[str] = []
    for offset in (0, 2, 4):  # pages of 2 across 5 tied rows
        page = client.get("/units", params={"limit": 2, "offset": offset}).json()["items"]
        seen.extend(u["id"] for u in page)
    assert len(seen) == 5  # every row was returned
    assert len(set(seen)) == 5  # each exactly once — nothing skipped or duplicated


def test_list_units_rejects_out_of_range_limit(client):
    assert client.get("/units", params={"limit": 0}).status_code == 422
    assert client.get("/units", params={"limit": 201}).status_code == 422
    assert client.get("/units", params={"offset": -1}).status_code == 422


def test_unit_facets_counts_per_faction(client, make_faction, make_unit):
    f1, f2 = make_faction(), make_faction()
    make_unit(faction=f1)
    make_unit(faction=f1)
    make_unit(faction=f2)
    resp = client.get("/units/facets")  # public, like the list
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["by_faction"][str(f1.id)] == 2
    assert body["by_faction"][str(f2.id)] == 1


def test_unit_facets_respects_search_filter(client, make_faction, make_unit):
    f = make_faction()
    make_unit(faction=f, unit_name="Intercessor")
    make_unit(faction=f, unit_name="Terminator")
    resp = client.get("/units/facets", params={"q": "inter"})
    body = resp.json()
    assert body["total"] == 1
    assert body["by_faction"][str(f.id)] == 1


def test_unit_facets_is_not_shadowed_by_unit_id_route(client):
    # `/units/facets` is a literal path; it must resolve to the facets endpoint,
    # not be parsed as `/units/{unit_id}` (which would 422 on the non-UUID).
    resp = client.get("/units/facets")
    assert resp.status_code == 200
    assert "by_faction" in resp.json()


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


def test_delete_unit_in_use_returns_409(admin_client, session, make_user, make_unit):
    from app.core.db.models import UserUnit

    unit = make_unit()
    session.add(UserUnit(owner_user_id=make_user().id, unit_id=unit.id, amount=1))
    session.commit()
    resp = admin_client.delete(f"/units/{unit.id}")
    assert resp.status_code == 409  # in use by an inventory


def test_link_weapon(admin_client, session, make_faction):
    f = make_faction()
    unit = admin_client.post("/units", json=_unit_payload(f.id)).json()
    weapon = Weapon(
        name="Bolt rifle",
        category="range",
        range_inches=24,
        attacks="2",
        weapon_skill=3,
        strength=4,
        armor_piercing=1,
        damage="1",
    )
    session.add(weapon)
    session.commit()
    session.refresh(weapon)
    resp = admin_client.post(f"/units/{unit['id']}/weapons", json={"weapon_id": str(weapon.id)})
    assert resp.status_code == 200
    assert [w["name"] for w in resp.json()["weapons"]] == ["Bolt rifle"]


def test_link_ability(admin_client, session, make_faction):
    f = make_faction()
    unit = admin_client.post("/units", json=_unit_payload(f.id)).json()
    ability = Ability(name="Oath", description="reroll")
    session.add(ability)
    session.commit()
    session.refresh(ability)
    resp = admin_client.post(f"/units/{unit['id']}/abilities", json={"ability_id": str(ability.id)})
    assert resp.status_code == 200
    assert [a["name"] for a in resp.json()["abilities"]] == ["Oath"]


def test_unlink_weapon(admin_client, session, make_faction):
    f = make_faction()
    unit = admin_client.post("/units", json=_unit_payload(f.id)).json()
    weapon = Weapon(
        name="Bolt rifle",
        category="range",
        range_inches=24,
        attacks="2",
        weapon_skill=3,
        strength=4,
        armor_piercing=1,
        damage="1",
    )
    session.add(weapon)
    session.commit()
    session.refresh(weapon)
    admin_client.post(f"/units/{unit['id']}/weapons", json={"weapon_id": str(weapon.id)})
    resp = admin_client.delete(f"/units/{unit['id']}/weapons/{weapon.id}")
    assert resp.status_code == 204
    assert admin_client.get(f"/units/{unit['id']}").json()["weapons"] == []


def test_unlink_ability(admin_client, session, make_faction):
    f = make_faction()
    unit = admin_client.post("/units", json=_unit_payload(f.id)).json()
    ability = Ability(name="Oath", description="reroll")
    session.add(ability)
    session.commit()
    session.refresh(ability)
    admin_client.post(f"/units/{unit['id']}/abilities", json={"ability_id": str(ability.id)})
    resp = admin_client.delete(f"/units/{unit['id']}/abilities/{ability.id}")
    assert resp.status_code == 204
    assert admin_client.get(f"/units/{unit['id']}").json()["abilities"] == []


def test_unlink_weapon_not_linked_is_idempotent(admin_client, session, make_faction):
    f = make_faction()
    unit = admin_client.post("/units", json=_unit_payload(f.id)).json()
    weapon = Weapon(
        name="Chainsword",
        category="melee",
        attacks="3",
        weapon_skill=3,
        strength=4,
        armor_piercing=1,
        damage="1",
    )
    session.add(weapon)
    session.commit()
    session.refresh(weapon)
    # never linked → still a clean 204
    resp = admin_client.delete(f"/units/{unit['id']}/weapons/{weapon.id}")
    assert resp.status_code == 204


def test_unlink_weapon_requires_admin(auth_client):
    # the admin gate runs before the body, so ids need not exist
    resp = auth_client.delete(f"/units/{uuid.uuid4()}/weapons/{uuid.uuid4()}")
    assert resp.status_code == 403
