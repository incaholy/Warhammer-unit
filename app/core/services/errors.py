"""Typed service exceptions.

Each subclasses the builtin the API layer already maps (`LookupError` /
`ValueError`), so the existing `app/main.py` handlers keep mapping it correctly
even before the dedicated handler runs — the migration is incremental. See
SPEC.md "Custom service errors".

Naming: generic for cross-cutting failures (`NotFoundError`, `ConflictError`);
one resource-named `*ValidationError` per service, carrying the offending
`field`. Split a rule into its own class only when its handling diverges.

Each error also carries a stable `code` (`ErrorCode`) — the machine-readable
category the client branches on. The `code` is a *semantic* label and lives
here; its HTTP status is mapped separately in the API layer
(`app/api/errors.py`), so the service layer stays HTTP-agnostic.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable, machine-readable error categories, carried on the wire as `code`.

    Independent of HTTP status (mapped in `app/api/errors.py`). The first three
    are raised by services; the rest are emitted by the API-layer handlers
    (auth, request validation, unexpected faults).
    """

    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    VALIDATION = "VALIDATION"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    INTERNAL = "INTERNAL"


class ServiceError(Exception):
    """Base for service-raised errors: a `code` + message + optional `field`.

    `status_code` is retained transitionally until the API-layer handler maps
    `code` -> status via `app/api/errors.py`; it is removed once that lands.
    """

    code = ErrorCode.VALIDATION
    status_code = 400

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class NotFoundError(ServiceError, LookupError):
    """A requested row does not exist."""

    code = ErrorCode.NOT_FOUND
    status_code = 404


class ConflictError(ServiceError, ValueError):
    """A uniqueness clash (duplicate)."""

    code = ErrorCode.CONFLICT
    status_code = 409


class ValidationError(ServiceError, ValueError):
    """Base for per-service validation errors, carrying the offending `field`.

    Constructed as `(field, message)` and rendered as ``"field: message"``.
    """

    code = ErrorCode.VALIDATION

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}", field=field)


class UserValidationError(ValidationError):
    """Bad user/account input."""


class UnitValidationError(ValidationError):
    """Bad catalog input (units, factions, subfactions, weapons)."""


class ArmyValidationError(ValidationError):
    """Bad army/roster input."""


class InventoryValidationError(ValidationError):
    """Bad inventory input."""
