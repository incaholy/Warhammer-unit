"""Unit tests for app/core/security.py."""

import uuid

import pytest

from app.core.errors import ErrorCode
from app.core.security import (
    ForbiddenError,
    UnauthorizedError,
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
    with pytest.raises(UnauthorizedError) as exc:
        get_current_user(token="bad", session=session)
    assert exc.value.code == ErrorCode.UNAUTHORIZED


def test_get_current_user_missing_token_401(session):
    with pytest.raises(UnauthorizedError) as exc:
        get_current_user(token=None, session=session)
    assert exc.value.code == ErrorCode.UNAUTHORIZED


def test_get_current_user_unknown_user_401(session):
    token = create_access_token(str(uuid.uuid4()))
    with pytest.raises(UnauthorizedError) as exc:
        get_current_user(token=token, session=session)
    assert exc.value.code == ErrorCode.UNAUTHORIZED


def test_get_current_admin_forbids_non_admin(make_user):
    user = make_user()  # is_admin defaults to False
    with pytest.raises(ForbiddenError) as exc:
        get_current_admin(user=user)
    assert exc.value.code == ErrorCode.FORBIDDEN


def test_get_current_admin_allows_admin(make_user):
    user = make_user(is_admin=True)
    assert get_current_admin(user=user).id == user.id


def test_resolve_secret_key_dev_default():
    from app.core.security import _DEV_SECRET, _resolve_secret_key

    assert _resolve_secret_key("dev", None) == _DEV_SECRET


def test_resolve_secret_key_uses_provided_value():
    from app.core.security import _resolve_secret_key

    assert _resolve_secret_key("production", "real-key") == "real-key"


def test_resolve_secret_key_required_outside_dev():
    import pytest

    from app.core.security import _resolve_secret_key

    with pytest.raises(RuntimeError):
        _resolve_secret_key("production", None)
