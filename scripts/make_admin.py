"""Promote a user to admin — an operator action, run out of band.

    python -m scripts.make_admin <username>
    make create-admin USERNAME=<username>

Because `is_admin` defaults false, the *first* admin can't be made over the
admin-gated HTTP API (chicken-and-egg). This uses a direct session (no HTTP/auth),
like the seed script, and needs only `DATABASE_URL`.
"""

import sys

from sqlmodel import Session, select

from app.core.db.connection import get_engine
from app.core.db.models import User
from app.core.services.errors import NotFoundError


def promote(session: Session, username: str) -> User:
    """Set `is_admin = True` on the named user; `NotFoundError` if absent."""
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None:
        raise NotFoundError(f"user {username!r} not found")
    user.is_admin = True
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m scripts.make_admin <username>", file=sys.stderr)
        raise SystemExit(2)
    try:
        with Session(get_engine()) as session:
            promote(session, sys.argv[1])
    except NotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"{sys.argv[1]!r} is now an admin")


if __name__ == "__main__":
    main()
