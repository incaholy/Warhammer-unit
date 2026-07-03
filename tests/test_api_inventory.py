"""API tests for the inventory router (via TestClient)."""

import uuid


def test_add_and_list_inventory(client, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    resp = client.post(
        f"/users/{user.id}/inventory",
        json={"unit_id": str(unit.id), "amount": 3},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["amount"] == 3
    assert body["unit"]["id"] == str(unit.id)

    listing = client.get(f"/users/{user.id}/inventory")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_add_twice_increments(client, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    first = client.post(f"/users/{user.id}/inventory", json={"unit_id": str(unit.id), "amount": 1})
    assert first.status_code == 201  # created
    resp = client.post(f"/users/{user.id}/inventory", json={"unit_id": str(unit.id), "amount": 2})
    assert resp.status_code == 200  # incremented existing
    assert resp.json()["amount"] == 3


def test_add_unknown_user_returns_404(client, make_unit):
    unit = make_unit()
    resp = client.post(
        f"/users/{uuid.uuid4()}/inventory",
        json={"unit_id": str(unit.id), "amount": 1},
    )
    assert resp.status_code == 404


def test_add_unknown_unit_returns_404(client, make_user):
    user = make_user()
    resp = client.post(
        f"/users/{user.id}/inventory",
        json={"unit_id": str(uuid.uuid4()), "amount": 1},
    )
    assert resp.status_code == 404


def test_set_amount(client, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    client.post(f"/users/{user.id}/inventory", json={"unit_id": str(unit.id), "amount": 1})
    resp = client.patch(f"/users/{user.id}/inventory/{unit.id}", json={"amount": 5})
    assert resp.status_code == 200
    assert resp.json()["amount"] == 5


def test_set_amount_below_one_returns_400(client, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    client.post(f"/users/{user.id}/inventory", json={"unit_id": str(unit.id), "amount": 1})
    resp = client.patch(f"/users/{user.id}/inventory/{unit.id}", json={"amount": 0})
    assert resp.status_code == 400


def test_remove_unit(client, make_user, make_unit):
    user = make_user()
    unit = make_unit()
    client.post(f"/users/{user.id}/inventory", json={"unit_id": str(unit.id), "amount": 1})
    resp = client.delete(f"/users/{user.id}/inventory/{unit.id}")
    assert resp.status_code == 204
    assert client.get(f"/users/{user.id}/inventory").json() == []
