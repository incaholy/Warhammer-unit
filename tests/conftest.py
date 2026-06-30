"""Shared test fixtures.

Tests run against an in-memory SQLite database (one fresh schema per test) with
foreign-key enforcement turned on, so cascades and FK/constraint rules behave
like Postgres. Object factories build valid rows so individual tests only have
to spell out what they care about.
"""

import itertools

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# Importing the models registers every table on SQLModel.metadata.
from app.core.db.models import Army, Faction, Subfaction, Unit, User

# Unique-ish suffixes for fields with UNIQUE constraints (username, email,
# faction name). A single global counter is enough: each test gets a fresh DB,
# so uniqueness only has to hold within one test.
_counter = itertools.count(1)


@pytest.fixture(name="engine")
def engine_fixture():
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
        sub = Subfaction(
            faction_id=faction.id, name=name or f"Subfaction {next(_counter)}"
        )
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
