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


def test_admin_can_promote_user(admin_client, make_user):
    user = make_user()
    resp = admin_client.patch(f"/users/{user.id}", json={"is_admin": True})
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is True


def test_promote_requires_admin(auth_client):
    import uuid

    resp = auth_client.patch(f"/users/{uuid.uuid4()}", json={"is_admin": True})
    assert resp.status_code == 403  # non-admin


def test_promote_missing_user_returns_404(admin_client):
    import uuid

    resp = admin_client.patch(f"/users/{uuid.uuid4()}", json={"is_admin": True})
    assert resp.status_code == 404
