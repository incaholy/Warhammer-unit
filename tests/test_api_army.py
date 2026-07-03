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
