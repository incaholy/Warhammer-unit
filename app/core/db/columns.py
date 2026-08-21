"""Schema introspection for the services layer.

Services need to know which of their updatable fields are backed by NOT NULL
columns, so a PATCH carrying an explicit null is rejected as a clean 400 instead
of reaching the database. That knowledge already lives in `models.py`; reading it
back off the mapped table keeps it in one place, so adding a NOT NULL column can
never leave a stale hand-written set behind.
"""

from sqlmodel import SQLModel


def not_nullable_fields(model: type[SQLModel], fields: set[str]) -> frozenset[str]:
    """Of `fields`, the ones mapped to a NOT NULL column on `model`.

    Field names that aren't columns (a relationship, or a write-only alias) are
    skipped rather than guessed at.
    """
    columns = model.__table__.columns  # type: ignore[attr-defined]
    return frozenset(name for name in fields if name in columns and not columns[name].nullable)
