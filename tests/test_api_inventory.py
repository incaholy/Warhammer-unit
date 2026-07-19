"""API tests for the inventory router (/me/inventory, authenticated)."""

import uuid


def test_add_and_list_inventory(auth_client, make_unit):
    unit = make_unit()
    resp = auth_client.post(
        "/me/inventory", json={"unit_id": str(unit.id), "amount": 3}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["amount"] == 3
    assert body["unit"]["id"] == str(unit.id)

    listing = auth_client.get("/me/inventory")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_add_twice_increments(auth_client, make_unit):
    unit = make_unit()
    first = auth_client.post("/me/inventory", json={"unit_id": str(unit.id), "amount": 1})
    assert first.status_code == 201  # created
    resp = auth_client.post("/me/inventory", json={"unit_id": str(unit.id), "amount": 2})
    assert resp.status_code == 200  # incremented existing
    assert resp.json()["amount"] == 3


def test_inventory_requires_auth(client):
    resp = client.get("/me/inventory")  # no token
    assert resp.status_code == 401


def test_add_unknown_unit_returns_404(auth_client):
    resp = auth_client.post(
        "/me/inventory", json={"unit_id": str(uuid.uuid4()), "amount": 1}
    )
    assert resp.status_code == 404


def test_set_amount(auth_client, make_unit):
    unit = make_unit()
    auth_client.post("/me/inventory", json={"unit_id": str(unit.id), "amount": 1})
    resp = auth_client.patch(f"/me/inventory/{unit.id}", json={"amount": 5})
    assert resp.status_code == 200
    assert resp.json()["amount"] == 5


def test_set_amount_below_one_returns_400(auth_client, make_unit):
    unit = make_unit()
    auth_client.post("/me/inventory", json={"unit_id": str(unit.id), "amount": 1})
    resp = auth_client.patch(f"/me/inventory/{unit.id}", json={"amount": 0})
    assert resp.status_code == 400


def test_remove_unit(auth_client, make_unit):
    unit = make_unit()
    auth_client.post("/me/inventory", json={"unit_id": str(unit.id), "amount": 1})
    resp = auth_client.delete(f"/me/inventory/{unit.id}")
    assert resp.status_code == 204
    assert auth_client.get("/me/inventory").json() == []


def test_add_unit_zero_amount_returns_422(auth_client, make_unit):
    unit = make_unit()
    resp = auth_client.post(
        "/me/inventory", json={"unit_id": str(unit.id), "amount": 0}
    )
    assert resp.status_code == 422  # schema ge=1


def test_list_inventory_search_filters_by_name(auth_client, make_unit):
    intercessor = make_unit(unit_name="Intercessor Squad")
    terminator = make_unit(unit_name="Terminator Squad")
    for unit in (intercessor, terminator):
        auth_client.post("/me/inventory", json={"unit_id": str(unit.id), "amount": 1})

    # case-insensitive substring match on unit_name
    resp = auth_client.get("/me/inventory", params={"q": "termin"})
    assert resp.status_code == 200
    assert [row["unit"]["unit_name"] for row in resp.json()] == ["Terminator Squad"]

    # no q -> the whole inventory
    assert len(auth_client.get("/me/inventory").json()) == 2
