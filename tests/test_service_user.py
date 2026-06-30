"""Tests for UserService.

These fail until app/core/services/service_user.py exists with a UserService
class that matches this contract:

    UserService(session)
      create_user(username, email, password_hash) -> User
          raises ValueError on duplicate username or email
      get_user(user_id) -> User
          raises LookupError if not found
"""

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
