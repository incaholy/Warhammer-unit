"""API tests for the /auth router (via TestClient)."""

PASSWORD = "password1"  # valid: meets Register_Create's min_length=8


def _register(client, username="max", email="max@test.io", password=PASSWORD):
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


def test_register_empty_email_returns_422(client):
    assert _register(client, email="").status_code == 422


def test_register_short_email_returns_422(client):
    assert _register(client, email="a@b").status_code == 422  # below min_length


def test_register_short_password_returns_422(client):
    assert _register(client, password="short").status_code == 422  # < 8 chars


def test_register_long_password_returns_422(client):
    assert _register(client, password="x" * 73).status_code == 422  # > 72 chars


def test_register_duplicate_returns_409(client):
    _register(client, username="dup", email="a@test.io")
    resp = _register(client, username="dup", email="b@test.io")
    assert resp.status_code == 409  # duplicate = conflict


def test_login_returns_token(client):
    _register(client, username="max")
    resp = client.post("/auth/login", data={"username": "max", "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_by_email(client):
    _register(client, username="max", email="max@test.io")
    resp = client.post(
        "/auth/login", data={"username": "max@test.io", "password": PASSWORD}
    )
    assert resp.status_code == 200


def test_login_wrong_password_returns_401(client):
    _register(client, username="max")
    resp = client.post("/auth/login", data={"username": "max", "password": "nope"})
    assert resp.status_code == 401


def test_login_unknown_user_returns_401(client):
    resp = client.post("/auth/login", data={"username": "ghost", "password": "pw"})
    assert resp.status_code == 401
