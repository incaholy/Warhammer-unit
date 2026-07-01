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
    """Yield a Session and close it afterwards.

    This is the FastAPI dependency shape: `Depends(get_session)` closes/rolls
    back the session after each request. Scripts that want a session directly
    can use `Session(get_engine())`.
    """
    with Session(get_engine()) as session:
        yield session
