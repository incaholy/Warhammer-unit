"""The `get_session` FastAPI dependency: rolls back on error, closes always.

`get_session` is a generator dependency, so we drive it by hand — `next()` to
enter (get the session at the `yield`), then `gen.throw(...)` to simulate the
request handler raising. The uncommitted row must not survive.
"""

import pytest
from sqlmodel import Session, select

from app.core.db import connection
from app.core.db.models import Faction


def test_get_session_rolls_back_uncommitted_work_on_error(engine, monkeypatch):
    # Point the dependency at the in-memory test engine (tables already created).
    monkeypatch.setattr(connection, "get_engine", lambda: engine)

    gen = connection.get_session()
    session = next(gen)
    session.add(Faction(name="Rollback Test"))  # staged, never committed

    # The request handler raises -> the dependency should roll back and re-raise.
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("boom"))

    # A fresh session on the same DB sees nothing — the add was discarded.
    with Session(engine) as check:
        found = check.exec(
            select(Faction).where(Faction.name == "Rollback Test")
        ).first()
    assert found is None


def test_get_session_keeps_committed_work(engine, monkeypatch):
    # Committed work survives even if the request later errors: commit() already
    # persisted it, and rollback only discards what's still pending.
    monkeypatch.setattr(connection, "get_engine", lambda: engine)

    gen = connection.get_session()
    session = next(gen)
    session.add(Faction(name="Committed"))
    session.commit()

    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("boom"))

    with Session(engine) as check:
        found = check.exec(
            select(Faction).where(Faction.name == "Committed")
        ).first()
    assert found is not None
