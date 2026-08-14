"""Shared error vocabulary.

`ErrorCode` is the stable, machine-readable category carried on every error
response as `code` (the client branches on it instead of HTTP status or message
text). It lives here — below both the service and API layers — so service errors
can carry a `code` and the API layer can map `code` -> HTTP status, without
either layer importing the other.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    VALIDATION = "VALIDATION"  # a business rule failed (well-formed request) -> 400
    REQUEST_VALIDATION = "REQUEST_VALIDATION"  # malformed request (Pydantic) -> 422
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    INTERNAL = "INTERNAL"
