"""API tests for the factions router (catalog: reads public, writes admin-only)."""

import uuid


def test_create_faction(admin_client):
    resp = admin_client.post("/factions", json={"name": "Space Marines"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Space Marines"
    assert body["subfactions"] == []
    assert "id" in body


def test_create_faction_duplicate_returns_400(admin_client):
    admin_client.post("/factions", json={"name": "Necrons"})
    resp = admin_client.post("/factions", json={"name": "Necrons"})
    assert resp.status_code == 400


def test_list_factions_is_public(client, make_faction):
    make_faction()
    resp = client.get("/factions")  # no auth required for reads
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_create_faction_requires_admin(auth_client):
    resp = auth_client.post("/factions", json={"name": "X"})  # non-admin
    assert resp.status_code == 403


def test_create_faction_requires_auth(client):
    resp = client.post("/factions", json={"name": "X"})  # no token
    assert resp.status_code == 401


def test_list_factions_with_subfactions(admin_client):
    faction = admin_client.post("/factions", json={"name": "Space Marines"}).json()
    admin_client.post(
        "/subfactions", json={"faction_id": faction["id"], "name": "Ultramarines"}
    )
    resp = admin_client.get("/factions")
    assert resp.status_code == 200
    listing = resp.json()
    assert len(listing) == 1
    assert [s["name"] for s in listing[0]["subfactions"]] == ["Ultramarines"]


def test_create_subfaction(admin_client):
    faction = admin_client.post("/factions", json={"name": "Space Marines"}).json()
    resp = admin_client.post(
        "/subfactions", json={"faction_id": faction["id"], "name": "Ultramarines"}
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Ultramarines"


def test_create_subfaction_unknown_faction_returns_404(admin_client):
    resp = admin_client.post(
        "/subfactions", json={"faction_id": str(uuid.uuid4()), "name": "X"}
    )
    assert resp.status_code == 404


def test_create_subfaction_duplicate_returns_400(admin_client):
    faction = admin_client.post("/factions", json={"name": "Necrons"}).json()
    admin_client.post("/subfactions", json={"faction_id": faction["id"], "name": "Sautekh"})
    resp = admin_client.post(
        "/subfactions", json={"faction_id": faction["id"], "name": "Sautekh"}
    )
    assert resp.status_code == 400


def test_create_weapon(admin_client):
    resp = admin_client.post(
        "/weapons",
        json={
            "name": "Bolt rifle", "category": "range", "attacks": "2",
            "weapon_skill": 3, "strength": 4, "armor_piercing": 1,
            "damage": "1", "range_inches": 24,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Bolt rifle"
    assert body["category"] == "range"
    assert "id" in body


def test_create_weapon_invalid_category_returns_400(admin_client):
    resp = admin_client.post(
        "/weapons",
        json={
            "name": "Bad", "category": "psychic", "attacks": "1",
            "weapon_skill": 3, "strength": 4, "armor_piercing": 0, "damage": "1",
        },
    )
    assert resp.status_code == 400


def test_create_ability(admin_client):
    resp = admin_client.post(
        "/abilities", json={"name": "Oath of Moment", "description": "reroll"}
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Oath of Moment"
