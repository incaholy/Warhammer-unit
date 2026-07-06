"""Unit tests for app/core/security.py."""

import uuid

import pytest
from fastapi import HTTPException

from app.core.security import (
    create_access_token,
    decode_token,
    get_current_admin,
    get_current_user,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password():
    h = hash_password("secret")
    assert h != "secret"
    assert verify_password("secret", h) is True
    assert verify_password("wrong", h) is False


def test_token_roundtrip():
    token = create_access_token("user-123")
    assert decode_token(token) == "user-123"


def test_decode_invalid_token_raises():
    with pytest.raises(ValueError):
        decode_token("not.a.valid.token")


def test_get_current_user_returns_user(session, make_user):
    user = make_user()
    token = create_access_token(str(user.id))
    assert get_current_user(token=token, session=session).id == user.id


def test_get_current_user_bad_token_401(session):
    with pytest.raises(HTTPException) as exc:
        get_current_user(token="bad", session=session)
    assert exc.value.status_code == 401


def test_get_current_user_unknown_user_401(session):
    token = create_access_token(str(uuid.uuid4()))
    with pytest.raises(HTTPException) as exc:
        get_current_user(token=token, session=session)
    assert exc.value.status_code == 401


def test_get_current_admin_forbids_non_admin(make_user):
    user = make_user()  # is_admin defaults to False
    with pytest.raises(HTTPException) as exc:
        get_current_admin(user=user)
    assert exc.value.status_code == 403


def test_get_current_admin_allows_admin(make_user):
    user = make_user(is_admin=True)
    assert get_current_admin(user=user).id == user.id
