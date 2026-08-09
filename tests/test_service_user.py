"""Tests for UserService — create/fetch users and grant/revoke admin."""

import uuid

import pytest

from app.core.services.service_user import UserService


def test_create_user(session):
    svc = UserService(session)
    user = svc.create_user(username="max", email="max@test.io", password_hash="hash")
    assert user.id is not None
    assert user.username == "max"
    assert user.email == "max@test.io"


def test_get_user(session):
    svc = UserService(session)
    created = svc.create_user(username="max", email="max@test.io", password_hash="h")
    fetched = svc.get_user(created.id)
    assert fetched.id == created.id


def test_get_user_missing_raises_lookup_error(session):
    svc = UserService(session)
    with pytest.raises(LookupError):
        svc.get_user(uuid.uuid4())


def test_duplicate_username_raises_value_error(session):
    svc = UserService(session)
    svc.create_user(username="dup", email="a@test.io", password_hash="h")
    with pytest.raises(ValueError):
        svc.create_user(username="dup", email="b@test.io", password_hash="h")


def test_duplicate_email_raises_value_error(session):
    svc = UserService(session)
    svc.create_user(username="a", email="dup@test.io", password_hash="h")
    with pytest.raises(ValueError):
        svc.create_user(username="b", email="dup@test.io", password_hash="h")


def test_set_admin_promotes(session):
    svc = UserService(session)
    user = svc.create_user("a", "a@test.io", "h")
    assert user.is_admin is False
    assert svc.set_admin(user.id, True).is_admin is True


def test_set_admin_missing_raises_not_found(session):
    from app.core.services.errors import NotFoundError

    svc = UserService(session)
    with pytest.raises(NotFoundError):
        svc.set_admin(uuid.uuid4(), True)


def test_set_admin_cannot_demote_last_admin(session):
    from app.core.services.errors import ConflictError

    svc = UserService(session)
    admin = svc.create_user("solo", "solo@test.io", "h")
    svc.set_admin(admin.id, True)  # the only admin
    with pytest.raises(ConflictError):
        svc.set_admin(admin.id, False)


def test_set_admin_can_demote_when_another_admin_exists(session):
    svc = UserService(session)
    a = svc.create_user("a", "a@test.io", "h")
    b = svc.create_user("b", "b@test.io", "h")
    svc.set_admin(a.id, True)
    svc.set_admin(b.id, True)  # two admins now
    assert svc.set_admin(a.id, False).is_admin is False  # demoting one is allowed
