"""The pagination convention, shared by every list endpoint (ROADMAP R4).

One offset-based envelope, applied uniformly:

    Page[X] = { items: list[X], total, limit, offset }

`total` is the count across the filter, ignoring paging, and it travels in the
**body** — never a response header, which is invisible to cross-origin JS unless
named in the CORS `expose_headers` allow-list (the trap that hid the catalog's
total in the Firebase→Cloud Run deploy). See ARCHITECTURE.md §2.3.
"""

from fastapi import Query
from pydantic import BaseModel

from app.api.fields import INT32_MAX

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_OFFSET = INT32_MAX


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams:
    """Shared `limit`/`offset` query params for list endpoints (`Depends()`).

    Both are bounded at BOTH ends. `offset` had only `ge=0`, and an unbounded upper
    end is a 500 rather than a 422: a Python int has no width, so `?offset=10**21`
    passed validation and reached the driver, which raised `OverflowError` on SQLite
    and `NumericValueOutOfRange` on Postgres -- neither a `CodedError`, so neither
    handled. `INT32_MAX` is the project's existing ceiling for an incoming int
    (`app/api/fields.py`), and it is far past any reachable page.
    """

    def __init__(
        self,
        limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
    ):
        self.limit = limit
        self.offset = offset


def paginate(items: list, total: int, params: PageParams) -> dict:
    """Build a Page body. Returned as a dict so FastAPI serializes `items`
    through the endpoint's `response_model=Page[X]` (the same ORM→schema path a
    bare `list[X]` return already uses)."""
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}
