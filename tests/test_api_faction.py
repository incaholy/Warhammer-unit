"""API tests for the factions router (catalog: reads public, writes admin-only)."""

import uuid


def test_create_faction(admin_client):
    resp = admin_client.post("/factions", json={"name": "Space Marines"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Space Marines"
    assert body["subfactions"] == []
    assert "id" in body


def test_create_faction_duplicate_returns_409(admin_client):
    admin_client.post("/factions", json={"name": "Chaos"})
    resp = admin_client.post("/factions", json={"name": "Chaos"})
    assert resp.status_code == 409  # duplicate = conflict


def test_list_factions_is_public(client, make_faction):
    make_faction()
    resp = client.get("/factions")  # no auth required for reads
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_create_faction_requires_admin(auth_client):
    resp = auth_client.post("/factions", json={"name": "Space Marines"})  # non-admin
    assert resp.status_code == 403


def test_create_faction_requires_auth(client):
    resp = client.post("/factions", json={"name": "Space Marines"})  # no token
    assert resp.status_code == 401


def test_create_faction_unknown_name_returns_422(admin_client):
    # A misspelling isn't a canonical faction, so the enum-typed body rejects it.
    resp = admin_client.post("/factions", json={"name": "Space Marnies"})
    assert resp.status_code == 422


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


def test_create_subfaction_duplicate_returns_409(admin_client):
    faction = admin_client.post("/factions", json={"name": "Chaos"}).json()
    admin_client.post("/subfactions", json={"faction_id": faction["id"], "name": "Death Guard"})
    resp = admin_client.post(
        "/subfactions", json={"faction_id": faction["id"], "name": "Death Guard"}
    )
    assert resp.status_code == 409  # duplicate = conflict


def test_create_subfaction_wrong_faction_returns_400(admin_client):
    # Ultramarines is a Space Marines chapter, not a Xenos army.
    faction = admin_client.post("/factions", json={"name": "Xenos"}).json()
    resp = admin_client.post(
        "/subfactions", json={"faction_id": faction["id"], "name": "Ultramarines"}
    )
    assert resp.status_code == 400
    assert resp.json()["field"] == "name"  # typed error surfaces the field


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


def test_list_weapons_is_public(client, admin_client):
    admin_client.post(
        "/weapons",
        json={
            "name": "Bolt rifle", "category": "range", "attacks": "2",
            "weapon_skill": 3, "strength": 4, "armor_piercing": 1,
            "damage": "1", "range_inches": 24,
        },
    )
    resp = client.get("/weapons")  # public read
    assert resp.status_code == 200
    assert [w["name"] for w in resp.json()] == ["Bolt rifle"]


def test_list_abilities_is_public(client, admin_client):
    admin_client.post("/abilities", json={"name": "Oath", "description": "reroll"})
    resp = client.get("/abilities")  # public read
    assert resp.status_code == 200
    assert [a["name"] for a in resp.json()] == ["Oath"]


def test_faction_taxonomy(client):
    resp = client.get("/factions/taxonomy")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"Imperium", "Xenos", "Chaos", "Space Marines"}
    assert "Necrons" in body["Xenos"]
    assert "Ultramarines" in body["Space Marines"]


def _make_subfaction(admin_client, faction="Space Marines", name="Ultramarines"):
    f = admin_client.post("/factions", json={"name": faction}).json()
    return admin_client.post(
        "/subfactions", json={"faction_id": f["id"], "name": name}
    ).json()


def test_delete_subfaction(admin_client):
    sub = _make_subfaction(admin_client)
    resp = admin_client.delete(f"/subfactions/{sub['id']}")
    assert resp.status_code == 204


def test_delete_subfaction_missing_returns_404(admin_client):
    resp = admin_client.delete(f"/subfactions/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_delete_subfaction_in_use_returns_409(admin_client, make_unit):
    sub = _make_subfaction(admin_client)
    make_unit(subfaction_id=uuid.UUID(sub["id"]))  # a unit now references it
    resp = admin_client.delete(f"/subfactions/{sub['id']}")
    assert resp.status_code == 409


def test_delete_subfaction_requires_admin(auth_client):
    resp = auth_client.delete(f"/subfactions/{uuid.uuid4()}")  # non-admin
    assert resp.status_code == 403
