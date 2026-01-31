import os
from sqlmodel import Session, create_engine

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(DATABASE_URL)


def get_session() -> Session:
    # Return a fresh SQLModel Session (has .exec for select statements)
    return Session(engine)
