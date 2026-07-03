"""API tests for the users router (via TestClient)."""

import uuid


def test_create_user(client):
    resp = client.post(
        "/users",
        json={"username": "max", "email": "max@test.io", "password_hash": "h"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "max"
    assert body["email"] == "max@test.io"
    assert "id" in body
    assert "password_hash" not in body  # internal field not exposed


def test_get_user(client):
    created = client.post(
        "/users",
        json={"username": "max", "email": "max@test.io", "password_hash": "h"},
    ).json()
    resp = client.get(f"/users/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_user_missing_returns_404(client):
    resp = client.get(f"/users/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_duplicate_username_returns_400(client):
    client.post(
        "/users",
        json={"username": "dup", "email": "a@test.io", "password_hash": "h"},
    )
    resp = client.post(
        "/users",
        json={"username": "dup", "email": "b@test.io", "password_hash": "h"},
    )
    assert resp.status_code == 400


def test_invalid_body_returns_422(client):
    resp = client.post("/users", json={"username": "onlyname"})
    assert resp.status_code == 422
