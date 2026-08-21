"""Shared error vocabulary.

`ErrorCode` is the stable, machine-readable category carried on every error
response as `code` (the client branches on it instead of HTTP status or message
text). It lives here — below both the service and API layers — so service errors
can carry a `code` and the API layer can map `code` -> HTTP status, without
either layer importing the other.
"""

from enum import StrEnum


class CodedError(Exception):
    """Marker base for every error that carries a wire `code`.

    Its only job is to be catchable as one family, so `app/main.py` registers a
    single handler instead of a hand-maintained tuple of concrete classes — a
    registry that silently turned a forgotten class into a 500.

    It adds no behaviour and is never raised directly. Concrete errors still
    inherit the builtin they map to *as well* (`NotFoundError(CodedError,
    LookupError)`), so service-level tests can keep catching `LookupError` /
    `ValueError`. Crucially this is **our** base, not a builtin: registering a
    handler for it can never swallow a library exception, which is what a blanket
    `ValueError` / `LookupError` handler would do.
    """

    code: "ErrorCode"
    message: str
    field: str | None = None


class ErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    VALIDATION = "VALIDATION"  # a business rule failed (well-formed request) -> 400
    REQUEST_VALIDATION = "REQUEST_VALIDATION"  # malformed request (Pydantic) -> 422
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    INTERNAL = "INTERNAL"
