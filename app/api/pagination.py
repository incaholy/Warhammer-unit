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

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams:
    """Shared `limit`/`offset` query params for list endpoints (`Depends()`)."""

    def __init__(
        self,
        limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        offset: int = Query(default=0, ge=0),
    ):
        self.limit = limit
        self.offset = offset


def paginate(items: list, total: int, params: PageParams) -> dict:
    """Build a Page body. Returned as a dict so FastAPI serializes `items`
    through the endpoint's `response_model=Page[X]` (the same ORM→schema path a
    bare `list[X]` return already uses)."""
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}
