"""API tests for the /auth router (via TestClient)."""


def _register(client, username="max", email="max@test.io", password="pw"):
    return client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": password},
    )


def test_register(client):
    resp = _register(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "max"
    assert body["email"] == "max@test.io"
    assert "password_hash" not in body  # never exposed
    assert body["is_admin"] is False  # exposed on User_Read; new users aren't admins


def test_register_duplicate_returns_409(client):
    _register(client, username="dup", email="a@test.io")
    resp = _register(client, username="dup", email="b@test.io")
    assert resp.status_code == 409  # duplicate = conflict


def test_login_returns_token(client):
    _register(client, username="max", email="max@test.io", password="pw")
    resp = client.post("/auth/login", data={"username": "max", "password": "pw"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_by_email(client):
    _register(client, username="max", email="max@test.io", password="pw")
    resp = client.post(
        "/auth/login", data={"username": "max@test.io", "password": "pw"}
    )
    assert resp.status_code == 200


def test_login_wrong_password_returns_401(client):
    _register(client, username="max", email="max@test.io", password="pw")
    resp = client.post("/auth/login", data={"username": "max", "password": "nope"})
    assert resp.status_code == 401


def test_login_unknown_user_returns_401(client):
    resp = client.post("/auth/login", data={"username": "ghost", "password": "pw"})
    assert resp.status_code == 401
