"""Assert the Alembic migrations and `models.py` actually agree (ROADMAP R6).

The suite normally builds its schema from the models (`create_all`), which never
runs a migration — so a model change with no matching migration would pass every
test and only fail at deploy time. This check closes that gap: it builds the
schema by running the migrations, then autogenerates a diff against the models
and fails on any difference.

Parity tier only: it needs a real database and the migrations, so it runs when
`TEST_DATABASE_URL` points at Postgres and is skipped in the default SQLite tier
(where the generated DDL diverges from Postgres anyway).
"""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import TEST_DATABASE_URL, _build_postgres_schema
from sqlmodel import SQLModel, create_engine

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="parity tier only — needs Postgres + the real migrations (set TEST_DATABASE_URL)",
)


def test_migrations_match_models():
    _build_postgres_schema(TEST_DATABASE_URL)
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diff = compare_metadata(context, SQLModel.metadata)
    finally:
        engine.dispose()

    assert diff == [], (
        "models.py and the Alembic migrations disagree; generate a migration for "
        f"the following differences:\n{diff}"
    )
