"""The parity tier drops and rebuilds the target schema, so `_guard_parity_target`
refuses to run against anything that isn't obviously a throwaway test database
(ROADMAP R6). These run in the default tier — the guard is pure URL inspection,
no database needed."""

import pytest
from conftest import _guard_parity_target


def test_guard_rejects_database_without_test_in_the_name():
    with pytest.raises(RuntimeError, match="does not contain 'test'"):
        _guard_parity_target("postgresql+psycopg2://u:p@localhost:5432/warhammer_unit")


def test_guard_rejects_target_equal_to_database_url(monkeypatch):
    # Even a 'test'-named DB is refused if it's the same one DATABASE_URL points at.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/wh_test")
    with pytest.raises(RuntimeError, match="same database as DATABASE_URL"):
        _guard_parity_target("postgresql+psycopg2://u:p@localhost:5432/wh_test")


def test_guard_allows_a_disposable_test_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # A throwaway '..._test' database with no clashing DATABASE_URL is fine.
    _guard_parity_target("postgresql+psycopg2://u:p@localhost:5432/wh_parity_test")
