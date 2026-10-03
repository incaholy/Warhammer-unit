"""Shared field types for the write schemas — the bounds the columns already have.

Every `*_Create`/`*_Update` schema validates shape before a service sees it, and until
now none of them bounded the TOP of a range. A SQLModel `Field(max_length=128)`
constrains the generated DDL, **not** the Pydantic schema a router validates against,
so an over-long name or an out-of-range int reached the database and came back as a
500. SQLite accepts both — its `VARCHAR(128)` is advisory and its `INTEGER` is 64-bit —
which is why the default test tier never saw it and only the Postgres parity tier can.

These types restate the column's own limit in the published schema, where it is a 422
and where `openapi.json` carries it to the frontend's generated types.

The rule applied across the routers: **the schema always bounds the top; it bounds the
bottom only where nothing else already does.** `KTRosterOperative_Update.position` and
`AmountSet.amount` keep their service-raised 400 for a value that is too small, because
that status is part of the published contract and tested. `points_limit` had no floor
anywhere — a negative one reached the `ck_army_points_limit_non_negative` CHECK and was
reported as a 409, as though it conflicted with another resource — so it gains one here.
"""

from typing import Annotated

from pydantic import AfterValidator, StringConstraints

#: The largest value a Postgres `integer` column holds. Above it, psycopg raises
#: `NumericValueOutOfRange` mid-flush, which is a `DataError` rather than a `CodedError`
#: and so reaches the client as an unhandled 500.
INT32_MAX = 2**31 - 1


def _not_blank(value: str) -> str:
    """Refuse a name that is empty or only whitespace, without rewriting it.

    `min_length` cannot see this case: `"   "` is three characters, which is how
    `Register_Create.username` accepted a whitespace username under `min_length=3`.

    Deliberately an `AfterValidator` and not `StringConstraints(strip_whitespace=True)`:
    stripping would store something other than what the client sent, and silently make
    `" Mine "` collide with an existing `"Mine"`. This checks and leaves the value
    alone, the same way `ck_kt_selection_rule_text` checks `length(trim(text)) > 0`.
    """
    if not value.strip():
        raise ValueError("cannot be blank")
    return value


#: A name a player types, for a thing they own: `VARCHAR(128)` on `armies.name` and
#: `kt_rosters.name` alike. Bounded at both ends, because nothing downstream bounds it.
Name = Annotated[str, StringConstraints(min_length=1, max_length=128), AfterValidator(_not_blank)]

#: A username: `VARCHAR(64)`, with the floor of 3 that `Register_Create` already had.
Username = Annotated[str, StringConstraints(min_length=3, max_length=64), AfterValidator(_not_blank)]
