"""API tests for the armies router (/me/armies, authenticated)."""

from app.core.db.models import UserUnit


def _make_army(auth_client, faction, **extra):
    body = {"name": "The Hollow Vigil", "faction_id": str(faction.id), **extra}
    return auth_client.post("/me/armies", json=body).json()


def test_create_and_list_armies(auth_client, make_faction):
    f = make_faction()
    resp = auth_client.post("/me/armies", json={"name": "Vigil", "faction_id": str(f.id)})
    assert resp.status_code == 201
    assert resp.json()["name"] == "Vigil"

    listing = auth_client.get("/me/armies")
    assert listing.status_code == 200
    body = listing.json()
    assert len(body["items"]) == 1
    assert body["total"] == 1


def test_armies_require_auth(client):
    resp = client.get("/me/armies")  # no token
    assert resp.status_code == 401


def test_cannot_access_another_users_army(auth_client, make_army):
    # make_army builds its own (different) user + army
    other_army = make_army()
    resp = auth_client.get(f"/me/armies/{other_army.id}")
    assert resp.status_code == 404  # ownership hides existence


def test_get_army_with_units(auth_client, make_faction, make_unit):
    f = make_faction()
    unit = make_unit()
    army = _make_army(auth_client, f)
    auth_client.post(
        f"/me/armies/{army['id']}/units",
        json={"unit_id": str(unit.id), "amount": 3},
    )
    resp = auth_client.get(f"/me/armies/{army['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["units"][0]["unit"]["id"] == str(unit.id)
    assert body["units"][0]["amount"] == 3


def test_update_army(auth_client, make_faction):
    f = make_faction()
    army = _make_army(auth_client, f)
    resp = auth_client.patch(f"/me/armies/{army['id']}", json={"name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


def test_update_army_explicit_null_on_required_field_returns_400(auth_client, make_faction):
    # A PATCH with an explicit null on a NOT NULL column must be a clean 400
    # (ArmyValidationError), never a 500 or an IntegrityError-backstop 409.
    f = make_faction()
    army = _make_army(auth_client, f)

    resp = auth_client.patch(f"/me/armies/{army['id']}", json={"faction_id": None})
    assert resp.status_code == 400
    assert resp.json()["field"] == "faction_id"

    resp = auth_client.patch(f"/me/armies/{army['id']}", json={"name": None})
    assert resp.status_code == 400
    assert resp.json()["field"] == "name"


def test_update_army_explicit_null_on_nullable_field_clears_it(auth_client, make_faction):
    # A nullable column may still be cleared with an explicit null.
    f = make_faction()
    army = _make_army(auth_client, f, points_limit=1000)
    resp = auth_client.patch(f"/me/armies/{army['id']}", json={"points_limit": None})
    assert resp.status_code == 200
    assert resp.json()["points_limit"] is None


def test_delete_army(auth_client, make_faction):
    f = make_faction()
    army = _make_army(auth_client, f)
    resp = auth_client.delete(f"/me/armies/{army['id']}")
    assert resp.status_code == 204
    assert auth_client.get(f"/me/armies/{army['id']}").status_code == 404


def test_add_unit_is_create_only_conflicts_on_repeat(auth_client, make_faction, make_unit):
    # Create-only (retry-safe): re-POSTing a unit already in the army is a 409,
    # not an increment. Change the amount with PATCH (idempotent). See ROADMAP R12.
    f = make_faction()
    unit = make_unit()
    army = _make_army(auth_client, f)
    first = auth_client.post(f"/me/armies/{army['id']}/units", json={"unit_id": str(unit.id), "amount": 2})
    assert first.status_code == 201
    resp = auth_client.post(f"/me/armies/{army['id']}/units", json={"unit_id": str(unit.id), "amount": 3})
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"
    # the amount is unchanged — the repeat did not apply
    patched = auth_client.patch(f"/me/armies/{army['id']}/units/{unit.id}", json={"amount": 5})
    assert patched.status_code == 200
    assert patched.json()["amount"] == 5


def test_set_amount_below_one_returns_400(auth_client, make_faction, make_unit):
    f = make_faction()
    unit = make_unit()
    army = _make_army(auth_client, f)
    auth_client.post(f"/me/armies/{army['id']}/units", json={"unit_id": str(unit.id), "amount": 1})
    resp = auth_client.patch(f"/me/armies/{army['id']}/units/{unit.id}", json={"amount": 0})
    assert resp.status_code == 400


def test_remove_unit(auth_client, make_faction, make_unit):
    f = make_faction()
    unit = make_unit()
    army = _make_army(auth_client, f)
    auth_client.post(f"/me/armies/{army['id']}/units", json={"unit_id": str(unit.id), "amount": 1})
    resp = auth_client.delete(f"/me/armies/{army['id']}/units/{unit.id}")
    assert resp.status_code == 204


def test_shortfall(auth_client, session, make_faction, make_unit):
    f = make_faction()
    unit = make_unit()
    army = _make_army(auth_client, f)
    auth_client.post(f"/me/armies/{army['id']}/units", json={"unit_id": str(unit.id), "amount": 3})
    session.add(UserUnit(owner_user_id=auth_client.user.id, unit_id=unit.id, amount=1))
    session.commit()
    resp = auth_client.get(f"/me/armies/{army['id']}/shortfall")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["need"] == 2


def test_army_read_includes_points(auth_client, make_faction, make_unit):
    f = make_faction()
    unit = make_unit(faction=f, points=100)
    army = _make_army(auth_client, f, points_limit=2000)
    auth_client.post(f"/me/armies/{army['id']}/units", json={"unit_id": str(unit.id), "amount": 3})
    resp = auth_client.get(f"/me/armies/{army['id']}")
    body = resp.json()
    assert body["points_limit"] == 2000
    assert body["points_total"] == 300


def test_create_army_with_points_limit(auth_client, make_faction):
    f = make_faction()
    resp = auth_client.post("/me/armies", json={"name": "A", "faction_id": str(f.id), "points_limit": 1000})
    assert resp.status_code == 201
    assert resp.json()["points_limit"] == 1000


def test_update_army_points_limit(auth_client, make_faction):
    f = make_faction()
    army = _make_army(auth_client, f)
    resp = auth_client.patch(f"/me/armies/{army['id']}", json={"points_limit": 1500})
    assert resp.status_code == 200
    assert resp.json()["points_limit"] == 1500


def test_validate_endpoint_ok(auth_client, make_faction, make_unit):
    f = make_faction()
    unit = make_unit(faction=f, points=80)
    army = _make_army(auth_client, f, points_limit=2000)
    auth_client.post(f"/me/armies/{army['id']}/units", json={"unit_id": str(unit.id), "amount": 2})
    resp = auth_client.get(f"/me/armies/{army['id']}/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["points_total"] == 160
    assert body["issues"] == []


def test_validate_endpoint_reports_over_points(auth_client, make_faction, make_unit):
    f = make_faction()
    unit = make_unit(faction=f, points=80)
    army = _make_army(auth_client, f, points_limit=100)
    auth_client.post(f"/me/armies/{army['id']}/units", json={"unit_id": str(unit.id), "amount": 2})
    resp = auth_client.get(f"/me/armies/{army['id']}/validate")
    assert resp.json()["ok"] is False
    assert any(i["kind"] == "over_points" for i in resp.json()["issues"])


def test_add_unit_zero_amount_returns_422(auth_client, make_faction, make_unit):
    army = _make_army(auth_client, make_faction())
    unit = make_unit()
    resp = auth_client.post(f"/me/armies/{army['id']}/units", json={"unit_id": str(unit.id), "amount": 0})
    assert resp.status_code == 422  # schema ge=1
