"""The first-admin bootstrap helper (scripts/make_admin.promote)."""

import pytest

from app.core.services.errors import NotFoundError
from scripts.make_admin import promote


def test_promote_sets_is_admin(session, make_user):
    user = make_user()
    assert user.is_admin is False
    promoted = promote(session, user.username)
    assert promoted.is_admin is True


def test_promote_unknown_user_raises_not_found(session):
    with pytest.raises(NotFoundError):
        promote(session, "nobody")
