"""API tests for the factions router (via TestClient)."""

import uuid


def test_create_faction(client):
    resp = client.post("/factions", json={"name": "Space Marines"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Space Marines"
    assert body["subfactions"] == []
    assert "id" in body


def test_create_faction_duplicate_returns_400(client):
    client.post("/factions", json={"name": "Necrons"})
    resp = client.post("/factions", json={"name": "Necrons"})
    assert resp.status_code == 400


def test_list_factions_with_subfactions(client):
    faction = client.post("/factions", json={"name": "Space Marines"}).json()
    client.post(
        "/subfactions", json={"faction_id": faction["id"], "name": "Ultramarines"}
    )
    resp = client.get("/factions")
    assert resp.status_code == 200
    listing = resp.json()
    assert len(listing) == 1
    assert [s["name"] for s in listing[0]["subfactions"]] == ["Ultramarines"]


def test_create_subfaction(client):
    faction = client.post("/factions", json={"name": "Space Marines"}).json()
    resp = client.post(
        "/subfactions", json={"faction_id": faction["id"], "name": "Ultramarines"}
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Ultramarines"


def test_create_subfaction_unknown_faction_returns_404(client):
    resp = client.post(
        "/subfactions", json={"faction_id": str(uuid.uuid4()), "name": "X"}
    )
    assert resp.status_code == 404


def test_create_subfaction_duplicate_returns_400(client):
    faction = client.post("/factions", json={"name": "Necrons"}).json()
    client.post("/subfactions", json={"faction_id": faction["id"], "name": "Sautekh"})
    resp = client.post(
        "/subfactions", json={"faction_id": faction["id"], "name": "Sautekh"}
    )
    assert resp.status_code == 400


def test_create_weapon(client):
    resp = client.post(
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


def test_create_weapon_invalid_category_returns_400(client):
    resp = client.post(
        "/weapons",
        json={
            "name": "Bad", "category": "psychic", "attacks": "1",
            "weapon_skill": 3, "strength": 4, "armor_piercing": 0, "damage": "1",
        },
    )
    assert resp.status_code == 400


def test_create_ability(client):
    resp = client.post(
        "/abilities", json={"name": "Oath of Moment", "description": "reroll"}
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Oath of Moment"
