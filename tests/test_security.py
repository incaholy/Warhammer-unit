"""Unit tests for app/core/security.py (hashing, tokens, key resolution)."""

import pytest

from app.core.security import (
    create_access_token,
    decode_token,
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


def test_resolve_secret_key_dev_default():
    from app.core.security import _DEV_SECRET, _resolve_secret_key

    assert _resolve_secret_key("dev", None) == _DEV_SECRET


def test_resolve_secret_key_uses_provided_value():
    from app.core.security import _resolve_secret_key

    assert _resolve_secret_key("production", "real-key") == "real-key"


def test_resolve_secret_key_required_outside_dev():
    from app.core.security import _resolve_secret_key

    with pytest.raises(RuntimeError):
        _resolve_secret_key("production", None)
