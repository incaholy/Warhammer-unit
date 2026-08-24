"""Shared test fixtures.

Tests run against an in-memory SQLite database (one fresh schema per test) with
foreign-key enforcement turned on, so cascades and FK/constraint rules behave
like Postgres. Object factories build valid rows so individual tests only have
to spell out what they care about.
"""

import itertools
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# Importing the models registers every table on SQLModel.metadata.
from app.core.db.connection import get_session
from app.core.db.models import Army, Faction, Subfaction, Unit, User
from app.core.security import create_access_token
from app.main import app

# Two test tiers (ROADMAP R6):
#   - default: fast in-memory SQLite, schema from the models (create_all).
#   - parity:  set TEST_DATABASE_URL to a Postgres URL and the schema is built by
#              running the real Alembic migrations, so the suite executes against
#              a Postgres schema produced by `alembic upgrade head`. That catches
#              what SQLite silently allows (length limits, tz-aware timestamps,
#              native UUID/JSON) and proves the migrations apply.
# A dedicated variable (not DATABASE_URL) keeps a plain `pytest` from ever
# touching a developer's real database.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
_ROOT = Path(__file__).resolve().parent.parent


def _guard_parity_target(url: str) -> None:
    """Refuse to run the parity tier against anything that isn't obviously
    disposable. This tier `DROP SCHEMA ... CASCADE`s and rebuilds, so a
    `TEST_DATABASE_URL` pointed at a real database would wipe it. The guard turns
    that silent data loss into a loud, up-front error."""
    target = make_url(url)
    name = (target.database or "").lower()
    if "test" not in name:
        raise RuntimeError(
            f"refusing to run the Postgres parity tier: TEST_DATABASE_URL names database "
            f"{target.database!r}, which does not contain 'test'. This tier drops and "
            f"rebuilds the schema — point it at a throwaway database (e.g. ..._test)."
        )
    prod = os.getenv("DATABASE_URL")
    if prod:
        p = make_url(prod)
        if (target.host, target.port, target.database) == (p.host, p.port, p.database):
            raise RuntimeError(
                "refusing to run the Postgres parity tier: TEST_DATABASE_URL points at the "
                "same database as DATABASE_URL. Use a separate throwaway database."
            )


def _build_postgres_schema(url: str) -> None:
    """Drop everything and rebuild the schema by running the migrations, so each
    test starts from a freshly *migrated* Postgres database."""
    from alembic import command
    from alembic.config import Config

    _guard_parity_target(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "app/core/db/alembic"))
    # env.py reads DATABASE_URL; point it at the test DB just for the upgrade.
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev


# The API version prefix all resource routes mount under (ROADMAP R5). Tests
# address routes by their bare path (e.g. "/units"); this client prepends the
# prefix so every test doesn't have to. "/health" is unversioned and passes
# through unchanged.
API_V1_PREFIX = "/api/v1"


class PrefixTestClient(TestClient):
    """A TestClient that prepends `API_V1_PREFIX` to absolute API paths, mirroring
    a real client's base URL — so tests read against the logical resource path
    while the app serves it under the versioned prefix."""

    def request(self, method, url, *args, **kwargs):
        if (
            isinstance(url, str)
            and url.startswith("/")
            and not url.startswith(API_V1_PREFIX)
            and not url.startswith("/health")
        ):
            url = API_V1_PREFIX + url
        return super().request(method, url, *args, **kwargs)


# Unique-ish suffixes for fields with UNIQUE constraints (username, email,
# faction name). A single global counter is enough: each test gets a fresh DB,
# so uniqueness only has to hold within one test.
_counter = itertools.count(1)


@pytest.fixture(name="engine")
def engine_fixture():
    # Parity tier: a freshly-migrated Postgres database per test (schema built by
    # the real migrations, not create_all). See TEST_DATABASE_URL above.
    if TEST_DATABASE_URL:
        _build_postgres_schema(TEST_DATABASE_URL)
        engine = create_engine(TEST_DATABASE_URL)
        yield engine
        engine.dispose()
        return

    # Default tier: fast in-memory SQLite, schema from the models.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared in-memory DB for the whole test
    )

    # SQLite only enforces foreign keys when asked, per connection.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="session")
def session_fixture(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session):
    """A TestClient whose routes run against the test `session` (same DB as the
    factories) by overriding the `get_session` dependency. The override mirrors
    production's boundary — commit on success, roll back on error — so tests
    exercise the real transaction behavior (services flush; the request commits).
    It does *not* close the session, which the `session` fixture owns."""

    def _session_override():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    app.dependency_overrides[get_session] = _session_override
    with PrefixTestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _authed_client(user):
    """A fresh TestClient bearing `user`'s JWT, with the user exposed as `.user`.
    Its own client (not the shared `client`) so auth_client and admin_client can be
    used together in one test."""
    c = PrefixTestClient(app)
    c.headers["Authorization"] = f"Bearer {create_access_token(str(user.id))}"
    c.user = user
    return c


@pytest.fixture(name="auth_client")
def auth_client_fixture(client, make_user):
    """A TestClient authenticated as a fresh non-admin user (`.user`). Depends on
    `client` only to set up/tear down the `get_session` override."""
    return _authed_client(make_user())


@pytest.fixture(name="admin_client")
def admin_client_fixture(client, make_user):
    """A TestClient authenticated as a fresh admin user (`.user`)."""
    return _authed_client(make_user(is_admin=True))


# --------------------------- object factories ---------------------------
# Each factory returns a committed, refreshed row. Pass overrides to customize.


@pytest.fixture
def make_faction(session):
    def _make(name=None):
        faction = Faction(name=name or f"Faction {next(_counter)}")
        session.add(faction)
        session.commit()
        session.refresh(faction)
        return faction

    return _make


@pytest.fixture
def make_subfaction(session, make_faction):
    def _make(faction=None, name=None):
        faction = faction or make_faction()
        sub = Subfaction(faction_id=faction.id, name=name or f"Subfaction {next(_counter)}")
        session.add(sub)
        session.commit()
        session.refresh(sub)
        return sub

    return _make


@pytest.fixture
def make_unit(session, make_faction):
    def _make(faction=None, **overrides):
        faction = faction or make_faction()
        data = dict(
            unit_name="Intercessor",
            movement=6,
            toughness=4,
            armor_save=3,
            wounds=2,
            leadership=6,
            objective_control=2,
            points=80,
        )
        data.update(overrides)
        unit = Unit(faction_id=faction.id, **data)
        session.add(unit)
        session.commit()
        session.refresh(unit)
        return unit

    return _make


@pytest.fixture
def make_user(session):
    def _make(**overrides):
        n = next(_counter)
        data = dict(username=f"user{n}", email=f"user{n}@test.io", password_hash="x")
        data.update(overrides)
        user = User(**data)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    return _make


@pytest.fixture
def make_army(session, make_user, make_faction):
    def _make(owner=None, faction=None, **overrides):
        owner = owner or make_user()
        faction = faction or make_faction()
        data = dict(name="The Hollow Vigil")
        data.update(overrides)
        army = Army(owner_user_id=owner.id, faction_id=faction.id, **data)
        session.add(army)
        session.commit()
        session.refresh(army)
        return army

    return _make
