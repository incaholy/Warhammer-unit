"""API tests for the armies router (via TestClient)."""

import uuid

from app.core.db.models import UserUnit


def _make_army(client, user, faction):
    return client.post(
        f"/users/{user.id}/armies",
        json={"name": "The Hollow Vigil", "faction_id": str(faction.id)},
    ).json()


def test_create_and_list_armies(client, make_user, make_faction):
    user = make_user()
    f = make_faction()
    resp = client.post(
        f"/users/{user.id}/armies",
        json={"name": "Vigil", "faction_id": str(f.id)},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Vigil"

    listing = client.get(f"/users/{user.id}/armies")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_create_army_unknown_user_returns_404(client, make_faction):
    f = make_faction()
    resp = client.post(
        f"/users/{uuid.uuid4()}/armies",
        json={"name": "X", "faction_id": str(f.id)},
    )
    assert resp.status_code == 404


def test_get_army_with_units(client, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    army = _make_army(client, user, f)
    client.post(
        f"/users/{user.id}/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 3},
    )
    resp = client.get(f"/users/{user.id}/armies/{army['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["units"][0]["unit"]["id"] == str(unit.id)
    assert body["units"][0]["amount"] == 3


def test_update_army(client, make_user, make_faction):
    user = make_user()
    f = make_faction()
    army = _make_army(client, user, f)
    resp = client.patch(
        f"/users/{user.id}/armies/{army['id']}", json={"name": "Renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


def test_delete_army(client, make_user, make_faction):
    user = make_user()
    f = make_faction()
    army = _make_army(client, user, f)
    resp = client.delete(f"/users/{user.id}/armies/{army['id']}")
    assert resp.status_code == 204
    assert client.get(f"/users/{user.id}/armies/{army['id']}").status_code == 404


def test_add_unit_twice_increments(client, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    army = _make_army(client, user, f)
    first = client.post(
        f"/users/{user.id}/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 2},
    )
    assert first.status_code == 201  # created
    resp = client.post(
        f"/users/{user.id}/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 3},
    )
    assert resp.status_code == 200  # incremented existing
    assert resp.json()["amount"] == 5


def test_set_amount_below_one_returns_400(client, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    army = _make_army(client, user, f)
    client.post(
        f"/users/{user.id}/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 1},
    )
    resp = client.patch(
        f"/users/{user.id}/armies/{army['id']}/units/{unit.id}", json={"amount": 0}
    )
    assert resp.status_code == 400


def test_remove_unit(client, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    army = _make_army(client, user, f)
    client.post(
        f"/users/{user.id}/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 1},
    )
    resp = client.delete(
        f"/users/{user.id}/armies/{army['id']}/units/{unit.id}"
    )
    assert resp.status_code == 204


def test_shortfall(client, session, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit()
    army = _make_army(client, user, f)
    client.post(
        f"/users/{user.id}/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 3},
    )
    # owner owns 1 of the unit
    session.add(UserUnit(owner_user_id=user.id, unit_id=unit.id, amount=1))
    session.commit()
    resp = client.get(f"/users/{user.id}/armies/{army['id']}/shortfall")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["need"] == 2


def test_army_read_includes_points(client, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit(faction=f, points=100)
    army = client.post(
        f"/users/{user.id}/armies",
        json={"name": "A", "faction_id": str(f.id), "points_limit": 2000},
    ).json()
    client.post(
        f"/users/{user.id}/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 3},
    )
    resp = client.get(f"/users/{user.id}/armies/{army['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["points_limit"] == 2000
    assert body["points_total"] == 300


def test_create_army_with_points_limit(client, make_user, make_faction):
    user = make_user()
    f = make_faction()
    resp = client.post(
        f"/users/{user.id}/armies",
        json={"name": "A", "faction_id": str(f.id), "points_limit": 1000},
    )
    assert resp.status_code == 201
    assert resp.json()["points_limit"] == 1000


def test_update_army_points_limit(client, make_user, make_faction):
    user = make_user()
    f = make_faction()
    army = client.post(
        f"/users/{user.id}/armies", json={"name": "A", "faction_id": str(f.id)}
    ).json()
    resp = client.patch(
        f"/users/{user.id}/armies/{army['id']}", json={"points_limit": 1500}
    )
    assert resp.status_code == 200
    assert resp.json()["points_limit"] == 1500


def test_validate_endpoint_ok(client, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit(faction=f, points=80)
    army = client.post(
        f"/users/{user.id}/armies",
        json={"name": "A", "faction_id": str(f.id), "points_limit": 2000},
    ).json()
    client.post(
        f"/users/{user.id}/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 2},
    )
    resp = client.get(f"/users/{user.id}/armies/{army['id']}/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["points_total"] == 160
    assert body["issues"] == []


def test_validate_endpoint_reports_over_points(client, make_user, make_faction, make_unit):
    user = make_user()
    f = make_faction()
    unit = make_unit(faction=f, points=80)
    army = client.post(
        f"/users/{user.id}/armies",
        json={"name": "A", "faction_id": str(f.id), "points_limit": 100},
    ).json()
    client.post(
        f"/users/{user.id}/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 2},
    )
    resp = client.get(f"/users/{user.id}/armies/{army['id']}/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert any(i["kind"] == "over_points" for i in body["issues"])
