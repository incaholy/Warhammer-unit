"""Schema helpers for the services layer: introspection, and a portable JSON filter.

Services need to know which of their updatable fields are backed by NOT NULL
columns, so a PATCH carrying an explicit null is rejected as a clean 400 instead
of reaching the database. That knowledge already lives in `models.py`; reading it
back off the mapped table keeps it in one place, so adding a NOT NULL column can
never leave a stale hand-written set behind.
"""

from sqlalchemy import Column, ColumnElement, exists, func, literal, select, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel


def not_nullable_fields(model: type[SQLModel], fields: set[str]) -> frozenset[str]:
    """Of `fields`, the ones mapped to a NOT NULL column on `model`.

    Field names that aren't columns (a relationship, or a write-only alias) are
    skipped rather than guessed at.
    """
    columns = model.__table__.columns  # type: ignore[attr-defined]
    return frozenset(name for name in fields if name in columns and not columns[name].nullable)


def json_list_contains(column: Column, value: str, *, dialect: str) -> ColumnElement[bool]:
    """Rows whose JSON string-array `column` contains `value`, on either backend.

    The one place in this codebase that has to know which database it is talking to, and
    it lives here rather than in a service so there is exactly one of them. `dialect` is
    `session.get_bind().dialect.name` -- taken from the CONNECTION, not the column,
    because `STRING_LIST` is the same mapped type either way and only the bind decides
    whether it is stored as `JSONB` or as `JSON` (decision #26).

      Postgres  `keywords @> '["LEADER"]'`, which a GIN index on `keywords` answers, so
                this is the form that stays fast as the catalog grows.
      SQLite    an EXISTS over `json_each(keywords)`, which is a scan. Fine at 454 rows,
                and the point of supporting it is that the default test tier is SQLite:
                a filter that only worked on Postgres would be untested on every run.

    Not done with a `LIKE '%"LEADER"%'` over the serialised text, which would be portable
    in one expression and wrong twice over: a `LIKE` cannot tell `GUN` from the `GUN` in
    `GUN SERVITOR` without relying on the quoting to tokenise for it, and it could never
    use the index.
    """
    if dialect == "postgresql":
        # `type_coerce` and not `cast`: the mapped type is plain `JSON` (the JSONB variant
        # applies at DDL time, not to operator dispatch), so `column.contains()` would
        # render a LIKE over the serialised text. Coercing emits no SQL of its own, so the
        # operator becomes `@>` and a GIN index on the column can still answer it -- which
        # a `cast(... AS JSONB)` would have prevented.
        return type_coerce(column, JSONB).contains([value])
    # No `render_derived()`: it emits `AS alias(value)`, the derived column list, which
    # SQLite rejects outright. Aliased without it, `json_each(keywords) AS x` is exactly
    # what that backend expects.
    element = func.json_each(column).table_valued("value")
    return exists(select(literal(1)).select_from(element).where(element.c.value == value))
