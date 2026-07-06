"""API tests for the users router (GET /me)."""


def test_get_me(auth_client):
    resp = auth_client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(auth_client.user.id)
    assert body["username"] == auth_client.user.username
    assert "password_hash" not in body


def test_me_requires_auth(client):
    resp = client.get("/me")  # no token
    assert resp.status_code == 401
