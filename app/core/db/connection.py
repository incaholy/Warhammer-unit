import os
from collections.abc import Iterator
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine


@lru_cache
def get_engine() -> Engine:
    """Create the engine on first use (not at import time).

    Loads `.env` and reads `DATABASE_URL` only when a session is actually
    requested, so merely importing this module (or anything that imports it)
    never requires a database. Cached, so there is one engine per process.
    """
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # pool_pre_ping avoids "server closed the connection" errors on stale
    # pooled connections in a long-running server.
    return create_engine(url, pool_pre_ping=True)


def get_session() -> Iterator[Session]:
    """Yield a Session, rolling it back on error and closing it afterwards.

    This is the FastAPI dependency shape (`Depends(get_session)`). If the
    request handler raises, the session is rolled back so a half-finished unit
    of work is never left pending — anything not already `commit()`-ed is
    discarded. On success the service's own `commit()` has already persisted the
    work. The session is closed either way. Scripts that want a session directly
    can use `Session(get_engine())`.
    """
    session = Session(get_engine())
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
