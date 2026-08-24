"""The `get_session` FastAPI dependency: commits on success, rolls back on error,
closes always — it is the request's transaction boundary.

`get_session` is a generator dependency, so we drive it by hand — `next()` to
enter (get the session at the `yield`), then either drive it to completion (which
commits) or `gen.throw(...)` to simulate the request handler raising (which rolls
back). Uncommitted work must not survive an error; flushed work must persist on
success.
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
        found = check.exec(select(Faction).where(Faction.name == "Rollback Test")).first()
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
        found = check.exec(select(Faction).where(Faction.name == "Committed")).first()
    assert found is not None


def test_get_session_commits_flushed_work_on_success(engine, monkeypatch):
    # Driving the generator to completion (no error) commits the flushed work, so
    # a service that only flushes still persists at the request boundary.
    monkeypatch.setattr(connection, "get_engine", lambda: engine)

    gen = connection.get_session()
    session = next(gen)
    session.add(Faction(name="Flushed then committed"))
    session.flush()  # a service flushes, never commits

    with pytest.raises(StopIteration):
        next(gen)  # completing the generator runs the commit after the yield

    with Session(engine) as check:
        found = check.exec(select(Faction).where(Faction.name == "Flushed then committed")).first()
    assert found is not None
