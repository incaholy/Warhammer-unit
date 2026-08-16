"""Unit tests for app/api/deps.py (the auth dependencies)."""

import uuid

import pytest

from app.api.deps import get_current_admin, get_current_user
from app.core.errors import ErrorCode
from app.core.security import ForbiddenError, UnauthorizedError, create_access_token


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
